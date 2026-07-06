"""systemctl-Wrapper: Start/Stop/Restart des eigenen Dienstes vom Handy aus.

Der Server tötet sich nicht selbst. Stattdessen ruft /api/control systemctl
per subprocess auf. Das setzt voraus, dass der Serverprozess unter einem
User läuft, der systemctl darf (polkit-Regel oder System-Dienst mit
entsprechenden Rechten – siehe deploy/README).

Auf Dev-Maschinen ohne systemctl (z. B. macOS) ist das Feature deaktiviert;
die Endpunkte liefern 503 mit klarem Hinweis.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Any

from . import config as cfg

log = logging.getLogger("tdf.control")

# Erlaubte Aktionen (Whitelist, keine Injektion via Shell).
ACTIONS = {"start", "stop", "restart", "is-active", "status"}


def is_available() -> bool:
    """True, wenn systemctl auf dem System ausführbar ist."""
    return shutil.which("systemctl") is not None


def service_action(action: str, *, service: str | None = None) -> dict[str, Any]:
    """Führt ``systemctl <action> <service>`` aus. Liefert {ok, rc, stdout, stderr}.

    ok=False bei nicht-verfügbarem systemctl, unbekannter Aktion oder Fehler.
    """
    if not is_available():
        return {"ok": False, "available": False,
                "error": "systemctl nicht verfügbar (kein Linux/systemd)"}
    if action not in ACTIONS:
        return {"ok": False, "error": f"unbekannte Aktion '{action}'"}
    svc = service or cfg.SERVICE_NAME
    try:
        proc = subprocess.run(
            ["systemctl", action, svc],
            capture_output=True, text=True, timeout=cfg.CONTROL_TIMEOUT_S,
        )
        return {
            "ok": proc.returncode == 0,
            "available": True,
            "rc": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "action": action,
            "service": svc,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "available": True,
                "error": f"systemctl {action} timed out",
                "action": action, "service": svc}
    except OSError as e:
        return {"ok": False, "available": True,
                "error": f"systemctl nicht ausführbar: {e}",
                "action": action, "service": svc}


def service_status(service: str | None = None) -> dict[str, Any]:
    """Status-Helper: aktiv/inaktiv + kurze Meldung, für /api/control (GET)."""
    res = service_action("is-active", service=service)
    if not res.get("available"):
        return res
    # is-active liefert "active"/"inactive"/"failed" auf stdout.
    active = (res.get("stdout") or "").strip() == "active"
    return {**res, "active": active, "state": (res.get("stdout") or "").strip()}
