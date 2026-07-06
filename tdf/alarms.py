"""Alarme (Feature 4).

Erkennt bemerkenswerte Ereignisse während des Rennens:
  - Ausreißergruppe (Gruppe vor dem Peloton mit signifikantem Gap > 30s)
  - Zusammenhalt der Gruppen (Gruppe wird vom Peloton eingeholt)
  - Trikotwechsel (Führungswechsel in Gelb/Grün/Berg/Weiß)
  - Aufgabe (rider withdraws)
  - Rennende (RaceStatus wechselt von True auf False)
  - Führungswechsel im GC (neuer Spitzenreiter)

Alarme werden in einem Ringpuffer gehalten (max 50) und über das /state-
Snapshot (Feld "alarms") sowie einen eigenen WS-Event "alarm" ausgeliefert.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("tdf.alarms")

# Maximale Anzahl an Alarmen im Ringpuffer.
MAX_ALARMS = 50
# Mindestabstand zwischen gleichen Alarmtypen (verhindert Spam).
COOLDOWN_S = 15.0
# Signifikanter Gap für Ausreißer (in Sekunden).
BREAKAWAY_GAP_S = 30.0
# Gap, bei dem eine Gruppe als "eingeholt" gilt.
CAUGHT_GAP_S = 5.0


@dataclass
class Alarm:
    """Ein einzelner Alarm."""
    id: int
    ts: float                     # Unix-Timestamp
    kind: str                     # "breakaway", "caught", "jersey_change",
                                  # "withdrawal", "race_ended", "gc_leader_change"
    severity: str                 # "info", "warning", "critical"
    message: str                  # Menschlich lesbare Nachricht
    details: dict[str, Any] = field(default_factory=dict)


class AlarmSystem:
    """Erkennt und speichert Alarme."""

    def __init__(self) -> None:
        self._alarms: list[Alarm] = []
        self._next_id: int = 1
        self._last_seen: dict[str, float] = {}  # kind -> letzter TS
        # Zustand für Delta-Erkennung.
        self._prev_jerseys: dict[str, int] = {}
        self._prev_leader_bib: int | None = None
        self._prev_race_status: bool | None = None
        self._prev_groups: dict[int, float] = {}  # order -> gap_s
        self._prev_withdrawals: set[int] = set()

    def _is_cooldown(self, tag: str) -> bool:
        """Prüft, ob der Alarm-Tag noch in der Cooldown-Phase ist."""
        now = time.time()
        last = self._last_seen.get(tag, 0.0)
        if now - last < COOLDOWN_S:
            return True
        self._last_seen[tag] = now
        return False

    def _add(self, kind: str, severity: str, message: str,
             details: dict[str, Any] | None = None) -> Alarm | None:
        """Fügt einen Alarm hinzu, unter Berücksichtigung des Cooldowns."""
        tag = f"{kind}:{details.get('_tag','')}" if details and "_tag" in details else kind
        if self._is_cooldown(tag):
            return None
        alarm = Alarm(
            id=self._next_id,
            ts=time.time(),
            kind=kind,
            severity=severity,
            message=message,
            details={k: v for k, v in (details or {}).items() if not k.startswith("_")},
        )
        self._next_id += 1
        self._alarms.append(alarm)
        if len(self._alarms) > MAX_ALARMS:
            self._alarms = self._alarms[-MAX_ALARMS:]
        return alarm

    def evaluate(self, jerseys: dict[str, int], groups: list[Any],
                 gc: list[Any], withdrawals: set[int],
                 race_status: bool | None) -> list[Alarm]:
        """Wertet den aktuellen Zustand aus und generiert neue Alarme.

        Args:
            jerseys: dict[code (Y/G/P/W) -> bib]
            groups: Liste von GroupInfo-Objekten oder Dicts.
            gc: Aktuelle GC (virtual_gc), Liste von RankEntry oder Dicts.
            withdrawals: Menge der aufgegebenen Bibs.
            race_status: Aktueller RaceStatus.

        Returns:
            Liste neuer Alarme (kann leer sein).
        """
        new_alarms: list[Alarm] = []

        # 1) Rennende erkennen.
        if race_status is not None and self._prev_race_status is True and race_status is False:
            alarm = self._add("race_ended", "critical",
                              "Das Rennen ist beendet!")
            if alarm:
                new_alarms.append(alarm)

        # 2) Ausreißer / Gruppen-Bewegungen.
        group_gaps: dict[int, float] = {}
        for g in groups:
            if hasattr(g, 'order') and hasattr(g, 'gap_seconds'):
                order = g.order
                gap = g.gap_seconds
            elif isinstance(g, dict):
                order = g.get("order")
                gap = g.get("gap_s") or g.get("gap_seconds")
            else:
                continue
            if order is not None and gap is not None:
                group_gaps[int(order)] = float(gap)

        for order, gap_s in group_gaps.items():
            prev_gap = self._prev_groups.get(order)
            name = ""
            for g in groups:
                if (hasattr(g, 'order') and g.order == order and
                        hasattr(g, 'name')):
                    name = g.name
                    break
                if isinstance(g, dict) and g.get("order") == order:
                    name = g.get("name", "?")
                    break

            # Ausreißer: Gruppe hat plötzlich einen großen Vorsprung.
            if (prev_gap is not None and gap_s >= BREAKAWAY_GAP_S
                    and prev_gap < BREAKAWAY_GAP_S * 0.5):
                alarm = self._add(
                    "breakaway", "warning",
                    f"Ausreißergruppe: {name} hat {gap_s:.0f}s Vorsprung!",
                    details={"_tag": str(order)},
                )
                if alarm:
                    new_alarms.append(alarm)

            # Eingeholt: Gruppe wurde (fast) eingeholt.
            if (prev_gap is not None and gap_s <= CAUGHT_GAP_S
                    and prev_gap > CAUGHT_GAP_S * 2):
                alarm = self._add(
                    "caught", "info",
                    f"Gruppe {name} wurde vom Peloton eingeholt.",
                    details={"_tag": str(order)},
                )
                if alarm:
                    new_alarms.append(alarm)

        # 3) Trikotwechsel.
        for code in ("Y", "G", "P", "W"):
            prev_bib = self._prev_jerseys.get(code)
            curr_bib = jerseys.get(code)
            if prev_bib is not None and curr_bib is not None and prev_bib != curr_bib:
                jersey_name = {"Y": "Gelb", "G": "Grün", "P": "Berg", "W": "Weiß"}.get(code, code)
                alarm = self._add(
                    "jersey_change", "warning",
                    f"Neuer Träger des {jersey_name}-Trikots! Bib {curr_bib}",
                    details={
                        "_tag": code,
                        "jersey_code": code,
                        "old_bib": prev_bib,
                        "new_bib": curr_bib,
                    },
                )
                if alarm:
                    new_alarms.append(alarm)

        # 4) GC-Führungswechsel.
        leader_bib = None
        if gc:
            first = gc[0]
            if hasattr(first, 'bib'):
                leader_bib = first.bib
            elif isinstance(first, dict):
                leader_bib = first.get("bib")
        if leader_bib is not None and self._prev_leader_bib is not None:
            if leader_bib != self._prev_leader_bib:
                alarm = self._add(
                    "gc_leader_change", "critical",
                    f"NEUER GESAMTFÜHRENDER! Bib {leader_bib} ist jetzt Erster.",
                    details={
                        "old_leader_bib": self._prev_leader_bib,
                        "new_leader_bib": leader_bib,
                    },
                )
                if alarm:
                    new_alarms.append(alarm)

        # 5) Aufgaben.
        # Pro-Bib-Cooldown (Tag "_tag"): bei mehreren gleichzeitigen Aufgaben
        # (z.B. Massensturz) darf jede eigene Alarm feuern, ohne dass die
        # erste die anderen blockiert. Parallele zu breakaway/caught.
        new_withdrawals = withdrawals - self._prev_withdrawals
        for bib in sorted(new_withdrawals):
            alarm = self._add(
                "withdrawal", "info",
                f"Aufgabe: Fahrer mit Bib {bib} hat das Rennen verlassen.",
                details={"_tag": str(bib), "bib": bib},
            )
            if alarm:
                new_alarms.append(alarm)

        # Zustand für nächstes Evaluate speichern.
        self._prev_jerseys = dict(jerseys)
        self._prev_leader_bib = leader_bib
        self._prev_race_status = race_status
        self._prev_groups = group_gaps
        self._prev_withdrawals = set(withdrawals)

        return new_alarms

    def get_alarms(self, limit: int = 10) -> list[dict[str, Any]]:
        """Liefert die letzten N Alarme als serialisierbare Liste."""
        recent = self._alarms[-limit:] if limit else self._alarms
        return [
            {
                "id": a.id,
                "ts": a.ts,
                "kind": a.kind,
                "severity": a.severity,
                "message": a.message,
                "details": a.details,
            }
            for a in reversed(recent)
        ]
