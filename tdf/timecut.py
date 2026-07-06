"""Time-Cut-Rechner (Feature 7): hors délai.

UCI-Regel für Grand Tours (Tour de France Artikel 2.6.032):
Alle Rider müssen innerhalb von X % der Siegerzeit ankommen, sonst werden
sie aus dem Klassement entfernt („hors délai").

Die Prozent-Schwelle hängt von Durchschnittsgeschwindigkeit ab:

  Etappe flach (avg speed):
    < 36 km/h  -> 12%
    36-40      -> 10%
    40-42      -> 8%
    > 42       -> 6% (selten, aber bei schnellem Feld)

  Etappe Berg/Mittelgebirge:
    Generösere Cuts (15-20%), da das Feld auseinanderfällt.

  Zeitfahren (ITT): eigener Cut, hier nicht modelliert.

Live berechnet der Tracker für jede Gruppe:
  - Ihre aktuelle Lücke (gap_seconds)
  - Projektion: wann wird sie ankommen?
  - Status: safe / warning / danger / eliminated
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("tdf.timecut")

# Status-Schwellen (relativ zur Cut-Zeit).
# Eine Gruppe mit Status "danger" ist akut gefährdet.
STATUS_THRESHOLDS = {
    "safe": 0.85,        # < 85% der Cut-Zeit
    "warning": 0.95,     # 85-95%
    "danger": 1.0,       # 95-100% (akute Gefahr)
    # >= 100% = eliminated
}


# --------------------------------------------------------------------------- #
# Threshold-Bestimmung
# --------------------------------------------------------------------------- #
def cut_threshold_percent(avg_speed_kph: float, stage_type: str = "flat") -> float:
    """Liefert den Time-Cut-Prozentsatz je nach Tempo und Etappentyp.

    Args:
        avg_speed_kph: Aktuelle Durchschnittsgeschwindigkeit des Siegers.
        stage_type: 'flat', 'mountain', 'medium', 'itt', 'ttt'.

    Returns:
        Cut-Prozent (z. B. 10.0 für 10%).
    """
    if stage_type in ("itt", "ttt"):
        # Zeitfahren: eigene Regeln (25% im ITT, 33% im TTT)
        # Wir behandeln ITT/TTT hier nicht vertieft, geben konservative Werte.
        return 25.0 if stage_type == "itt" else 33.0

    if stage_type in ("mountain", "medium"):
        # Berg/Mittelgebirge: generöser, weil Feld zerfällt
        if avg_speed_kph < 35:
            return 18.0
        elif avg_speed_kph < 38:
            return 16.0
        else:
            return 15.0

    # Default / flat
    if avg_speed_kph < 36:
        return 12.0
    elif avg_speed_kph < 40:
        return 10.0
    elif avg_speed_kph < 42:
        return 8.0
    else:
        return 6.0


# --------------------------------------------------------------------------- #
# Cut-Zeit aus Siegerzeit
# --------------------------------------------------------------------------- #
def cut_time_seconds(winner_time_s: float, threshold_pct: float) -> float:
    """Cut-Zeit = Siegerzeit · (1 + threshold/100).

    Args:
        winner_time_s: Absolute Siegerzeit in Sekunden.
        threshold_pct: Cut-Prozent (z. B. 10.0).

    Returns:
        Absolute Cut-Zeit in Sekunden. 0 bei winner_time_s=0.
    """
    if winner_time_s <= 0:
        return 0.0
    return winner_time_s * (1.0 + threshold_pct / 100.0)


# --------------------------------------------------------------------------- #
# Projektion
# --------------------------------------------------------------------------- #
def project_arrival(remaining_km: float, speed_kph: float) -> float | None:
    """Projiziert die Restzeit in Sekunden bis zum Ziel.

    Returns:
        Sekunden bis Ziel, None bei speed=0, 0 bei km=0.
    """
    if speed_kph <= 0:
        return None
    if remaining_km <= 0:
        return 0.0
    hours = remaining_km / speed_kph
    return hours * 3600.0


# --------------------------------------------------------------------------- #
# Status-Berechnung
# --------------------------------------------------------------------------- #
def group_status(group_gap_s: float, cut_time_s: float,
                 projected_arrival_s: float | None,
                 winner_time_s: float) -> dict[str, Any]:
    """Bestimmt den Time-Cut-Status einer Gruppe.

    Args:
        group_gap_s: Aktuelle Lücke der Gruppe zur Spitze in Sekunden.
        cut_time_s: Absolute Cut-Zeit in Sekunden.
        projected_arrival_s: Projektion der Ankunftszeit (absolut) oder None.
        winner_time_s: Aktuelle projizierte/absolute Siegerzeit.

    Returns:
        Dict mit status, gap_s, cut_time_s, margin_s, margin_pct.
    """
    # Wie viel Puffer hat die Gruppe aktuell?
    # Wenn cut_time = 18000s und die Gruppe bei 17000s liegt: margin = 1000s.
    # Da wir oft keine absolute Ankunftszeit haben, nehmen wir die aktuelle
    # Lücke: gap_s vs. erlaubte Lücke = cut_time - winner_time.
    if cut_time_s <= 0 or winner_time_s <= 0:
        allowed_gap = cut_time_s  # Fallback
    else:
        allowed_gap = cut_time_s - winner_time_s

    # Margin: wie viel Lücke ist noch erlaubt?
    margin_s = allowed_gap - group_gap_s
    margin_pct = (margin_s / allowed_gap * 100.0) if allowed_gap > 0 else 0.0

    # Status anhand der Ausnutzung der erlaubten Lücke
    ratio = group_gap_s / allowed_gap if allowed_gap > 0 else 1.0

    if ratio < STATUS_THRESHOLDS["safe"]:
        status = "safe"
    elif ratio < STATUS_THRESHOLDS["warning"]:
        status = "warning"
    elif ratio < STATUS_THRESHOLDS["danger"]:
        status = "danger"
    else:
        status = "eliminated"

    return {
        "status": status,
        "gap_s": round(group_gap_s, 0),
        "cut_time_s": round(cut_time_s, 0),
        "winner_time_s": round(winner_time_s, 0),
        "margin_s": round(margin_s, 0),
        "margin_pct": round(margin_pct, 1),
    }


# --------------------------------------------------------------------------- #
# Vollständige Analyse
# --------------------------------------------------------------------------- #
def analyze_groups(groups: list[Any], winner_time_s: float,
                   avg_speed_kph: float, stage_type: str = "flat") -> dict[str, Any]:
    """Analysiert alle Gruppen auf Time-Cut-Gefahr.

    Args:
        groups: Liste von GroupInfo-Objekten oder -Dicts mit gap_seconds,
                remaining_km, speed_kph, name.
        winner_time_s: Aktuelle/geschätzte Siegerzeit.
        avg_speed_kph: Avg-Speed für Threshold-Bestimmung.
        stage_type: 'flat', 'mountain', 'medium'.

    Returns:
        Dict mit threshold_pct, cut_time_s, groups[] (sortiert nach Risiko).
    """
    threshold = cut_threshold_percent(avg_speed_kph, stage_type)
    cut_time = cut_time_seconds(winner_time_s, threshold)

    analyzed: list[dict[str, Any]] = []
    for g in groups:
        # Dict oder Objekt? Beide unterstützen.
        if hasattr(g, "gap_seconds"):
            gap = g.gap_seconds or 0.0
            name = getattr(g, "name", "?")
            order = getattr(g, "order", 0)
            remaining_km = getattr(g, "remaining_km", None)
            # GroupInfo nutzt .speed, manche Typen .speed_kph
            speed = getattr(g, "speed", None) or getattr(g, "speed_kph", None)
        elif isinstance(g, dict):
            gap = g.get("gap_seconds") or g.get("gap_s") or 0.0
            name = g.get("name", "?")
            order = g.get("order", 0)
            remaining_km = g.get("remaining_km")
            speed = g.get("speed_kph") or g.get("speed")
        else:
            continue

        # Projektion (optional)
        proj = None
        if remaining_km is not None and speed is not None:
            proj = project_arrival(remaining_km, speed)

        s = group_status(gap, cut_time, proj, winner_time_s)
        s["name"] = name
        s["order"] = order
        if proj is not None:
            s["projected_arrival_s"] = round(proj, 0)
        analyzed.append(s)

    # Sortieren: danger/eliminated zuerst (akute Fälle), dann warning, dann safe.
    priority = {"eliminated": 0, "danger": 1, "warning": 2, "safe": 3}
    analyzed.sort(key=lambda x: priority.get(x["status"], 4))

    return {
        "threshold_pct": threshold,
        "cut_time_s": round(cut_time, 0),
        "winner_time_s": round(winner_time_s, 0),
        "stage_type": stage_type,
        "groups": analyzed,
    }
