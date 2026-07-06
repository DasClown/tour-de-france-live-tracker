"""Tests für tdf/static.py — Statische Daten (Rider, Teams, Etappen).

Nutzt einen lokalen Mock-Server (statt aioresponses, das mit aiohttp 3.14
inkompatibel ist).
"""
from __future__ import annotations

import json
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from tdf import config as cfg
from tdf import static


FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
# Mock-Server Fixture
# --------------------------------------------------------------------------- #
@pytest.fixture
async def aso_server():
    app = web.Application()

    async def fixture_handler(fixture_name: str):
        async def h(request):
            data = json.loads((FIXTURES / f"{fixture_name}.json").read_text())
            return web.json_response(data)
        return h

    app.router.add_get("/api/allCompetitors-2026",
                       await fixture_handler("allCompetitors-2026"))
    app.router.add_get("/api/team-2026",
                       await fixture_handler("team-2026"))
    app.router.add_get("/api/stage-2026",
                       await fixture_handler("stage-2026"))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = list(site._server.sockets)[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    yield app, base
    await runner.cleanup()


def patched_get_json(base_url: str):
    async def _p(session, url: str):
        local = url.replace(cfg.BASE, base_url)
        async with session.get(local, headers=cfg.JSON_HEADERS,
                               timeout=aiohttp.ClientTimeout(total=5)) as r:
            r.raise_for_status()
            return await r.json()
    return _p


# --------------------------------------------------------------------------- #
# _resolve_team_code (rein synchron, kein Mock nötig)
# --------------------------------------------------------------------------- #
class TestResolveTeamCode:
    def test_team_join_match(self):
        rider = {"$team": "team-2026-UAE"}
        assert static._resolve_team_code(rider, {"team-2026-UAE": "UAE"}) == "UAE"

    def test_origin_with_3letter_code(self):
        rider = {"_origin": "competitor-2026-UAE-123"}
        assert static._resolve_team_code(rider, {}) == "UAE"

    def test_virtual_with_code(self):
        rider = {"_virtual": "UAE-1-POGACAR"}
        assert static._resolve_team_code(rider, {}) == "UAE"

    def test_no_team_info(self):
        assert static._resolve_team_code({}, {}) == ""

    def test_origin_too_short(self):
        rider = {"_origin": "x-y"}
        assert static._resolve_team_code(rider, {}) == ""

    def test_team_join_takes_precedence(self):
        rider = {"$team": "team-2026-UAE", "_origin": "competitor-2026-INE-1"}
        assert static._resolve_team_code(rider, {"team-2026-UAE": "UAE"}) == "UAE"

    def test_real_fixture_data(self, aso_all_competitors):
        # Gegen echte Rider aus dem Fixture prüfen
        # Finde einen Rider mit $team oder _origin
        for r in aso_all_competitors[:50]:
            if isinstance(r, dict) and r.get("$team"):
                code = static._resolve_team_code(r, {})
                assert isinstance(code, str)
                return
        pytest.skip("Kein Rider mit $team in Fixture")


# --------------------------------------------------------------------------- #
# load_riders (mit Mock-Server)
# --------------------------------------------------------------------------- #
class TestLoadRiders:
    async def test_loads_real_fixture(self, aso_server, monkeypatch):
        _, base = aso_server
        monkeypatch.setattr(static, "_get_json", patched_get_json(base))
        async with aiohttp.ClientSession() as s:
            riders = await static.load_riders(s, 2026)
        assert len(riders) >= 150  # ~184 Starter
        # Jeder Rider hat team_code und team_name
        for bib, r in list(riders.items())[:5]:
            assert "team_code" in r
            assert "team_name" in r
            assert isinstance(r["team_code"], str)

    async def test_bibs_are_ints(self, aso_server, monkeypatch):
        _, base = aso_server
        monkeypatch.setattr(static, "_get_json", patched_get_json(base))
        async with aiohttp.ClientSession() as s:
            riders = await static.load_riders(s, 2026)
        assert all(isinstance(b, int) for b in riders.keys())

    async def test_vingegaard_present(self, aso_server, monkeypatch):
        _, base = aso_server
        monkeypatch.setattr(static, "_get_json", patched_get_json(base))
        async with aiohttp.ClientSession() as s:
            riders = await static.load_riders(s, 2026)
        assert 11 in riders  # Vingegaards bib


# --------------------------------------------------------------------------- #
# load_teams
# --------------------------------------------------------------------------- #
class TestLoadTeams:
    async def test_returns_code_to_name(self, aso_server, monkeypatch):
        _, base = aso_server
        monkeypatch.setattr(static, "_get_json", patched_get_json(base))
        async with aiohttp.ClientSession() as s:
            teams = await static.load_teams(s, 2026)
        assert len(teams) >= 20  # ~22 Teams
        assert all(isinstance(k, str) and len(k) == 3 for k in teams.keys())
        assert all(isinstance(v, str) for v in teams.values())


# --------------------------------------------------------------------------- #
# Etappen-Helper (rein synchron)
# --------------------------------------------------------------------------- #
class TestStages:
    def test_find_stage_by_number(self):
        stages = [{"stage": 1}, {"stage": 2, "departureCity": {"label": "X"}}]
        s = static.find_stage(stages, 2)
        assert s is not None
        assert s["stage"] == 2

    def test_find_stage_missing(self):
        assert static.find_stage([{"stage": 1}], 99) is None

    def test_stage_label_format(self):
        stage = {
            "stage": 5,
            "departureCity": {"label": "Paris"},
            "arrivalCity": {"label": "Lyon"},
            "lengthDisplay": "200",
        }
        label = static.stage_label(stage)
        assert "Paris" in label
        assert "Lyon" in label
        assert "Etappe 5" in label
        assert "200" in label

    def test_stage_label_missing_cities(self):
        stage = {"stage": 1}
        label = static.stage_label(stage)
        assert "?" in label

    def test_load_stages_returns_list(self, aso_server, monkeypatch):
        # kann nicht async in synchrone Klasse; in eigener Klasse unten
        pass


class TestLoadStagesAsync:
    async def test_load_stages_returns_list(self, aso_server, monkeypatch):
        _, base = aso_server
        monkeypatch.setattr(static, "_get_json", patched_get_json(base))
        async with aiohttp.ClientSession() as s:
            stages = await static.load_stages(s, 2026)
        assert isinstance(stages, list)
        assert len(stages) == 21  # 21 Etappen 2026

    async def test_stages_contain_stage_numbers(self, aso_server, monkeypatch):
        _, base = aso_server
        monkeypatch.setattr(static, "_get_json", patched_get_json(base))
        async with aiohttp.ClientSession() as s:
            stages = await static.load_stages(s, 2026)
        stage_nums = [s.get("stage") for s in stages if isinstance(s, dict)]
        assert 1 in stage_nums
        assert 21 in stage_nums
