"""Gap-Chart (Feature 2).

Zeitreihen-Datenbank für Gap-Verläufe der GC-Spitze.
Speichert periodisch (2s) einen Schnappschuss der Top-N-Gaps und stellt ihn
als /api/gap-chart zur Verfügung. Das Frontend kann damit Gap-Verläufe
über die Zeit zeichnen (z. B. Liniendiagramm).

Architektur:
  - gap_history: Ringpuffer (max N Einträge, konfigurierbar).
  - Jeder Eintrag: {ts, gaps: {bib: relative_s}, groups: [{order, gap_s, name}]}
  - cleanup: altert Einträge > MAX_AGE_S werden beim nächsten Record entfernt.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("tdf.gap_chart")

# Maximale Anzahl an History-Einträgen im Ringpuffer.
MAX_HISTORY = 500
# Maximale Alter der History in Sekunden (ältere Einträge werden entfernt).
MAX_AGE_S = 600.0  # 10 Minuten
# Mindestabstand zwischen zwei Recordings (verhindert Rauschen).
MIN_INTERVAL_S = 2.0


@dataclass
class GapSnapshot:
    """Ein zeitlicher Schnappschuss der Gap-Situation."""
    timestamp: float                # Unix-Epoche
    gaps: dict[int, float]          # bib -> relative_seconds
    groups: list[dict[str, Any]]    # [{order, gap_s, name, size}]
    race_status: bool | None = None


class GapHistory:
    """Ringpuffer für Gap-Verläufe."""

    def __init__(self, max_history: int = MAX_HISTORY,
                 max_age_s: float = MAX_AGE_S,
                 min_interval_s: float = MIN_INTERVAL_S) -> None:
        self._snapshots: list[GapSnapshot] = []
        self._max_history = max_history
        self._max_age_s = max_age_s
        self._min_interval_s = min_interval_s
        self._last_ts: float = 0.0

    def record(self, gc: list[Any], groups: list[Any],
               race_status: bool | None) -> None:
        """Nimmt einen Gap-Schnappschuss auf (throttelt auf min_interval_s)."""
        now = time.time()
        if now - self._last_ts < self._min_interval_s:
            return
        self._last_ts = now

        # GC-Gaps extrahieren (Top-20 reichen).
        gaps: dict[int, float] = {}
        for e in gc:
            bib = None
            if hasattr(e, 'bib'):
                bib = e.bib
            elif isinstance(e, dict):
                bib = e.get("bib")
            
            rel = None
            if hasattr(e, 'relative_seconds'):
                rel = e.relative_seconds
            elif isinstance(e, dict):
                rel = e.get("relative_seconds")
                if rel is None:
                    rel = e.get("rel_s")
            
            if bib is not None and rel is not None:
                gaps[int(bib)] = float(rel)

        # Gruppen-Daten.
        group_data: list[dict[str, Any]] = []
        for g in groups:
            order = g.order if hasattr(g, 'order') else g.get("order")
            gap_s = g.gap_seconds if hasattr(g, 'gap_seconds') else (g.get("gap_s") or g.get("gap_seconds"))
            name = g.name if hasattr(g, 'name') else g.get("name", "?")
            size = g.size if hasattr(g, 'size') else g.get("size", 0)
            group_data.append({
                "order": order,
                "gap_s": gap_s,
                "name": name,
                "size": size,
            })

        self._snapshots.append(GapSnapshot(
            timestamp=now,
            gaps=gaps,
            groups=group_data,
            race_status=race_status,
        ))

        # Aufräumen: alte Einträge und max_history-Limit.
        self._cleanup(now)

    def _cleanup(self, now: float) -> None:
        """Entfernt alte Einträge und trimmt auf max_history."""
        cutoff = now - self._max_age_s
        self._snapshots = [s for s in self._snapshots
                           if s.timestamp >= cutoff]
        if len(self._snapshots) > self._max_history:
            self._snapshots = self._snapshots[-self._max_history:]

    def get_history(self, top_n: int = 20,
                    since: float | None = None) -> dict[str, Any]:
        """Liefert Gap-Chart-Daten als serialisierbares Dict.

        Args:
            top_n: Nur die Top-N-Bibs im Ergebnis tracken.
            since: Unix-Timestamp; nur Einträge danach.

        Returns:
            {
              "latest_ts": float,
              "count": int,
              "bibs": [int, ...],           # alle beobachteten Bibs
              "series": [
                {"ts": float, "gaps": {bib: gap, ...}, "groups": [...]},
                ...
              ]
            }
        """
        if not self._snapshots:
            return {"latest_ts": 0, "count": 0, "bibs": [], "series": []}

        if since is None:
            since = self._snapshots[0].timestamp

        # Alle Bibs sammeln (nur die relevanten Top-N).
        all_bibs: set[int] = set()
        for s in self._snapshots:
            all_bibs.update(s.gaps.keys())

        # Nur Einträge seit `since`.
        filtered = [s for s in self._snapshots if s.timestamp >= since]

        series = []
        for s in filtered:
            entry: dict[str, Any] = {
                "ts": round(s.timestamp, 1),
                "gaps": {str(bib): round(gap, 1)
                         for bib, gap in s.gaps.items()},
                "groups": s.groups,
            }
            if s.race_status is not None:
                entry["race_status"] = s.race_status
            series.append(entry)

        return {
            "latest_ts": self._snapshots[-1].timestamp if self._snapshots else 0,
            "count": len(series),
            "bibs": sorted(all_bibs),
            "series": series,
        }
