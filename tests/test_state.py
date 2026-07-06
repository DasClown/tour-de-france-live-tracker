"""Tests für tdf/state.py — State-Engine, Parser, Dispatcher, Hybrid-GC.

Dies ist das Herzstück. Besonders kritisch:
  - parse_rankings: ASO-Zeiten sind MS, Code muss /1000.
  - parse_groups: remainingDistance ist in Metern.
  - recompute_virtual_gc: Hybrid aus base_gc + Live-Gruppen.
  - dispatch: SSE-Bind-Routing.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from tdf import state as st


# --------------------------------------------------------------------------- #
# RankEntry / GroupInfo / State Konstruktion
# --------------------------------------------------------------------------- #
def make_state() -> st.State:
    return st.State(year=2026, stage=3)


# --------------------------------------------------------------------------- #
# parse_rankings
# --------------------------------------------------------------------------- #
class TestParseRankings:
    def test_basic_parsing(self):
        data = {"rankings": [
            {"position": 1, "bib": 11, "relative": 0, "absolute": 14508000},
            {"position": 2, "bib": 1, "relative": 6000, "absolute": 14514000,
             "bonus": 6000},
            {"position": 3, "bib": 21, "relative": 15000, "absolute": 14523000,
             "bonus": 4000},
        ]}
        out = st.parse_rankings(data)
        assert len(out) == 3
        assert all(isinstance(e, st.RankEntry) for e in out)

    def test_ms_to_seconds_conversion(self):
        # ⚠️ Kritisch: 6000 ms -> 6 s, nicht 6000 s
        data = {"rankings": [
            {"position": 1, "bib": 11, "relative": 0, "absolute": 14508000},
            {"position": 2, "bib": 1, "relative": 6000, "absolute": 14514000,
             "bonus": 6000},
        ]}
        out = st.parse_rankings(data)
        assert out[0].relative_seconds == 0
        assert out[0].absolute_seconds == 14508  # nicht 14508000
        assert out[1].relative_seconds == 6  # nicht 6000
        assert out[1].bonus_seconds == 6  # nicht 6000

    def test_penality_typo_handled(self):
        # ASO hat "penality" statt "penalty" geschrieben!
        data = {"rankings": [
            {"position": 1, "bib": 1, "relative": 0, "absolute": 1000,
             "penality": 20000},
        ]}
        out = st.parse_rankings(data)
        assert out[0].penalty_seconds == 20

    def test_none_values_handled(self):
        data = {"rankings": [
            {"position": 1, "bib": 1, "relative": None, "absolute": None},
        ]}
        out = st.parse_rankings(data)
        assert out[0].relative_seconds is None
        assert out[0].absolute_seconds is None

    def test_sorted_by_position(self):
        data = {"rankings": [
            {"position": 3, "bib": 30, "relative": 0, "absolute": 0},
            {"position": 1, "bib": 10, "relative": 0, "absolute": 0},
            {"position": 2, "bib": 20, "relative": 0, "absolute": 0},
        ]}
        out = st.parse_rankings(data)
        assert [e.bib for e in out] == [10, 20, 30]

    def test_corrupt_entries_skipped(self):
        data = {"rankings": [
            {"position": 1, "bib": "not-a-number"},  # fehlerhaft
            {"position": 2, "bib": 11, "relative": 0, "absolute": 0},
        ]}
        out = st.parse_rankings(data)
        assert len(out) == 1
        assert out[0].bib == 11

    def test_missing_rankings_key(self):
        assert st.parse_rankings({}) == []
        assert st.parse_rankings({"rankings": None}) == []

    def test_bonus_defaults_zero(self):
        data = {"rankings": [{"position": 1, "bib": 1, "relative": 0,
                              "absolute": 0}]}
        out = st.parse_rankings(data)
        assert out[0].bonus_seconds == 0
        assert out[0].penalty_seconds == 0


# --------------------------------------------------------------------------- #
# parse_groups
# --------------------------------------------------------------------------- #
class TestParseGroups:
    def test_basic_group(self):
        data = {"groups": [{
            "order": 0, "name": "Peloton", "size": 100,
            "computedSpeed": 42.5, "remainingDistance": 50000,
            "computedRelative": 0,
            "isComputedGap": True,
            "latitude": 45.0, "longitude": 7.0,
            "bibs": [{"bib": i} for i in range(1, 101)],  # 100 echte Bibs
        }]}
        out = st.parse_groups(data)
        assert len(out) == 1
        g = out[0]
        assert g.order == 0
        assert g.name == "Peloton"
        assert g.size == 100  # size stimmt, weil 100 echte Bibs da sind
        assert g.speed == 42.5
        assert g.gap_seconds == 0  # isComputedGap=True -> computedRelative genutzt

    def test_gap_uses_computed_when_flagged(self):
        # isComputedGap=True -> computedRelative ist zuverlässig
        data = {"groups": [{
            "order": 0, "name": "X", "size": 1,
            "computedRelative": 42.5, "relative": 999,
            "isComputedGap": True,
        }]}
        out = st.parse_groups(data)
        assert out[0].gap_seconds == 42.5

    def test_gap_fallback_to_relative_without_flag(self):
        # ⚠️ BEKANNTER CODE-QUIRK: ohne isComputedGap-Flag fällt Code auf
        # 'relative' zurück. Wenn 'relative' None ist (und computedRelative
        # existiert), geht die Info verloren. Siehe REPORT.md.
        data = {"groups": [{
            "order": 0, "name": "X", "size": 1,
            "computedRelative": 0, "relative": None,
            "isComputedGap": False,
        }]}
        out = st.parse_groups(data)
        # Hier erwartet der Code isComputedGap=False -> nimmt 'relative' (None)
        assert out[0].gap_seconds is None

    def test_remaining_distance_meters_to_km(self):
        # ⚠️ ASO liefert Meter, Code muss /1000
        data = {"groups": [{
            "order": 0, "name": "X", "size": 1,
            "remainingDistance": 50000,  # 50 km in Metern
        }]}
        out = st.parse_groups(data)
        assert out[0].remaining_km == 50.0

    def test_bibs_extracted_from_dict_list(self):
        # bibs kommt als [{"bib": 11}, ...], nicht [11, 11, ...]
        data = {"groups": [{
            "order": 0, "name": "X", "size": 3,
            "bibs": [{"bib": 11}, {"bib": 1}, {"bib": 21}],
        }]}
        out = st.parse_groups(data)
        assert out[0].bibs == [11, 1, 21]

    def test_size_fallback_to_bibs_count(self):
        data = {"groups": [{
            "order": 0, "name": "X",
            "bibs": [{"bib": 1}, {"bib": 2}],
        }]}
        out = st.parse_groups(data)
        assert out[0].size == 2

    def test_size_999_dummy_replaced_by_bibs_count(self):
        # ASO-Dummy: Peloton mit size=999, aber nur 158 echte Bibs.
        # Code muss 999 durch echte Anzahl ersetzen.
        data = {"groups": [{
            "order": 0, "name": "Peloton", "size": 999,
            "bibs": [{"bib": i} for i in range(1, 159)],  # 158 echte Bibs
        }]}
        out = st.parse_groups(data)
        assert out[0].size == 158  # nicht 999

    def test_size_999_without_bibs_kept(self):
        # Wenn keine echten Bibs da sind, behalten wir size (auch 999),
        # weil wir keine bessere Info haben.
        data = {"groups": [{
            "order": 0, "name": "Peloton", "size": 999, "bibs": [],
        }]}
        out = st.parse_groups(data)
        assert out[0].size == 999

    def test_size_mismatch_small_group_corrected(self):
        # size=100 aber nur 3 Bibs (ungleichgewichtig >50): nimm 3.
        data = {"groups": [{
            "order": 0, "name": "X", "size": 100,
            "bibs": [{"bib": 1}, {"bib": 2}, {"bib": 3}],
        }]}
        out = st.parse_groups(data)
        assert out[0].size == 3

    def test_gps_position_extracted(self):
        data = {"groups": [{
            "order": 0, "name": "X", "size": 1,
            "latitude": 48.85, "longitude": 2.35,
        }]}
        out = st.parse_groups(data)
        assert out[0].lat == 48.85
        assert out[0].lon == 2.35

    def test_missing_fields(self):
        data = {"groups": [{"order": 1}]}
        out = st.parse_groups(data)
        assert len(out) == 1
        assert out[0].speed is None
        assert out[0].remaining_km is None

    def test_empty_groups(self):
        assert st.parse_groups({"groups": []}) == []
        assert st.parse_groups({}) == []


# --------------------------------------------------------------------------- #
# parse_checkpoints
# --------------------------------------------------------------------------- #
class TestParseCheckpoints:
    def test_dict_indexed(self):
        # checkpoint-Bind ist numerisch indiziert: {"0": {...}, "1": {...}}
        data = {
            "0": {"latitude": 45.0, "longitude": 7.0, "place": "Start",
                  "checkpointSummits": [{"category": "1"}]},  # -> mountain
            "1": {"latitude": 45.5, "longitude": 7.5, "place": "Sprint",
                  "checkpointTypes": [{"type": "sprint"}]},  # -> sprint
        }
        out = st.parse_checkpoints(data)
        assert len(out) == 2
        assert out[0].kind == "mountain"
        assert out[1].kind == "sprint"
        assert out[0].place == "Start"

    def test_list_input(self):
        # Akzeptiert auch Listen
        data = [
            {"latitude": 45.0, "longitude": 7.0, "place": "X"},
            {"latitude": 45.5, "longitude": 7.5, "place": "Y"},
        ]
        out = st.parse_checkpoints(data)
        assert len(out) == 2

    def test_kind_detected_from_summits(self):
        data = {"0": {"checkpointSummits": [{"category": "HC"}]}}
        out = st.parse_checkpoints(data)
        assert out[0].kind == "mountain"

    def test_kind_detected_from_types(self):
        data = {"0": {"checkpointTypes": [{"type": "sprint"}]}}
        out = st.parse_checkpoints(data)
        assert out[0].kind == "sprint"

    def test_empty(self):
        assert st.parse_checkpoints({}) == []
        assert st.parse_checkpoints([]) == []


# --------------------------------------------------------------------------- #
# State + recompute_virtual_gc
# --------------------------------------------------------------------------- #
class TestState:
    def test_state_initial(self):
        s = make_state()
        assert s.year == 2026
        assert s.stage == 3
        assert s.base_gc == []
        assert s.virtual_gc == []

    def test_virtual_gc_starts_as_base(self):
        s = make_state()
        s.base_gc = [
            st.RankEntry(position=1, bib=11, relative_seconds=0, absolute_seconds=1000),
            st.RankEntry(position=2, bib=1, relative_seconds=6, absolute_seconds=1006),
        ]
        st.recompute_virtual_gc(s)
        assert len(s.virtual_gc) == 2
        assert s.virtual_gc[0].bib == 11

    def test_apply_telemetry_sets_race_status(self):
        s = make_state()
        snap = {"RaceStatus": True, "TimeStamp": 1234567890, "Riders": []}
        st.apply_telemetry(s, snap, from_bootstrap=True)
        assert s.telemetry.race_status is True
        assert s.telemetry.bootstrapped is True

    def test_apply_telemetry_derives_withdrawals_from_starter_diff(self):
        # 20 Starter, 19 in Telemetrie -> 1 DNF abgeleitet.
        s = make_state()
        s.meta = {i: {} for i in range(1, 21)}  # 20 Starter
        snap = {"RaceStatus": True, "TimeStamp": 1234567890,
                "Riders": [{"Bib": i} for i in range(1, 20)]}  # 19 Rider
        st.apply_telemetry(s, snap)
        # Bib 20 fehlt in Telemetrie -> DNF
        assert 20 in s.withdrawals
        assert 1 not in s.withdrawals

    def test_apply_telemetry_no_dnf_when_telemetry_small(self):
        # Bei sehr kleiner Telemetrie (<10 Rider) nicht ableiten, weil
        # das auf Telemetrie-Lücken statt echte DNFs hindeutet.
        s = make_state()
        s.meta = {i: {} for i in range(1, 21)}  # 20 Starter
        snap = {"RaceStatus": True, "TimeStamp": 1, "Riders": [{"Bib": 1}]}
        st.apply_telemetry(s, snap)
        assert s.withdrawals == set()

    def test_apply_telemetry_no_dnf_if_too_many_missing(self):
        # Wenn >30% der Starter fehlen, ist die Telemetrie wahrscheinlich
        # kaputt, nicht alle Rider haben aufgegeben.
        s = make_state()
        s.meta = {i: {} for i in range(1, 21)}  # 20 Starter
        snap = {"RaceStatus": True, "TimeStamp": 1,
                "Riders": [{"Bib": i} for i in range(1, 11)]}  # nur 10 von 20
        st.apply_telemetry(s, snap)
        # 10 fehlen (50%) -> keine DNF-Ableitung
        assert s.withdrawals == set()

    def test_apply_telemetry_no_dnf_without_meta(self):
        # Ohne Starterfeld kann nicht ableiten werden.
        s = make_state()
        s.meta = {}
        snap = {"RaceStatus": True, "TimeStamp": 1,
                "Riders": [{"Bib": 1}, {"Bib": 2}]}
        st.apply_telemetry(s, snap)
        assert s.withdrawals == set()


# --------------------------------------------------------------------------- #
# dispatch (SSE-Bind-Routing)
# --------------------------------------------------------------------------- #
class TestDispatch:
    def test_telemetry_bind_updates_state(self):
        s = make_state()
        # SSE-Nachrichten-Struktur: {bind, data}
        msg = {
            "bind": "telemetryCompetitor-2026",
            "data": {
                "Riders": [],
                "RaceStatus": True,
                "TimeStamp": 1234567890,
            },
        }
        result = st.dispatch(msg, s, year=2026, stage=3, next_stage=4)
        assert result is True
        assert s.telemetry.race_status is True

    def test_unknown_bind_returns_false(self):
        s = make_state()
        msg = {"bind": "unknown-thing-2026", "data": {}}
        result = st.dispatch(msg, s, year=2026, stage=3, next_stage=4)
        assert result is False

    def test_arrival_bind_updates_gc(self):
        s = make_state()
        msg = {
            "bind": "rankingTypeArrival-2026-2",
            "data": {
                "checkpoint": 0,
                "type": "itg",
                "rankings": [
                    {"position": 1, "bib": 11, "relative": 0, "absolute": 1000},
                    {"position": 2, "bib": 1, "relative": 6000, "absolute": 16000,
                     "bonus": 6000},
                ],
            },
        }
        result = st.dispatch(msg, s, year=2026, stage=2, next_stage=3)
        assert result is True
        assert len(s.base_gc) == 2
        assert s.base_gc[0].bib == 11

    def test_pack_bind_updates_groups(self):
        s = make_state()
        msg = {
            "bind": "pack-2026-2",
            "data": {
                "groups": [{
                    "order": 0, "name": "Peloton", "size": 100,
                    "computedRelative": 0, "isComputedGap": True,
                }],
            },
        }
        result = st.dispatch(msg, s, year=2026, stage=2, next_stage=3)
        assert result is True
        assert len(s.groups) == 1
        assert s.groups[0].name == "Peloton"

    def test_checkpoint_bind_updates_checkpoints(self):
        s = make_state()
        msg = {
            "bind": "checkpoint-2026-2",
            "data": {"0": {"latitude": 45.0, "longitude": 7.0, "place": "X"}},
        }
        result = st.dispatch(msg, s, year=2026, stage=2, next_stage=3)
        assert result is True
        assert len(s.checkpoints) == 1

    def test_withdrawals_bind_adds_bibs(self):
        s = make_state()
        msg = {
            "bind": "stageWithdrawals-2026-2",
            "data": {"rankings": [{"bib": 11}, {"bib": 1}]},
        }
        result = st.dispatch(msg, s, year=2026, stage=2, next_stage=3)
        assert result is True
        assert 11 in s.withdrawals
        assert 1 in s.withdrawals

    def test_jerseys_bind_updates_jerseys(self):
        s = make_state()
        msg = {
            "bind": "rankingTypeJerseys-2026-3",
            "data": {
                "type": "pmt",
                "rankings": [{"position": 1, "bib": 11, "relative": 0,
                              "absolute": 1000}],
            },
        }
        result = st.dispatch(msg, s, year=2026, stage=2, next_stage=3)
        assert result is True
        # pmt = Gelb (Y)
        assert s.jerseys.get("Y") == 11

    def test_telemetry_dedup_same_timestamp(self):
        # Gleicher TimeStamp nach bereits eingetroffen -> False
        s = make_state()
        s._sse_last_ts = 1234567890
        s.telemetry.bootstrapped = False
        msg = {
            "bind": "telemetryCompetitor-2026",
            "data": {"TimeStamp": 1234567890, "RaceStatus": True, "Riders": []},
        }
        result = st.dispatch(msg, s, year=2026, stage=3, next_stage=4)
        assert result is False


# --------------------------------------------------------------------------- #
# name_of (Helper)
# --------------------------------------------------------------------------- #
class TestNameOf:
    def test_basic(self):
        meta = {11: {"firstname": "Jonas", "lastnameshort": "VINGEGAARD"}}
        n = st.name_of(meta, 11)
        assert "Jonas" in n
        assert "VINGEGAARD" in n

    def test_with_team(self):
        meta = {11: {"firstname": "Jonas", "lastnameshort": "VINGEGAARD",
                     "team_name": "VISMA"}}
        n = st.name_of(meta, 11)
        assert "VISMA" in n  # Team in Klammern

    def test_missing_bib_returns_empty(self):
        # meta.get(99, {}) -> {} -> leerer String
        assert st.name_of({}, 99) == ""

    def test_partial_info(self):
        meta = {11: {"firstname": "Jonas"}}  # kein lastname
        n = st.name_of(meta, 11)
        assert "Jonas" in n


# --------------------------------------------------------------------------- #
# to_snapshot (Serialisierung)
# --------------------------------------------------------------------------- #
class TestSnapshot:
    def test_snapshot_has_required_fields(self):
        s = make_state()
        snap = st.to_snapshot(s, top_limit=5)
        assert "year" in snap
        assert "stage" in snap
        assert "status" in snap
        assert "gc" in snap
        assert "groups" in snap

    def test_snapshot_gc_limited(self):
        s = make_state()
        s.virtual_gc = [
            st.RankEntry(position=i, bib=i, relative_seconds=i * 10,
                         absolute_seconds=1000 + i * 10)
            for i in range(1, 21)
        ]
        s.top_n = [1, 2, 3]
        snap = st.to_snapshot(s, top_limit=5)
        assert len(snap["gc"]) <= 5

    def test_snapshot_to_json_string(self):
        s = make_state()
        js = st.snapshot_to_json(s, top_limit=3)
        assert isinstance(js, str)
        # Muss valides JSON sein
        import json
        parsed = json.loads(js)
        assert "year" in parsed
