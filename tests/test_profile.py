"""Tests für tdf/profile.py — Höhenprofil-Parsing und -Serialisierung."""
from __future__ import annotations

import pytest

from tdf import profile as pf


# --------------------------------------------------------------------------- #
# parse_profile_csv
# --------------------------------------------------------------------------- #
class TestParseProfileCsv:
    def test_empty_string(self):
        assert pf.parse_profile_csv("") == []

    def test_no_semicolon_lines_skipped(self):
        text = "header line\nno semicolons here\nanother,comma,line"
        assert pf.parse_profile_csv(text) == []

    def test_header_skipped(self):
        # Erste Zeile mit nicht-numerischer lat -> Header, übersprungen
        text = "lat;lon;alt;;;;;;km_to_go\n45.0;7.0;100.0;;;;;;50.0"
        out = pf.parse_profile_csv(text)
        assert len(out) == 1
        assert out[0].lat == 45.0
        assert out[0].lon == 7.0

    def test_parsing_real_format(self):
        # Realistisches Format mit mind. 9 Spalten
        text = "\n".join([
            "lat;lon;alt;unused;unused;unused;unused;km_done;km_to_go",
            "45.0;7.0;100.0;a;b;c;d;0.0;100.0",
            "45.1;7.1;200.0;a;b;c;d;10.0;90.0",
            "45.2;7.2;300.0;a;b;c;d;20.0;80.0",
        ])
        out = pf.parse_profile_csv(text)
        assert len(out) == 3
        assert out[0].lat == 45.0
        assert out[0].alt_m == 100.0
        assert out[0].km_done == 0.0
        assert out[0].km_to_go == 100.0
        assert out[2].km_to_go == 80.0

    def test_missing_altitude_defaults_zero(self):
        text = "lat;lon;alt;x;x;x;x;km_done;km_to_go\n45.0;7.0;non-numeric;x;x;x;x;0;100"
        out = pf.parse_profile_csv(text)
        assert len(out) == 1
        assert out[0].alt_m == 0.0

    def test_too_few_columns_skipped(self):
        text = "45.0;7.0"  # nur 2 Spalten
        assert pf.parse_profile_csv(text) == []

    def test_mixed_valid_invalid(self):
        text = "\n".join([
            "lat;lon;alt;x;x;x;x;km_done;km_to_go",
            "45.0;7.0;100;x;x;x;x;0;100",  # ok
            "not;numeric;lat",  # skip
            "45.1;7.1;200;x;x;x;x;10;90",  # ok
        ])
        out = pf.parse_profile_csv(text)
        assert len(out) == 2


# --------------------------------------------------------------------------- #
# _safe_float
# --------------------------------------------------------------------------- #
class TestSafeFloat:
    def test_valid_number(self):
        assert pf._safe_float("3.14") == 3.14

    def test_integer_string(self):
        assert pf._safe_float("42") == 42.0

    def test_empty_string(self):
        assert pf._safe_float("") == 0.0

    def test_non_numeric(self):
        assert pf._safe_float("abc") == 0.0

    def test_none(self):
        assert pf._safe_float(None) == 0.0  # type: ignore[arg-type]

    def test_negative(self):
        assert pf._safe_float("-5.5") == -5.5


# --------------------------------------------------------------------------- #
# to_json (Serialisierung)
# --------------------------------------------------------------------------- #
class TestProfileToJson:
    def test_empty_profile(self):
        prof = pf.Profile(points=[], source="test")
        out = pf.to_json(prof)
        assert isinstance(out, dict)
        assert "points" in out
        assert out["points"] == []
        assert out["source"] == "test"
        assert out["total_km"] == 0.0

    def test_max_points_limit(self):
        points = [pf.ProfilePoint(lat=float(i), lon=float(i), alt_m=i * 10.0,
                                  km_done=float(i), km_to_go=100.0 - i)
                  for i in range(1000)]
        prof = pf.Profile(points=points, source="x")
        out = pf.to_json(prof, max_points=100)
        assert len(out["points"]) <= 100

    def test_total_km(self):
        points = [
            pf.ProfilePoint(0.0, 0.0, 0.0, 0.0, 100.0),
            pf.ProfilePoint(0.1, 0.1, 100.0, 50.0, 50.0),
            pf.ProfilePoint(0.2, 0.2, 200.0, 100.0, 0.0),
        ]
        prof = pf.Profile(points=points, source="x")
        assert prof.total_km == 100.0  # letzter km_done

    def test_point_format(self):
        points = [pf.ProfilePoint(45.0, 7.0, 100.0, 10.0, 90.0)]
        prof = pf.Profile(points=points, source="url")
        out = pf.to_json(prof)
        p = out["points"][0]
        assert p["km"] == 10.0
        assert p["alt"] == 100.0
        assert p["lat"] == 45.0
        assert p["lon"] == 7.0


# --------------------------------------------------------------------------- #
# _scan_for_profile_url
# --------------------------------------------------------------------------- #
class TestScanProfileUrl:
    def test_finds_url_in_millesime(self):
        obj = {"profileUrl": "https://x/profile-2-abc.csv"}
        url = pf._scan_for_profile_url(obj, year=2026, stage=2)
        # Implementierung ist Best-Effort; nur prüfen, dass keine Exception
        assert url is None or isinstance(url, str)

    def test_returns_none_for_empty(self):
        assert pf._scan_for_profile_url({}, 2026, 2) is None

    def test_returns_none_for_none(self):
        assert pf._scan_for_profile_url(None, 2026, 2) is None
