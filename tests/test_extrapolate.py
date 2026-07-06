"""Tests für tdf/extrapolate.py — Geodaten-Mathematik.

Verifiziert Haversine, Destination-Point, Dead-Reckon und Lerp gegen
bekannte Referenzwerte. Haversine-Referenz übernommen aus
mullummer/racecenter (gleiche Implementierung, R=6371 km).
"""
from __future__ import annotations

import math

import pytest

from tdf import extrapolate as ex
from tdf import config as cfg


# --------------------------------------------------------------------------- #
# Haversine
# --------------------------------------------------------------------------- #
class TestHaversine:
    def test_zero_distance(self):
        assert ex.haversine_km(48.0, 8.0, 48.0, 8.0) == pytest.approx(0.0, abs=1e-9)

    def test_identical_point(self):
        # Selber Punkt -> 0
        assert ex.haversine_km(0, 0, 0, 0) == 0.0

    def test_known_paris_london(self):
        # Paris (48.8566, 2.3522) -> London (51.5074, -0.1278) ~344 km
        d = ex.haversine_km(48.8566, 2.3522, 51.5074, -0.1278)
        assert 340 <= d <= 350

    def test_known_berlin_munich(self):
        # Berlin (52.52, 13.405) -> München (48.137, 11.575) ~504 km
        d = ex.haversine_km(52.52, 13.405, 48.137, 11.575)
        assert 500 <= d <= 510

    def test_symmetry(self):
        # Distanz muss richtungsunabhängig sein
        d1 = ex.haversine_km(45.0, 5.0, 46.0, 6.0)
        d2 = ex.haversine_km(46.0, 6.0, 45.0, 5.0)
        assert d1 == pytest.approx(d2, abs=1e-9)

    def test_quarter_meridian(self):
        # 1° latitude ~ 111.19 km. 0.25° -> ~27.8 km
        d = ex.haversine_km(0, 0, 0.25, 0)
        assert 27.0 <= d <= 29.0

    def test_uses_correct_earth_radius(self):
        # R muss aus config kommen
        assert cfg.EARTH_RADIUS_KM == 6371.0


# --------------------------------------------------------------------------- #
# Destination-Point
# --------------------------------------------------------------------------- #
class TestDestinationPoint:
    def test_zero_distance_returns_origin(self):
        lat, lon = ex.destination_point(48.0, 8.0, 90.0, 0.0)
        assert lat == pytest.approx(48.0, abs=1e-9)
        assert lon == pytest.approx(8.0, abs=1e-9)

    def test_due_north(self):
        # 111.19 km nach Norden -> +1° latitude
        lat, lon = ex.destination_point(0.0, 0.0, 0.0, 111.19)
        assert lat == pytest.approx(1.0, abs=0.01)
        assert lon == pytest.approx(0.0, abs=0.01)

    def test_due_east(self):
        # ~111 km nach Osten am Äquator -> ~+1° longitude
        lat, lon = ex.destination_point(0.0, 0.0, 90.0, 111.32)
        assert lat == pytest.approx(0.0, abs=0.01)
        assert lon == pytest.approx(1.0, abs=0.01)

    def test_longitude_normalized(self):
        # Punkt nah bei 180° muss normalisiert werden auf [-180, 180]
        _, lon = ex.destination_point(0.0, 179.5, 90.0, 200.0)
        assert -180.0 <= lon <= 180.0

    def test_roundtrip_with_haversine(self):
        # Projektion um d km, dann Haversine zurück -> ~d km
        start = (45.0, 7.0)
        d_km = 25.0
        lat2, lon2 = ex.destination_point(*start, 45.0, d_km)
        back = ex.haversine_km(*start, lat2, lon2)
        assert back == pytest.approx(d_km, abs=0.1)


# --------------------------------------------------------------------------- #
# Lerp (Re-Anchor)
# --------------------------------------------------------------------------- #
class TestLerpReanchor:
    def test_alpha_zero_keeps_old(self):
        # α=0 -> alte Position bleibt
        lat, lon = ex.lerp_reanchor(48.0, 8.0, 49.0, 9.0, alpha=0.0)
        assert lat == pytest.approx(48.0)
        assert lon == pytest.approx(8.0)

    def test_alpha_one_takes_new(self):
        # α=1 -> neue Position
        lat, lon = ex.lerp_reanchor(48.0, 8.0, 49.0, 9.0, alpha=1.0)
        assert lat == pytest.approx(49.0)
        assert lon == pytest.approx(9.0)

    def test_alpha_default(self):
        # Default α=0.3: 30% neu, 70% alt
        lat, lon = ex.lerp_reanchor(40.0, 100.0, 50.0, 110.0)
        assert lat == pytest.approx(43.0, abs=0.01)  # 40*0.7 + 50*0.3 = 43
        assert lon == pytest.approx(103.0, abs=0.01)

    def test_alpha_half(self):
        lat, lon = ex.lerp_reanchor(0.0, 0.0, 10.0, 20.0, alpha=0.5)
        assert lat == pytest.approx(5.0)
        assert lon == pytest.approx(10.0)


# --------------------------------------------------------------------------- #
# Dead-Reckon
# --------------------------------------------------------------------------- #
class TestDeadReckon:
    def test_missing_fields_returns_none(self):
        rider = {"Latitude": 48.0}  # Longitude fehlt
        assert ex.dead_reckon(rider, dt_s=2.0) is None

    def test_none_values_return_none(self):
        rider = {"Latitude": None, "Longitude": None, "Course": 90, "kph": 40}
        assert ex.dead_reckon(rider, dt_s=2.0) is None

    def test_stationary_rider(self):
        # kph=0 -> 0 Distanz -> gleiche Position
        rider = {"Latitude": 48.0, "Longitude": 8.0, "Course": 90, "kph": 0}
        result = ex.dead_reckon(rider, dt_s=10.0)
        assert result is not None
        lat, lon = result
        assert lat == pytest.approx(48.0)
        assert lon == pytest.approx(8.0)

    def test_moving_east_2s(self):
        # 40 km/h für 2s = ~22.2m. Bei Course=90 (Osten) am Äquator:
        rider = {"Latitude": 0.0, "Longitude": 0.0, "Course": 90, "kph": 40}
        lat, lon = ex.dead_reckon(rider, dt_s=2.0)
        assert lat == pytest.approx(0.0, abs=1e-4)
        # ~0.0002° longitude
        assert 0 < lon < 0.001

    def test_invalid_kph_returns_none(self):
        rider = {"Latitude": 0.0, "Longitude": 0.0, "Course": 90, "kph": "fast"}
        assert ex.dead_reckon(rider, dt_s=2.0) is None


# --------------------------------------------------------------------------- #
# step_extrapolation & reanchor_on_tick
# --------------------------------------------------------------------------- #
class TestStepExtrapolation:
    def test_extrapolates_top_n(self):
        riders = {
            11: {"Latitude": 0.0, "Longitude": 0.0, "Course": 90, "kph": 40},
            1:  {"Latitude": 0.0, "Longitude": 0.001, "Course": 90, "kph": 42},
        }
        state_ext = {}
        ex.step_extrapolation(state_ext, riders, [11, 1], dt_s=2.0)
        assert 11 in state_ext
        assert 1 in state_ext
        # Beide haben sich nach Osten bewegt
        assert state_ext[11][1] > 0
        assert state_ext[1][1] > 0.001

    def test_unknown_bib_skipped(self):
        riders = {11: {"Latitude": 0.0, "Longitude": 0.0, "Course": 90, "kph": 40}}
        state_ext = {}
        ex.step_extrapolation(state_ext, riders, [11, 99], dt_s=2.0)
        assert 11 in state_ext
        assert 99 not in state_ext

    def test_continues_from_extrapolated_position(self):
        riders = {11: {"Latitude": 0.0, "Longitude": 0.0, "Course": 90, "kph": 40}}
        state_ext = {11: (0.0, 0.5)}  # bereits extrapoliert
        ex.step_extrapolation(state_ext, riders, [11], dt_s=2.0)
        # Muss vom extrapolierten Punkt weitergehen, nicht von (0,0)
        assert state_ext[11][1] > 0.5


class TestReanchorOnTick:
    def test_first_tick_sets_raw_position(self):
        riders = {11: {"Latitude": 45.0, "Longitude": 7.0}}
        state_ext = {}
        ex.reanchor_on_tick(state_ext, riders, [11])
        assert state_ext[11] == (45.0, 7.0)

    def test_subsequent_tick_lerps(self):
        riders = {11: {"Latitude": 50.0, "Longitude": 10.0}}
        state_ext = {11: (40.0, 0.0)}  # alt
        ex.reanchor_on_tick(state_ext, riders, [11])
        # Lerp α=0.3: 40*0.7 + 50*0.3 = 43
        lat, lon = state_ext[11]
        assert lat == pytest.approx(43.0)
        assert lon == pytest.approx(3.0)

    def test_missing_lat_lon_skipped(self):
        riders = {11: {"Latitude": None, "Longitude": None}}
        state_ext = {11: (40.0, 0.0)}
        ex.reanchor_on_tick(state_ext, riders, [11])
        assert state_ext[11] == (40.0, 0.0)  # unverändert
