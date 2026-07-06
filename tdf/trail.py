"""JSONL-Trail: schreibt jeden Snapshot in eine .jsonl-Datei (wie v2 --json).

Ermöglicht Nachanalyse und die /api/trail-Rückgabe der letzten N Snapshots.
Throttelt auf JSONL_FLUSH_EVERY_S, damit die Disk nicht geflutet wird.

Rotation: Sobald die Datei MAX_FILE_SIZE_BYTES überschreitet, wird sie
automatisch nach path.archive.1, .2, ... rotiert.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from typing import Any

# Standardmäßig 256 MB bevor rotiert wird.
MAX_FILE_SIZE_BYTES = 256 * 1024 * 1024
# Maximal 5 Archive behalten.
MAX_ARCHIVES = 5


class JsonlTrail:
    """Schreibt Snapshots append-only in eine JSONL-Datei (throttelt + Rotation)."""

    def __init__(self, path: str, *, flush_every_s: float | None = None,
                 max_size: int = MAX_FILE_SIZE_BYTES) -> None:
        self.path = path
        self._max_size = max_size
        from . import config as cfg
        self._flush_every = (flush_every_s if flush_every_s is not None
                             else cfg.JSONL_FLUSH_EVERY_S)
        self._last_write = 0.0
        # Verzeichnis anlegen, falls fehlt (nur wenn Pfad gesetzt).
        if path:
            d = os.path.dirname(path)
            if d:
                try:
                    os.makedirs(d, exist_ok=True)
                except OSError:
                    pass  # Details im write()/Log

    def _rotate_if_needed(self) -> None:
        """Rotiert die Datei, wenn sie die Maximalgröße überschreitet."""
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size < self._max_size:
            return
        # Archive nach hinten schieben (.5 wegwerfen, .4 → .5, ...)
        for i in range(MAX_ARCHIVES - 1, 0, -1):
            old = f"{self.path}.archive.{i}"
            new = f"{self.path}.archive.{i + 1}"
            if os.path.exists(old):
                try:
                    os.replace(old, new) if os.name != "nt" else shutil.move(old, new)
                except OSError:
                    pass
        # Aktuelle Datei → .archive.1
        try:
            os.replace(self.path, f"{self.path}.archive.1") if os.name != "nt" \
                else shutil.move(self.path, f"{self.path}.archive.1")
        except OSError:
            pass

    def write(self, snap: dict[str, Any]) -> bool:
        """Schreibt eine Zeile, falls throttle abgelaufen. Liefert True bei Write."""
        if not self.path:
            return False
        now = time.time()
        if now - self._last_write < self._flush_every:
            return False
        self._last_write = now
        try:
            self._rotate_if_needed()
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(snap, ensure_ascii=False))
                f.write("\n")
                f.flush()
            return True
        except OSError:
            return False


def read_last_n(path: str, n: int = 20) -> list[dict[str, Any]]:
    """Liefert die letzten N vollständigen Zeilen einer JSONL-Datei (Tail).

    Liest die letzten 256 KB (großzügig, da ein Snapshot ~12 KB groß ist)
    und parst die vollständigen Zeilen davon.
    """
    if not path:
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return []
            chunk = min(size, 256 * 1024)
            f.seek(size - chunk)
            data = f.read()
    except FileNotFoundError:
        return []
    except OSError:
        return []

    lines = data.splitlines()
    # Wenn wir in eine Zeile hineingeschnitten haben, erste verwerfen.
    if size > 256 * 1024 and lines:
        lines = lines[1:]
    lines = [ln for ln in lines if ln.strip()]
    out: list[dict[str, Any]] = []
    for ln in lines[-n:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out
