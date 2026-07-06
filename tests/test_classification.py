"""Tests für tdf/classification.py — Berg/Sprint-Klassifikation."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from tdf import classification as cl


# --------------------------------------------------------------------------- #
# Doubles für Checkpoint-Dataclass
# --------------------------------------------------------------------------- #
@dataclass
class FakeCheckpoint:
    lat: float = 45.0
    lon: float = 7.0
    kind: str = ""
    place: str = ""


# --------------------------------------------------------------------------- #
# parse_mountain_from_checkpoint
# --------------------------------------------------------------------------- #
class TestParseMountain:
    def test_no_summit_returns_none(self):
        cp = {"place": "Col", "latitude": 45, "longitude": 7}
        assert cl.parse_mountain_from_checkpoint(cp, 0) is None

    def test_with_summit_array(self):
        cp = {
            "place": "Col du Galibier",
            "latitude": 45.0, "longitude": 7.0,
            "checkpointSummits": [{"category": "HC", "length": 18.0, "avgGradient": 7.5}],
        }
        m = cl.parse_mountain_from_checkpoint(cp, 0, remaining_distance=50.0)
        assert m is not None
        assert m.name == "Col du Galibier"
        assert m.category == "HC"
        assert m.length_km == 18.0
        assert m.avg_gradient == 7.5
        assert m.km_to_finish == 50.0
        assert m.summit_lat == 45.0

    def test_with_single_summit_object(self):
        cp = {
            "name": "Alpe d'Huez",
            "lat": 45.09, "lng": 6.13,
            "checkpointSummit": {"category": "1", "length": 13.8, "avgGradient": 8.1},
        }
        m = cl.parse_mountain_from_checkpoint(cp, 1)
        assert m is not None
        assert m.name == "Alpe d'Huez"
        assert m.category == "1"

    def test_fallback_name_when_missing(self):
        cp = {
            "checkpointSummits": [{"category": "3"}],
            "latitude": 45, "longitude": 7,
        }
        m = cl.parse_mountain_from_checkpoint(cp, 2)
        assert m is not None
        assert "Berg" in m.name  # "Berg 3"

    def test_invalid_latitude_returns_none(self):
        cp = {
            "checkpointSummits": [{"category": "3"}],
            "latitude": "not-a-number", "longitude": 7,
        }
        # TypeError wird abgefangen
        assert cl.parse_mountain_from_checkpoint(cp, 0) is None


# --------------------------------------------------------------------------- #
# parse_sprint_from_checkpoint
# --------------------------------------------------------------------------- #
class TestParseSprint:
    def test_no_checkpoint_types_returns_none(self):
        cp = {"place": "X", "latitude": 45, "longitude": 7}
        assert cl.parse_sprint_from_checkpoint(cp, 0) is None

    def test_with_sprint_type_array(self):
        cp = {
            "place": "Sprint Nîmes",
            "latitude": 43.83, "longitude": 4.35,
            "checkpointTypes": [{"type": "sprint"}],
        }
        s = cl.parse_sprint_from_checkpoint(cp, 0, remaining_distance=30.0)
        assert s is not None
        assert s.place == "Sprint Nîmes"
        assert s.km_to_finish == 30.0
        assert s.lat == 43.83

    def test_with_sprint_type_string(self):
        cp = {
            "place": "X",
            "checkpointTypes": "sprint",
        }
        s = cl.parse_sprint_from_checkpoint(cp, 0)
        assert s is not None

    def test_non_sprint_type_returns_none(self):
        cp = {
            "place": "X",
            "checkpointTypes": [{"type": "feedzone"}],
        }
        assert cl.parse_sprint_from_checkpoint(cp, 0) is None

    def test_fallback_place(self):
        cp = {"checkpointTypes": [{"type": "sprint"}], "lat": 45, "lon": 7}
        s = cl.parse_sprint_from_checkpoint(cp, 3)
        assert s is not None
        assert "Sprint" in s.place


# --------------------------------------------------------------------------- #
# parse_checkpoints_to_classifications
# --------------------------------------------------------------------------- #
class TestParseCheckpoints:
    def test_empty_checkpoints(self):
        state = cl.parse_checkpoints_to_classifications([])
        assert state.mountains == []
        assert state.sprints == []

    def test_mix_of_mountains_and_sprints(self):
        checkpoints = [
            {"kind": "mountain", "place": "Col 1",
             "checkpointSummits": [{"category": "2"}], "latitude": 45, "longitude": 7},
            {"kind": "sprint", "place": "Sprint 1",
             "checkpointTypes": [{"type": "sprint"}], "latitude": 45.1, "longitude": 7.1},
            {"kind": "mountain", "place": "Col 2",
             "checkpointSummits": [{"category": "1"}], "latitude": 45.2, "longitude": 7.2},
        ]
        state = cl.parse_checkpoints_to_classifications(checkpoints)
        assert len(state.mountains) == 2
        assert len(state.sprints) == 1

    def test_dataclass_checkpoints(self):
        cps = [
            FakeCheckpoint(lat=45.0, lon=7.0, kind="mountain", place="Berg X"),
            FakeCheckpoint(lat=45.1, lon=7.1, kind="sprint", place="Sprint Y"),
        ]
        state = cl.parse_checkpoints_to_classifications(cps)
        assert len(state.mountains) == 1
        assert state.mountains[0].name == "Berg X"
        assert len(state.sprints) == 1

    def test_remaining_distance_from_groups(self):
        # Wenn Gruppe remaining_km trägt, wird sie genutzt
        @dataclass
        class G:
            remaining_km: float = 42.5

        cps = [{"kind": "mountain", "place": "X",
                "checkpointSummits": [{"category": "2"}], "latitude": 1, "longitude": 2}]
        state = cl.parse_checkpoints_to_classifications(cps, groups=[G()])
        assert state.mountains[0].km_to_finish == 42.5


# --------------------------------------------------------------------------- #
# extract_mountain_ranking / extract_sprint_ranking
# --------------------------------------------------------------------------- #
class TestRankings:
    def test_mountain_ranking_from_pmm(self):
        jerseys = {"P": 22}  # Pogacar trägt Bergtrikot
        meta = {22: {"firstname": "Tadej", "lastnameshort": "POGACAR",
                     "team_name": "UAE"}}
        # gc_pos = Index des bib in der gc-Liste +1 (nicht das pos-Feld)
        gc = [{"bib": 11}, {"bib": 22}, {"bib": 1}]
        r = cl.extract_mountain_ranking(jerseys, meta, gc)
        assert len(r) == 1
        assert r[0]["bib"] == 22
        assert "POGACAR" in r[0]["name"]
        assert r[0]["team"] == "UAE"
        assert r[0]["gc_pos"] == 2  # Index 1 + 1

    def test_mountain_ranking_bib_not_in_gc(self):
        jerseys = {"P": 99}
        r = cl.extract_mountain_ranking(jerseys, {99: {"firstname": "X",
                                                       "lastnameshort": "Y"}}, [])
        assert len(r) == 1
        assert r[0]["gc_pos"] is None  # nicht in gc gefunden

    def test_mountain_ranking_empty_when_no_pmm(self):
        r = cl.extract_mountain_ranking({"Y": 11}, {}, [])
        assert r == []

    def test_sprint_ranking_from_pmp(self):
        jerseys = {"G": 11}
        meta = {11: {"firstname": "Jonas", "lastnameshort": "VINGEGAARD",
                     "team_code": "TVL"}}
        gc = [{"bib": 11, "pos": 1}]
        r = cl.extract_sprint_ranking(jerseys, meta, gc)
        assert len(r) == 1
        assert r[0]["bib"] == 11
        assert r[0]["points_sprint"] == 0


# --------------------------------------------------------------------------- #
# to_json (Serialisierung)
# --------------------------------------------------------------------------- #
class TestToJson:
    def test_full_serialization(self):
        state = cl.ClassificationState(
            mountains=[cl.MountainClimb(index=0, name="Galibier", category="HC",
                                        km_to_finish=50.0, length_km=18.0,
                                        avg_gradient=7.5,
                                        summit_lat=45.0, summit_lon=6.5)],
            sprints=[cl.Sprint(index=1, place="Sprint", km_to_finish=30.0,
                               lat=44.0, lon=5.0)],
            kom_ranking=[{"bib": 22, "rank": 1}],
            sprint_ranking=[],
        )
        out = cl.to_json(state, {}, {}, [])
        assert len(out["mountains"]) == 1
        assert out["mountains"][0]["name"] == "Galibier"
        assert out["mountains"][0]["category_label"] == "Außer Kategorie"
        assert out["mountains"][0]["length_km"] == 18.0
        assert len(out["sprints"]) == 1
        assert out["kom_ranking"] == [{"bib": 22, "rank": 1}]
        assert out["sprint_ranking"] == []

    def test_category_labels_mapping(self):
        # Alle Kategorien müssen ein Label haben
        for cat, label in cl.CATEGORY_LABELS.items():
            state = cl.ClassificationState(
                mountains=[cl.MountainClimb(index=0, category=cat)])
            out = cl.to_json(state, {}, {}, [])
            assert out["mountains"][0]["category_label"] == label
