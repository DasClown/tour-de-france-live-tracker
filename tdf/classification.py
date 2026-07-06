"""Berg/Sprint-Klassifikation (Feature 3).

Wertet Zwischenwertungen aus dem checkpoint-Bind und den rankingTypeJerseys-
sowie rankingType-Binds aus. Liefert:

  1. Bergwertung (KOM): Punkte aus pmm-Trikot, kategorisierte Pässe aus
     checkpoint-checkpointSummits.
  2. Sprintwertung: Punkte aus pmp-Trikot, Zwischensprints aus Checkpoints
     vom Typ sprint (checkpointTypes).

Daten werden über das /state-Snapshot-Feld "classifications" ausgeliefert
sowie als eigener Endpoint /api/classification.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from . import config as cfg

log = logging.getLogger("tdf.classification")


@dataclass
class MountainClimb:
    """Ein kategorisierter Berg / Anstieg."""
    index: int
    name: str = ""
    category: str = ""          # "HC", "1", "2", "3", "4" oder ""
    km_to_finish: float | None = None
    length_km: float | None = None
    avg_gradient: float | None = None
    summit_lat: float | None = None
    summit_lon: float | None = None
    points_kom: int = 0         # Punkte für Bergwertung
    points_polka: int = 0       # Punkte für gepunktetes Trikot


@dataclass
class Sprint:
    """Ein Zwischensprint."""
    index: int
    place: str = ""
    km_to_finish: float | None = None
    lat: float | None = None
    lon: float | None = None
    points: int = 0


@dataclass
class ClassificationState:
    """Aktuelle Berg- und Sprint-Klassifikation."""
    mountains: list[MountainClimb] = field(default_factory=list)
    sprints: list[Sprint] = field(default_factory=list)
    # Aktuelle Rangliste der Bergwertung (aus Jerseys-Bind, pmm).
    kom_ranking: list[dict[str, Any]] = field(default_factory=list)
    # Aktuelle Rangliste der Sprintwertung (aus Jerseys-Bind, pmp).
    sprint_ranking: list[dict[str, Any]] = field(default_factory=list)


# Berg-Kategorien basierend auf ASO checkpointSummit-Klassifikation.
# Vereinfachte Abbildung.
CATEGORY_LABELS = {
    "HC": "Außer Kategorie",
    "1": "1. Kategorie",
    "2": "2. Kategorie",
    "3": "3. Kategorie",
    "4": "4. Kategorie",
}


def parse_mountain_from_checkpoint(
    cp: dict[str, Any],
    index: int,
    remaining_distance: float | None = None,
) -> MountainClimb | None:
    """Wandelt einen Checkpoint in ein MountainClimb-Objekt, falls es ein Berg ist.

    ASO-Checkpoints mit checkpointSummits enthalten:
      - name/place: Name des Anstiegs
      - checkpointSummits: Array von Summit-Objekten mit category, length, avgGradient
      - latitude, longitude
    """
    summit_data = cp.get("checkpointSummits") or cp.get("checkpointSummit")
    if not summit_data:
        return None

    lat = cp.get("latitude") or cp.get("lat")
    lon = cp.get("longitude") or cp.get("lon") or cp.get("lng")
    name = cp.get("place") or cp.get("name") or f"Berg {index + 1}"

    # Wenn es ein Array ist, nimm das erste Element.
    if isinstance(summit_data, list):
        summit = summit_data[0] if summit_data else {}
    else:
        summit = summit_data

    category = str(summit.get("category", ""))
    length = summit.get("length")
    gradient = summit.get("avgGradient") or summit.get("averageGradient")

    try:
        return MountainClimb(
            index=index,
            name=str(name),
            category=category,
            km_to_finish=float(remaining_distance) if remaining_distance is not None else None,
            length_km=float(length) if length is not None else None,
            avg_gradient=float(gradient) if gradient is not None else None,
            summit_lat=float(lat) if lat is not None else None,
            summit_lon=float(lon) if lon is not None else None,
        )
    except (TypeError, ValueError):
        return None


def parse_sprint_from_checkpoint(
    cp: dict[str, Any],
    index: int,
    remaining_distance: float | None = None,
) -> Sprint | None:
    """Wandelt einen Checkpoint in ein Sprint-Objekt, falls es ein Sprint ist.

    ASO-Checkpoints mit checkpointTypes enthalten Sprint-Daten.
    """
    cp_types = cp.get("checkpointTypes")
    if not cp_types:
        return None

    # Prüfen, ob einer der Typen ein Sprint ist.
    is_sprint = False
    if isinstance(cp_types, list):
        for t in cp_types:
            if isinstance(t, dict) and t.get("type") == "sprint":
                is_sprint = True
                break
            if isinstance(t, str) and "sprint" in t.lower():
                is_sprint = True
                break
    elif isinstance(cp_types, str) and "sprint" in cp_types.lower():
        is_sprint = True

    if not is_sprint:
        return None

    lat = cp.get("latitude") or cp.get("lat")
    lon = cp.get("longitude") or cp.get("lon") or cp.get("lng")
    place = cp.get("place") or cp.get("name") or f"Sprint {index + 1}"

    return Sprint(
        index=index,
        place=str(place),
        km_to_finish=float(remaining_distance) if remaining_distance is not None else None,
        lat=float(lat) if lat is not None else None,
        lon=float(lon) if lon is not None else None,
    )


def parse_checkpoints_to_classifications(
    checkpoints: list[Any],
    groups: list[Any] | None = None,
) -> ClassificationState:
    """Durchläuft alle Checkpoints und extrahiert Berg- und Sprint-Daten.

    Args:
        checkpoints: Liste der Checkpoint-Dataclass-Objekte (aus state.checkpoints).
        groups: Optionale Gruppen-Daten für remainingDistance-Kontext.

    Returns:
        ClassificationState mit Mountains und Sprints.
    """
    state = ClassificationState()

    # Restdistanz des Pelotons (falls vorhanden).
    remaining_km: float | None = None
    if groups:
        for g in groups:
            if hasattr(g, 'remaining_km'):
                remaining_km = g.remaining_km
                break
            elif isinstance(g, dict) and g.get("remaining_km") is not None:
                remaining_km = g["remaining_km"]
                break

    for i, cp in enumerate(checkpoints):
        # Rohdaten, falls cp ein Dataclass-Objekt ist.
        if hasattr(cp, 'lat') and hasattr(cp, 'lon'):
            kind = cp.kind if hasattr(cp, 'kind') else ""
            if kind == "mountain":
                climb = MountainClimb(
                    index=i,
                    name=cp.place or f"Berg {i + 1}",
                    category="",
                    km_to_finish=remaining_km,
                    summit_lat=cp.lat,
                    summit_lon=cp.lon,
                )
                state.mountains.append(climb)
            elif kind == "sprint":
                sprint = Sprint(
                    index=i,
                    place=cp.place or f"Sprint {i + 1}",
                    km_to_finish=remaining_km,
                    lat=cp.lat,
                    lon=cp.lon,
                )
                state.sprints.append(sprint)
            continue
        elif isinstance(cp, dict):
            raw_cp = cp
            kind = cp.get("kind", "")
        else:
            continue

        if kind == "mountain":
            climb = parse_mountain_from_checkpoint(raw_cp, i, remaining_km)
            if climb:
                state.mountains.append(climb)
        elif kind == "sprint":
            sprint = parse_sprint_from_checkpoint(raw_cp, i, remaining_km)
            if sprint:
                state.sprints.append(sprint)

    return state


def _get_bib(entry: Any) -> int | None:
    """Extrahiert bib aus einem GC-Eintrag (dict oder RankEntry)."""
    if hasattr(entry, 'bib'):
        return entry.bib
    if isinstance(entry, dict):
        return entry.get("bib")
    return None


def _get_pos(entry: Any) -> int | None:
    """Extrahiert Position aus einem GC-Eintrag (dict oder RankEntry)."""
    if hasattr(entry, 'position'):
        return entry.position
    if isinstance(entry, dict):
        return entry.get("pos") or entry.get("position")
    return None


def extract_mountain_ranking(jerseys: dict[str, int],
                             meta: dict[int, dict[str, Any]],
                             gc: list[Any]) -> list[dict[str, Any]]:
    """Erzeugt die Berg-KOM-Rangliste aus Jersey-Daten.

    Das Bergtrikot (pmm/code=P) zeigt den Führenden.
    Wir erzeugen daraus eine Rangliste mit aktuellem Stand.
    """
    if "P" not in jerseys:
        return []

    # Für eine vollständige Rangliste bräuchten wir das offizielle
    # rankingTypeJerseys-Bind mit allen Positionen. Fallback:
    # nur den Führenden zeigen.
    bib = jerseys["P"]
    entries: list[dict[str, Any]] = []

    nm = _name(meta, bib)
    pos_in_gc = next(
        (i + 1 for i, e in enumerate(gc)
         if _get_bib(e) == bib),
        None,
    )
    entries.append({
        "rank": 1,
        "bib": bib,
        "name": nm.split("  (")[0] if "  (" in nm else nm,
        "team": nm.split("  (")[-1].rstrip(")") if "  (" in nm else "",
        "points_kom": 0,  # ASO liefert keine Roh-Punkte im REST-Call.
        "gc_pos": pos_in_gc,
    })
    return entries


def extract_sprint_ranking(jerseys: dict[str, int],
                           meta: dict[int, dict[str, Any]],
                           gc: list[Any]) -> list[dict[str, Any]]:
    """Erzeugt die Sprint-Punkte-Rangliste aus Jersey-Daten."""
    if "G" not in jerseys:
        return []

    bib = jerseys["G"]
    entries: list[dict[str, Any]] = []
    nm = _name(meta, bib)
    pos_in_gc = next(
        (i + 1 for i, e in enumerate(gc)
         if _get_bib(e) == bib),
        None,
    )
    entries.append({
        "rank": 1,
        "bib": bib,
        "name": nm.split("  (")[0] if "  (" in nm else nm,
        "team": nm.split("  (")[-1].rstrip(")") if "  (" in nm else "",
        "points_sprint": 0,
        "gc_pos": pos_in_gc,
    })
    return entries


def _name(meta: dict[int, dict[str, Any]], bib: int) -> str:
    r = meta.get(bib, {})
    first = (r.get("firstname") or "").strip()
    last = (r.get("lastnameshort") or r.get("lastname") or "").strip()
    team = r.get("team_name") or r.get("team_code") or ""
    full = f"{first} {last}".strip()
    return full + (f"  ({team})" if team else "")


def to_json(state: ClassificationState,
            meta: dict[int, dict[str, Any]],
            jerseys: dict[str, int],
            gc: list[Any]) -> dict[str, Any]:
    """Serialisiert die Klassifikation für den Snapshot."""
    return {
        "mountains": [
            {
                "index": m.index,
                "name": m.name,
                "category": m.category,
                "category_label": CATEGORY_LABELS.get(m.category, m.category),
                "km_to_finish": m.km_to_finish,
                "length_km": round(m.length_km, 1) if m.length_km is not None else None,
                "avg_gradient": round(m.avg_gradient, 1) if m.avg_gradient is not None else None,
                "summit_lat": m.summit_lat,
                "summit_lon": m.summit_lon,
            }
            for m in state.mountains
        ],
        "sprints": [
            {
                "index": s.index,
                "place": s.place,
                "km_to_finish": s.km_to_finish,
                "lat": s.lat,
                "lon": s.lon,
            }
            for s in state.sprints
        ],
        "kom_ranking": state.kom_ranking,
        "sprint_ranking": state.sprint_ranking,
    }
