"""Token-Authentifizierung (Middleware).

Schema (kompatibel mit web_ui.py):
  • Query-Parameter  ?k=<TOKEN>
  • Header           Authorization: Bearer <TOKEN>

Ist ``TDF_TOKEN`` leer (Default), ist alles offen (Dev/localhost).
Sonst müssen /api/* und /ws mit Token kommen; / und statische Assets
bleiben frei, damit das Frontend lädt. Das Frontend hängt das Token aus
?k= bzw. localStorage an seine Fetch-/WS-Aufrufe.
"""

from __future__ import annotations

import hmac

from aiohttp import web

from . import config as cfg


def _const_eq(a: str, b: str) -> bool:
    """Timing-sicherer String-Vergleich (verhindert Timing-Angriff auf Token)."""
    return hmac.compare_digest(a.encode(), b.encode())


def is_authorized(request: web.Request) -> bool:
    """True, wenn kein Token konfiguriert ODER Anfrage ein gültiges Token trägt."""
    if not cfg.TOKEN:
        return True
    # 1) ?k=
    if _const_eq(request.query.get("k", "") or "", cfg.TOKEN):
        return True
    # 2) Authorization: Bearer
    auth = request.headers.get("Authorization", "")
    parts = auth.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and _const_eq(parts[1], cfg.TOKEN):
        return True
    return False


def _needs_auth(path: str) -> bool:
    """Geschützte Pfade: /api/*, /ws, /state, /profile. / und statische Assets frei."""
    return (path.startswith("/api/")
            or path == "/ws"
            or path in ("/state", "/profile"))


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if _needs_auth(request.path) and not is_authorized(request):
        return web.json_response(
            {"error": "unauthorized", "hint": "füge ?k=<TOKEN> hinzu oder "
             "setze 'Authorization: Bearer <TOKEN>'"},
            status=401,
        )
    return await handler(request)
