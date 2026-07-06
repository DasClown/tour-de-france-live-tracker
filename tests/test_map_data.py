"""Tests für tdf/map_data.py — Karten-Aggregation."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from tdf import map_data as md


@dataclass
class FakeGroup:
    lat: float = 45.0
    lon: float = 7.0
    order: int = 0


@dataclass
class FakeCheckpoint:
    lat: float = 45.5
    lon: float = 7.5


# --------------------------------------------------------------------------- #
# compute_bounding_box
# --------------------------------------------------------------------------- #
class TestBoundingBox:
    def test_empty_points(self):
        assert md.compute_bounding_box([]) is None

    def test_single_point(self):
        # Braucht >= 2 Punkte, sonst None
        assert md.compute_bounding_box([(45.0, 7.0)]) is None

    def test_two_points(self):
        bbox = md.compute_bounding_box([(45.0, 7.0), (46.0, 8.0)])
        assert bbox is not None
        assert bbox["south"] == 45.0
        assert bbox["north"] == 46.0
        assert bbox["west"] == 7.0
        assert bbox["east"] == 8.0

    def test_more_than_two_points(self):
        bbox = md.compute_bounding_box([(45.0, 7.0), (46.0, 8.0), (44.5, 6.5)])
        assert bbox is not None
        assert bbox["south"] == 44.5
        assert bbox["north"] == 46.0
        assert bbox["west"] == 6.5
        assert bbox["east"] == 8.0


# --------------------------------------------------------------------------- #
# compute_center
# --------------------------------------------------------------------------- #
class TestCenter:
    def test_empty(self):
        assert md.compute_center([]) is None

    def test_single(self):
        assert md.compute_center([(45.0, 7.0)]) == (45.0, 7.0)

    def test_two_points(self):
        c = md.compute_center([(40.0, 0.0), (50.0, 10.0)])
        assert c == pytest.approx((45.0, 5.0))

    def test_three_points(self):
        c = md.compute_center([(0, 0), (3, 0), (0, 4)])
        assert c == pytest.approx((1.0, 4 / 3))


# --------------------------------------------------------------------------- #
# extract_route_polyline
# --------------------------------------------------------------------------- #
class TestExtractRoute:
    def test_none_profile(self):
        assert md.extract_route_polyline(None) is None

    def test_empty_profile(self):
        assert md.extract_route_polyline({}) is None

    def test_dict_with_checkpoints(self):
        profile = {"checkpoints": [
            {"lat": 45.0, "lon": 7.0},
            {"lat": 45.1, "lon": 7.1},
            {"lat": 45.2, "lon": 7.2},
        ]}
        route = md.extract_route_polyline(profile)
        assert route is not None
        assert len(route) == 3
        assert route[0] == [45.0, 7.0]

    def test_dict_with_points(self):
        # Manche Profile nutzen "points" statt "checkpoints"
        profile = {"points": [{"latitude": 45.0, "longitude": 7.0}]}
        route = md.extract_route_polyline(profile)
        assert route == [[45.0, 7.0]]

    def test_filters_incomplete_points(self):
        profile = {"checkpoints": [
            {"lat": 45.0, "lon": 7.0},
            {"lat": None, "lon": 7.1},  # unvollständig
            {"lat": 45.2, "lon": 7.2},
        ]}
        route = md.extract_route_polyline(profile)
        assert len(route) == 2

    def test_object_with_checkpoints(self):
        @dataclass
        class P:
            checkpoints: list = None
        profile = P(checkpoints=[{"lat": 1.0, "lon": 2.0}])
        route = md.extract_route_polyline(profile)
        assert route == [[1.0, 2.0]]


# --------------------------------------------------------------------------- #
# build_map_data
# --------------------------------------------------------------------------- #
class TestBuildMapData:
    def test_empty_inputs(self):
        result = md.build_map_data([], [], [], None, None)
        assert result["route"] is None
        assert result["mountain_markers"] == []
        assert result["sprint_markers"] == []
        assert result["checkpoints_count"] == 0
        # bbox fehlt bei <2 Punkten
        assert "bbox" not in result

    def test_with_groups_and_checkpoints(self):
        groups = [FakeGroup(lat=45.0, lon=7.0), FakeGroup(lat=45.1, lon=7.1)]
        cps = [FakeCheckpoint(lat=45.5, lon=7.5)]
        result = md.build_map_data(groups, [], cps, None, None)
        assert result["checkpoints_count"] == 1
        assert "bbox" in result
        assert result["bbox"]["south"] == 45.0
        assert result["bbox"]["north"] == 45.5

    def test_dict_groups_supported(self):
        groups = [{"lat": 45.0, "lon": 7.0}]
        result = md.build_map_data(groups, [], [], None, None)
        # 1 Punkt -> kein bbox, aber keine Exception
        assert "bbox" not in result

    def test_classification_markers_extracted(self):
        classifications = {
            "mountains": [{
                "summit_lat": 45.5, "summit_lon": 6.5,
                "name": "Galibier", "category": "HC",
                "category_label": "Außer Kategorie",
                "length_km": 18.0, "avg_gradient": 7.5,
            }],
            "sprints": [{
                "lat": 44.0, "lon": 5.0, "place": "Sprint",
            }],
        }
        result = md.build_map_data([], [], [], classifications, None)
        assert len(result["mountain_markers"]) == 1
        assert result["mountain_markers"][0]["name"] == "Galibier"
        assert len(result["sprint_markers"]) == 1
        assert result["sprint_markers"][0]["place"] == "Sprint"

    def test_incomplete_markers_filtered(self):
        classifications = {
            "mountains": [{"summit_lat": None, "summit_lon": 6.5}],  # lat fehlt
            "sprints": [{"lat": 44.0, "lon": None}],  # lon fehlt
        }
        result = md.build_map_data([], [], [], classifications, None)
        assert result["mountain_markers"] == []
        assert result["sprint_markers"] == []

    def test_route_from_profile(self):
        profile = {"checkpoints": [{"lat": 45.0, "lon": 7.0}]}
        result = md.build_map_data([], [], [], None, profile)
        assert result["route"] == [[45.0, 7.0]]

    def test_center_calculated(self):
        groups = [FakeGroup(lat=40.0, lon=0.0), FakeGroup(lat=50.0, lon=10.0)]
        result = md.build_map_data(groups, [], [], None, None)
        assert "center" in result
        # Center der bbox
        assert result["center"]["lat"] == pytest.approx(45.0, abs=0.01)
