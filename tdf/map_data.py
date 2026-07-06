"""Karten-Koordinaten (Feature 5).

Bereitet erweiterte Karten-Daten für das Frontend auf:

  1. Gruppen-Marker: existieren bereits via state.groups[].lat/lon.
  2. Fahrer-Positionen: existieren via Top-N.
  3. Routen-Polyline der Etappe: aus profils-CSV extrahieren (falls vorhanden).
  4. Berg- und Sprint-Marker auf der Karte: aus classification.
  5. Karten-Anpassungen: optimale Zoom-Stufe, Mittelpunkt, Bounding-Box.

Dieses Modul aggregiert alle kartographisch relevanten Daten für den
/api/map-data Endpoint sowie das "map" Feld im /state-Snapshot.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("tdf.map_data")


def compute_bounding_box(
    points: list[tuple[float, float]],
) -> dict[str, float] | None:
    """Berechne Bounding-Box aus einer Liste von (lat, lon)-Punkten.

    Returns:
        Dict mit north, south, east, west oder None, wenn zu wenige Punkte.
    """
    if len(points) < 2:
        return None
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return {
        "north": max(lats),
        "south": min(lats),
        "east": max(lons),
        "west": min(lons),
    }


def compute_center(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Berechne Mittelpunkt aus einer Liste von (lat, lon)-Punkten."""
    if not points:
        return None
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return (sum(lats) / len(lats), sum(lons) / len(lons))


def extract_route_polyline(profile: Any | None) -> list[list[float]] | None:
    """Extrahiert die Routen-Polyline aus dem Profil-Modul.

    Das Profil-Modul liefert eine Liste von Checkpoints mit lat/lon.
    Daraus wird eine Polyline (Liste von [lat, lon]) erzeugt.

    Args:
        profile: Das Profil-Objekt aus app["profile"].

    Returns:
        Liste von [lat, lon]-Paaren, None wenn kein Profil verfügbar.
    """
    if profile is None:
        return None
    # Prüfe, ob profile ein Dict mit "checkpoints" ist.
    if isinstance(profile, dict):
        checkpoints = profile.get("checkpoints") or profile.get("points") or []
    else:
        # Profile-Objekt mit checkpoints/latlon.
        checkpoints = getattr(profile, "checkpoints", None) or getattr(profile, "points", None) or []
    if not checkpoints:
        return None

    route: list[list[float]] = []
    for cp in checkpoints:
        if isinstance(cp, dict):
            lat = cp.get("lat") or cp.get("latitude")
            lon = cp.get("lon") or cp.get("lng") or cp.get("longitude")
        else:
            lat = getattr(cp, "lat", None) or getattr(cp, "latitude", None)
            lon = getattr(cp, "lon", None) or getattr(cp, "lng", None) or getattr(cp, "longitude", None)
        if lat is not None and lon is not None:
            route.append([float(lat), float(lon)])
    return route if route else None


def build_map_data(
    groups: list[Any],
    top_riders: list[dict[str, Any]],
    checkpoints: list[Any],
    classifications: dict[str, Any] | None = None,
    profile: Any | None = None,
) -> dict[str, Any]:
    """Aggregiert alle Karten-Daten für den Snapshot/Endpoint.

    Args:
        groups: Liste von GroupInfo-Objekten oder -Dicts.
        top_riders: Liste von Top-N-Rider-Dicts (aus telemetry.riders).
        checkpoints: Liste von Checkpoint-Dataclass-Objekten.
        classifications: Dict aus classification.to_json() (optional).
        profile: Profil-Objekt aus dem App-Context (optional).

    Returns:
        Dict mit allen Karten-Daten.
    """
    # Alle verfügbaren Koordinaten sammeln.
    all_points: list[tuple[float, float]] = []

    # Gruppen-Koordinaten.
    for g in groups:
        if hasattr(g, 'lat') and hasattr(g, 'lon'):
            if g.lat is not None and g.lon is not None:
                all_points.append((g.lat, g.lon))
        elif isinstance(g, dict):
            lat = g.get("lat")
            lon = g.get("lon")
            if lat is not None and lon is not None:
                all_points.append((float(lat), float(lon)))

    # Checkpoint-Koordinaten.
    for cp in checkpoints:
        if hasattr(cp, 'lat') and hasattr(cp, 'lon'):
            if cp.lat is not None and cp.lon is not None:
                all_points.append((cp.lat, cp.lon))
        elif isinstance(cp, dict):
            lat = cp.get("lat") or cp.get("latitude")
            lon = cp.get("lon") or cp.get("lng") or cp.get("longitude")
            if lat is not None and lon is not None:
                all_points.append((float(lat), float(lon)))

    # Routen-Polyline.
    route = extract_route_polyline(profile)

    # Berg- und Sprint-Marker aus classification.
    mountain_markers: list[dict[str, Any]] = []
    sprint_markers: list[dict[str, Any]] = []
    if classifications:
        for m in classifications.get("mountains", []):
            if m.get("summit_lat") is not None and m.get("summit_lon") is not None:
                mountain_markers.append({
                    "lat": m["summit_lat"],
                    "lon": m["summit_lon"],
                    "name": m.get("name", "Berg"),
                    "category": m.get("category", ""),
                    "category_label": m.get("category_label", ""),
                    "length_km": m.get("length_km"),
                    "avg_gradient": m.get("avg_gradient"),
                })
        for s in classifications.get("sprints", []):
            if s.get("lat") is not None and s.get("lon") is not None:
                sprint_markers.append({
                    "lat": s["lat"],
                    "lon": s["lon"],
                    "place": s.get("place", "Sprint"),
                })

    result: dict[str, Any] = {
        "route": route,
        "mountain_markers": mountain_markers,
        "sprint_markers": sprint_markers,
        "checkpoints_count": len(checkpoints),
    }

    # Bounding-Box und Mittelpunkt, falls genug Punkte.
    bbox = compute_bounding_box(all_points) if len(all_points) >= 2 else None
    if bbox:
        result["bbox"] = bbox
        center = compute_center([
            (bbox["south"], bbox["west"]),
            (bbox["north"], bbox["east"]),
        ])
        if center:
            result["center"] = {"lat": round(center[0], 4), "lon": round(center[1], 4)}

    return result
