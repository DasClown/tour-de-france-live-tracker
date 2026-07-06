"""Tests für tdf/config.py — URLs, Bind-Namen, ENV-Defaults, Codes."""
from __future__ import annotations

import importlib

import pytest

from tdf import config as cfg


class TestUrls:
    def test_base(self):
        assert cfg.BASE == "https://racecenter.letour.fr"

    def test_url_all_competitors(self):
        assert cfg.url_all_competitors(2026) == f"{cfg.BASE}/api/allCompetitors-2026"

    def test_url_teams(self):
        assert cfg.url_teams(2026) == f"{cfg.BASE}/api/team-2026"

    def test_url_stages(self):
        assert cfg.url_stages(2026) == f"{cfg.BASE}/api/stage-2026"

    def test_url_ranking_arrival(self):
        u = cfg.url_ranking_arrival(2026, 3)
        assert u == f"{cfg.BASE}/api/rankingTypeArrival-2026-3"
        assert "2026" in u and "-3" in u

    def test_url_ranking_jerseys(self):
        assert cfg.url_ranking_jerseys(2026, 4) == f"{cfg.BASE}/api/rankingTypeJerseys-2026-4"

    def test_url_telemetry(self):
        assert cfg.url_telemetry(2026) == f"{cfg.BASE}/api/telemetryCompetitor-2026"

    def test_url_pack(self):
        assert cfg.url_pack(2026, 2) == f"{cfg.BASE}/api/pack-2026-2"

    def test_url_millesime(self):
        assert cfg.url_millesime(2026) == f"{cfg.BASE}/api/millesime-2026"


class TestBindNames:
    def test_bind_telemetry(self):
        assert cfg.bind_telemetry(2026) == "telemetryCompetitor-2026"

    def test_bind_pack(self):
        assert cfg.bind_pack(2026, 2) == "pack-2026-2"

    def test_bind_arrival(self):
        assert cfg.bind_arrival(2026, 2) == "rankingTypeArrival-2026-2"

    def test_bind_jerseys_uses_next_stage(self):
        # Jerseys-Bind trägt n+1 (nächste Etappe)
        assert cfg.bind_jerseys(2026, 3) == "rankingTypeJerseys-2026-3"

    def test_bind_checkpoint(self):
        assert cfg.bind_checkpoint(2026, 2) == "checkpoint-2026-2"

    def test_bind_withdrawals(self):
        assert cfg.bind_withdrawals(2026, 2) == "stageWithdrawals-2026-2"


class TestCodes:
    def test_jersey_type_to_code_complete(self):
        # Alle 4 Trikot-Farben müssen gemappt sein
        assert cfg.JERSEY_TYPE_TO_CODE == {
            "pmt": "Y", "pmp": "G", "pmm": "P", "pmj": "W",
        }

    def test_jerseys_human_names(self):
        assert cfg.JERSEYS == {"Y": "Gelb", "G": "Grün", "P": "Punkt", "W": "Weiß"}

    def test_ranking_types(self):
        assert cfg.TYPE_GC == "itg"
        assert cfg.TYPE_STAGE == "ete"
        assert cfg.TYPE_TEAMS == "ite"


class TestEngineConstants:
    def test_earth_radius(self):
        assert cfg.EARTH_RADIUS_KM == 6371.0

    def test_extrapolate_interval_positive(self):
        assert cfg.EXTRAPOLATE_INTERVAL_S > 0

    def test_lerp_alpha_in_range(self):
        assert 0 < cfg.LERP_ALPHA < 1

    def test_throttle_positive(self):
        assert cfg.NOTIFY_THROTTLE_S > 0

    def test_jsonl_flush_every(self):
        assert cfg.JSONL_FLUSH_EVERY_S > 0


class TestEnvDefaults:
    def test_token_default_empty(self, monkeypatch):
        # ENV nicht gesetzt -> Token leer
        monkeypatch.delenv("TDF_TOKEN", raising=False)
        importlib.reload(cfg)
        assert cfg.TOKEN == ""

    def test_token_from_env(self, monkeypatch):
        monkeypatch.setenv("TDF_TOKEN", "fromenv")
        importlib.reload(cfg)
        assert cfg.TOKEN == "fromenv"
        # Aufräumen, damit andere Tests nicht beeinflusst werden
        monkeypatch.delenv("TDF_TOKEN", raising=False)
        importlib.reload(cfg)

    def test_jsonl_path_from_env(self, monkeypatch):
        monkeypatch.setenv("TDF_JSONL_PATH", "/tmp/test.jsonl")
        importlib.reload(cfg)
        assert cfg.JSONL_PATH == "/tmp/test.jsonl"
        monkeypatch.delenv("TDF_JSONL_PATH", raising=False)
        importlib.reload(cfg)

    def test_service_name_default(self, monkeypatch):
        monkeypatch.delenv("TDF_SERVICE_NAME", raising=False)
        importlib.reload(cfg)
        assert cfg.SERVICE_NAME == "tdf-tracker"
