"""Statische Daten vom ASO Race Center (Rider, Teams, Etappen).

Rein synchron-free: alle Fetches laufen über eine aiohttp.ClientSession und
sind daher ``async``. Die Funktionen werden sowohl vom Bootstrap (state.py)
als auch vom Server-Startup verwendet.

Wichtig (Recon 2026-07-05):
  * Rider-Objekt hat KEIN ``code``-Feld. Team kommt über den ``$team``-Join
    auf /api/team-{jahr} (Matching team._id == rider.$team) bzw. als 3-Buchstaben-
    Code im ``_origin`` (z. B. "competitor-2026-UAE") oder ``_virtual``.
  * Etappen kommen unsortiert zurück; filter nach Feld ``stage``, nicht Index.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiohttp

from . import config as cfg


async def _get_json(session: aiohttp.ClientSession, url: str) -> Any:
    async with session.get(url, headers=cfg.JSON_HEADERS,
                           timeout=aiohttp.ClientTimeout(total=cfg.REST_TIMEOUT_S)) as r:
        r.raise_for_status()
        return await r.json()


# --------------------------------------------------------------------------- #
# Rider
# --------------------------------------------------------------------------- #
async def load_riders(session: aiohttp.ClientSession, year: int) -> dict[int, dict[str, Any]]:
    """Liefert Bib -> Rider-Objekt.

    Fügt pro Rider ein aufgelöstes ``team_code`` (z. B. "UAE") und ``team_name``
    hinzu, damit Verbraucher nicht selbst joinen müssen.
    """
    riders_list = await _get_json(session, cfg.url_all_competitors(year))
    # Ein einzelner Team-Fetch; daraus beide Maps ableiten.
    teams_raw = await _get_json(session, cfg.url_teams(year))
    teams: dict[str, str] = {}
    id_to_code: dict[str, str] = {}
    for t in teams_raw:
        code = t.get("code")
        name = t.get("nameShort") or t.get("name")
        if code and name:
            teams[code] = name
        tid = t.get("_id") or ""
        if tid and code:
            id_to_code[tid] = code
            if ":" in tid:
                id_to_code[tid.split(":", 1)[1]] = code

    out: dict[int, dict[str, Any]] = {}
    for rider in riders_list:
        bib = rider.get("bib")
        if not isinstance(bib, int):
            continue
        # Team auflösen: bevorzugt $team-Join, sonst _origin/-virtual-Code.
        code = _resolve_team_code(rider, id_to_code)
        rider["team_code"] = code or ""
        rider["team_name"] = teams.get(code, code or "")
        out[bib] = rider
    return out


def _resolve_team_code(rider: dict[str, Any], id_to_code: dict[str, str]) -> str:
    team_ref = rider.get("$team") or ""
    if team_ref in id_to_code:
        return id_to_code[team_ref]
    origin = rider.get("_origin") or ""
    # "competitor-2026-UAE-123" -> "UAE"
    parts = origin.split("-") if origin else []
    if len(parts) >= 4:
        cand = parts[-2]
        if len(cand) == 3 and cand.isalpha():
            return cand.upper()
    virt = rider.get("_virtual") or ""
    # "UAE-1-POGACAR" -> "UAE"
    if virt:
        head = virt.split("-", 1)[0]
        if len(head) == 3 and head.isalpha():
            return head.upper()
    return ""


# --------------------------------------------------------------------------- #
# Teams
# --------------------------------------------------------------------------- #
async def load_teams(session: aiohttp.ClientSession, year: int) -> dict[str, str]:
    """Liefert Team-Code (3 Buchstaben) -> nameShort."""
    teams = await _get_json(session, cfg.url_teams(year))
    out: dict[str, str] = {}
    for t in teams:
        code = t.get("code")
        name = t.get("nameShort") or t.get("name")
        if code and name:
            out[code] = name
    return out


# --------------------------------------------------------------------------- #
# Etappen
# --------------------------------------------------------------------------- #
async def load_stages(session: aiohttp.ClientSession, year: int) -> list[dict[str, Any]]:
    return await _get_json(session, cfg.url_stages(year))


def today_stage_number(stages: list[dict[str, Any]]) -> int | None:
    """Heutige Etappe anhand Feld ``date`` (ISO-8601 mit Offset).

    Die Datum-Strings kommen als "2026-07-06T00:00:00+02:00" zurück. Wir
    vergleichen nur das Datum (lokaler Tag des Servers).
    """
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    for s in stages:
        d = str(s.get("date", ""))
        if d[:10] == today:
            stage = s.get("stage")
            if isinstance(stage, int):
                return stage
    return None


def find_stage(stages: list[dict[str, Any]], stage_no: int) -> dict[str, Any] | None:
    for s in stages:
        if s.get("stage") == stage_no:
            return s
    return None


def stage_label(stage: dict[str, Any]) -> str:
    dep = (stage.get("departureCity") or {}).get("label", "?")
    arr = (stage.get("arrivalCity") or {}).get("label", "?")
    n = stage.get("stage", "?")
    length = stage.get("lengthDisplay") or stage.get("length") or "?"
    return f"Etappe {n}: {dep} → {arr}  ({length} km)"
