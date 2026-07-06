"""Tests für tdf/power.py — Live-Gradient, Watt, W/kg.

Reine Physik, deterministisch. Validiert gegen bekannte Referenzwerte
aus der Radsport-Trainingslehre.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tdf import power as pw


# --------------------------------------------------------------------------- #
# Gewicht-Lookup
# --------------------------------------------------------------------------- #
class TestWeightLookup:
    def test_known_rider(self):
        # Vingegaard, bib 11, sollte 60 kg sein
        w = pw.rider_weight(11)
        assert w == 60

    def test_unknown_rider_default(self):
        # Bib 999 nicht in weights.json -> Default 70 kg
        w = pw.rider_weight(9999)
        assert w == pw.DEFAULT_KG

    def test_pogacar(self):
        assert pw.rider_weight(1) == 66

    def test_evenepoel(self):
        assert pw.rider_weight(21) == 63

    def test_returns_positive(self):
        for bib in [1, 11, 21, 31, 9999]:
            assert pw.rider_weight(bib) > 0


# --------------------------------------------------------------------------- #
# Live-Gradient (Steigung in %)
# --------------------------------------------------------------------------- #
class TestGradient:
    def test_flat(self):
        # 0 Höhenmeter auf 1000m Distanz -> 0%
        g = pw.gradient(distance_m=1000.0, altitude_start=100.0,
                        altitude_end=100.0)
        assert g == pytest.approx(0.0)

    def test_climb_10_percent(self):
        # 100 m Höhe auf 1000 m = 10% Steigung
        g = pw.gradient(distance_m=1000.0, altitude_start=0.0,
                        altitude_end=100.0)
        assert g == pytest.approx(10.0, abs=0.01)

    def test_descent_negative(self):
        g = pw.gradient(distance_m=1000.0, altitude_start=100.0,
                        altitude_end=50.0)
        assert g == pytest.approx(-5.0, abs=0.01)

    def test_zero_distance_returns_zero(self):
        # Division durch 0 vermeiden
        assert pw.gradient(distance_m=0.0, altitude_start=0,
                           altitude_end=100) == 0.0

    def test_steep_climb(self):
        # 200m auf 1000m = 20% (galibier-extrem)
        g = pw.gradient(distance_m=1000.0, altitude_start=0.0,
                        altitude_end=200.0)
        assert g == pytest.approx(20.0, abs=0.01)


# --------------------------------------------------------------------------- #
# Watt-Berechnung ( physikalische Modellierung )
# --------------------------------------------------------------------------- #
class TestPowerCalc:
    def test_flat_cruise(self):
        # 70kg Rider, 40 km/h, 0% Steigung
        # Rollwiderstand + Luftwiderstand, ~150-200W erwartet
        w = pw.power_watts(speed_kph=40.0, gradient_pct=0.0,
                           rider_kg=70.0)
        assert 100 < w < 300

    def test_climbing_more_power_than_flat(self):
        # Bergauf braucht viel mehr Watt
        flat = pw.power_watts(speed_kph=20.0, gradient_pct=0.0,
                              rider_kg=65.0)
        climb = pw.power_watts(speed_kph=20.0, gradient_pct=8.0,
                               rider_kg=65.0)
        assert climb > flat * 2  # deutlich mehr

    def test_descent_less_power(self):
        # Bergab braucht kaum Kraft
        flat = pw.power_watts(speed_kph=40.0, gradient_pct=0.0,
                              rider_kg=70.0)
        descent = pw.power_watts(speed_kph=40.0, gradient_pct=-5.0,
                                 rider_kg=70.0)
        assert descent < flat
        # Bei steiler Abfahrt kann Watt sogar negativ sein (Bremsen nötig)
        steep = pw.power_watts(speed_kph=50.0, gradient_pct=-10.0,
                               rider_kg=70.0)
        assert steep < flat

    def test_realistic_climb_value(self):
        # 60 kg Vingegaard am 7.5% Anstieg mit 20 km/h
        # Erwartet: ~350-420W (realistisch für GC-Fahrer am Anstieg)
        w = pw.power_watts(speed_kph=20.0, gradient_pct=7.5,
                           rider_kg=60.0)
        assert 300 < w < 500

    def test_zero_speed_zero_power(self):
        # Stehend -> 0W (nur Rollwiderstand-Theorie, aber praktisch 0)
        w = pw.power_watts(speed_kph=0.0, gradient_pct=7.0,
                           rider_kg=65.0)
        assert w == pytest.approx(0.0, abs=1.0)

    def test_aero_dominates_at_high_speed(self):
        # Bei 50 km/h flach dominiert Luftwiderstand
        # Watt sollte deutlich höher sein als bei 30 km/h
        slow = pw.power_watts(speed_kph=30.0, gradient_pct=0.0,
                              rider_kg=70.0)
        fast = pw.power_watts(speed_kph=50.0, gradient_pct=0.0,
                              rider_kg=70.0)
        # v^3 -> 50/30 = 1.67x, Watt-Verhältnis ~4.6x
        assert fast > slow * 3


# --------------------------------------------------------------------------- #
# W/kg (relative Leistung)
# --------------------------------------------------------------------------- #
class TestWattsPerKg:
    def test_basic_calc(self):
        # 350 W / 70 kg = 5.0 W/kg
        wkg = pw.watts_per_kg(power_w=350.0, rider_kg=70.0)
        assert wkg == pytest.approx(5.0)

    def test_zero_weight_safe(self):
        # Division durch 0 vermeiden
        wkg = pw.watts_per_kg(power_w=300.0, rider_kg=0.0)
        assert wkg == 0.0

    def test_vingegaard_climb(self):
        # Vingegaard 60kg, ~380W am Anstieg -> ~6.3 W/kg
        w = pw.power_watts(speed_kph=20.0, gradient_pct=7.5, rider_kg=60.0)
        wkg = pw.watts_per_kg(w, 60.0)
        # Realistisch für einen GC-Anstieg: 5.5-7 W/kg
        assert 5.0 < wkg < 7.5

    def test_zero_power(self):
        assert pw.watts_per_kg(power_w=0.0, rider_kg=70.0) == 0.0


# --------------------------------------------------------------------------- #
# Drafting-Korrektur (Peloton-Savings)
# --------------------------------------------------------------------------- #
class TestDrafting:
    def test_solo_no_correction(self):
        # Solo = kein Drafting
        w = pw.power_watts(speed_kph=40.0, gradient_pct=0.0,
                           rider_kg=70.0, drafting=False)
        assert w > 0

    def test_peloton_less_power(self):
        # Im Peloton braucht man weniger Watt
        solo = pw.power_watts(speed_kph=40.0, gradient_pct=0.0,
                              rider_kg=70.0, drafting=False)
        drafted = pw.power_watts(speed_kph=40.0, gradient_pct=0.0,
                                 rider_kg=70.0, drafting=True)
        # Peloton spart ~30% Luftwiderstand
        assert drafted < solo

    def test_drafting_helps_more_at_high_speed(self):
        # Bei 50 km/h bringt Drafting mehr als bei 20 km/h (mehr Luftwiderstand)
        save_fast = pw.power_watts(speed_kph=50.0, gradient_pct=0.0,
                                   rider_kg=70.0, drafting=False) \
                    - pw.power_watts(speed_kph=50.0, gradient_pct=0.0,
                                     rider_kg=70.0, drafting=True)
        save_slow = pw.power_watts(speed_kph=20.0, gradient_pct=0.0,
                                   rider_kg=70.0, drafting=False) \
                    - pw.power_watts(speed_kph=20.0, gradient_pct=0.0,
                                     rider_kg=70.0, drafting=True)
        assert save_fast > save_slow


# --------------------------------------------------------------------------- #
# Vollständige Rider-Power-Berechnung (Aggregator)
# --------------------------------------------------------------------------- #
class TestComputeRiderPower:
    def test_with_all_inputs(self):
        rider = {
            "Bib": 11, "kph": 20.0, "kmToFinish": 50.0,
            "Latitude": 45.0, "Longitude": 7.0,
            "_name": "Jonas VINGEGAARD",
        }
        # Gradient aus Context: 7.5%
        result = pw.compute_rider_power(rider, gradient_pct=7.5)
        assert result is not None
        assert "bib" in result
        assert "watts" in result
        assert "w_per_kg" in result
        assert result["bib"] == 11
        assert result["watts"] > 200
        assert 4.0 < result["w_per_kg"] < 8.0

    def test_missing_speed_returns_none(self):
        rider = {"Bib": 11}  # kein kph
        assert pw.compute_rider_power(rider, gradient_pct=5.0) is None

    def test_zero_speed_filtered(self):
        rider = {"Bib": 11, "kph": 0.0}
        assert pw.compute_rider_power(rider, gradient_pct=5.0) is None

    def test_label_includes_name(self):
        rider = {"Bib": 11, "kph": 20.0, "_name": "VINGEGAARD"}
        result = pw.compute_rider_power(rider, gradient_pct=5.0)
        assert "VINGEGAARD" in result["label"]

    def test_in_peloton_flag(self):
        rider = {"Bib": 11, "kph": 40.0}
        solo = pw.compute_rider_power(rider, gradient_pct=0.0, in_peloton=False)
        peloton = pw.compute_rider_power(rider, gradient_pct=0.0, in_peloton=True)
        assert peloton["watts"] < solo["watts"]


# --------------------------------------------------------------------------- #
# Gradient aus Höhenprofil (Live-Segment)
# --------------------------------------------------------------------------- #
class TestProfileGradient:
    def test_from_two_points(self):
        # Zwischen zwei Profilknoten: Distanz und Höhendifferenz
        # Punkt A: km 10.0, alt 500m
        # Punkt B: km 12.0, alt 560m
        # -> 60m / 2000m = 3%
        profile = [
            {"km_done": 10.0, "alt": 500.0},
            {"km_done": 12.0, "alt": 560.0},
        ]
        g = pw.gradient_at_km(profile, current_km=11.0)
        assert g == pytest.approx(3.0, abs=0.1)

    def test_empty_profile_returns_none(self):
        assert pw.gradient_at_km([], current_km=5.0) is None

    def test_single_point_returns_zero(self):
        assert pw.gradient_at_km([{"km_done": 0, "alt": 100}], current_km=0) == 0.0

    def test_before_profile_start(self):
        profile = [{"km_done": 10.0, "alt": 500.0},
                   {"km_done": 20.0, "alt": 1000.0}]
        g = pw.gradient_at_km(profile, current_km=5.0)
        # Vor dem Profil -> ersten Punkt nehmen
        assert g == pytest.approx(5.0, abs=0.1)  # selbe Steigung

    def test_descending_segment(self):
        profile = [
            {"km_done": 10.0, "alt": 1000.0},
            {"km_done": 12.0, "alt": 940.0},
        ]
        g = pw.gradient_at_km(profile, current_km=11.0)
        assert g == pytest.approx(-3.0, abs=0.1)
