"""Tests für tdf/timecut.py — Time-Cut-Rechner (hors délai).

Die UCI/ASO-Regel für Grand Tours: alle Rider müssen innerhalb von X %
der Siegerzeit ankommen, sonst werden sie ausgeschlossen (hors délai).
Die Prozent-Schwelle hängt von Durchschnittsgeschwindigkeit und Etappentyp ab.
"""
from __future__ import annotations

import pytest

from tdf import timecut as tc


# --------------------------------------------------------------------------- #
# Zeit-Cut-Threshold (Regelwerk)
# --------------------------------------------------------------------------- #
class TestCutThreshold:
    def test_slow_flat_stage(self):
        # < 36 km/h: 12% Cut (klares Feld, langsames Tempo)
        pct = tc.cut_threshold_percent(avg_speed_kph=34.0, stage_type="flat")
        assert pct == 12.0

    def test_medium_flat_stage(self):
        # 36-40 km/h: 10% Cut
        pct = tc.cut_threshold_percent(avg_speed_kph=38.0, stage_type="flat")
        assert pct == 10.0

    def test_fast_flat_stage(self):
        # 40-42 km/h: 8% Cut
        pct = tc.cut_threshold_percent(avg_speed_kph=41.0, stage_type="flat")
        assert pct == 8.0

    def test_very_fast_stage(self):
        # > 42 km/h: 6% Cut
        pct = tc.cut_threshold_percent(avg_speed_kph=45.0, stage_type="flat")
        assert pct == 6.0

    def test_mountain_stage_more_generous(self):
        # Berg-Etappen: ~18% Cut (langsameres Feld, hartes Tempo vorne)
        pct = tc.cut_threshold_percent(avg_speed_kph=33.0, stage_type="mountain")
        assert 15.0 <= pct <= 20.0

    def test_unknown_stage_type_default(self):
        pct = tc.cut_threshold_percent(avg_speed_kph=40.0, stage_type="weird")
        # Default sollte im üblichen Bereich liegen
        assert 5.0 <= pct <= 20.0


# --------------------------------------------------------------------------- #
# Cut-Zeit-Berechnung
# --------------------------------------------------------------------------- #
class TestCutTime:
    def test_basic_calculation(self):
        # Siegerzeit 4h = 14400s, 10% Cut -> 4h + 24min = 16560s
        cut = tc.cut_time_seconds(winner_time_s=14400.0, threshold_pct=10.0)
        assert cut == pytest.approx(15840.0, abs=1.0)  # 14400 * 1.10

    def test_zero_winner_time(self):
        # 0 Siegerzeit -> Cut ist 0 (sinnlos)
        assert tc.cut_time_seconds(0.0, 10.0) == 0.0

    def test_higher_threshold_more_generous(self):
        # Mehr % = späterer Cut = leichter
        easy = tc.cut_time_seconds(14400.0, 18.0)
        hard = tc.cut_time_seconds(14400.0, 6.0)
        assert easy > hard

    def test_typical_tour_stage(self):
        # 5h Sieger, 10% -> 5h30 Cut
        cut = tc.cut_time_seconds(18000.0, threshold_pct=10.0)
        hours = cut / 3600.0
        assert hours == pytest.approx(5.5, abs=0.01)


# --------------------------------------------------------------------------- #
# Gruppen-Status
# --------------------------------------------------------------------------- #
class TestGroupStatus:
    def test_safe_group(self):
        # Sieger 4h (14400s), Cut bei 10% -> 15840s.
        # Erlaubte Lücke = 15840 - 14400 = 1440s = 24min.
        # Gruppe liegt 5 min (300s) hinter Sieger -> ratio = 300/1440 = 0.21 -> SAFE.
        s = tc.group_status(group_gap_s=300.0, cut_time_s=15840.0,
                            projected_arrival_s=17800.0, winner_time_s=14400.0)
        assert s["status"] in ("safe", "warning", "danger", "eliminated")
        assert s["status"] == "safe"

    def test_warning_group(self):
        # Sieger 14400s, Cut 15840s, erlaubte Lücke 1440s.
        # Gruppe 1300s hinter Sieger -> ratio 0.90 -> warning (85-95%)
        s = tc.group_status(group_gap_s=1300.0, cut_time_s=15840.0,
                            projected_arrival_s=0.0, winner_time_s=14400.0)
        assert s["status"] == "warning"

    def test_danger_group(self):
        # Sehr nah am Cut: 1430s von 1440s erlaubt -> ratio 0.99 -> danger
        s = tc.group_status(group_gap_s=1430.0, cut_time_s=15840.0,
                            projected_arrival_s=0.0, winner_time_s=14400.0)
        assert s["status"] == "danger"

    def test_eliminated_group(self):
        # Über dem Cut: 1500s > 1440s erlaubt -> eliminated
        s = tc.group_status(group_gap_s=1500.0, cut_time_s=15840.0,
                            projected_arrival_s=0.0, winner_time_s=14400.0)
        assert s["status"] == "eliminated"

    def test_returns_relevant_fields(self):
        s = tc.group_status(group_gap_s=400.0, cut_time_s=600.0,
                            projected_arrival_s=18000.0, winner_time_s=14400.0)
        for k in ("status", "gap_s", "cut_time_s", "margin_s", "margin_pct"):
            assert k in s


# --------------------------------------------------------------------------- #
# Vollständige Analyse
# --------------------------------------------------------------------------- #
class TestAnalyzeGroups:
    def test_empty_groups(self):
        result = tc.analyze_groups(groups=[], winner_time_s=14400.0,
                                   avg_speed_kph=40.0, stage_type="flat")
        assert result["groups"] == []
        assert "cut_time_s" in result
        assert "threshold_pct" in result

    def test_mixed_groups(self):
        from dataclasses import dataclass

        @dataclass
        class G:
            # Spiegelt GroupInfo: nutzt .speed (nicht .speed_kph)
            gap_seconds: float
            name: str = "X"
            order: int = 0
            remaining_km: float = 50.0
            speed: float = 40.0

        groups = [
            G(gap_seconds=120.0, name="Ausreißer", order=1),   # safe
            G(gap_seconds=1300.0, name="Gruppetto", order=2),  # warning
            G(gap_seconds=1500.0, name="Letzte", order=3),     # eliminated
        ]
        result = tc.analyze_groups(groups=groups, winner_time_s=14400.0,
                                   avg_speed_kph=40.0, stage_type="flat")
        assert len(result["groups"]) == 3
        # Gruppen mit Status
        statuses = [g["status"] for g in result["groups"]]
        assert "safe" in statuses
        assert "eliminated" in statuses

    def test_works_with_dicts(self):
        groups = [
            {"gap_seconds": 200, "name": "X", "remaining_km": 50, "speed_kph": 40},
            {"gap_seconds": 1000, "name": "Y", "remaining_km": 50, "speed_kph": 30},
        ]
        result = tc.analyze_groups(groups=groups, winner_time_s=14400.0,
                                   avg_speed_kph=40.0, stage_type="flat")
        assert len(result["groups"]) == 2

    def test_danger_groups_listed_first(self):
        groups = [
            {"gap_seconds": 100, "name": "safe", "remaining_km": 50, "speed_kph": 40},
            {"gap_seconds": 1000, "name": "danger", "remaining_km": 50, "speed_kph": 30},
        ]
        result = tc.analyze_groups(groups=groups, winner_time_s=14400.0,
                                   avg_speed_kph=40.0, stage_type="flat")
        # Letzte (danger) sollte oben sein, weil akut bedroht
        assert result["groups"][0]["name"] == "danger"


# --------------------------------------------------------------------------- #
# Projektion (wann kommt die Gruppe an?)
# --------------------------------------------------------------------------- #
class TestProjection:
    def test_basic_projection(self):
        # Gruppe 30 km vom Ziel, 40 km/h -> 2700s = 45min Restzeit
        proj = tc.project_arrival(remaining_km=30.0, speed_kph=40.0)
        assert proj == pytest.approx(2700.0, abs=10)

    def test_zero_speed_no_projection(self):
        # Stehend -> None (kann nicht projizieren)
        assert tc.project_arrival(remaining_km=30.0, speed_kph=0.0) is None

    def test_zero_distance(self):
        # Im Ziel -> 0
        assert tc.project_arrival(remaining_km=0.0, speed_kph=40.0) == 0.0
