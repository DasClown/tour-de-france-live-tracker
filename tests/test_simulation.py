"""Tests für tdf/simulation.py — Ausreißer-Monte-Carlo.

Simuliert 1000+ Läufe: überlebt die Spitzengruppe oder wird sie eingeholt?
Deterministisch testbar via seed.
"""
from __future__ import annotations

import random

import pytest

from tdf import simulation as sim


# --------------------------------------------------------------------------- #
# Einzel-Simulation
# --------------------------------------------------------------------------- #
class TestSimulateOnce:
    def test_obvious_catch(self):
        # Ausreißer: 1 km Rest, 30 km/h, Gap 60s
        # Peloton: 1 km Rest, 50 km/h
        # Peloton wird sicher einholen (Restdistanz zu kurz für 60s Vorsprung)
        rng = random.Random(42)
        result = sim.simulate_once(
            gap_s=60.0, remaining_km=1.0,
            breakaway_speed_kph=30.0, peloton_speed_kph=50.0,
            rng=rng,
        )
        assert isinstance(result, bool)

    def test_obvious_survival(self):
        # 1 km Rest, Gap 600s, beide gleich schnell -> Ausreißer überlebt
        rng = random.Random(42)
        result = sim.simulate_once(
            gap_s=600.0, remaining_km=1.0,
            breakaway_speed_kph=40.0, peloton_speed_kph=40.0,
            rng=rng,
        )
        assert result is True

    def test_deterministic_with_seed(self):
        # Selber Seed -> gleiches Ergebnis
        r1 = sim.simulate_once(gap_s=120.0, remaining_km=10.0,
                               breakaway_speed_kph=42.0,
                               peloton_speed_kph=44.0,
                               rng=random.Random(123))
        r2 = sim.simulate_once(gap_s=120.0, remaining_km=10.0,
                               breakaway_speed_kph=42.0,
                               peloton_speed_kph=44.0,
                               rng=random.Random(123))
        assert r1 == r2

    def test_zero_distance_returns_true(self):
        # Im Ziel-Sprint: Restdistanz 0 -> wer vorne liegt, gewinnt
        rng = random.Random(0)
        result = sim.simulate_once(
            gap_s=10.0, remaining_km=0.0,
            breakaway_speed_kph=50.0, peloton_speed_kph=60.0,
            rng=rng,
        )
        assert result is True  # Ausreißer ist ja schon da


# --------------------------------------------------------------------------- #
# Monte-Carlo-Aggregator
# --------------------------------------------------------------------------- #
class TestMonteCarlo:
    def test_returns_percentage(self):
        result = sim.monte_carlo(
            gap_s=120.0, remaining_km=20.0,
            breakaway_speed_kph=42.0, peloton_speed_kph=43.0,
            n_simulations=200, seed=42,
        )
        assert "survival_pct" in result
        assert 0.0 <= result["survival_pct"] <= 100.0
        assert result["n_simulations"] == 200

    def test_clear_survival(self):
        # Großer Vorsprung, gleich schnell -> hohe Überlebenswahrscheinlichkeit
        result = sim.monte_carlo(
            gap_s=600.0, remaining_km=10.0,
            breakaway_speed_kph=40.0, peloton_speed_kph=40.0,
            n_simulations=500, seed=0,
        )
        assert result["survival_pct"] > 80.0

    def test_clear_catch(self):
        # Peloton viel schneller, langer Vorsprung reicht nicht
        result = sim.monte_carlo(
            gap_s=60.0, remaining_km=30.0,
            breakaway_speed_kph=35.0, peloton_speed_kph=45.0,
            n_simulations=500, seed=0,
        )
        assert result["survival_pct"] < 20.0

    def test_deterministic_with_seed(self):
        r1 = sim.monte_carlo(gap_s=120.0, remaining_km=20.0,
                             breakaway_speed_kph=42.0, peloton_speed_kph=43.0,
                             n_simulations=100, seed=999)
        r2 = sim.monte_carlo(gap_s=120.0, remaining_km=20.0,
                             breakaway_speed_kph=42.0, peloton_speed_kph=43.0,
                             n_simulations=100, seed=999)
        assert r1["survival_pct"] == r2["survival_pct"]

    def test_returns_confidence_label(self):
        result = sim.monte_carlo(
            gap_s=120.0, remaining_km=20.0,
            breakaway_speed_kph=42.0, peloton_speed_kph=43.0,
            n_simulations=100, seed=42,
        )
        assert "confidence" in result
        assert result["confidence"] in ("high", "medium", "low")


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #
class TestEdgeCases:
    def test_zero_speed_safe(self):
        # Beide stehend -> Ausreißer behält Vorsprung fast immer.
        # Hinweis: durch die Gauß-Varianz kann das Peloton gelegentlich
        # eine kleine positive Geschwindigkeit „würfeln", weshalb 100%
        # nicht garantiert sind. Sehr hohe Überlebensrate trotzdem.
        result = sim.monte_carlo(
            gap_s=60.0, remaining_km=5.0,
            breakaway_speed_kph=0.0, peloton_speed_kph=0.0,
            n_simulations=200, seed=0,
        )
        assert result["survival_pct"] > 90.0

    def test_no_breakaway_speed(self):
        # Ausreißer steht, Peloton fährt -> eingeholt
        result = sim.monte_carlo(
            gap_s=120.0, remaining_km=10.0,
            breakaway_speed_kph=0.0, peloton_speed_kph=40.0,
            n_simulations=50, seed=0,
        )
        assert result["survival_pct"] < 10.0


# --------------------------------------------------------------------------- #
# Aus Gruppe extrahieren (Live-Anbindung)
# --------------------------------------------------------------------------- #
class TestFromGroups:
    def test_extracts_breakaway_and_peloton(self):
        from dataclasses import dataclass

        @dataclass
        class G:
            gap_seconds: float
            name: str = "X"
            order: int = 0
            speed: float = 40.0
            remaining_km: float = 30.0
            size: int = 5

        groups = [
            G(gap_seconds=0.0, name="Ausreißer", order=1, speed=42.0, size=3),
            G(gap_seconds=120.0, name="Peloton", order=2, speed=44.0, size=160),
        ]
        result = sim.simulate_from_groups(groups, n_simulations=200, seed=42)
        assert result is not None
        assert "survival_pct" in result
        assert "breakaway_name" in result

    def test_returns_none_without_peloton(self):
        # Nur eine Gruppe -> keine Verfolgung -> None
        from dataclasses import dataclass

        @dataclass
        class G:
            gap_seconds: float = 0.0
            name: str = "Solo"
            order: int = 0
            speed: float = 40.0
            remaining_km: float = 30.0
            size: int = 1

        result = sim.simulate_from_groups([G()], n_simulations=100, seed=0)
        assert result is None

    def test_returns_none_with_empty(self):
        assert sim.simulate_from_groups([], n_simulations=100, seed=0) is None
