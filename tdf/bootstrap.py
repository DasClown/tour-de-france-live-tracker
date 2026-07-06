"""REST-Bootstrap: aktuelle GC, Trikots, letzte Telemetrie holen.

Ermöglicht „starten, wo wir gerade sind": statt auf SSE zu warten, zieht der
Server beim Startup die offizielle Rangliste und letzte Telemetrie per REST.
Verifiziert am 2026-07-05:
  /api/rankingTypeArrival-2026-2 -> Array von {checkpoint,type,rankings[]};
    filter type=="itg" liefert die Gesamtwertung, Zeiten in MS.
  /api/rankingTypeJerseys-2026-3 -> Array von {checkpoint,type,rankings[]};
    type=pmt/pmp/pmm/pmj pro Trikot, position 1 = Träger.
  /api/telemetryCompetitor-2026 -> 1-Eintrag-Array mit letztem Snapshot.
  /api/pack-2026-2               -> "dirty" Array; Filter _bind=="pack-2026-2".
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from . import config as cfg
from . import state as st

log = logging.getLogger("tdf.bootstrap")


async def _get_json(session: aiohttp.ClientSession, url: str) -> Any:
    async with session.get(url, headers=cfg.JSON_HEADERS,
                           timeout=aiohttp.ClientTimeout(total=cfg.REST_TIMEOUT_S)) as r:
        r.raise_for_status()
        return await r.json()


# --------------------------------------------------------------------------- #
# GC
# --------------------------------------------------------------------------- #
async def fetch_current_gc(session: aiohttp.ClientSession, year: int,
                           stage: int) -> list[st.RankEntry]:
    """Liefert die aktuelle Gesamtwertung (type=itg) als RankEntries.

    Der Arrival-Endpoint liefert mehrere Blöcke (itg/ete/ite/...). Wir nehmen
    den itg-Block mit der längsten Ranking-Liste (die echte GC).
    """
    data = await _get_json(session, cfg.url_ranking_arrival(year, stage))
    candidates: list[list[st.RankEntry]] = []
    for block in data:
        if not isinstance(block, dict):
            continue
        if block.get("type") == cfg.TYPE_GC:
            entries = st.parse_rankings(block)
            if entries:
                candidates.append(entries)
    if not candidates:
        log.warning("Kein itg-Block in rankingTypeArrival-%d-%d", year, stage)
        return []
    # Echte GC = längste Liste (andere itg-Blöcke sind Zwischenwerte).
    best = max(candidates, key=len)
    log.info("Bootstrap-GC: %d Fahrer (Spitze bib %d)", len(best),
             best[0].bib if best else -1)
    return best


# --------------------------------------------------------------------------- #
# Jerseys
# --------------------------------------------------------------------------- #
async def fetch_jerseys(session: aiohttp.ClientSession, year: int,
                        next_stage: int) -> dict[str, int]:
    """Liefert Trikot-Code -> Bib des Trägers.

    next_stage: die Nummer im Jerseys-Bind (meist aktuelle Etappe + 1).
    """
    data = await _get_json(session, cfg.url_ranking_jerseys(year, next_stage))
    out: dict[str, int] = {}
    for block in data:
        if not isinstance(block, dict):
            continue
        rtype = block.get("type") or ""
        code = cfg.JERSEY_TYPE_TO_CODE.get(rtype)
        if not code:
            continue
        for r in block.get("rankings") or []:
            if isinstance(r, dict) and int(r.get("position", 0) or 0) == 1:
                bib = r.get("bib")
                if isinstance(bib, int):
                    out[code] = bib
                break
    log.info("Bootstrap-Jerseys: %s", {cfg.JERSEYS.get(k, k): v for k, v in out.items()})
    return out


# --------------------------------------------------------------------------- #
# Telemetrie
# --------------------------------------------------------------------------- #
async def fetch_last_telemetry(session: aiohttp.ClientSession, year: int) -> dict | None:
    """Liefert den letzten telemetryCompetitor-Snapshot (oder None)."""
    data = await _get_json(session, cfg.url_telemetry(year))
    if isinstance(data, list) and data:
        snap = data[0]
        if isinstance(snap, dict):
            log.info("Bootstrap-Telemetrie: %d Riders, RaceStatus=%s",
                     len(snap.get("Riders") or []), snap.get("RaceStatus"))
            return snap
    elif isinstance(data, dict) and data:
        return data
    return None


# --------------------------------------------------------------------------- #
# Pack (optional)
# --------------------------------------------------------------------------- #
async def fetch_pack(session: aiohttp.ClientSession, year: int,
                     stage: int) -> list[st.GroupInfo]:
    """Liefert Gruppen aus /api/pack-<jahr>-<etappe>.

    Der Endpunkt ist „dirty": er enthält eingebettete allCompetitors-Objekte.
    Filter auf _bind == "pack-<jahr>-<etappe>".
    """
    try:
        data = await _get_json(session, cfg.url_pack(year, stage))
    except aiohttp.ClientError as e:
        log.warning("Pack-Bootstrap fehlgeschlagen (%s)", e)
        return []
    target = cfg.bind_pack(year, stage)
    for entry in data:
        if isinstance(entry, dict) and entry.get("_bind") == target:
            return st.parse_groups(entry)
    return []


# --------------------------------------------------------------------------- #
# Gesamt-Bootstrap
# --------------------------------------------------------------------------- #
async def bootstrap_state(state: st.State, session: aiohttp.ClientSession, *,
                          year: int, stage: int, next_stage: int | None) -> None:
    """Füllt ``state`` mit REST-Werten (GC, Jerseys, Telemetrie, Pack).

    next_stage: Etappen-Nummer für Jerseys-Bind (meist stage+1). Falls None,
    wird stage+1 angenommen.
    """
    if next_stage is None:
        next_stage = stage + 1

    # 1) GC der aktuellen Etappe (Live-Ergebnis, type=itg).
    #    Bleibt oft leer, solange ASO keine aktuelle GC published (typisch
    #    während des Rennens). Schreibt in current_stage_gc.
    try:
        state.current_stage_gc = await fetch_current_gc(session, year, stage)
        if state.current_stage_gc:
            log.info("Bootstrap: aktuelle Etappen-GC geladen (%d Fahrer)",
                     len(state.current_stage_gc))
    except (aiohttp.ClientError, ValueError) as e:
        log.info("Aktuelle Etappen-GC nicht verfügbar (%s) — erwarte Vortages-GC", e)

    # 1b) GC der VORHERIGEN Etappe (offiziell gültige GC vom Vortag).
    #     Das ist die Quelle, die zählt, solange die aktuelle Etappe läuft.
    #     Lädt auch dann, wenn current_stage_gc oben erfolgreich war — dann
    #     hat current Vorrang, aber prev bleibt für die UI-Spalte „Vortag".
    if stage > 1:
        try:
            state.prev_stage_gc = await fetch_current_gc(session, year, stage - 1)
            if state.prev_stage_gc:
                log.info("Bootstrap: Vortages-GC (Etappe %d) geladen (%d Fahrer)",
                         stage - 1, len(state.prev_stage_gc))
        except (aiohttp.ClientError, ValueError) as e:
            log.warning("Vortages-GC nicht ladbar: %s", e)

    # 2) Jerseys.
    # WICHTIG: asyncio.TimeoutError ist NICHT Unterklasse von aiohttp.ClientError
    # und würde sonst durchbrechen und den ganzen Bootstrap killen. Wir fangen
    # alle drei Exception-Typen explizit ab.
    try:
        jerseys = await fetch_jerseys(session, year, next_stage)
        state.jerseys = jerseys
    except (aiohttp.ClientError, ValueError, asyncio.TimeoutError) as e:
        log.warning("Jerseys-Bootstrap fehlgeschlagen: %s", e)

    # 3) Letzte Telemetrie (als Startpunkt; SSE übernimmt danach).
    try:
        snap = await fetch_last_telemetry(session, year)
        if snap:
            st.apply_telemetry(state, snap, from_bootstrap=True)
    except (aiohttp.ClientError, ValueError, asyncio.TimeoutError) as e:
        log.warning("Telemetrie-Bootstrap fehlgeschlagen: %s", e)

    # 4) Pack (optional, SSE liefert ohnehin bald ein Update).
    try:
        state.groups = await fetch_pack(session, year, stage)
    except (aiohttp.ClientError, ValueError, asyncio.TimeoutError) as e:
        log.warning("Pack-Bootstrap fehlgeschlagen: %s", e)

    # Virtual GC einmal initial berechnen.
    st.recompute_virtual_gc(state)
    log.info("Bootstrap fertig: virtual_gc=%d Top-N=%s",
             len(state.virtual_gc), state.top_n[:3])
    # Initiale Feature-Evaluierung (Gap-Chart, Alarme).
    state.evaluate_features()
