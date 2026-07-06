"""Geodaten-Mathematik: Haversine, Destination-Point, Dead-Reckon, Lerp.

Haversine übernommen aus mullummer/racecenter (R=6371 km).
Destination-Point ergänzt (sphärisch, δ=d/R), da mullummer sie nicht enthält.
Dead-Reckon und Lerp dienen nur der UI-Glättung der Top-N-Positionen zwischen
zwei SSE-Ticks – sie ersetzen nicht die offiziellen Lücken aus dem pack-Bind.
"""

from __future__ import annotations

import math
from typing import Any

from . import config as cfg

_R = cfg.EARTH_RADIUS_KM


# --------------------------------------------------------------------------- #
# Haversine (Referenz: mullummer/racecenter)
# --------------------------------------------------------------------------- #
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Großkreis-Distanz in km, sphärisch, R=6371."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _R * c


# --------------------------------------------------------------------------- #
# Destination-Point (Vorwärtsprojektion auf dem Großkreis)
# --------------------------------------------------------------------------- #
def destination_point(lat: float, lon: float, bearing_deg: float,
                      distance_km: float) -> tuple[float, float]:
    """Sphärische Vorwärtsprojektion.

    δ = d / R; φ₂ = asin(sinφ₁·cosδ + cosφ₁·sinδ·cosθ);
    λ₂ = λ₁ + atan2(sinθ·sinδ·cosφ₁, cosδ − sinφ₁·sinφ₂).
    """
    if distance_km == 0:
        return lat, lon
    rlat = math.radians(lat)
    rlon = math.radians(lon)
    theta = math.radians(bearing_deg)
    delta = distance_km / _R

    sin_phi2 = (math.sin(rlat) * math.cos(delta)
                + math.cos(rlat) * math.sin(delta) * math.cos(theta))
    phi2 = math.asin(max(-1.0, min(1.0, sin_phi2)))
    y = math.sin(theta) * math.sin(delta) * math.cos(rlat)
    x = math.cos(delta) - math.sin(rlat) * math.sin(phi2)
    lam2 = rlon + math.atan2(y, x)

    return (math.degrees(phi2),
            ((math.degrees(lam2) + 540) % 360) - 180)  # Normalisierung [-180,180]


# --------------------------------------------------------------------------- #
# Lerp (Re-Anchor)
# --------------------------------------------------------------------------- #
def lerp_reanchor(old_lat: float, old_lon: float, new_lat: float, new_lon: float,
                  alpha: float = cfg.LERP_ALPHA) -> tuple[float, float]:
    """Lineare Interpolation zwischen alter und neuer Position.

    Verhindert harte Sprünge, wenn nach kurzer Extrapolation ein echter
    GPS-Tick eintrifft. α=0.3 → 30% neue Position, 70% alte.
    """
    return (old_lat * (1 - alpha) + new_lat * alpha,
            old_lon * (1 - alpha) + new_lon * alpha)


# --------------------------------------------------------------------------- #
# Dead-Reckon (Top-N-Glättung)
# --------------------------------------------------------------------------- #
def dead_reckon(rider: dict[str, Any], dt_s: float) -> tuple[float, float] | None:
    """Projiziert die Rider-Position um dt_s Sekunden nach vorne.

    Nutzt letzte lat/lon + Course (Bearing) + kph. Liefert None, wenn
    Eingangsdaten fehlen. Nur zur UI-Glättung zwischen SSE-Ticks.
    """
    lat = rider.get("Latitude")
    lon = rider.get("Longitude")
    course = rider.get("Course")
    kph = rider.get("kph")
    if lat is None or lon is None or course is None or kph is None:
        return None
    try:
        distance_km = float(kph) * dt_s / 3600.0
        return destination_point(float(lat), float(lon), float(course), distance_km)
    except (TypeError, ValueError):
        return None


def step_extrapolation(state_extrapolated: dict[int, tuple[float, float]],
                       riders: dict[int, dict[str, Any]], top_n: list[int],
                       dt_s: float) -> None:
    """Extrapoliert Top-N-Positionen um dt_s und schreibt sie in ``state_extrapolated``.

    Mutiert das übergebene Dict in place. rider ohne taugliche Eingangsdaten
    behalten ihre alte Position.
    """
    for bib in top_n:
        r = riders.get(bib)
        if not r:
            continue
        # Vom zuletzt extrapolierten Punkt weitergehen, falls vorhanden –
        # sonst vom rohen GPS-Punkt.
        if bib in state_extrapolated:
            lat, lon = state_extrapolated[bib]
            base = {**r, "Latitude": lat, "Longitude": lon}
        else:
            base = r
        np = dead_reckon(base, dt_s)
        if np is not None:
            state_extrapolated[bib] = np


def reanchor_on_tick(state_extrapolated: dict[int, tuple[float, float]],
                     riders: dict[int, dict[str, Any]], top_n: list[int]) -> None:
    """Bei echtem SSE-Tick: geglättete Annäherung an neue Roh-GPS-Daten."""
    for bib in top_n:
        r = riders.get(bib)
        if not r:
            continue
        lat = r.get("Latitude")
        lon = r.get("Longitude")
        if lat is None or lon is None:
            continue
        old = state_extrapolated.get(bib)
        if old is None:
            state_extrapolated[bib] = (float(lat), float(lon))
        else:
            state_extrapolated[bib] = lerp_reanchor(old[0], old[1],
                                                     float(lat), float(lon))
