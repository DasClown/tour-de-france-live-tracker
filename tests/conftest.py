"""Pytest-Konfiguration und gemeinsame Fixtures für die TDF-Tracker-Tests.

Lädt das Paket aus /opt/tdf-tracker und stellt Hilfsfunktionen für den
Zugriff auf die gespeicherten ASO-Fixtures bereit.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Paket aus dem Repo-Root importierbar machen.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"


# --------------------------------------------------------------------------- #
# Fixture-Helfer
# --------------------------------------------------------------------------- #
def load_fixture(name: str) -> object:
    """Lädt eine JSON-Datei aus tests/fixtures/ (ohne .json-Endung angeben)."""
    path = FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        pytest.skip(f"Fixture fehlt: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def fx():
    """Gibt eine Funktion zurück, die Fixtures nach Namen lädt."""
    return load_fixture


@pytest.fixture
def aso_all_competitors():
    return load_fixture("allCompetitors-2026")


@pytest.fixture
def aso_teams():
    return load_fixture("team-2026")


@pytest.fixture
def aso_stages():
    return load_fixture("stage-2026")


@pytest.fixture
def aso_arrival_stage2():
    return load_fixture("rankingTypeArrival-2026-2")


@pytest.fixture
def aso_jerseys():
    return load_fixture("rankingTypeJerseys-2026-3")


@pytest.fixture
def aso_telemetry():
    return load_fixture("telemetryCompetitor-2026")


@pytest.fixture
def aso_pack_stage2():
    return load_fixture("pack-2026-2")
