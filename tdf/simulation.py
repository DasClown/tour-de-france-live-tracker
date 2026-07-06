"""Ausreißer-Simulation (Feature 8): Monte-Carlo-Überlebenswahrscheinlichkeit.

Für jede Ausreißergruppe simulieren wir N Läufe (Default 1000) mit
realistischer Varianz in den Geschwindigkeiten und schätzen, mit welcher
Wahrscheinlichkeit die Gruppe vor dem Ziel bleibt.

Modell:
  - Aktuelle Lücke gap_s
  - Restdistanz remaining_km
  - Aktuelle Geschwindigkeiten v_break, v_pelo
  - In jedem Simulationsschritt: Geschwindigkeiten mit Normalverteilung
    variieren (σ abgeleitet aus typischer Renn-Volatilität).
  - Überlebt = (Ankunftszeit Ausreißer) <= (Ankunftszeit Peloton)

Vereinfachungen (bewusst):
  - Keine Taktik-Modellierung (Temposchwankungen, Angriffe)
  - Kein Wind
  - Konstante Varianz über die Distanz

Trotzdem liefert die Simulation eine nützliche LIVE-Einordnung:
"Wahrscheinlichkeit 23 %, dass die Ausreißer überleben" ist viel
aussagekräftiger als die reine Lücke in Sekunden.
"""
from __future__ import annotations

import logging
import random
from typing import Any

log = logging.getLogger("tdf.simulation")

# Simulations-Parameter
DEFAULT_N_SIMULATIONS = 1000
# Geschwindigkeits-Varianz: typische Renn-Volatilität in km/h.
# Peloton variiert stärker (Verfolgungsjagd mit Tempowechseln),
# Ausreißer bleiben gleichmäßiger (sie haben ihr Tempo gefunden).
SPEED_STD_BREAKAWAY = 1.5    # km/h σ für Ausreißer
SPEED_STD_PELTON = 2.5       # km/h σ für Peloton (Verfolgung = turbulent)
# Mindestgeschwindigkeit, sonst gilt als "Stehen"
MIN_SPEED_KPH = 5.0


# --------------------------------------------------------------------------- #
# Einzel-Simulation
# --------------------------------------------------------------------------- #
def simulate_once(gap_s: float, remaining_km: float,
                  breakaway_speed_kph: float, peloton_speed_kph: float,
                  rng: random.Random) -> bool:
    """Ein Simulationslauf: überlebt der Ausreißer?

    Args:
        gap_s: Aktuelle Lücke in Sekunden.
        remaining_km: Reststrecke in km.
        breakaway_speed_kph: Aktuelle Ausreißer-Geschwindigkeit.
        peloton_speed_kph: Aktuelle Peloton-Geschwindigkeit.
        rng: random.Random-Instanz (für deterministische Tests).

    Returns:
        True, wenn der Ausreißer ins Ziel kommt, bevor ihn das Peloton einholt.
    """
    # Varianz aufschlagen
    v_break = rng.gauss(breakaway_speed_kph, SPEED_STD_BREAKAWAY)
    v_pelo = rng.gauss(peloton_speed_kph, SPEED_STD_PELTON)
    # Negativ-Geschwindigkeiten abfangen
    v_break = max(0.0, v_break)
    v_pelo = max(0.0, v_pelo)

    # Wenn beide stehend: wer vorne liegt, gewinnt.
    if v_break < MIN_SPEED_KPH and v_pelo < MIN_SPEED_KPH:
        return True  # Ausreißer ist ja vorne, kein Holt-Einhol-Mechanismus

    # Wenn Peloton nicht fährt: Ausreißer überlebt.
    if v_pelo < MIN_SPEED_KPH:
        return True

    # Wenn Ausreißer steht, Peloton aber fährt: eingeholt
    # (außer Restdistanz ist 0)
    if v_break < MIN_SPEED_KPH:
        return remaining_km <= 0.0

    # Ankunftszeiten berechnen (Sekunden)
    t_break = (remaining_km / v_break) * 3600.0 if v_break > 0 else float("inf")
    t_pelo_gap = gap_s + (remaining_km / v_pelo) * 3600.0 if v_pelo > 0 else float("inf")

    # Überlebt = Ausreißer kommt früher oder zeitgleich an
    return t_break <= t_pelo_gap


# --------------------------------------------------------------------------- #
# Monte-Carlo
# --------------------------------------------------------------------------- #
def monte_carlo(gap_s: float, remaining_km: float,
                breakaway_speed_kph: float, peloton_speed_kph: float,
                *, n_simulations: int = DEFAULT_N_SIMULATIONS,
                seed: int | None = None) -> dict[str, Any]:
    """Führt N Simulationen durch und liefert die Überlebenswahrscheinlichkeit.

    Args:
        gap_s: Lücke in Sekunden.
        remaining_km: Reststrecke in km.
        breakaway_speed_kph, peloton_speed_kph: Aktuelle Geschwindigkeiten.
        n_simulations: Anzahl der Läufe (Default 1000).
        seed: Für deterministische Ergebnisse.

    Returns:
        Dict mit survival_pct (0-100), n_simulations, confidence.
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    n_simulations = max(1, n_simulations)

    survived = 0
    for _ in range(n_simulations):
        if simulate_once(gap_s, remaining_km,
                         breakaway_speed_kph, peloton_speed_kph, rng):
            survived += 1

    survival_pct = (survived / n_simulations) * 100.0

    # Konfidenz-Label: wie "sicher" ist die Aussage?
    if survival_pct >= 90 or survival_pct <= 10:
        confidence = "high"
    elif survival_pct >= 70 or survival_pct <= 30:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "survival_pct": round(survival_pct, 1),
        "n_simulations": n_simulations,
        "confidence": confidence,
        "gap_s": round(gap_s, 0),
        "remaining_km": round(remaining_km, 1),
        "breakaway_speed_kph": round(breakaway_speed_kph, 1),
        "peloton_speed_kph": round(peloton_speed_kph, 1),
    }


# --------------------------------------------------------------------------- #
# Live-Anbindung: aus Gruppen-Liste
# --------------------------------------------------------------------------- #
def simulate_from_groups(groups: list[Any],
                         *, n_simulations: int = DEFAULT_N_SIMULATIONS,
                         seed: int | None = None) -> dict[str, Any] | None:
    """Findet Ausreißer + Peloton in einer Gruppen-Liste und simuliert.

    Heuristik:
      - Spitzengruppe = order 0 (oder kleinste order mit gap_seconds=0)
      - Peloton = größte Gruppe (size) oder Gruppe mit der größten size.

    Args:
        groups: Liste von GroupInfo-Objekten oder -Dicts.
        n_simulations, seed: wie bei monte_carlo().

    Returns:
        Dict mit survival_pct etc., None wenn keine sinnvolle Konstellation.
    """
    if not groups or len(groups) < 2:
        return None

    # Auspacken: einheitlich als Dicts
    parsed: list[dict[str, Any]] = []
    for g in groups:
        if hasattr(g, "gap_seconds"):
            parsed.append({
                "order": getattr(g, "order", 0),
                "name": getattr(g, "name", "?"),
                "gap_s": getattr(g, "gap_seconds", 0.0) or 0.0,
                "speed": getattr(g, "speed", None) or getattr(g, "speed_kph", None),
                "remaining_km": getattr(g, "remaining_km", None),
                "size": getattr(g, "size", 0),
            })
        elif isinstance(g, dict):
            parsed.append({
                "order": g.get("order", 0),
                "name": g.get("name", "?"),
                "gap_s": g.get("gap_seconds") or g.get("gap_s") or 0.0,
                "speed": g.get("speed_kph") or g.get("speed"),
                "remaining_km": g.get("remaining_km"),
                "size": g.get("size", 0),
            })

    if len(parsed) < 2:
        return None

    # Sortieren nach order (Spitze zuerst)
    parsed.sort(key=lambda x: x["order"])
    breakaway = parsed[0]

    # Peloton = die größte Gruppe (meistens das Hauptfeld)
    peloton = max(parsed[1:], key=lambda x: x.get("size", 0))

    # Werte validieren
    if (breakaway.get("speed") is None or peloton.get("speed") is None
            or breakaway.get("remaining_km") is None):
        return None

    # Lücke des Pelotons zur Spitze = gap des Peloton
    gap_s = peloton["gap_s"]
    if gap_s <= 0:
        # Kein echter Vorsprung -> 100% dass die Spitze „überlebt"
        # (sie ist ja nicht wirklich verfolgt)
        return None

    result = monte_carlo(
        gap_s=gap_s,
        remaining_km=breakaway["remaining_km"],
        breakaway_speed_kph=breakaway["speed"],
        peloton_speed_kph=peloton["speed"],
        n_simulations=n_simulations,
        seed=seed,
    )
    result["breakaway_name"] = breakaway["name"]
    result["breakaway_size"] = breakaway.get("size", 0)
    result["peloton_name"] = peloton["name"]
    result["peloton_size"] = peloton.get("size", 0)
    return result
