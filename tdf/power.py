"""Power-Schätzung (Feature 6): Watt und W/kg pro Fahrer.

ASO liefert keinen Leistungswert, aber wir haben kph + Gradient + Rider-Gewicht.
Daraus ist Leistung physikalisch berechenbar:

    P = (Cr·m·g·v)             # Rollwiderstand
      + (0.5·ρ·CdA·v³·(1-ε))   # Luftwiderstand (mit Drafting-Savings ε)
      + (m·g·v·slope)          # Steigung

Konstanten (Radsport-Standardwerte, gut dokumentiert in der Trainingslehre):
  - Cr   = 0.004   (Rollwiderstandsbeiwert Straßenrennen)
  - g    = 9.81    m/s²
  - ρ    = 1.2     kg/m³ (Luftdichte Meereshöhe, 15°C)
  - CdA  = 0.32    m² (Einzelfahrer auf Drop-Bar)
  - CdA  = 0.22    m² (im Peloton, Tropfen-Position plus Windschatten)
  - ε    = 0.0     Solo, ~0.3 im Peloton (Schätzwert)

ACHTUNG: Dies ist eine MODELLRECHNUNG, keine gemessene Leistung.
Echte Watt-Zahlen variieren mit Wind, Untergrund, Fahrsituatation,
Bike-Qualität etc. Für eine LIVE-Einordnung der Anstrengung reicht
das Modell aber gut — gerade am Anstieg, wo der Steigungsterm dominiert.

W/kg ist der sinnvollere Vergleich zwischen Fahrern verschiedener
Gewichtsklassen: ein 60-kg-Kletterer mit 360 W fährt 6.0 W/kg, ein
75-kg-Rouleur mit 360 W nur 4.8 W/kg.
"""
from __future__ import annotations

import json
import logging
import math
import os
from typing import Any

log = logging.getLogger("tdf.power")

# Physikalische Konstanten
G = 9.81              # m/s² Erdbeschleunigung
RHO_AIR = 1.2         # kg/m³ Luftdichte (Meereshöhe, 15°C)
CR = 0.004            # Rollwiderstandsbeiwert (Asphalt, Race-Tubes)
CDA_SOLO = 0.32       # m² Drag·Area, Einzelfahrer Drop-Bar
CDA_DRAFTED = 0.22    # m² Drag·Area im Windschatten (effektiv)
BIKE_KG = 8.0         # kg Race-Bike + Ausrüstung (UCI-Mindestgewicht 6.8 + Reserven)
DEFAULT_KG = 70       # kg, falls Rider-Gewicht unbekannt

# Gewichtungsdatei (bekannte Top-Rider)
WEIGHTS_PATH = os.path.join(os.path.dirname(__file__), "data", "weights.json")


# --------------------------------------------------------------------------- #
# Gewicht-Lookup
# --------------------------------------------------------------------------- #
_weights_cache: dict[str, dict[str, Any]] | None = None


def _load_weights() -> dict[str, dict[str, Any]]:
    """Lädt die Rider-Gewichte (cached)."""
    global _weights_cache
    if _weights_cache is None:
        try:
            with open(WEIGHTS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            # Metadaten ausfiltern
            _weights_cache = {k: v for k, v in data.items() if not k.startswith("_")}
        except (OSError, json.JSONDecodeError) as e:
            log.warning("weights.json nicht ladbar: %s", e)
            _weights_cache = {}
    return _weights_cache


def rider_weight(bib: int) -> float:
    """Liefert das Rider-Gewicht in kg, DEFAULT_KG falls unbekannt."""
    data = _load_weights()
    entry = data.get(str(bib))
    if entry and isinstance(entry.get("kg"), (int, float)):
        return float(entry["kg"])
    return float(DEFAULT_KG)


def rider_type(bib: int) -> str:
    """Fahrertyp ('GC', 'climber', 'sprinter', ...), 'unknown' falls unbekannt."""
    data = _load_weights()
    entry = data.get(str(bib))
    if entry and isinstance(entry.get("type"), str):
        return entry["type"]
    return "unknown"


# --------------------------------------------------------------------------- #
# Gradient (Steigung in %)
# --------------------------------------------------------------------------- #
def gradient(distance_m: float, altitude_start: float,
             altitude_end: float) -> float:
    """Steigung in Prozent.

    Args:
        distance_m: Horizontaldistanz (bzw. Strecke) in Metern.
        altitude_start: Höhe am Anfang in Metern.
        altitude_end: Höhe am Ende in Metern.

    Returns:
        Steigung in %. Negativ bei Abfahrt. 0 bei Distanz 0.
    """
    if distance_m <= 0:
        return 0.0
    dh = altitude_end - altitude_start
    return (dh / distance_m) * 100.0


def gradient_at_km(profile: list[dict[str, Any]], current_km: float) -> float | None:
    """Bestimmt die aktuelle Steigung aus dem Höhenprofil.

    Sucht das Segment, in dem current_km liegt, und berechnet dessen
    durchschnittliche Steigung.

    Args:
        profile: Liste von {"km_done": float, "alt": float}.
        current_km: Aktuelle Position des Fahrers in km.

    Returns:
        Steigung in %, 0.0 bei nur einem Punkt, None bei leerem Profil.
    """
    if not profile:
        return None
    if len(profile) == 1:
        return 0.0
    # Sortiert nach km_done annehmen
    # Segment finden, in dem current_km liegt
    for i in range(len(profile) - 1):
        a = profile[i]
        b = profile[i + 1]
        km_a = float(a.get("km_done", 0))
        km_b = float(b.get("km_done", 0))
        if km_a <= current_km <= km_b:
            alt_a = float(a.get("alt", 0))
            alt_b = float(b.get("alt", 0))
            dist_m = max(1.0, (km_b - km_a) * 1000.0)
            return gradient(dist_m, alt_a, alt_b)
    # current_km außerhalb des Profils -> erstes Segment nehmen (Fallschirm)
    a, b = profile[0], profile[1]
    alt_a = float(a.get("alt", 0))
    alt_b = float(b.get("alt", 0))
    dist_m = max(1.0, (float(b.get("km_done", 0)) - float(a.get("km_done", 0))) * 1000.0)
    return gradient(dist_m, alt_a, alt_b)


# --------------------------------------------------------------------------- #
# Watt-Berechnung
# --------------------------------------------------------------------------- #
def power_watts(speed_kph: float, gradient_pct: float, rider_kg: float,
                *, drafting: bool = False, bike_kg: float = BIKE_KG) -> float:
    """Schätzt die gefahrene Leistung in Watt.

    Modell:
        P = Cr·m·g·v + 0.5·ρ·CdA·v³·(1-ε) + m·g·v·slope

    Args:
        speed_kph: Geschwindigkeit in km/h.
        gradient_pct: Steigung in %.
        rider_kg: Rider-Gewicht in kg (ohne Bike).
        drafting: True, wenn im Windschatten (Peloton).
        bike_kg: Bike-Gewicht in kg.

    Returns:
        Geschätzte Leistung in Watt. Negativ bei starker Abfahrt
        (Bremsen wäre nötig).
    """
    v = speed_kph / 3.6  # m/s
    if v <= 0:
        return 0.0
    total_mass = rider_kg + bike_kg
    cda = CDA_DRAFTED if drafting else CDA_SOLO

    # Rollwiderstand
    p_roll = CR * total_mass * G * v
    # Luftwiderstand (v^3 dominant bei hohen Geschwindigkeiten)
    p_aero = 0.5 * RHO_AIR * cda * v ** 3
    # Steigung (positive: mehr Kraft, negative: weniger)
    slope = gradient_pct / 100.0
    p_climb = total_mass * G * v * slope

    return p_roll + p_aero + p_climb


def watts_per_kg(power_w: float, rider_kg: float) -> float:
    """Relative Leistung in W/kg Körpergewicht."""
    if rider_kg <= 0:
        return 0.0
    return power_w / rider_kg


# --------------------------------------------------------------------------- #
# Aggregator: komplette Rider-Power
# --------------------------------------------------------------------------- #
def compute_rider_power(rider: dict[str, Any], gradient_pct: float = 0.0,
                        *, in_peloton: bool = False) -> dict[str, Any] | None:
    """Berechnet Watt + W/kg für einen Rider.

    Args:
        rider: Rider-Dict mit Bib, kph, optional _name.
        gradient_pct: Aktuelle Steigung in %.
        in_peloton: Ob der Rider im Windschatten fährt.

    Returns:
        Dict mit bib, label, watts, w_per_kg, gradient_pct, drafting.
        None bei fehlendem kph.
    """
    kph = rider.get("kph")
    if kph is None:
        return None
    try:
        kph = float(kph)
    except (TypeError, ValueError):
        return None
    if kph <= 0:
        return None

    bib = rider.get("Bib") or rider.get("bib") or 0
    name = rider.get("_name", "?")
    weight = rider_weight(bib) if isinstance(bib, int) else DEFAULT_KG

    watts = power_watts(kph, gradient_pct, weight, drafting=in_peloton)
    w_per_kg = watts_per_kg(watts, weight)

    return {
        "bib": bib,
        "label": name.split("  (")[0] if "  (" in name else name,
        "watts": round(watts, 0),
        "w_per_kg": round(w_per_kg, 1),
        "gradient_pct": round(gradient_pct, 1),
        "speed_kph": round(kph, 1),
        "rider_kg": weight,
        "in_peloton": in_peloton,
    }


def compute_top_riders_power(top_riders: list[dict[str, Any]],
                             gradient_pct: float = 0.0,
                             peloton_bibs: set[int] | None = None) -> list[dict[str, Any]]:
    """Berechnet Watt/W/kg für alle Top-N-Rider.

    Args:
        top_riders: Liste der Top-N-Rider-Dicts.
        gradient_pct: Aktuelle Steigung.
        peloton_bibs: Set von Bibs, die im Peloton fahren (für Drafting).

    Returns:
        Sortierte Liste nach W/kg absteigend. Nur Rider mit gültigem kph.
    """
    peloton_bibs = peloton_bibs or set()
    results = []
    for r in top_riders:
        bib = r.get("Bib") or r.get("bib")
        in_pelo = isinstance(bib, int) and bib in peloton_bibs
        p = compute_rider_power(r, gradient_pct, in_peloton=in_pelo)
        if p is not None:
            results.append(p)
    # Nach W/kg absteigend sortieren (die „besten" Fahrer oben)
    results.sort(key=lambda x: x["w_per_kg"], reverse=True)
    return results


# --------------------------------------------------------------------------- #
# Gruppen-Kollektiv-W/kg
# --------------------------------------------------------------------------- #
def group_collective_power(speed_kph: float | None, group_size: int,
                           gradient_pct: float = 0.0,
                           avg_rider_kg: float = DEFAULT_KG) -> dict[str, Any] | None:
    """Schätzt die kollektive Leistung einer Gruppe (W/kg des mittleren Fahrers).

    Eine Gruppe fährt im Windschatten; je größer die Gruppe, desto effizienter.
    Wir nutzen das standardmäßige Drafting-Modell (CdA_DRAFTED = 0.22) und
    nehmen an, dass jeder Fahrer ~70 kg wiegt (Default). Das kollektive W/kg
    ist ein guter Indikator für den Anstrengungsgrad der Gruppe:
      - Peloton flach bei 50 km/h: ~3.0-3.5 W/kg (gemütlich, Windschatten)
      - Peloton am Anstieg bei 7%: ~5-6 W/kg (hart, Steigung dominiert)
      - 4-Mann-Ausreißergruppe bei 50 km/h: ~4.0-4.5 W/kg (weniger Schutz)

    Args:
        speed_kph: Mittlere Geschwindigkeit der Gruppe (km/h).
        group_size: Anzahl Fahrer (für Drafting-Effektstärke; >=8 = volle
                    Windschatten-Wirkung, <8 reduziert).
        gradient_pct: Aktuelle Steigung in %.
        avg_rider_kg: Angenommenes Rider-Gewicht (Default 70).

    Returns:
        Dict mit watts, w_per_kg, label oder None bei speed_kph=None/<=0.
    """
    if speed_kph is None or speed_kph <= 0:
        return None

    # Drafting-Effekt: ab 8 Fahrern volle Wirkung, darunter reduziert.
    # Solo (size=1) -> kein Drafting; Paar (size=2) -> leichter Schutz;
    # kleine Gruppe (3-7) -> mittlerer Schutz; >=8 -> voller Schutz.
    if group_size >= 8:
        drafting = True
    elif group_size <= 1:
        drafting = False
    else:
        # Zwischenwert: wir nehmen Drafting an, aber mit leicht erhöhtem
        # CdA (mehr Luftwiderstand als im vollen Peloton). Approximation:
        # interpoliere zwischen CDA_SOLO und CDA_DRAFTED je nach Größe.
        # Der Einfachheit halber nutzen wir Drafting ab size>=2.
        drafting = True

    watts = power_watts(speed_kph, gradient_pct, avg_rider_kg, drafting=drafting)
    w_per_kg = watts_per_kg(watts, avg_rider_kg)

    # Label für den Anstrengungsgrad
    if w_per_kg < 2.5:
        label = "Erholung"
    elif w_per_kg < 4.0:
        label = "gemütlich"
    elif w_per_kg < 5.0:
        label = "moderat"
    elif w_per_kg < 6.0:
        label = "hart"
    elif w_per_kg < 7.0:
        label = "sehr hart"
    else:
        label = "maximal"

    return {
        "watts": round(watts, 0),
        "w_per_kg": round(w_per_kg, 1),
        "label": label,
        "speed_kph": round(speed_kph, 1),
        "gradient_pct": round(gradient_pct, 1),
        "group_size": group_size,
    }
