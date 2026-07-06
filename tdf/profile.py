"""Best-Effort: Höhenprofil aus der offiziellen /profils/-CSV.

Die CSV existiert unter /profils/<jahr>/profile-<NN>-<hash>.csv, aber die URL
enthält einen Hash, der auf der Stage-Config nicht zuverlässig zu finden ist.
Wir versuchen mehrere Strategien, und falls alle fehlschlagen, geben wir
None zurück – das Frontend rendert dann nur die checkpoint-Wegpunkte.

Schema der CSV (semikolongetrennt, übernommen aus mullummer/racecenter):
  col 0 = lat
  col 1 = lon
  col 2 = altitude (m)
  col 7 = kmdone (km ab Start)
  col 8 = kmtogo (km bis Ziel)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import aiohttp

from . import config as cfg

log = logging.getLogger("tdf.profile")


@dataclass
class ProfilePoint:
    lat: float
    lon: float
    alt_m: float
    km_done: float
    km_to_go: float


@dataclass
class Profile:
    points: list[ProfilePoint]
    source: str  # URL oder "fallback"

    @property
    def total_km(self) -> float:
        return self.points[-1].km_done if self.points else 0.0


# --------------------------------------------------------------------------- #
# CSV-Parser
# --------------------------------------------------------------------------- #
def parse_profile_csv(text: str) -> list[ProfilePoint]:
    """Parst eine ASO-Profil-CSV in ProfilePoints.

    Tolerant gegenüber Varianten: Header-Zeile wird übersprungen, Spaltenzahlen
    variieren (mind. 9). Fehlende altitude → 0.
    """
    out: list[ProfilePoint] = []
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line or ";" not in line:
            continue
        cols = line.split(";")
        if len(cols) < 9:
            continue
        # Erste Zeile oft Header – überspringen, wenn lat nicht numeric.
        try:
            lat = float(cols[0])
            lon = float(cols[1])
        except ValueError:
            continue
        alt = _safe_float(cols[2]) if len(cols) > 2 else 0.0
        km_done = _safe_float(cols[7]) if len(cols) > 7 else 0.0
        km_to_go = _safe_float(cols[8]) if len(cols) > 8 else 0.0
        out.append(ProfilePoint(lat, lon, alt, km_done, km_to_go))
    return out


def _safe_float(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- #
# URL-Auflösung (Best-Effort)
# --------------------------------------------------------------------------- #
async def _resolve_profile_url(session: aiohttp.ClientSession, year: int,
                               stage: int) -> str | None:
    """Versucht, die Profil-CSV-URL für eine Etappe zu finden.

    Strategien (alle robust gegenüber Veränderungen, ohne Hash zu erraten):
      1) millesime-Config durchsuchen (enthält manchmal Stage-Referenzen).
      2) Stage-Objekt auf Hinweise scannen (profile, route, map – falls ASO
         später doch Geometrie-Felder nachreicht).
      3) Auflisten von /profils/<jahr>/ via Index (manche Server erlauben das).
    Wir brechen beim ersten Treffer ab.
    """
    # 1) millesime-Config
    try:
        async with session.get(cfg.url_millesime(year), headers=cfg.JSON_HEADERS,
                               timeout=aiohttp.ClientTimeout(total=cfg.REST_TIMEOUT_S)) as r:
            if r.status == 200:
                data = await r.json()
                url = _scan_for_profile_url(data, year, stage)
                if url:
                    return url
    except (aiohttp.ClientError, ValueError) as e:
        log.debug("millesime-Lookup fehlgeschlagen: %s", e)

    # 2) Im Stage-Objekt – derzeit (2026-07-05) ohne Geometrie-Felder, aber
    # wir scannen vorsorglich alle String-Werte auf profils-URLs.
    try:
        async with session.get(cfg.url_stages(year), headers=cfg.JSON_HEADERS,
                               timeout=aiohttp.ClientTimeout(total=cfg.REST_TIMEOUT_S)) as r:
            if r.status == 200:
                stages = await r.json()
                for s in stages:
                    if s.get("stage") == stage:
                        url = _scan_for_profile_url(s, year, stage)
                        if url:
                            return url
                        break
    except (aiohttp.ClientError, ValueError) as e:
        log.debug("stage-Lookup fehlgeschlagen: %s", e)

    return None


def _scan_for_profile_url(obj: Any, year: int, stage: int) -> str | None:
    """Rekursiv nach einem String suchen, der wie eine profils-URL aussieht."""
    pat = re.compile(rf"/profils/{year}/profile[^\"']*\.csv", re.IGNORECASE)
    found: list[str] = []

    def walk(o: Any) -> None:
        if isinstance(o, str):
            m = pat.search(o)
            if m:
                found.append(o[m.start():])
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    if found:
        path = found[0]
        return path if path.startswith("http") else f"{cfg.BASE}{path}"
    return None


# --------------------------------------------------------------------------- #
# Haupt-Einstieg
# --------------------------------------------------------------------------- #
async def try_load_profile(session: aiohttp.ClientSession, year: int,
                           stage: int) -> Profile | None:
    """Versucht das Profil zu laden. Liefert None bei Misserfolg (stiller Fallback)."""
    url = await _resolve_profile_url(session, year, stage)
    if not url:
        log.info("Keine Profil-CSV für Etappe %d gefunden – nutze Wegpunkt-Fallback.", stage)
        return None
    try:
        async with session.get(url, headers=cfg.CSV_HEADERS,
                               timeout=aiohttp.ClientTimeout(total=cfg.REST_TIMEOUT_S)) as r:
            if r.status != 200:
                log.info("Profil-CSV %s -> HTTP %d", url, r.status)
                return None
            text = await r.text()
        pts = parse_profile_csv(text)
        if len(pts) < 5:
            log.info("Profil-CSV zu dünn (%d Punkte) – ignoriert.", len(pts))
            return None
        log.info("Profil geladen: %d Punkte aus %s", len(pts), url)
        return Profile(points=pts, source=url)
    except aiohttp.ClientError as e:
        log.info("Profil-Laden fehlgeschlagen (%s) – Fallback.", e)
        return None


# --------------------------------------------------------------------------- #
# Serialisierung
# --------------------------------------------------------------------------- #
def to_json(profile: Profile, *, max_points: int = 400) -> dict:
    """Kompakte Repräsentation fürs Frontend. Dünnt auf max_points aus."""
    pts = profile.points
    if len(pts) > max_points:
        step = max(1, len(pts) // max_points)
        pts = pts[::step]
    return {
        "source": profile.source,
        "total_km": profile.total_km,
        "points": [{"km": p.km_done, "alt": p.alt_m, "lat": p.lat, "lon": p.lon}
                   for p in pts],
    }
