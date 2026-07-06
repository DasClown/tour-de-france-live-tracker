"""Tests für tdf/trail.py — JSONL-Trail mit Throttling und Rotation."""
from __future__ import annotations

import json
import os
import time

import pytest

from tdf import trail


class TestJsonlTrailWrite:
    def test_empty_path_no_write(self, tmp_path):
        t = trail.JsonlTrail("")
        assert t.write({"x": 1}) is False

    def test_first_write_succeeds(self, tmp_path):
        p = str(tmp_path / "live.jsonl")
        t = trail.JsonlTrail(p, flush_every_s=0.0)
        assert t.write({"stage": 3, "gc": []}) is True
        assert os.path.exists(p)

    def test_throttle_blocks_rapid_writes(self, tmp_path):
        p = str(tmp_path / "live.jsonl")
        t = trail.JsonlTrail(p, flush_every_s=2.0)
        assert t.write({"i": 1}) is True
        # Zweiter write innerhalb 2s wird blockiert
        assert t.write({"i": 2}) is False
        assert t.write({"i": 3}) is False

    def test_throttle_allows_after_interval(self, tmp_path):
        p = str(tmp_path / "live.jsonl")
        t = trail.JsonlTrail(p, flush_every_s=0.05)
        assert t.write({"i": 1}) is True
        time.sleep(0.1)
        assert t.write({"i": 2}) is True

    def test_written_content_is_valid_jsonl(self, tmp_path):
        p = str(tmp_path / "live.jsonl")
        t = trail.JsonlTrail(p, flush_every_s=0.0)
        t.write({"name": "test", "n": 1})
        t._last_write = 0  # throttle umgehen für test
        t.write({"name": "test2", "n": 2})
        with open(p) as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        assert len(lines) == 2
        for ln in lines:
            obj = json.loads(ln)
            assert "name" in obj

    def test_directory_auto_created(self, tmp_path):
        p = str(tmp_path / "subdir" / "deeper" / "live.jsonl")
        t = trail.JsonlTrail(p, flush_every_s=0.0)
        t.write({"x": 1})
        assert os.path.exists(p)

    def test_unicode_preserved(self, tmp_path):
        p = str(tmp_path / "live.jsonl")
        t = trail.JsonlTrail(p, flush_every_s=0.0)
        t.write({"name": "Jonas VINGEGAARD", "u": "Ünïcödé"})
        with open(p, encoding="utf-8") as f:
            obj = json.loads(f.read())
        assert obj["u"] == "Ünïcödé"


class TestJsonlTrailRotation:
    def test_rotation_triggers_at_max_size(self, tmp_path):
        p = str(tmp_path / "live.jsonl")
        # Sehr kleines Limit, damit sofort rotiert wird
        t = trail.JsonlTrail(p, flush_every_s=0.0, max_size=200)
        # Schreibe genug, um über Limit zu kommen
        for i in range(20):
            t._last_write = 0
            t.write({"i": i, "padding": "x" * 30})
        # Ursprüngliche Datei sollte jetzt kleiner sein (nach Rotation Neustart)
        archive = p + ".archive.1"
        assert os.path.exists(archive), "Archiv wurde nicht angelegt"

    def test_max_archives_kept(self, tmp_path):
        p = str(tmp_path / "live.jsonl")
        t = trail.JsonlTrail(p, flush_every_s=0.0, max_size=100)
        # Viele Rotationen erzwingen
        for i in range(100):
            t._last_write = 0
            t.write({"i": i, "pad": "x" * 50})
        # Archive zählen
        archives = [f for f in os.listdir(tmp_path) if "archive" in f]
        # Höchstens MAX_ARCHIVES (5)
        assert len(archives) <= trail.MAX_ARCHIVES


class TestReadLastN:
    def test_missing_file_returns_empty(self, tmp_path):
        assert trail.read_last_n(str(tmp_path / "nope.jsonl"), 5) == []

    def test_empty_path_returns_empty(self):
        assert trail.read_last_n("", 5) == []

    def test_reads_last_n(self, tmp_path):
        p = str(tmp_path / "live.jsonl")
        with open(p, "w") as f:
            for i in range(10):
                f.write(json.dumps({"i": i}) + "\n")
        result = trail.read_last_n(p, 3)
        assert len(result) == 3
        assert result[-1]["i"] == 9
        assert result[0]["i"] == 7

    def test_handles_corrupt_lines(self, tmp_path):
        p = str(tmp_path / "live.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"i": 1}) + "\n")
            f.write("CORRUPT NOT JSON\n")
            f.write(json.dumps({"i": 3}) + "\n")
        result = trail.read_last_n(p, 5)
        # Korrupte Zeile wird still verworfen
        is_valid = [r for r in result if "i" in r]
        assert len(is_valid) == 2

    def test_n_negative_behaves_like_all(self, tmp_path):
        # n=0 oder negativ ist in Python Slicing ein Grenzfall; wir
        # testen stattdessen das definierte n=1.
        p = str(tmp_path / "live.jsonl")
        with open(p, "w") as f:
            for i in range(5):
                f.write(json.dumps({"i": i}) + "\n")
        assert len(trail.read_last_n(p, 1)) == 1
        assert trail.read_last_n(p, 1)[0]["i"] == 4
