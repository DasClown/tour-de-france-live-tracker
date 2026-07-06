"""Tests für tdf/gap_chart.py — Gap-Verlauf-Ringpuffer."""
from __future__ import annotations

import time

import pytest

from tdf import gap_chart as gc


class TestGapHistoryRecording:
    def test_empty_history(self):
        h = gc.GapHistory()
        result = h.get_history()
        assert result["count"] == 0
        assert result["series"] == []
        assert result["bibs"] == []

    def test_single_record(self):
        h = gc.GapHistory(min_interval_s=0.0)
        gc_entry = {"bib": 11, "relative_seconds": 0.0}
        h.record([gc_entry], [], race_status=True)
        result = h.get_history()
        assert result["count"] == 1
        assert 11 in result["bibs"]
        assert len(result["series"]) == 1
        assert result["series"][0]["gaps"]["11"] == 0.0
        assert result["series"][0].get("race_status") is True

    def test_throttle_prevents_rapid_records(self):
        # Default min_interval=2s -> zweite record innerhalb 2s wird ignoriert
        h = gc.GapHistory(min_interval_s=2.0)
        h.record([{"bib": 1, "relative_seconds": 0}], [], True)
        h.record([{"bib": 1, "relative_seconds": 5}], [], True)
        result = h.get_history()
        assert result["count"] == 1  # nur der erste

    def test_accepts_dict_with_rel_s_field(self):
        # Manche Einträge haben rel_s statt relative_seconds
        h = gc.GapHistory(min_interval_s=0.0)
        h.record([{"bib": 11, "rel_s": 12.5}], [], None)
        result = h.get_history()
        assert result["series"][0]["gaps"]["11"] == 12.5

    def test_records_groups(self):
        h = gc.GapHistory(min_interval_s=0.0)
        groups = [{"order": 0, "gap_s": 0.0, "name": "Peloton", "size": 100}]
        h.record([], groups, True)
        result = h.get_history()
        assert len(result["series"][0]["groups"]) == 1
        assert result["series"][0]["groups"][0]["name"] == "Peloton"


class TestGapHistoryLimits:
    def test_max_history_enforced(self):
        h = gc.GapHistory(max_history=5, min_interval_s=0.0, max_age_s=99999)
        for i in range(20):
            h.record([{"bib": 1, "relative_seconds": i}], [], None)
            time.sleep(0.001)
        result = h.get_history()
        assert result["count"] <= 5

    def test_max_age_eviction(self):
        # Sehr kurzes max_age -> Einträge werden weggeräumt
        h = gc.GapHistory(max_age_s=0.05, min_interval_s=0.0)
        h.record([{"bib": 1, "relative_seconds": 0}], [], None)
        time.sleep(0.2)
        # Beim nächsten record wird der alte Eintrag entsorgt
        h.record([{"bib": 2, "relative_seconds": 5}], [], None)
        result = h.get_history()
        # Nur der neue sollte übrig sein
        bibs_seen = set()
        for s in result["series"]:
            bibs_seen.update(int(b) for b in s["gaps"].keys())
        assert 1 not in bibs_seen


class TestGapHistoryQuery:
    def test_since_filter(self):
        h = gc.GapHistory(min_interval_s=0.0, max_age_s=99999)
        h.record([{"bib": 1, "relative_seconds": 0}], [], None)
        t_mid = time.time() + 0.5
        time.sleep(0.6)
        h.record([{"bib": 1, "relative_seconds": 10}], [], None)
        # Nur Einträge seit t_mid
        result = h.get_history(since=t_mid)
        assert result["count"] == 1

    def test_top_n_does_not_filter_bibs(self):
        # top_n ist im aktuellen Code nur ein Param, der nicht hart filtert.
        # Test: alle Bibs tauchen auf.
        h = gc.GapHistory(min_interval_s=0.0, max_age_s=99999)
        h.record([{"bib": b, "relative_seconds": b} for b in (1, 2, 3)], [], None)
        result = h.get_history(top_n=1)
        assert sorted(result["bibs"]) == [1, 2, 3]

    def test_rounding(self):
        h = gc.GapHistory(min_interval_s=0.0)
        h.record([{"bib": 1, "relative_seconds": 12.3456}], [], None)
        result = h.get_history()
        # Gaps werden auf 1 Nachkommastelle gerundet
        assert result["series"][0]["gaps"]["1"] == 12.3
