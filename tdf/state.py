"""State-Engine: Datenklassen, Dispatcher (Fix), Hybrid-GC.

Das ist das Herzstück. Es hält den Zustand beider Quellen (Ranking + Telemetrie
+ Pack) und berechnet daraus die ``virtual_gc`` (Start-GC + Etappen-Gap + Boni
- Penalties) sowie die dynamische Top-N-Liste.

Korrekturen gegenüber v2 (tdf_tracker.py):
  * Dispatcher-Bug-Fix: initiales ``telemetryCompetitor``-Event wird nicht mehr
    verworfen, nur weil sein TimeStamp mit dem gebootstrappten übereinstimmt.
    Dedup läuft gegen ``_sse_last_ts`` (separat geführt), nicht gegen den
    Bootstrap-Wert. Bootstrap-Telemetrie wird markiert und löst kein SSE-Dedup
    aus.
  * Jerseys-Fix: Trikot-Code wird aus dem ``type``-Feld des Jerseys-Binds
    abgeleitet (pmt->Y, pmp->G, pmm->P, pmj->W), nicht pauschal gespeichert.
  * Erweiterungen (Features 1-5): Restzeit-Vorhersage (prediction),
    Gap-Chart (gap_chart), Berg/Sprint-Klassifikation (classification),
    Alarme (alarms), Karten-Koordinaten (map_data).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("tdf.state")

from . import config as cfg
from . import prediction as prediction_mod
from . import gap_chart as gap_chart_mod
from . import alarms as alarms_mod
from . import classification as classification_mod
from . import map_data as map_data_mod
from . import power as power_mod
from . import timecut as timecut_mod
from . import simulation as simulation_mod

# --------------------------------------------------------------------------- #
# Datenklassen
# --------------------------------------------------------------------------- #


@dataclass
class TelemetrySnapshot:
    """Letzter Snapshot der GPS/Speed-Telemetrie."""
    timestamp: int | None = None           # TimeStamp aus dem Stream (ms? s? – siehe unten)
    race_status: bool | None = None        # RaceStatus (True = Rennen läuft)
    stage_index: int | None = None
    riders: dict[int, dict[str, Any]] = field(default_factory=dict)
    bootstrapped: bool = False             # True: Wert kam aus REST, nicht SSE


@dataclass
class GroupInfo:
    """Eine Live-Gruppe aus dem pack-Bind."""
    order: int
    name: str
    size: int
    speed: float | None                    # computedSpeed [km/h]
    remaining_km: float | None             # remainingDistance [m] -> /1000
    gap_seconds: float | None              # computedRelative [s] zur Spitze
    bibs: list[int] = field(default_factory=list)
    # Mittlere GPS-Position der Gruppe (für Karten-Marker).
    lat: float | None = None
    lon: float | None = None


@dataclass
class RankEntry:
    """Ein Eintrag aus einer Rangliste (GC oder virtuell).

    Zeiten-Einheiten (verifiziert 2026-07-05):
      * relative_ms / absolute_ms kommen roh in ms aus dem Feed.
      * relative_seconds und absolute_seconds sind die UI-freundlichen Sekunden.
    """
    position: int
    bib: int
    relative_seconds: int | None          # Rückstand auf Spitze [s]
    absolute_seconds: int | None          # absolute Zeit [s]
    bonus_seconds: int = 0
    penalty_seconds: int = 0


@dataclass
class Checkpoint:
    """Wegpunkt aus dem checkpoint-Bind (Berg/Sprint/Wetter)."""
    index: int
    lat: float | None
    lon: float | None
    place: str = ""
    road: str = ""
    # Bergwertung / Sprint-Typ – roh aus checkpointTypes/checkpointSummits.
    kind: str = ""


@dataclass
class State:
    """Gesamtzustand beider Quellen, thread-/task-sicher über ``lock``."""
    # Statische Meta-Daten (Bib -> Rider-Objekt) – vom Server gesetzt.
    meta: dict[int, dict[str, Any]] = field(default_factory=dict)
    year: int = 2026
    stage: int | None = None

    # Live-Daten
    telemetry: TelemetrySnapshot = field(default_factory=TelemetrySnapshot)
    groups: list[GroupInfo] = field(default_factory=list)
    groups_timestamp: int | None = None
    checkpoints: list[Checkpoint] = field(default_factory=list)

    # Offizielle Ranglisten
    base_gc: list[RankEntry] = field(default_factory=list)   # type=itg (Start-Basis)
    stage_result: list[RankEntry] = field(default_factory=list)  # type=ete
    jerseys: dict[str, int] = field(default_factory=dict)    # code (Y/G/P/W) -> bib
    withdrawals: set[int] = field(default_factory=set)

    # Abgeleitet
    virtual_gc: list[RankEntry] = field(default_factory=list)
    top_n: list[int] = field(default_factory=list)
    extrapolated_pos: dict[int, tuple[float, float]] = field(default_factory=dict)

    # Neue Features (Features 1-5)
    # Gap-Chart (Feature 2)
    gap_history: gap_chart_mod.GapHistory = field(
        default_factory=gap_chart_mod.GapHistory)
    # Alarme (Feature 4)
    alarm_system: alarms_mod.AlarmSystem = field(
        default_factory=alarms_mod.AlarmSystem)
    # Aktuelle Alarmliste für den Snapshot
    _pending_alarms: list[dict[str, Any]] = field(default_factory=list)

    # Technische Features (6-9): Power, Gradient, Time-Cut, Simulation
    # Profil-Punkte (km_done, alt) für Live-Gradient. Wird vom Server beim
    # Laden des Profils gesetzt. Leer -> Gradient 0 (Flachland-Annahme).
    profile_points: list[dict[str, Any]] = field(default_factory=list)
    # Etappentyp für Time-Cut ('flat', 'mountain', 'medium', 'itt', 'ttt').
    stage_type: str = "flat"
    # Geschätzte Siegerzeit (Sekunden) — wird aus der Spitzengruppe projiziert.
    # None, solange keine valide Projektion möglich (vor Start / keine Daten).
    estimated_winner_time_s: float | None = None

    # Bookkeeping
    last_useful_update: float = 0.0
    _sse_last_ts: int | None = None          # separater SSE-Dedup-Marker
    _last_notify: float = 0.0

    # Concurrency
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _listeners: list[Callable[["State"], Any]] = field(default_factory=list)

    # ----------------------------------------------------------------- #
    # Listener / Notify
    # ----------------------------------------------------------------- #
    def add_listener(self, fn: Callable[["State"], Any]) -> None:
        self._listeners.append(fn)

    async def notify(self, *, force: bool = False) -> None:
        """Benachrichtige Listener (throttled). Listener werden awaited."""
        now = time.time()
        if not force and now - self._last_notify < cfg.NOTIFY_THROTTLE_S:
            return
        self._last_notify = now
        for fn in self._listeners:
            try:
                res = fn(self)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # noqa: BLE001 – Listener dürfen den Stream nicht kippen
                pass

    # ----------------------------------------------------------------- #
    # Evaluate alarms + record gap history (periodisch aufgerufen)
    # ----------------------------------------------------------------- #
    def evaluate_features(self) -> None:
        """Wertet Alarme aus und zeichnet Gap-Chart-Daten auf.
        Wird nach jedem relevanten Update vom Server aufgerufen.
        """
        # Gap-Chart aufzeichnen.
        self.gap_history.record(
            gc=self.virtual_gc or self.base_gc,
            groups=list(self.groups),
            race_status=self.telemetry.race_status,
        )
        # Alarme evaluieren.
        jersey_dict = dict(self.jerseys)
        groups_list = list(self.groups)
        new_alarms = self.alarm_system.evaluate(
            jerseys=jersey_dict,
            groups=groups_list,
            gc=self.virtual_gc or self.base_gc,
            withdrawals=self.withdrawals,
            race_status=self.telemetry.race_status,
        )
        for a in new_alarms:
            self._pending_alarms.append({
                "id": a.id,
                "ts": a.ts,
                "kind": a.kind,
                "severity": a.severity,
                "message": a.message,
                "details": a.details,
            })
        # Max 20 pending alarms, älteste zuerst verwerfen.
        if len(self._pending_alarms) > 20:
            self._pending_alarms = self._pending_alarms[-20:]


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def parse_rankings(data: dict) -> list[RankEntry]:
    """
    Ranking-Felder sind in Millisekunden, nicht Sekunden!
    Verifiziert am 2026-07-05:
      relative=6000  -> 6s  (Pogacar +6s auf Vingegaard)
      absolute=14508000 -> 14508s = 4h 1m 48s (Vingegaard Etappenzeit)
      bonus=6000 -> 6s Bonus
    """
    out: list[RankEntry] = []
    for r in data.get("rankings") or []:
        try:
            rel_ms = r.get("relative")
            abs_ms = r.get("absolute")
            out.append(RankEntry(
                position=int(r.get("position", 0) or 0),
                bib=int(r.get("bib", 0) or 0),
                relative_seconds=(int(rel_ms // 1000) if rel_ms is not None else None),
                absolute_seconds=(int(abs_ms // 1000) if abs_ms is not None else None),
                bonus_seconds=int(r.get("bonus", 0) or 0) // 1000,
                penalty_seconds=int(r.get("penality", 0) or 0) // 1000,
            ))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda e: e.position)
    return out


def parse_groups(data: dict) -> list[GroupInfo]:
    out: list[GroupInfo] = []
    for g in data.get("groups") or []:
        bib_objs = g.get("bibs") or []
        bibs = [b.get("bib") for b in bib_objs if isinstance(b, dict)]
        bibs = [b for b in bibs if isinstance(b, int)]
        # computed-Felder sind die zuverlässigen, von ASO berechneten Werte.
        speed = g.get("computedSpeed") or g.get("speed")
        rem = (g.get("computedRemainingDistance")
               or g.get("remainingDistance"))
        gap = g.get("computedRelative") if g.get("isComputedGap") else g.get("relative")
        lat = g.get("latitude")
        lon = g.get("longitude")
        # size-Feld validieren: ASO liefert bei großen Gruppen (Peloton)
        # manchmal den Dummy-Wert 999. In dem Fall nehmen wir die echte
        # Anzahl der gelisteten Bibs, die zuverlässiger ist.
        size_field = g.get("size")
        if (not isinstance(size_field, int)
                or size_field >= 200                  # 999-Dummy-Schwelle
                or (bibs and abs(size_field - len(bibs)) > 50)):
            size = len(bibs) if bibs else size_field
        else:
            size = size_field
        try:
            out.append(GroupInfo(
                order=int(g.get("order", 0) or 0),
                name=str(g.get("name", "?")),
                size=int(size or len(bibs)),
                speed=float(speed) if speed is not None else None,
                remaining_km=float(rem) / 1000.0 if rem is not None else None,
                gap_seconds=float(gap) if gap is not None else None,
                bibs=bibs,
                lat=float(lat) if lat is not None else None,
                lon=float(lon) if lon is not None else None,
            ))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x.order)
    return out


def parse_checkpoints(data: dict) -> list[Checkpoint]:
    """checkpoint-Bind ist numerisch indiziert ({"0": {...}, "1": {...}, ...})."""
    out: list[Checkpoint] = []
    items = []
    if isinstance(data, dict):
        items = list(data.values())
    elif isinstance(data, list):
        items = data
    for i, cp in enumerate(items):
        if not isinstance(cp, dict):
            continue
        lat = cp.get("latitude") or cp.get("lat")
        lon = cp.get("longitude") or cp.get("lon") or cp.get("lng")
        place = cp.get("place") or cp.get("name") or ""
        road = cp.get("road") or ""
        kind = ""
        if cp.get("checkpointSummits"):
            kind = "mountain"
        elif cp.get("checkpointTypes"):
            kind = "sprint"
        try:
            out.append(Checkpoint(
                index=i,
                lat=float(lat) if lat is not None else None,
                lon=float(lon) if lon is not None else None,
                place=str(place),
                road=str(road),
                kind=kind,
            ))
        except (TypeError, ValueError):
            continue
    return out


# --------------------------------------------------------------------------- #
# Anwenden von Updates
# --------------------------------------------------------------------------- #
def name_of(meta: dict[int, dict[str, Any]], bib: int) -> str:
    r = meta.get(bib, {})
    first = (r.get("firstname") or "").strip()
    last = (r.get("lastnameshort") or r.get("lastname") or "").strip()
    team = r.get("team_name") or r.get("team_code") or ""
    full = f"{first} {last}".strip()
    return full + (f"  ({team})" if team else "")


def apply_telemetry(state: State, data: dict, *, from_bootstrap: bool = False) -> None:
    """Merge neuer Telemetrie-Snapshot in den Zustand.

    Zusätzlich: Ableitung von Abreisern (withdrawals). ASO liefert den
    stageWithdrawals-Bind während eines laufenden Rennens oft leer oder
    unvollständig. Die zuverlässigste Quelle ist therefore der Diff zwischen
    Starterfeld (state.meta) und aktueller Telemetrie: wer Starter ist, aber
    nicht mehr in der Telemetrie auftaucht, hat aufgegeben.
    """
    ts = data.get("TimeStamp")
    state.telemetry.race_status = data.get("RaceStatus")
    state.telemetry.stage_index = data.get("StageIndex")
    if ts is not None:
        state.telemetry.timestamp = ts
    state.telemetry.bootstrapped = from_bootstrap
    # Riders im aktuellen Snapshot sammeln (für DNF-Ableitung).
    seen_bibs: set[int] = set()
    # Defensiver Merge: nur enthaltene Rider überschreiben.
    for r in data.get("Riders") or []:
        bib = r.get("Bib")
        if isinstance(bib, int):
            seen_bibs.add(bib)
            existing = state.telemetry.riders.get(bib, {})
            existing.update(r)
            if "_name" not in existing:
                existing["_name"] = name_of(state.meta, bib)
            state.telemetry.riders[bib] = existing
    state.last_useful_update = time.time()

    # DNF-Ableitung: Starter ohne Telemetrie = aufgegeben.
    # Nur wenn wir ein Starterfeld haben (meta) und die Telemetrie substanziell
    # ist (mind. 10 Rider, sonst falscher Alarm bei Telemetrie-Lücken).
    if state.meta and len(seen_bibs) >= 10:
        starter_bibs = set(state.meta.keys())
        # Vorsichtig: nur Rider als DNF markieren, die WIRKLICH im Starterfeld
        # waren und jetzt komplett fehlen. Rider, die im alten Snapshot da
        # waren aber im neuen fehlen, könnten auch Telemetrie-Lücke sein.
        # Daher: nur als DNF werten, wenn sie auch nicht schon kürzlich
        # gesehen wurden (state.telemetry.riders behält alte Rider bei).
        # Einfache robuste Heuristik: DNF = starter_bibs - seen_bibs,
        # aber nur wenn diese Menge kleiner ist als 30% der Starter
        # (sonst ist wahrscheinlich die Telemetrie kaputt, nicht der Rider).
        dnf_candidates = starter_bibs - seen_bibs
        if dnf_candidates and len(dnf_candidates) < len(starter_bibs) * 0.3:
            state.withdrawals.update(dnf_candidates)
            # Riders, die DNF sind, nicht mehr als „aktiv" propagieren.
            for bib in dnf_candidates:
                state.telemetry.riders.pop(bib, None)


def apply_jerseys_ranking(state: State, data: dict) -> None:
    """Jerseys-Bind: eine Ranking-Liste pro Trikot. Typ -> Code via pmt/pmp/pmm/pmj."""
    rtype = data.get("type") or ""
    code = cfg.JERSEY_TYPE_TO_CODE.get(rtype)
    entries = parse_rankings(data)
    if code:
        for e in entries:
            if e.position == 1:
                state.jerseys[code] = e.bib
                break
    state.last_useful_update = time.time()


# --------------------------------------------------------------------------- #
# Dispatcher (SSE-Events)
# --------------------------------------------------------------------------- #
def dispatch(msg: dict, state: State, *, year: int, stage: int | None,
             next_stage: int | None) -> bool:
    """Wendet eine SSE-Nachricht an. Liefert True, wenn etwas Nützliches passiert wurde."""
    bind = msg.get("bind", "")
    data = msg.get("data") or {}

    # Telemetrie – Dedup gegen _sse_last_ts (nicht gegen Bootstrap-TS!).
    if bind == cfg.bind_telemetry(year):
        ts = data.get("TimeStamp")
        # Dedup nur, wenn TS identisch UND nicht aus dem Bootstrap stammt.
        if (ts is not None and ts == state._sse_last_ts
                and not state.telemetry.bootstrapped):
            return False
        if ts is not None:
            state._sse_last_ts = ts
        apply_telemetry(state, data)
        recompute_virtual_gc(state)
        state.evaluate_features()
        return True

    if stage is not None and bind == cfg.bind_pack(year, stage):
        state.groups = parse_groups(data)
        d = data.get("date")
        if isinstance(d, str):
            from datetime import datetime
            try:
                dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
                state.groups_timestamp = int(dt.timestamp())
            except ValueError:
                pass
        recompute_virtual_gc(state)
        state.last_useful_update = time.time()
        state.evaluate_features()
        return True

    if stage is not None and bind == cfg.bind_arrival(year, stage):
        rtype = data.get("type")
        entries = parse_rankings(data)
        if rtype == cfg.TYPE_GC and entries:
            state.base_gc = entries  # offizielle GC aktualisiert sich live
        elif rtype == cfg.TYPE_STAGE and entries:
            state.stage_result = entries
        recompute_virtual_gc(state)
        state.last_useful_update = time.time()
        state.evaluate_features()
        return True

    if next_stage is not None and bind.startswith(f"rankingTypeJerseys-{year}"):
        apply_jerseys_ranking(state, data)
        state.evaluate_features()
        return True

    if stage is not None and bind == cfg.bind_checkpoint(year, stage):
        state.checkpoints = parse_checkpoints(data)
        state.last_useful_update = time.time()
        state.evaluate_features()
        return True

    if stage is not None and bind == cfg.bind_withdrawals(year, stage):
        for r in data.get("rankings") or []:
            bib = r.get("bib") if isinstance(r, dict) else r
            if isinstance(bib, int):
                state.withdrawals.add(bib)
        state.last_useful_update = time.time()
        state.evaluate_features()
        return True

    return False


# --------------------------------------------------------------------------- #
# Hybrid-GC
# --------------------------------------------------------------------------- #
def recompute_virtual_gc(state: State) -> None:
    """Virtual GC = offizielles itg-Ranking + gruppeninterne Live-Lücke.

    Entschieden (2026-07-05): Virtual-GC hält die **offizielle Reihenfolge**
    des itg-Rankings bei (Vingegaard pos 1, Pogacar +6s, ...). Bonus/Penalty
    werden NUR angezeigt, NICHT in die Sortierung eingerechnet – ASO hat sie
    bereits im ``relative``-Rückstand verrechnet oder nicht, jedenfalls ist
    die offizielle Position maßgeblich.

    Was die Engine zusätzlich leistet: die pack-Gruppen-Lücken. Ein Fahrer
    bekommt seinen Gruppen-Gap minus den Gap des Gruppenführers aufgeschlagen
    (gruppeninterne Differenz), so dass das Feld "atmet", wenn Gruppen
    abreißen. Diese Zusatz-Lücke verändert aber NICHT die Grundsortierung
    einer etablierten GC – sie wirkt sich erst aus, wenn Gruppen auseinander-
    reißen. Defensiv: fehlen Gruppen-Daten, bleibt es exakt beim itg-Ranking.
    """
    if not state.base_gc:
        state.virtual_gc = []
        state.top_n = []
        return

    # Gruppierung Bib -> Gruppe, plus Gap des Gruppenführers (kleinster Gap).
    bib_to_group: dict[int, GroupInfo] = {}
    group_leader_gap: dict[int, float] = {}
    for g in state.groups:
        for b in g.bibs:
            bib_to_group[b] = g
        if g.gap_seconds is not None:
            prev = group_leader_gap.get(g.order)
            if prev is None or g.gap_seconds < prev:
                group_leader_gap[g.order] = g.gap_seconds

    # Sortierschlüssel: primär itg-position (offiziell), sekundär bib.
    # Die Live-Lücke wird NUR für die Anzeige aufgeschlagen, nicht für Sort.
    virtual: list[RankEntry] = []
    for e in state.base_gc:
        rel = e.relative_seconds if e.relative_seconds is not None else 0
        g = bib_to_group.get(e.bib)
        live_extra = 0
        if g is not None and g.gap_seconds is not None:
            leader_gap = group_leader_gap.get(g.order, 0.0)
            live_extra = int(max(0.0, g.gap_seconds - leader_gap))
        virtual.append(RankEntry(
            position=e.position,
            bib=e.bib,
            relative_seconds=int(rel) + live_extra,
            absolute_seconds=e.absolute_seconds,
            bonus_seconds=e.bonus_seconds,
            penalty_seconds=e.penalty_seconds,
        ))

    # Sortierung folgt der offiziellen Position; nur bei echten Lücken-
    # Verschiebungen (live_extra > 0) würde neu geordnet werden. Wir sortieren
    # daher nach (relative_seconds, position, bib), so dass Live-Lücken eine
    # Rolle spielen, Gleichstände aber auf der offiziellen Pos basieren.
    virtual.sort(key=lambda x: (
        x.relative_seconds if x.relative_seconds is not None else 1 << 31,
        x.position, x.bib))
    for i, v in enumerate(virtual, start=1):
        v.position = i
    state.virtual_gc = virtual
    state.top_n = [v.bib for v in virtual[:10]]


# --------------------------------------------------------------------------- #
# Serialisierung (für /state und WS)
# --------------------------------------------------------------------------- #
def to_snapshot(state: State, *, top_limit: int | None = None,
                include_all_riders: bool = False) -> dict:
    """Kompakter JSON-Snapshot für HTTP / WS."""
    now = time.time()
    ts = state.telemetry.timestamp
    rs = state.telemetry.race_status

    if ts is None:
        status, fresh = "NO TELEMETRY", "warte auf ersten Event"
    else:
        # TimeStamp im telemetry-Bind ist Sekunden (Epoche), nicht ms.
        # v2 hat ihn roh behandelt; wir sichern mit try/except ab.
        try:
            age = now - float(ts)
            if age < 0:
                age = 0
        except (TypeError, ValueError):
            age = 0
        age_str = (f"{int(age // 60)}m{int(age % 60):02d}s her"
                   if age < 3600 else f"{age/3600:.1f}h her")
        if rs is True:
            status, fresh = "LIVE", f"Telemetrie {age_str}"
        elif rs is False:
            status, fresh = "STALE", f"Rennen beendet, letzte Daten {age_str}"
        else:
            status, fresh = "UNKNOWN", f"Telemetrie {age_str}"

    gc = state.virtual_gc or state.base_gc
    if top_limit:
        gc = gc[:top_limit]

    # Feature 1: Restzeit-Vorhersage
    top_riders_data = [
        state.telemetry.riders.get(bib, {}) for bib in state.top_n
    ]
    predictions = prediction_mod.compute_predictions(
        groups=[_group_json(g, state.meta) for g in state.groups],
        top_riders=top_riders_data,
    )

    # Feature 3: Berg/Sprint-Klassifikation
    cls_state = classification_mod.parse_checkpoints_to_classifications(
        state.checkpoints, list(state.groups))
    cls_state.kom_ranking = classification_mod.extract_mountain_ranking(
        state.jerseys, state.meta, gc)
    cls_state.sprint_ranking = classification_mod.extract_sprint_ranking(
        state.jerseys, state.meta, gc)
    classifications = classification_mod.to_json(
        cls_state, state.meta, state.jerseys, gc)

    # Feature 5: Karten-Koordinaten
    map_data = map_data_mod.build_map_data(
        groups=[_group_json(g, state.meta) for g in state.groups],
        top_riders=top_riders_data,
        checkpoints=state.checkpoints,
        classifications=classifications,
    )

    # --- Technische Features (6-9) ---
    # Live-Gradient aus dem Profil anhand der aktuellen Spitzengruppe.
    # Wenn kein Profil vorhanden -> 0.0 (Flachland-Annahme).
    current_gradient = 0.0
    if state.profile_points and state.groups:
        # Aktuelle km-Position = completedDistance der Spitzengruppe.
        lead = state.groups[0]
        lead_km = getattr(lead, "remaining_km", None)
        # Wir brauchen km_done, aber GroupInfo hat remaining_km.
        # Approximation: total_km - remaining_km = km_done.
        # Ohne total_km -> wir nehmen das Profilende als Referenz und
        # rechnen vom Ende her. Einfacher: Wir bitten den Server, die
        # korrekte km-Position via app["profile"] zu setzen; hier reicht
        # der Fallback, dass wir die Spitzengruppe über remaining_km
        # im Profil suchen.
        try:
            total_km = (state.profile_points[-1].get("km_done", 0.0)
                        if state.profile_points else 0.0)
            if lead_km is not None and total_km > 0:
                current_km = max(0.0, total_km - float(lead_km))
                g = power_mod.gradient_at_km(state.profile_points, current_km)
                if g is not None:
                    current_gradient = g
        except (TypeError, ValueError, IndexError):
            pass

    # Feature 6: Power (Watt + W/kg) für die Top-N-Rider.
    # Peloton-Bibs = alle Bibs der größten Gruppe (für Drafting-Savings).
    peloton_bibs: set[int] = set()
    if state.groups:
        biggest = max(state.groups, key=lambda x: x.size)
        peloton_bibs = set(biggest.bibs)
    power_data = power_mod.compute_top_riders_power(
        top_riders_data, gradient_pct=current_gradient,
        peloton_bibs=peloton_bibs,
    )

    # Feature 7: Time-Cut-Analyse.
    # Siegerzeit aus der Spitzengruppe projizieren, falls möglich.
    winner_time = state.estimated_winner_time_s
    if winner_time is None and state.groups:
        lead = state.groups[0]
        lead_speed = getattr(lead, "speed", None)
        lead_rem = getattr(lead, "remaining_km", None)
        # Wir kennen die bisherige Fahrzeit nicht direkt. Approximation:
        # Wenn die Spitze Restdistanz und Geschwindigkeit hat, und wir
        # die Gesamtetappenlänge kennen, können wir die zurückgelegte
        # Zeit abschätzen. Ohne Gesamt-Länge -> None (keine Time-Cut-Aussage).
        # Vereinfachung: wenn eine gc-Zeit existiert (base_gc), nutzen wir
        # deren Spitze als Schätzer.
        if not gc and not state.base_gc:
            winner_time = None
    if gc and winner_time is None:
        # GC-Spitze hat absolute_seconds -> das ist die echte Siegerzeit.
        try:
            winner_time = float(gc[0].absolute_seconds or 0) or None
        except (TypeError, ValueError, IndexError):
            winner_time = None

    avg_speed = 40.0  # Default, falls keine echte Messung
    if state.groups:
        lead_speed = getattr(state.groups[0], "speed", None)
        if lead_speed:
            avg_speed = float(lead_speed)

    time_cut: dict[str, Any] | None = None
    if winner_time and winner_time > 0:
        time_cut = timecut_mod.analyze_groups(
            groups=list(state.groups),
            winner_time_s=winner_time,
            avg_speed_kph=avg_speed,
            stage_type=state.stage_type,
        )

    # Feature 8: Ausreißer-Überlebenssimulation.
    survival = None
    try:
        survival = simulation_mod.simulate_from_groups(
            list(state.groups), n_simulations=200, seed=42)
    except Exception as e:  # defensive: darf Snapshot nie crashen
        log.warning("Survival-Simulation fehlgeschlagen: %s", e)

    result: dict[str, Any] = {
        "year": state.year,
        "stage": state.stage,
        "status": status,
        "freshness": fresh,
        "race_status": rs,
        "telemetry_ts": ts,
        "server_now": now,
        "gc": [_entry_json(e, state.meta) for e in gc],
        "groups": [_group_json(g, state.meta) for g in state.groups],
        "top_n": [_top_rider_json(state, b) for b in state.top_n],
        "jerseys": {code: _jersey_json(state, code, bib)
                    for code, bib in state.jerseys.items()},
        "withdrawals": sorted(state.withdrawals),
        "checkpoints": [{"index": c.index, "lat": c.lat, "lon": c.lon,
                         "place": c.place, "road": c.road, "kind": c.kind}
                        for c in state.checkpoints
                        if c.lat is not None and c.lon is not None],
        "extrapolated_pos": {str(b): list(p)
                             for b, p in state.extrapolated_pos.items()},
        "include_all_riders": include_all_riders,
        # Neue Features
        "predictions": predictions,
        "classifications": classifications,
        "map": map_data,
        "alarms": state.alarm_system.get_alarms(limit=10),
        # Technische Features (6-9)
        "power": {
            "gradient_pct": round(current_gradient, 1),
            "riders": power_data[:10],  # Top 10 nach W/kg
        },
        "time_cut": time_cut,
        "breakaway_survival": survival,
    }
    return result


def _entry_json(e: RankEntry, meta: dict[int, dict[str, Any]]) -> dict:
    nm = name_of(meta, e.bib)
    name = nm.split("  (")[0]
    team = nm.split("  (")[-1].rstrip(")") if "  (" in nm else ""
    return {
        "pos": e.position, "bib": e.bib, "name": name, "team": team,
        "rel_s": e.relative_seconds, "abs_s": e.absolute_seconds,
        "bonus_s": e.bonus_seconds or 0, "pen_s": e.penalty_seconds or 0,
    }


def _group_json(g: GroupInfo, meta: dict[int, dict[str, Any]]) -> dict:
    names = [name_of(meta, b).split("  (")[0] for b in g.bibs[:6] if b in meta]
    return {
        "order": g.order, "name": g.name, "size": g.size,
        "speed_kph": g.speed, "remaining_km": g.remaining_km,
        "gap_s": g.gap_seconds, "lat": g.lat, "lon": g.lon,
        "bibs": g.bibs, "sample_names": names,
        "extra": max(0, len(g.bibs) - 6),
    }


def _top_rider_json(state: State, bib: int) -> dict:
    r = state.telemetry.riders.get(bib, {})
    pos = state.extrapolated_pos.get(bib)
    if pos is None:
        lat = r.get("Latitude")
        lon = r.get("Longitude")
        pos = (float(lat), float(lon)) if lat is not None and lon is not None else None
    nm = name_of(state.meta, bib)
    # Position in virtual_gc:
    rank = next((i + 1 for i, e in enumerate(state.virtual_gc) if e.bib == bib), None)
    return {
        "bib": bib,
        "name": nm.split("  (")[0],
        "team": nm.split("  (")[-1].rstrip(")") if "  (" in nm else "",
        "kph": r.get("kph"),
        "kph_avg": r.get("kphAvg"),
        "km_to_finish": r.get("kmToFinish"),
        "gradient": r.get("Gradient"),
        "deg_c": r.get("degC"),
        "jersey": r.get("Jersey"),
        "status": r.get("Status"),
        "rank": rank,
        "pos": list(pos) if pos else None,
    }


def _jersey_json(state: State, code: str, bib: int) -> dict:
    nm = name_of(state.meta, bib)
    return {
        "code": code,
        "label": cfg.JERSEYS.get(code, code),
        "bib": bib,
        "name": nm.split("  (")[0],
        "team": nm.split("  (")[-1].rstrip(")") if "  (" in nm else "",
    }


def snapshot_to_json(state: State, **kw) -> str:
    return json.dumps(to_snapshot(state, **kw), ensure_ascii=False)
