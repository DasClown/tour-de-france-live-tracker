#!/usr/bin/env python3
"""
Tour-de-France Live-Tracker (v2)
=================================

Dual-Source-Design auf Basis der Offiziellen ASO Race-Center Feeds.
Korrektur der v1-Schwachstellen:

  * RaceStatus-Guard: Telemetrie wird nur als "live" ausgegeben, solange das
    Rennen läuft (RaceStatus == True). Danach: klare "stale"-Markierung mit
    Alter der Daten.
  * Wahrheitsquelle für Ränge/Zeiten sind die rankingType-Binds, NICHT die
    GPS-basierte secToFirstRider aus der Telemetrie (die nach Rennende bzw.
    bei Spitzenwendungen unsinnig ist).
  * Live-Gruppen & Lücken aus pack-Bind (computedRelative/computedSpeed/
    remainingDistance) - die zuverlässigen, von ASO berechneten Werte.
  * Defensiver Merge: Updates ergänzen den letzten Stand, statt ihn zu
    überschreiben; so gehen Fahrer nicht verloren, wenn ein Update
    unvollständig ist.

Genutzte Feeds (alle unter https://racecenter.letour.fr):

  REST (statisch, stark gecacht):
    /api/allCompetitors-<jahr>   -> Starter (Bib -> Name/Team)
    /api/stage-<jahr>            -> Etappen (heutige Etappe finden)
    /api/team-<jahr>             -> Teams (Code -> Name)

  SSE /live-stream, gefiltert nach bind:
    telemetryCompetitor-<j>            GPS/Speed pro Fahrer (live während Rennen)
    pack-<j>-<etappe>                  Live-Gruppen + berechnete Lücken
    rankingTypeArrival-<j>-<etappe>    offizielle Etappen-Rangliste (Typ itg)
    rankingTypeJerseys-<j>-<n+1>       Trikot-Träger nächste Etappe (Typ pmt)
    stageWithdrawals-<j>-<etappe>      Aufgabe von Fahrern
    rankingType-<j>-<etappe>           Zwischenwertungen (Typ ipe)

Typ-Codes (Feld "type"):
  itg = Intermediate General Classification   (Gesamt- bzw. Etappenrangliste)
  pmt = Points Maillot (Trikots)              (Y=gelb, G=grün, P=punkt, W=weiß)
  ipe = Intermediate Point (Zwischensprints/Bergwertungen)

Usage:
    python3 tdf_tracker.py                         # beide Quellen, live
    python3 tdf_tracker.py --top 10                # nur Top 10 der GC
    python3 tdf_tracker.py --bib 1,11,21           # bestimmte Fahrer
    python3 tdf_tracker.py --telemetry-only        # nur GPS/Speed (Dauertest)
    python3 tdf_tracker.py --rankings-only         # nur offizielle Rangliste
    python3 tdf_tracker.py --once                  # ein Snapshot, dann Ende
    python3 tdf_tracker.py --json out.jsonl        # Snaps als JSONL mitschneiden
    python3 tdf_tracker.py --list-stages
    python3 tdf_tracker.py --list-riders
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

BASE = "https://racecenter.letour.fr"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
JSON_HEADERS = {"User-Agent": UA, "Accept": "application/json",
                "Referer": f"{BASE}/en/"}
SSE_HEADERS = {"User-Agent": UA, "Accept": "text/event-stream",
               "Referer": f"{BASE}/en/", "Origin": BASE,
               "Cache-Control": "no-cache"}

# Trikot-Code -> Klartext. ASO nutzt im Feld "types"/Trikot-Bind die Codes.
JERSEYS = {"Y": "Gelb", "G": "Grün", "P": "Punkt", "W": "Weiß"}
# Ranking-Typen, die wir auswerten
TYPE_STAGE_GC = "itg"   # Etappen-/Gesamt-Rangliste (arrival)
TYPE_JERSEYS = "pmt"    # Trikot-Träger
TYPE_INTERMEDIATE = "ipe"  # Zwischenwertung


# --------------------------------------------------------------------------- #
# Statische Daten
# --------------------------------------------------------------------------- #
def load_riders(year: int) -> dict[int, dict[str, Any]]:
    url = f"{BASE}/api/allCompetitors-{year}"
    r = requests.get(url, headers=JSON_HEADERS, timeout=20)
    r.raise_for_status()
    out: dict[int, dict[str, Any]] = {}
    for rider in r.json():
        bib = rider.get("bib")
        if isinstance(bib, int):
            out[bib] = rider
    return out


def load_team_names(year: int) -> dict[str, str]:
    url = f"{BASE}/api/team-{year}"
    try:
        r = requests.get(url, headers=JSON_HEADERS, timeout=20)
        r.raise_for_status()
    except requests.RequestException:
        return {}
    out: dict[str, str] = {}
    for t in r.json():
        code, name = t.get("code"), t.get("nameShort") or t.get("name")
        if code and name:
            out[code] = name
    return out


def rider_team_code(rider: dict[str, Any], year: int) -> str:
    if rider.get("code"):
        return rider["code"]
    origin = rider.get("_origin") or ""
    suffix = origin.rsplit("-", 1)[-1] if origin else ""
    if suffix and suffix != str(year) and not suffix.isdigit():
        return suffix
    virt = rider.get("_virtual") or ""
    return virt.split("-", 1)[0] if virt else ""


def load_stages(year: int) -> list[dict[str, Any]]:
    url = f"{BASE}/api/stage-{year}"
    r = requests.get(url, headers=JSON_HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def today_stage_number(stages: list[dict[str, Any]]) -> int | None:
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    for s in stages:
        if str(s.get("date", "")).startswith(today):
            return s.get("stage")
    return None


# --------------------------------------------------------------------------- #
# Zustand
# --------------------------------------------------------------------------- #
@dataclass
class TelemetrySnapshot:
    """Letzter Snapshot der GPS/Speed-Tlemetrie."""
    timestamp: int | None = None           # TimeStamp aus dem Stream
    race_status: bool | None = None        # RaceStatus (True = Rennen läuft)
    stage_index: int | None = None
    riders: dict[int, dict[str, Any]] = field(default_factory=dict)


@dataclass
class GroupInfo:
    """Eine Live-Gruppe aus dem pack-Bind."""
    order: int
    name: str
    size: int
    speed: float | None                    # computedSpeed [km/h]
    remaining_km: float | None             # remainingDistance [m] -> /1000
    gap_seconds: float | None              # computedRelative [s]
    bibs: list[int] = field(default_factory=list)


@dataclass
class RankEntry:
    """Ein Eintrag aus der offiziellen Rangliste."""
    position: int
    bib: int
    relative_seconds: int | None          # Rückstand auf Spitze [s]
    absolute_ms: int | None               # absolute Zeit [ms]
    bonus_seconds: int = 0
    penalty_seconds: int = 0


@dataclass
class State:
    """Gesamtzustand beider Quellen."""
    telemetry: TelemetrySnapshot = field(default_factory=TelemetrySnapshot)
    groups: list[GroupInfo] = field(default_factory=list)
    groups_timestamp: int | None = None
    stage_gc: list[RankEntry] = field(default_factory=list)  # type=itg
    jerseys: dict[str, int] = field(default_factory=dict)    # code -> bib
    withdrawals: set[int] = field(default_factory=set)
    # bookkeeping: wann haben wir zuletzt etwas Nützliches empfangen?
    last_useful_update: float = 0.0


# --------------------------------------------------------------------------- #
# Parsing der SSE-Nachrichten
# --------------------------------------------------------------------------- #
def _parse_rankings(data: dict) -> list[RankEntry]:
    """
    Ranking-Felder sind in Millisekunden, nicht Sekunden!
    Verifiziert am 2026-07-05:
      relative=6000  -> 6s  (Pogacar +6s auf Vingegaard, korrekt)
      absolute=14508000 -> 14508s = 4h 1m 48s (Vingegaard Etappenzeit)
      bonus=6000 -> 6s Bonus
    """
    out: list[RankEntry] = []
    for r in data.get("rankings") or []:
        try:
            rel_ms = r.get("relative")
            abs_ms = r.get("absolute")
            out.append(RankEntry(
                position=int(r.get("position", 0)),
                bib=int(r.get("bib", 0)),
                # ms -> s
                relative_seconds=(int(rel_ms / 1000) if rel_ms is not None else None),
                absolute_ms=(int(abs_ms) if abs_ms is not None else None),
                bonus_seconds=int(r.get("bonus", 0) or 0) // 1000,
                penalty_seconds=int(r.get("penality", 0) or 0) // 1000,
            ))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda e: e.position)
    return out


def _parse_groups(data: dict) -> list[GroupInfo]:
    out: list[GroupInfo] = []
    for g in data.get("groups") or []:
        bibs = [b.get("bib") for b in (g.get("bibs") or []) if isinstance(b, dict)]
        bibs = [b for b in bibs if isinstance(b, int)]
        # computed-Felder sind die zuverlässigen, von ASO berechneten Werte.
        speed = g.get("computedSpeed") or g.get("speed")
        rem = g.get("computedRemainingDistance") or g.get("remainingDistance")
        gap = g.get("computedRelative") if g.get("isComputedGap") else g.get("relative")
        try:
            out.append(GroupInfo(
                order=int(g.get("order", 0) or 0),
                name=str(g.get("name", "?")),
                size=int(g.get("size", len(bibs)) or len(bibs)),
                speed=float(speed) if speed is not None else None,
                remaining_km=float(rem) / 1000.0 if rem is not None else None,
                gap_seconds=float(gap) if gap is not None else None,
                bibs=bibs,
            ))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x.order)
    return out


def _apply_telemetry(state: State, data: dict, meta: dict[int, dict]):
    """Merge neuer Telemetrie-Snapshot in den Zustand."""
    ts = data.get("TimeStamp")
    # RaceStatus: True solange das Rennen läuft. Sobald False, sind nachfolgende
    # Telemetrie-Daten "stale" (Fahrer auf der Ehrenrunde, am Bus, etc.).
    state.telemetry.race_status = data.get("RaceStatus")
    state.telemetry.stage_index = data.get("StageIndex")
    if ts is not None:
        state.telemetry.timestamp = ts
    # Defensiver Merge: nur Fahrer überschreiben, die im Update enthalten sind.
    for r in data.get("Riders") or []:
        bib = r.get("Bib")
        if isinstance(bib, int):
            existing = state.telemetry.riders.get(bib, {})
            existing.update(r)
            if "_name" not in existing:
                existing["_name"] = _name(meta, bib)
            state.telemetry.riders[bib] = existing
    state.last_useful_update = time.time()


def _apply_ranking(state: State, bind: str, data: dict, year: int):
    """Verteile Ranking-Updates je nach Typ."""
    rtype = data.get("type")
    entries = _parse_rankings(data)
    if not entries:
        return
    # Etappen-/Gesamt-Rangliste (Ankunft): type=itg
    if rtype == TYPE_STAGE_GC and bind.startswith(f"rankingTypeArrival-{year}"):
        state.stage_gc = entries
    # Trikots: type=pmt - eine Ranking-Liste pro Trikot (gelb/grün/...)
    elif rtype == TYPE_JERSEYS:
        # Im Stream kommen einzelne Ranking-Listen; "types" bezeichnet das
        # Trikot (N=normal, A=...). Wir nehmen einfach Position 1 je Liste.
        for e in entries:
            if e.position == 1:
                # Map Jersey-Code; wir können ihn nicht aus types ableiten,
                # also speichern wir pauschal und ordnen später via Farbe.
                state.jerseys[str(e.bib)] = e.bib
    state.last_useful_update = time.time()


def _name(meta: dict[int, dict], bib: int) -> str:
    r = meta.get(bib, {})
    first = (r.get("firstname") or "").strip()
    last = (r.get("lastnameshort") or r.get("lastname") or "").strip()
    code = r.get("code") or rider_team_code(r, 0)
    team = TEAM_NAMES.get(code) or code
    full = f"{first} {last}".strip()
    return full + (f"  ({team})" if team else "")


# --------------------------------------------------------------------------- #
# Ausgabe
# --------------------------------------------------------------------------- #
def _fmt_dur(sec: float | int | None) -> str:
    if sec is None:
        return "  -  "
    s = int(sec)
    if s <= 0:
        return "leader"
    h, rem = divmod(s, 3600)
    mm, ss = divmod(rem, 60)
    return (f"+{h:d}:{mm:02d}:{ss:02d}" if h else f"+{mm:02d}:{ss:02d}")


def _fmt_abs(ms: int | None) -> str:
    if ms is None:
        return "-"
    sec = ms / 1000.0
    h = int(sec // 3600)
    mm = int((sec % 3600) // 60)
    ss = int(sec % 60)
    return f"{h:d}:{mm:02d}:{ss:02d}"


def _freshness(state: State, now: float) -> tuple[str, str]:
    """Liefert (Status-Zeile, Alter-Hinweis)."""
    ts = state.telemetry.timestamp
    rs = state.telemetry.race_status
    if ts is None:
        return ("NO TELEMETRY YET", "warte auf ersten telemetryCompetitor-Event")
    age_sec = now - ts
    age_str = (f"{int(age_sec // 60)}m{int(age_sec % 60):02d}s her"
               if age_sec < 3600 else f"{age_sec/3600:.1f}h her")
    if rs is True:
        return ("LIVE", f"Telemetrie {age_str}")
    if rs is False:
        return ("STALE (Rennen beendet)", f"letzte Telemetrie {age_str}")
    return ("UNKNOWN", f"Telemetrie {age_str}")


def render_rankings(state: State, meta: dict[int, dict], *, top: int | None,
                    only_bibs: set[int] | None) -> str:
    """Offizielle Etappen-/Gesamt-Rangliste (type=itg)."""
    if not state.stage_gc:
        return "[noch keine Rangliste (rankingTypeArrival) empfangen]"
    entries = state.stage_gc
    if only_bibs:
        entries = [e for e in entries if e.bib in only_bibs]
    if top:
        entries = entries[:top]

    lines = ["", "── Offizielle Etappen-Rangliste (RankingSource) ──",
             f"{'#':>3} {'BIB':>4}  {'NAME':<28}{'ZEIT':>10}{'GAP':>9}  BONUS/PEN"]
    for e in entries:
        nm = _name(meta, e.bib)
        name = nm.split("  (")[0][:28]
        team = (nm.split("  (")[-1].rstrip(")") if "  (" in nm else "").ljust(0)
        time_s = _fmt_abs(e.absolute_ms)
        gap = _fmt_dur(e.relative_seconds)
        extras = []
        if e.bonus_seconds:
            extras.append(f"B{e.bonus_seconds}s")
        if e.penalty_seconds:
            extras.append(f"P{e.penalty_seconds}s")
        lines.append(f"{e.position:>3} {e.bib:>4}  {name:<28}{time_s:>10}"
                     f"{gap:>9}  {' '.join(extras)}")
    return "\n".join(lines)


def render_groups(state: State, meta: dict[int, dict], *, only_bibs: set[int] | None
                  ) -> str:
    """Live-Gruppen aus pack-Bind mit berechneten Lücken."""
    if not state.groups:
        return "[noch keine Gruppen (pack) empfangen]"
    lines = ["", "── Live-Gruppen (TelemetrieSource) ──"]
    for g in state.groups:
        # Falls ein Bib-Filter gesetzt ist, nur Gruppen zeigen, die ihn treffen.
        if only_bibs and not (set(g.bibs) & only_bibs):
            continue
        spd = f"{g.speed:.1f} km/h" if g.speed is not None else "speed -"
        rem = f"{g.remaining_km:.1f} km zu Ziel" if g.remaining_km is not None else ""
        gap = _fmt_dur(g.gap_seconds)
        lines.append(f"  [{g.order}] {g.name}  ({g.size} F.)  {spd}  {rem}  {gap}")
        # Bis zu 6 Namen der Gruppe zeigen
        names = [_name(meta, b).split("  (")[0] for b in g.bibs[:6]
                 if b in meta]
        if names:
            lines.append("       " + ", ".join(names) +
                         (f"  +{len(g.bibs)-6}" if len(g.bibs) > 6 else ""))
    return "\n".join(lines)


def render_telemetry(state: State, *, top: int | None,
                     only_bibs: set[int] | None) -> str:
    """GPS/Speed der Einzelfahrer (nur sinnvoll wenn Rennen läuft)."""
    if not state.telemetry.riders:
        return ""
    rs = state.telemetry.race_status
    if rs is not True:
        # Nach Rennende: GPS-Anzeige ist irreführend, nur Hinweis ausgeben.
        return ("", "GPS/Einzeltelemetrie ausgeblendet "
                "(Rennen nicht aktiv). Zur Live-Anzeige während der Etappe "
                "laufen lassen.")[-1]
    riders = list(state.telemetry.riders.values())
    riders = [r for r in riders if r.get("Bib") not in state.withdrawals]
    if only_bibs:
        riders = [r for r in riders if r.get("Bib") in only_bibs]
    riders.sort(key=lambda r: r.get("kmToFinish") if r.get("kmToFinish") is not None else 1e9)
    if top:
        riders = riders[:top]
    lines = ["", "── Einzeltelemetrie (GPS/Speed) ──",
             f"{'BIB':>4}  {'NAME':<26}{'KM/H':>6}{'KM→Z':>7}  EXTRAS"]
    for r in riders:
        bib = r.get("Bib", 0)
        name = (r.get("_name") or "").split("  (")[0][:26]
        kph = r.get("kph")
        kph_s = f"{float(kph):.1f}" if kph is not None else "-"
        kmtg = r.get("kmToFinish")
        kmtg_s = f"{float(kmtg):.1f}" if kmtg is not None else "-"
        extras = []
        if r.get("Jersey"):
            extras.append(f"{JERSEYS.get(r['Jersey'], r['Jersey'])}-Trikot")
        if r.get("Status") and r["Status"] != "active":
            extras.append(r["Status"])
        if r.get("degC") is not None:
            extras.append(f"{float(r['degC']):.0f}°C")
        if r.get("Gradient") is not None:
            extras.append(f"{float(r['Gradient']):+.0f}%")
        lines.append(f"{bib:>4}  {name:<26}{kph_s:>6}{kmtg_s:>7}  {' '.join(extras)}")
    return "\n".join(lines)


def render(state: State, meta: dict[int, dict], *, top: int | None,
           only_bibs: set[int] | None, stage_no: int | None, year: int,
           mode: str) -> str:
    status, fresh = _freshness(state, time.time())
    now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    head = (f"=== Tour de France {year} Live  |  Etappe {stage_no}  |  "
            f"{now_str}  |  {status}  ({fresh}) ===")

    parts = [head]
    if mode in ("both", "rankings"):
        parts.append(render_rankings(state, meta, top=top, only_bibs=only_bibs))
    if mode in ("both", "telemetry"):
        parts.append(render_groups(state, meta, only_bibs=only_bibs))
        t = render_telemetry(state, top=top, only_bibs=only_bibs)
        if t:
            parts.append(t)
    if state.withdrawals:
        parts.append("\n── Aufgabe ──")
        for bib in sorted(state.withdrawals):
            parts.append(f"  bib {bib}  {_name(meta, bib)}")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# SSE-Stream
# --------------------------------------------------------------------------- #
def stream_sse(state: State, meta: dict[int, dict], year: int,
               stage_no: int | None, on_update, mode: str,
               deadline: float | None = None):
    """
    SSE-Verbindung dauerhaft konsumieren.

    deadline (epoch seconds): falls gesetzt, wird die Verbindung beim Erreichen
    kontrolliert geschlossen (für --once).
    """
    url = f"{BASE}/live-stream"
    backoff = 1
    while True:
        if deadline is not None and time.time() > deadline:
            return
        try:
            with requests.get(url, headers=SSE_HEADERS, stream=True,
                              timeout=(15, None)) as resp:
                resp.raise_for_status()
                backoff = 1
                event_name: str | None = None
                for raw in resp.iter_lines(decode_unicode=True):
                    if raw is None:
                        continue
                    if raw == "":
                        event_name = None
                        continue
                    if raw.startswith("event: "):
                        event_name = raw[7:].strip()
                    elif raw.startswith("data: ") and event_name == "update":
                        try:
                            msg = json.loads(raw[6:])
                        except json.JSONDecodeError:
                            continue
                        _dispatch(msg, state, meta, year, stage_no, mode)
                        if state.last_useful_update > 0:
                            on_update(state)
                    # once-Abbruch prüfen (on_update kann stop setzen)
                    if deadline is not None and time.time() > deadline:
                        return
        except (requests.RequestException, ConnectionError) as e:
            if deadline is not None and time.time() > deadline:
                return
            print(f"[stream] Verbindung verloren ({type(e).__name__}: {e}); "
                  f"Reconnect in {backoff}s", file=sys.stderr)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


def _dispatch(msg: dict, state: State, meta, year, stage_no, mode: str):
    bind = msg.get("bind", "")
    data = msg.get("data") or {}

    # Immer auswerten (auch wenn mode != both), damit State konsistent bleibt.
    if bind == f"telemetryCompetitor-{year}":
        # Dedup: gleiche Nachricht kommt als 'added' und 'modified' doppelt.
        ts = data.get("TimeStamp")
        if ts is not None and ts == state.telemetry.timestamp:
            return
        _apply_telemetry(state, data, meta)
        return

    if stage_no is not None and bind == f"pack-{year}-{stage_no}":
        state.groups = _parse_groups(data)
        # Datum aus dem Pack liefert einen groben Zeitstempel der Berechnung.
        d = data.get("date")
        if isinstance(d, str):
            try:
                # ISO 8601, z.B. "2026-07-05T15:48:36.000Z"
                dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
                state.groups_timestamp = int(dt.timestamp())
            except ValueError:
                pass
        state.last_useful_update = time.time()
        return

    if stage_no is not None and bind == f"rankingTypeArrival-{year}-{stage_no}":
        _apply_ranking(state, bind, data, year)
        return

    if stage_no is not None and bind.startswith(f"rankingTypeJerseys-{year}"):
        _apply_ranking(state, bind, data, year)
        return

    if stage_no is not None and bind == f"stageWithdrawals-{year}-{stage_no}":
        for r in data.get("rankings") or []:
            bib = r.get("bib") if isinstance(r, dict) else r
            if isinstance(bib, int):
                state.withdrawals.add(bib)
        state.last_useful_update = time.time()
        return


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args():
    p = argparse.ArgumentParser(description="Tour de France Live Tracker (v2)")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--stage", type=int, default=None,
                   help="Etappe (sonst: heutige Etappe, sonst erste)")
    p.add_argument("--bib", default="",
                   help="Komma-getrennte Startnummern (Filter)")
    p.add_argument("--top", type=int, default=None, help="Nur Top N der GC")
    p.add_argument("--json", default=None,
                   help="Jeden Snapshot als Zeile in diese Datei (JSONL)")
    p.add_argument("--once", action="store_true",
                   help="Auf ersten nützlichen Snapshot warten, dann beenden")
    p.add_argument("--mode", default="both",
                   choices=["both", "rankings", "telemetry"],
                   help="both=Ranking+Telemetrie (default); rankings=GC only; "
                        "telemetry=Gruppen+GPS only")
    p.add_argument("--list-stages", action="store_true")
    p.add_argument("--list-riders", action="store_true")
    return p.parse_args()


def main() -> int:
    global TEAM_NAMES
    args = parse_args()

    print(f"[init] Lade statische Daten für TdF {args.year} ...",
          file=sys.stderr)
    try:
        riders = load_riders(args.year)
        stages = load_stages(args.year)
    except requests.RequestException as e:
        print(f"[error] Endpunkte nicht erreichbar: {e}", file=sys.stderr)
        return 2

    TEAM_NAMES = load_team_names(args.year)
    for bib, r in riders.items():
        if not r.get("code"):
            r["code"] = rider_team_code(r, args.year)

    stage_no = (args.stage or today_stage_number(stages)
                or (stages[0]["stage"] if stages else None))
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    print(f"[init] {len(riders)} Starter, {len(stages)} Etappen, "
          f"heute={today}, verwendete Etappe={stage_no}", file=sys.stderr)

    if args.list_stages:
        for s in stages:
            print(f"  Etappe {s.get('stage')}: {s.get('date','')[:10]}  "
                  f"{(s.get('departureCity') or {}).get('label','?')} -> "
                  f"{(s.get('arrivalCity') or {}).get('label','?')}  "
                  f"({s.get('length')} km)")
        return 0

    if args.list_riders:
        for bib, r in sorted(riders.items()):
            print(f"  {bib:>3}  {(r.get('firstname') or '')} "
                  f"{(r.get('lastnameshort') or r.get('lastname') or '')}  "
                  f"({r.get('code') or '?'})")
        return 0

    only_bibs = ({int(b) for b in args.bib.split(",") if b.strip()}
                 if args.bib else None)

    state = State()
    jsonl = open(args.json, "a", encoding="utf-8") if args.json else None
    stop = {"value": False}
    last_render: dict[str, float] = {"ts": 0.0}
    RENDER_THROTTLE_S = 1.0   # max 1 Render/Sekunde, verhindert Flackern

    def render_now(st: State):
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.write(render(st, riders, top=args.top,
                                only_bibs=only_bibs, stage_no=stage_no,
                                year=args.year, mode=args.mode))
        sys.stdout.write("\n")
        sys.stdout.flush()
        if jsonl:
            jsonl.write(_snapshot_jsonl(st, stage_no, args.mode) + "\n")
            jsonl.flush()

    def on_update(st: State):
        now = time.time()
        if args.once:
            # Im once-Modus: stop-Check immer ausführen (auch wenn throttled),
            # damit das initiale Telemetrie-Event (RaceStatus) nicht durch
            # throttling-verschluckte soziale Updates verpasst wird.
            tstat = st.telemetry.race_status
            ready = (bool(st.stage_gc)
                     and tstat is not None) or (now - start_time > 12)
            if ready:
                if not last_render["ts"]:
                    render_now(st)
                stop["value"] = True
                return
        # Throttling: nicht häufiger als 1x pro Sekunde rendern.
        if now - last_render["ts"] < RENDER_THROTTLE_S:
            return
        last_render["ts"] = now
        render_now(st)

    start_time = time.time()

    def _sig(_s, _f):
        print("\n[stop] Beende Tracker.", file=sys.stderr)
        if jsonl:
            jsonl.close()
        sys.exit(0)
    signal.signal(signal.SIGINT, _sig)

    print(f"[stream] Verbinde mit {BASE}/live-stream  "
          f"(mode={args.mode})\n", file=sys.stderr)

    # once-Modus: nach erstem nützlichen Snapshot beenden, spätestens nach 15s.
    def run():
        deadline = start_time + 15 if args.once else None
        stream_sse(state, riders, args.year, stage_no,
                   on_update=on_update, mode=args.mode, deadline=deadline)
        # Wenn --once und noch nichts gerendert wurde, einmal final rendern.
        if args.once and not last_render["ts"]:
            render_now(state)
    try:
        run()
    except SystemExit:
        if jsonl:
            jsonl.close()
        return 0
    return 0


def _snapshot_jsonl(state: State, stage_no: int | None, mode: str) -> str:
    """Serialisiere den aktuellen Zustand in eine JSONL-Zeile."""
    ts = state.telemetry.timestamp
    return json.dumps({
        "ts": ts,
        "stage": stage_no,
        "race_status": state.telemetry.race_status,
        "mode": mode,
        "groups": [{
            "order": g.order, "name": g.name, "size": g.size,
            "speed_kph": g.speed, "remaining_km": g.remaining_km,
            "gap_s": g.gap_seconds, "bibs": g.bibs,
        } for g in state.groups],
        "gc": [{
            "pos": e.position, "bib": e.bib,
            "rel_s": e.relative_seconds, "abs_ms": e.absolute_ms,
        } for e in state.stage_gc],
    }, ensure_ascii=False)


TEAM_NAMES: dict[str, str] = {}


if __name__ == "__main__":
    sys.exit(main())
