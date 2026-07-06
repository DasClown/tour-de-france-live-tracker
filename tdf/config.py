"""Zentrale Konstanten für den TDF-Tracker.

Alle URLs, Header, Bind-Namen, Typ-Codes und Trikot-Mappings leben hier.
Verifiziert am 2026-07-05 gegen das live ASO Race Center.
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------- #
# Basis / HTTP
# --------------------------------------------------------------------------- #
BASE = "https://racecenter.letour.fr"
LIVE_STREAM = f"{BASE}/live-stream"
PROFILS = f"{BASE}/profils"  # /profils/<jahr>/profile-<NN>-<hash>.csv

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

JSON_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json",
    "Referer": f"{BASE}/en/",
}

SSE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/event-stream",
    "Referer": f"{BASE}/en/",
    "Origin": BASE,
    "Cache-Control": "no-cache",
}

CSV_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/csv,*/*;q=0.9",
    "Referer": f"{BASE}/en/",
}

# --------------------------------------------------------------------------- #
# REST-Endpunkte (verifiziert 2026-07-05)
# --------------------------------------------------------------------------- #
#  /api/allCompetitors-<jahr>                 -> Starter (Bib -> Rider-Objekt)
#  /api/team-<jahr>                           -> Teams (code -> nameShort)
#  /api/stage-<jahr>                          -> Etappen (Stage-Objekte)
#  /api/rankingTypeArrival-<jahr>-<etappe>    -> Etappen-/Gesamt-Rangliste
#                                                 Array von {checkpoint,type,rankings[]}
#                                                 filter type=="itg" für GC.
#                                                 Zeiten in MS!
#  /api/rankingTypeJerseys-<jahr>-<n>         -> Trikotträger nächste Etappe
#                                                 type=pmt(Y)/pmp(G)/pmm(P)/pmj(W)
#  /api/telemetryCompetitor-<jahr>            -> 1-Eintrag-Array, letzte Telemetrie
#  /api/pack-<jahr>-<etappe>                  -> "dirty" Array; filter _bind=="pack-..."
#  /api/millesime                            -> Renn-Config (Profile-Hash, etc.)
# --------------------------------------------------------------------------- #


def url_all_competitors(year: int) -> str:
    return f"{BASE}/api/allCompetitors-{year}"


def url_teams(year: int) -> str:
    return f"{BASE}/api/team-{year}"


def url_stages(year: int) -> str:
    return f"{BASE}/api/stage-{year}"


def url_ranking_arrival(year: int, stage: int) -> str:
    return f"{BASE}/api/rankingTypeArrival-{year}-{stage}"


def url_ranking_jerseys(year: int, jersey_stage_n: int) -> str:
    return f"{BASE}/api/rankingTypeJerseys-{year}-{jersey_stage_n}"


def url_telemetry(year: int) -> str:
    return f"{BASE}/api/telemetryCompetitor-{year}"


def url_pack(year: int, stage: int) -> str:
    return f"{BASE}/api/pack-{year}-{stage}"


def url_millesime(year: int) -> str:
    return f"{BASE}/api/millesime-{year}"


# --------------------------------------------------------------------------- #
# SSE-Bind-Namen
# --------------------------------------------------------------------------- #
def bind_telemetry(year: int) -> str:
    return f"telemetryCompetitor-{year}"


def bind_pack(year: int, stage: int) -> str:
    return f"pack-{year}-{stage}"


def bind_telemetry_pack(year: int, stage: int) -> str:
    return f"telemetryPack-{year}-{stage}"


def bind_arrival(year: int, stage: int) -> str:
    return f"rankingTypeArrival-{year}-{stage}"


# Jerseys-Bind trägt die Nummer der NÄCHSTEN Etappe (n+1), nicht die aktuelle.
# Beispiel 2026-07-05: Etappe 2 läuft, Jerseys-Bind = rankingTypeJerseys-2026-3.
def bind_jerseys(year: int, next_stage: int) -> str:
    return f"rankingTypeJerseys-{year}-{next_stage}"


def bind_checkpoint(year: int, stage: int) -> str:
    return f"checkpoint-{year}-{stage}"


def bind_withdrawals(year: int, stage: int) -> str:
    return f"stageWithdrawals-{year}-{stage}"


def bind_millesime(year: int) -> str:
    return f"millesime-{year}"


# --------------------------------------------------------------------------- #
# Codes
# --------------------------------------------------------------------------- #
# Jersey-Farbe im type-Code des Jerseys-Binds (nicht in einem eigenen Feld).
#   pmt = Points Maillot (Trikots), pmt(Y=gelb)/pmp(G=grün)/pmm(P=berg)/pmj(W=weiß)
# Achtung: absolute bei pmt/pmj = Millisekunden, bei pmp/pmm = Punkte!
JERSEY_TYPE_TO_CODE = {
    "pmt": "Y",  # gelb  (Gesamtwertung)
    "pmp": "G",  # grün  (Punktewertung)
    "pmm": "P",  # Punkt (Bergwertung)
    "pmj": "W",  # weiß  (Nachwuchs)
}

JERSEYS = {"Y": "Gelb", "G": "Grün", "P": "Punkt", "W": "Weiß"}

# Ranking-Typen
TYPE_GC = "itg"  # Intermediate General Classification (Gesamtrangliste, arrival)
TYPE_STAGE = "ete"  # Etappen-Ergebnis (Ankunft dieser Etappe)
TYPE_TEAMS = "ite"  # Team-Wertung
TYPE_JERSEYS = "pmt"  # Trikot-Container (Untertypen pmt/pmp/pmm/pmj)

# --------------------------------------------------------------------------- #
# Engine-Parameter
# --------------------------------------------------------------------------- #
EARTH_RADIUS_KM = 6371.0

# Glättung: zwischen zwei SSE-Ticks extrapolieren wir Top-N-Positionen.
EXTRAPOLATE_INTERVAL_S = 2.0
LERP_ALPHA = 0.3  # Re-Anchor: pos = old*(1-α) + new*α, kein harter Sprung.

# Mindestabstand zwischen zwei Notify/Broadcasts (verhindert UI-Flackern).
NOTIFY_THROTTLE_S = 1.0

# HTTP-Timeouts.
REST_TIMEOUT_S = 20.0
SSE_CONNECT_TIMEOUT_S = 15.0
SSE_READ_TIMEOUT_S = None  # kein Lese-Timeout (Dauerbindung)

# Default-Port.
DEFAULT_PORT = 8000

# Snap-Fehler-Toleranz (für optionales Profilsnap, siehe extrapolate).
SNAP_ERROR_KM = 0.05  # 50 m

# --------------------------------------------------------------------------- #
# Betrieb / Steuerung (ENV-gesteuert)
# --------------------------------------------------------------------------- #
# Auth: Leer = keine Auth (nur localhost/Vertrauensnetz). Sonst muss jede
# /api/*- und /ws-Anfrage ?k=<TOKEN> oder Authorization: Bearer <TOKEN> tragen.
TOKEN: str = os.environ.get("TDF_TOKEN", "")

# JSONL-Trail: Pfad, in den jeder Snapshot geschrieben wird (wie v2 --json).
# Leer = kein Trail. Default zeigt auf den klassischen systemd-Pfad.
JSONL_PATH: str = os.environ.get("TDF_JSONL_PATH", "/var/lib/tdf-tracker/live.jsonl")

# systemd-Dienstname für /api/control (Start/Stop/Restart via subprocess).
SERVICE_NAME: str = os.environ.get("TDF_SERVICE_NAME", "tdf-tracker")

# Trail-Schreibthrottle: max alle N Sekunden eine Zeile, verhindert Disk-Flut.
JSONL_FLUSH_EVERY_S = 2.0

# subprocess-Timeout für systemctl-Aufrufe.
CONTROL_TIMEOUT_S = 5.0
