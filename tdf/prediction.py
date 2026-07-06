"""Restzeit-Vorhersage (Feature 1).

Berechnet für jede Gruppe und jeden Top-N-Fahrer die voraussichtliche
Zielankunftszeit basierend auf aktueller Geschwindigkeit und Restdistanz.

Zwei Modi:
  - group: Vorhersage aus pack-Gruppen-Daten (computedSpeed + remainingDistance).
  - rider: Vorhersage aus Einzeltelemetrie (kph + kmToFinish) – nur Top-N.

Die Vorhersage wird im /state-Snapshot unter "predictions" ausgeliefert.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Prediction:
    """Vorhersage für eine Gruppe/einen Fahrer."""
    bib_or_group: int | str       # Bib (Fahrer) oder Group-Order (Gruppe)
    label: str                    # Name oder Gruppenname
    remaining_km: float | None    # Restdistanz in km
    speed_kph: float | None       # Aktuelle Geschwindigkeit km/h
    eta_seconds: float | None     # Voraussichtliche Restzeit in Sekunden
    eta_absolute: str | None      # Voraussichtliche Ankunftszeit als HH:MM:SS
    # Geschätzter Zielzeitpunkt (epoch seconds)
    estimated_finish_ts: float | None = None


def _fmt_eta(seconds: float) -> str:
    """Format seconds since now as absolute HH:MM:SS."""
    now = time.time()
    eta_ts = now + seconds
    import datetime
    dt = datetime.datetime.fromtimestamp(eta_ts, tz=datetime.timezone.utc)
    return dt.strftime("%H:%M:%S UTC")


def predict_group_pace(remaining_km: float, speed_kph: float) -> float:
    """Berechne Restzeit in Sekunden aus Restdistanz und Geschwindigkeit.

    Args:
        remaining_km: Restdistanz in Kilometern.
        speed_kph: Durchschnittsgeschwindigkeit in km/h.

    Returns:
        Voraussichtliche Restzeit in Sekunden.
    """
    if speed_kph <= 0:
        return 0.0
    hours = remaining_km / speed_kph
    return hours * 3600.0


def compute_predictions(
    groups: list[Any],
    top_riders: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Berechne Vorhersagen für alle Gruppen und Top-N-Fahrer.

    Args:
        groups: Liste der GroupInfo-Objekte (aus state.groups).
        top_riders: Liste der Top-N-Rider-Dicts (aus state.telemetry.riders).

    Returns:
        Liste von Prediction-Dicts (serialisierbar).
    """
    predictions: list[dict[str, Any]] = []

    # Gruppen-Vorhersagen
    for g in groups:
        remaining_km = g.get("remaining_km")
        speed = g.get("speed_kph")
        if remaining_km is not None and speed is not None and speed > 0:
            eta_s = predict_group_pace(remaining_km, speed)
            predictions.append({
                "type": "group",
                "id": g.get("order"),
                "label": g.get("name", "?"),
                "remaining_km": round(remaining_km, 1),
                "speed_kph": round(speed, 1),
                "eta_seconds": round(eta_s, 0),
                "eta_absolute": _fmt_eta(eta_s),
            })

    # Top-Rider-Vorhersagen
    for r in top_riders:
        bib = r.get("Bib") or r.get("bib")
        name = r.get("_name", "?")
        km_to_finish = r.get("kmToFinish")
        kph = r.get("kph")
        if (
            km_to_finish is not None
            and kph is not None
            and kph > 0
            and km_to_finish > 0
        ):
            eta_s = predict_group_pace(float(km_to_finish), float(kph))
            predictions.append({
                "type": "rider",
                "id": bib,
                "label": name.split("  (")[0] if "  (" in name else name,
                "remaining_km": round(float(km_to_finish), 1),
                "speed_kph": round(float(kph), 1),
                "eta_seconds": round(eta_s, 0),
                "eta_absolute": _fmt_eta(eta_s),
            })

    return predictions
