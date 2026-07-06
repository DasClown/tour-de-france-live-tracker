"""Tests für tdf/auth.py — Token-Authentifizierung.

Testet alle 4 Auth-Pfade: kein Token konfiguriert, ?k=, Bearer, falsches
Token. Stellt sicher, dass die Pfad-Logik (/api/* vs /) korrekt ist.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web

from tdf import auth


def make_request(path: str = "/", *, query_k: str | None = None,
                 authorization: str | None = None):
    """Baut eine Mock-Request, die für is_authorized() reicht.

    is_authorized braucht nur request.query.get('k') und request.headers.get().
    Ein MagicMock ist hier robuster als eine echte web.Request (die eine
    laufende App/Loop voraussetzt).
    """
    req = MagicMock()
    req.path = path
    # Query als dict-ähnliches Objekt
    query = {}
    if query_k is not None:
        query["k"] = query_k
    req.query = query  # dict hat .get()
    # Headers als dict
    headers = {}
    if authorization is not None:
        headers["Authorization"] = authorization
    req.headers = headers
    return req


class TestNeedsAuth:
    def test_api_paths_need_auth(self):
        for p in ["/api/health", "/api/control", "/api/state", "/api/gap-chart",
                  "/api/classification", "/api/map-data", "/api/trail"]:
            assert auth._needs_auth(p) is True, f"{p} sollte Auth brauchen"

    def test_ws_needs_auth(self):
        assert auth._needs_auth("/ws") is True

    def test_state_and_profile_need_auth(self):
        assert auth._needs_auth("/state") is True
        assert auth._needs_auth("/profile") is True

    def test_root_is_open(self):
        assert auth._needs_auth("/") is False

    def test_static_assets_open(self):
        for p in ["/index.html", "/app.js", "/style.css", "/validation.html"]:
            assert auth._needs_auth(p) is False, f"{p} sollte offen sein"


class TestIsAuthorized:
    def test_no_token_configured_allows_all(self):
        with patch.object(auth.cfg, "TOKEN", ""):
            req = make_request("/api/health")
            assert auth.is_authorized(req) is True

    def test_query_param_valid(self):
        with patch.object(auth.cfg, "TOKEN", "secret123"):
            req = make_request("/api/health", query_k="secret123")
            assert auth.is_authorized(req) is True

    def test_query_param_invalid(self):
        with patch.object(auth.cfg, "TOKEN", "secret123"):
            req = make_request("/api/health", query_k="wrong")
            assert auth.is_authorized(req) is False

    def test_query_param_missing(self):
        with patch.object(auth.cfg, "TOKEN", "secret123"):
            req = make_request("/api/health")
            assert auth.is_authorized(req) is False

    def test_bearer_token_valid(self):
        with patch.object(auth.cfg, "TOKEN", "secret123"):
            req = make_request("/api/health", authorization="Bearer secret123")
            assert auth.is_authorized(req) is True

    def test_bearer_case_insensitive_scheme(self):
        with patch.object(auth.cfg, "TOKEN", "secret123"):
            req = make_request("/api/health", authorization="bearer secret123")
            assert auth.is_authorized(req) is True

    def test_bearer_invalid_token(self):
        with patch.object(auth.cfg, "TOKEN", "secret123"):
            req = make_request("/api/health", authorization="Bearer wrong")
            assert auth.is_authorized(req) is False

    def test_authorization_wrong_scheme(self):
        with patch.object(auth.cfg, "TOKEN", "secret123"):
            req = make_request("/api/health", authorization="Basic secret123")
            assert auth.is_authorized(req) is False

    def test_const_eq_uses_hmac_compare(self):
        # Timing-sicherer Vergleich darf nicht gleiten
        with patch.object(auth.cfg, "TOKEN", "abcdef"):
            assert auth._const_eq("abcdef", "abcdef") is True
            assert auth._const_eq("abcdef", "abcdefg") is False
            assert auth._const_eq("", "") is True
