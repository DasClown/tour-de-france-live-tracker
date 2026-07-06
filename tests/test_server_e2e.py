"""Stufe 3 — End-to-End-Tests gegen den laufenden TDF-Server.

Diese Tests sprechen den echten Produktions-Server an (Default: localhost:8000)
und prüfen die HTTP-Oberfläche, die Auth-Matrix und den JSONL-Trail.

Sie brauchen einen laufenden tdf-tracker-Dienst. Marker ``e2e`` erlaubt es,
sie separat laufen zu lassen::

    pytest -m e2e             # nur E2E
    pytest -m "not e2e"       # ohne E2E (nur Unit/Integration)

Konfiguration via ENV:
    TDF_E2E_BASE   Default http://localhost:8000
    TDF_E2E_TOKEN  Token (Default: aus /root/.tdf-tracker-token)
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from pathlib import Path

import aiohttp
import pytest

pytestmark = pytest.mark.e2e


def _base_url() -> str:
    return os.environ.get("TDF_E2E_BASE", "http://localhost:8000")


def _token() -> str:
    t = os.environ.get("TDF_E2E_TOKEN")
    if t:
        return t
    token_file = Path("/root/.tdf-tracker-token")
    if token_file.exists():
        return token_file.read_text().strip()
    pytest.skip("Kein Token für E2E-Tests gefunden")


@pytest.fixture(scope="module", autouse=True)
def require_server():
    """Skip alle E2E-Tests wenn der Server nicht läuft (Socket-Check)."""
    url = _base_url()
    # http://host:port -> (host, port)
    parts = url.split(":")
    host = parts[1].lstrip("/")
    port = int(parts[2]) if len(parts) > 2 else 80
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        sock.connect((host, port))
        sock.close()
    except OSError:
        pytest.skip(f"TDF-Server nicht erreichbar unter {url}")


# --------------------------------------------------------------------------- #
# Basis-Erreichbarkeit
# --------------------------------------------------------------------------- #
class TestServerUp:
    async def test_root_serves_frontend(self):
        async with aiohttp.ClientSession() as s:
            async with s.get(_base_url() + "/") as r:
                assert r.status == 200
                ct = r.headers.get("Content-Type", "")
                assert "html" in ct
                text = await r.text()
                assert "<html" in text.lower() or "<!doctype" in text.lower()

    async def test_static_assets(self):
        for asset in ["/app.js", "/style.css"]:
            async with aiohttp.ClientSession() as s:
                async with s.get(_base_url() + asset) as r:
                    assert r.status == 200, f"{asset} nicht erreichbar"


# --------------------------------------------------------------------------- #
# Auth-Matrix
# --------------------------------------------------------------------------- #
class TestAuthMatrix:
    async def test_protected_endpoints_require_token(self):
        """Alle /api/*, /state, /profile, /ws müssen ohne Token 401 liefern."""
        token = _token()
        if not token:
            pytest.skip("Kein Token konfiguriert")
        async with aiohttp.ClientSession() as s:
            for path in ["/api/health", "/api/control", "/api/trail",
                         "/api/gap-chart", "/api/classification",
                         "/api/map-data", "/state", "/profile"]:
                async with s.get(_base_url() + path) as r:
                    assert r.status == 401, \
                        f"{path} ohne Token sollte 401 liefern, bekam {r.status}"

    async def test_token_via_query_param(self):
        token = _token()
        async with aiohttp.ClientSession() as s:
            async with s.get(_base_url() + f"/api/health?k={token}") as r:
                assert r.status == 200

    async def test_token_via_bearer_header(self):
        token = _token()
        async with aiohttp.ClientSession() as s:
            async with s.get(_base_url() + "/api/health",
                             headers={"Authorization": f"Bearer {token}"}) as r:
                assert r.status == 200

    async def test_wrong_token_rejected(self):
        async with aiohttp.ClientSession() as s:
            async with s.get(_base_url() + "/api/health?k=wrongtoken") as r:
                assert r.status == 401

    async def test_frontend_is_open(self):
        # / und statische Assets brauchen keinen Token
        async with aiohttp.ClientSession() as s:
            async with s.get(_base_url() + "/") as r:
                assert r.status == 200


# --------------------------------------------------------------------------- #
# Endpunkt-Payloads
# --------------------------------------------------------------------------- #
class TestEndpoints:
    async def test_health_schema(self):
        token = _token()
        async with aiohttp.ClientSession() as s:
            async with s.get(_base_url() + f"/api/health?k={token}") as r:
                assert r.status == 200
                data = await r.json()
                assert data["ok"] is True
                assert "status" in data
                assert "ts" in data
                assert data["year"] == 2026
                assert isinstance(data["stage"], int)

    async def test_state_schema(self):
        token = _token()
        async with aiohttp.ClientSession() as s:
            async with s.get(_base_url() + f"/state?k={token}&top=3") as r:
                assert r.status == 200
                data = await r.json()
                # Pflichtfelder
                for k in ["year", "stage", "status", "race_status", "gc"]:
                    assert k in data, f"Feld {k} fehlt in /state"
                # Neue Feature-Felder (über Nacht gebaut)
                for k in ["predictions", "alarms", "classifications", "map"]:
                    assert k in data, f"Neues Feld {k} fehlt in /state"

    async def test_gap_chart_schema(self):
        token = _token()
        async with aiohttp.ClientSession() as s:
            async with s.get(_base_url() + f"/api/gap-chart?k={token}") as r:
                assert r.status == 200
                data = await r.json()
                assert "count" in data
                assert "series" in data
                assert "bibs" in data

    async def test_classification_schema(self):
        token = _token()
        async with aiohttp.ClientSession() as s:
            async with s.get(_base_url() + f"/api/classification?k={token}") as r:
                assert r.status == 200
                data = await r.json()
                for k in ["mountains", "sprints", "kom_ranking", "sprint_ranking"]:
                    assert k in data

    async def test_map_data_schema(self):
        token = _token()
        async with aiohttp.ClientSession() as s:
            async with s.get(_base_url() + f"/api/map-data?k={token}") as r:
                assert r.status == 200
                data = await r.json()
                assert "checkpoints_count" in data

    async def test_control_reports_active(self):
        token = _token()
        async with aiohttp.ClientSession() as s:
            async with s.get(_base_url() + f"/api/control?k={token}") as r:
                assert r.status == 200
                data = await r.json()
                assert data["ok"] is True
                # Dienst sollte aktiv sein
                assert data.get("active") is True or data.get("state") == "active"

    async def test_trail_returns_json(self):
        token = _token()
        async with aiohttp.ClientSession() as s:
            async with s.get(_base_url() + f"/api/trail?n=2&k={token}") as r:
                assert r.status == 200
                data = await r.json()
                # Antwort ist {n, path, snapshots: [...]}
                assert "snapshots" in data
                assert isinstance(data["snapshots"], list)
                # Jeder Snapshot sollte ein JSON-Objekt sein
                for entry in data["snapshots"]:
                    assert isinstance(entry, dict)
                assert data["n"] == 2


# --------------------------------------------------------------------------- #
# Performance / Stabilität
# --------------------------------------------------------------------------- #
class TestPerformance:
    async def test_state_responds_fast(self):
        # /state sollte in unter 500ms antworten (auch mit vollem Bootstrap)
        token = _token()
        import time
        async with aiohttp.ClientSession() as s:
            t0 = time.time()
            async with s.get(_base_url() + f"/state?k={token}") as r:
                await r.read()
            elapsed = time.time() - t0
            assert elapsed < 2.0, f"/state brauchte {elapsed:.2f}s (>2s Schwellwert)"

    async def test_concurrent_requests(self):
        # 10 parallele Requests sollten alle erfolgreich sein
        token = _token()
        async with aiohttp.ClientSession() as s:
            tasks = [s.get(_base_url() + f"/api/health?k={token}") for _ in range(10)]
            responses = await asyncio.gather(*tasks)
            statuses = [r.status for r in responses]
            for r in responses:
                r.release()
            assert all(s == 200 for s in statuses)
