"""Tests für tdf/bootstrap.py — REST-Bootstrap.

Nutzt einen lokalen Mock-Server, der die Fixture-JSON pro Route ausliefert.
Zusätzlich LIVE-Tests gegen echtes ASO (Etappe 2), skip wenn offline.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from tdf import bootstrap as bs
from tdf import config as cfg
from tdf import state as st


FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
# Mock-Server Fixture: jede Route -> ihre Fixture-Datei
# --------------------------------------------------------------------------- #
def _make_handler(fixture_name: str):
    async def handler(request: web.Request):
        data = json.loads((FIXTURES / f"{fixture_name}.json").read_text())
        return web.json_response(data)
    return handler


@pytest.fixture
async def aso_server():
    """Lokaler Server, der alle bekannten ASO-Pfade auf Fixtures mappt."""
    app = web.Application()
    routes = [
        ("/api/allCompetitors-2026", "allCompetitors-2026"),
        ("/api/team-2026", "team-2026"),
        ("/api/stage-2026", "stage-2026"),
        ("/api/rankingTypeArrival-2026-2", "rankingTypeArrival-2026-2"),
        ("/api/rankingTypeJerseys-2026-3", "rankingTypeJerseys-2026-3"),
        ("/api/telemetryCompetitor-2026", "telemetryCompetitor-2026"),
        ("/api/pack-2026-2", "pack-2026-2"),
    ]
    for path, fixture in routes:
        app.router.add_get(path, _make_handler(fixture))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = list(site._server.sockets)[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    yield app, base_url
    await runner.cleanup()


def patched_get_json(base_url: str):
    """Erzeugt eine _get_json-Variante, die auf base_url statt cfg.BASE geht."""
    async def _patched(session, url: str):
        local_url = url.replace(cfg.BASE, base_url)
        async with session.get(local_url, headers=cfg.JSON_HEADERS,
                               timeout=aiohttp.ClientTimeout(total=5)) as r:
            r.raise_for_status()
            return await r.json()
    return _patched


def has_internet() -> bool:
    try:
        socket.gethostbyname("racecenter.letour.fr")
        return True
    except OSError:
        return False


needs_live_aso = pytest.mark.skipif(not has_internet(),
                                    reason="ASO/Internet nicht erreichbar")


# --------------------------------------------------------------------------- #
# fetch_current_gc (offline via Mock-Server)
# --------------------------------------------------------------------------- #
class TestFetchCurrentGcOffline:
    async def test_stage2_returns_183_riders(self, aso_server, monkeypatch):
        _, base = aso_server
        monkeypatch.setattr(bs, "_get_json", patched_get_json(base))
        async with aiohttp.ClientSession() as s:
            gc = await bs.fetch_current_gc(s, 2026, 2)
        assert len(gc) == 183
        assert gc[0].bib == 11
        assert gc[0].position == 1

    async def test_times_in_seconds_not_ms(self, aso_server, monkeypatch):
        # ⚠️ Kritisch: ASO liefert ms, Code muss /1000
        _, base = aso_server
        monkeypatch.setattr(bs, "_get_json", patched_get_json(base))
        async with aiohttp.ClientSession() as s:
            gc = await bs.fetch_current_gc(s, 2026, 2)
        # Vingegaard absolute = 14508000 ms = 14508 s
        assert gc[0].absolute_seconds == 14508
        # Pogacar +6s, nicht +6000
        pogacar = next(e for e in gc if e.bib == 1)
        assert pogacar.relative_seconds == 6
        assert pogacar.bonus_seconds == 6

    async def test_evenepoel_bonus(self, aso_server, monkeypatch):
        # Evenepoel (bib 21) hatte +15s und 4s Bonus laut Handoff
        _, base = aso_server
        monkeypatch.setattr(bs, "_get_json", patched_get_json(base))
        async with aiohttp.ClientSession() as s:
            gc = await bs.fetch_current_gc(s, 2026, 2)
        remco = next(e for e in gc if e.bib == 21)
        assert remco.relative_seconds == 15
        assert remco.bonus_seconds == 4

    async def test_204_raises_for_unstarted_stage(self, aso_server, monkeypatch):
        # Stage 9 nicht im Mock -> 404 vom Server -> ClientError
        _, base = aso_server
        monkeypatch.setattr(bs, "_get_json", patched_get_json(base))
        async with aiohttp.ClientSession() as s:
            with pytest.raises((aiohttp.ClientError, Exception)):
                await bs.fetch_current_gc(s, 2026, 9)


# --------------------------------------------------------------------------- #
# fetch_jerseys
# --------------------------------------------------------------------------- #
class TestFetchJerseysOffline:
    async def test_parses_at_least_one_jersey(self, aso_server, monkeypatch):
        _, base = aso_server
        monkeypatch.setattr(bs, "_get_json", patched_get_json(base))
        async with aiohttp.ClientSession() as s:
            jerseys = await bs.fetch_jerseys(s, 2026, 3)
        assert isinstance(jerseys, dict)
        assert len(jerseys) >= 1

    async def test_jersey_codes_valid(self, aso_server, monkeypatch):
        _, base = aso_server
        monkeypatch.setattr(bs, "_get_json", patched_get_json(base))
        async with aiohttp.ClientSession() as s:
            jerseys = await bs.fetch_jerseys(s, 2026, 3)
        for code in jerseys:
            assert code in ("Y", "G", "P", "W")
            assert isinstance(jerseys[code], int)


# --------------------------------------------------------------------------- #
# fetch_last_telemetry
# --------------------------------------------------------------------------- #
class TestFetchTelemetryOffline:
    async def test_returns_snapshot(self, aso_server, monkeypatch):
        _, base = aso_server
        monkeypatch.setattr(bs, "_get_json", patched_get_json(base))
        async with aiohttp.ClientSession() as s:
            snap = await bs.fetch_last_telemetry(s, 2026)
        assert snap is not None
        assert "Riders" in snap or "RaceStatus" in snap


# --------------------------------------------------------------------------- #
# fetch_pack
# --------------------------------------------------------------------------- #
class TestFetchPackOffline:
    async def test_returns_5_groups(self, aso_server, monkeypatch):
        _, base = aso_server
        monkeypatch.setattr(bs, "_get_json", patched_get_json(base))
        async with aiohttp.ClientSession() as s:
            groups = await bs.fetch_pack(s, 2026, 2)
        assert len(groups) == 5
        assert all(isinstance(g, st.GroupInfo) for g in groups)


# --------------------------------------------------------------------------- #
# bootstrap_state (Integration)
# --------------------------------------------------------------------------- #
class TestBootstrapStateOffline:
    async def test_full_bootstrap_populates_state(self, aso_server, monkeypatch):
        _, base = aso_server
        monkeypatch.setattr(bs, "_get_json", patched_get_json(base))
        s = st.State(year=2026, stage=2)
        async with aiohttp.ClientSession() as session:
            await bs.bootstrap_state(s, session, year=2026, stage=2,
                                     next_stage=3)
        assert len(s.base_gc) == 183
        assert len(s.virtual_gc) >= 1
        assert len(s.groups) == 5
        assert s.base_gc[0].bib == 11

    async def test_bootstrap_resilient_to_errors(self, aso_server, monkeypatch):
        # Wenn eine URL 404 liefert, sollte bootstrap_state nicht crashen,
        # sondern leer weiterlaufen. Patchen _get_json so, dass immer 404.
        async def failing_get(session, url):
            raise aiohttp.ClientError("simulated")
        monkeypatch.setattr(bs, "_get_json", failing_get)
        s = st.State(year=2026, stage=2)
        async with aiohttp.ClientSession() as session:
            await bs.bootstrap_state(s, session, year=2026, stage=2,
                                     next_stage=3)
        # Alle Felder leer, aber kein Crash
        assert s.base_gc == []
        assert s.virtual_gc == []


# --------------------------------------------------------------------------- #
# LIVE-Tests gegen echtes ASO
# --------------------------------------------------------------------------- #
@needs_live_aso
class TestBootstrapLiveASO:
    async def test_live_gc_stage2(self):
        async with aiohttp.ClientSession() as s:
            gc = await bs.fetch_current_gc(s, 2026, 2)
        assert len(gc) >= 150
        assert gc[0].position == 1

    async def test_live_jerseys(self):
        async with aiohttp.ClientSession() as s:
            jerseys = await bs.fetch_jerseys(s, 2026, 3)
        assert isinstance(jerseys, dict)
        assert len(jerseys) >= 1

    async def test_live_times_correct(self):
        # Verifiziert dass ms->s Conversion auch live stimmt
        async with aiohttp.ClientSession() as s:
            gc = await bs.fetch_current_gc(s, 2026, 2)
        if gc[0].absolute_seconds:
            # Spitze absolut sollte ~4h sein (14000-16000s), nicht ~4000h
            assert 10000 < gc[0].absolute_seconds < 100000
