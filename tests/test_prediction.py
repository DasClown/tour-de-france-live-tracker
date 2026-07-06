"""Tests für tdf/prediction.py — Restzeit-Vorhersage."""
from __future__ import annotations

import re

import pytest

from tdf import prediction as pr


class TestPredictGroupPace:
    def test_zero_speed_returns_zero(self):
        assert pr.predict_group_pace(100.0, 0.0) == 0.0

    def test_negative_speed_returns_zero(self):
        # Defensive: negative Geschwindigkeit als "stehend" interpretiert
        assert pr.predict_group_pace(100.0, -5.0) == 0.0

    def test_simple_calculation(self):
        # 100 km bei 50 km/h = 2h = 7200s
        assert pr.predict_group_pace(100.0, 50.0) == pytest.approx(7200.0)

    def test_typical_tour_speed(self):
        # 50 km Rest bei 42 km/h = 1.19h = ~4286s
        s = pr.predict_group_pace(50.0, 42.0)
        assert 4200 <= s <= 4400

    def test_one_km_at_36_kph(self):
        # 1 km bei 36 km/h = 100s
        assert pr.predict_group_pace(1.0, 36.0) == pytest.approx(100.0)

    def test_zero_distance_zero_speed(self):
        # 0 km, egal welche Speed -> 0s (außer Speed=0 schon covered)
        assert pr.predict_group_pace(0.0, 50.0) == pytest.approx(0.0)


class TestComputePredictions:
    def test_empty_inputs(self):
        assert pr.compute_predictions([], []) == []

    def test_group_prediction(self):
        groups = [{"order": 0, "name": "Peloton", "remaining_km": 50.0, "speed_kph": 42.0}]
        preds = pr.compute_predictions(groups, [])
        assert len(preds) == 1
        p = preds[0]
        assert p["type"] == "group"
        assert p["id"] == 0
        assert p["label"] == "Peloton"
        assert p["remaining_km"] == 50.0
        assert p["speed_kph"] == 42.0
        assert p["eta_seconds"] == pytest.approx(4286, abs=10)
        assert "eta_absolute" in p
        assert "UTC" in p["eta_absolute"]

    def test_group_skipped_when_speed_zero(self):
        groups = [{"order": 0, "name": "Stehend", "remaining_km": 50.0, "speed_kph": 0}]
        assert pr.compute_predictions(groups, []) == []

    def test_group_skipped_when_distance_missing(self):
        groups = [{"order": 0, "name": "?", "speed_kph": 40}]
        assert pr.compute_predictions(groups, []) == []

    def test_rider_prediction(self):
        riders = [{
            "Bib": 11, "_name": "Jonas VINGEGAARD  (VISMA)",
            "kmToFinish": 30.0, "kph": 45.0,
        }]
        preds = pr.compute_predictions([], riders)
        assert len(preds) == 1
        p = preds[0]
        assert p["type"] == "rider"
        assert p["id"] == 11
        assert "Jonas VINGEGAARD" in p["label"]
        assert "  (" not in p["label"]  # Team-Suffix abgeschnitten
        assert p["eta_seconds"] == pytest.approx(2400, abs=10)

    def test_rider_skipped_when_zero_speed(self):
        riders = [{"Bib": 1, "_name": "?", "kmToFinish": 30.0, "kph": 0}]
        assert pr.compute_predictions([], riders) == []

    def test_rider_skipped_when_km_zero(self):
        # kmToFinish=0 -> schon im Ziel, keine Vorhersage
        riders = [{"Bib": 1, "_name": "?", "kmToFinish": 0, "kph": 40}]
        assert pr.compute_predictions([], riders) == []

    def test_mixed_groups_and_riders(self):
        groups = [{"order": 0, "name": "Lead", "remaining_km": 10.0, "speed_kph": 50.0}]
        riders = [{"Bib": 11, "_name": "X", "kmToFinish": 10.0, "kph": 50.0}]
        preds = pr.compute_predictions(groups, riders)
        assert len(preds) == 2
        assert preds[0]["type"] == "group"
        assert preds[1]["type"] == "rider"

    def test_eta_absolute_format(self):
        # Format sollte HH:MM:SS UTC sein
        groups = [{"order": 0, "name": "X", "remaining_km": 1.0, "speed_kph": 36.0}]
        preds = pr.compute_predictions(groups, [])
        assert re.match(r"^\d{2}:\d{2}:\d{2} UTC$", preds[0]["eta_absolute"])


class TestPredictionDataclass:
    def test_construction(self):
        p = pr.Prediction(
            bib_or_group=11, label="X", remaining_km=10.0,
            speed_kph=40.0, eta_seconds=900.0, eta_absolute="12:00:00 UTC",
        )
        assert p.bib_or_group == 11
        assert p.estimated_finish_ts is None

    def test_with_finish_ts(self):
        p = pr.Prediction(
            bib_or_group=0, label="X", remaining_km=10.0,
            speed_kph=40.0, eta_seconds=900.0, eta_absolute="X",
            estimated_finish_ts=1234567890.0,
        )
        assert p.estimated_finish_ts == 1234567890.0
