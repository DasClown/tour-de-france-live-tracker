#!/usr/bin/env bash
# Richtet den TdF-Tracker als systemd-Dienst ein.
#
# Aufruf:  sudo bash deploy/install.sh [REPO_PFAD] [PORT] [TOKEN]
# Beispiel:sudo bash deploy/install.sh /opt/tdf-tracker 8000 "$(openssl rand -hex 24)"
#
# Was es tut:
#   1. Abhängigkeiten via pip installieren (aiohttp, rich)
#   2. State-Verzeichnis /var/lib/tdf-tracker anlegen
#   3. systemd-Unit installieren (mit Token/Port/WorkingDir ausgefüllt)
#   4. Dienst aktivieren und starten
#   5. polkit-Hinweis ausgeben, damit /api/control funktioniert
#
# Es ist idempotent (mehrfaches Ausführen aktualisiert die Unit).

set -euo pipefail

REPO="${1:-/opt/tdf-tracker}"
PORT="${2:-8000}"
TOKEN="${3:-$(openssl rand -hex 24 2>/dev/null || echo CHANGE_ME)}"
SERVICE_FILE="/etc/systemd/system/tdf-tracker.service"

if [[ "$EUID" -ne 0 ]]; then
  echo "Bitte mit sudo ausführen." >&2
  exit 1
fi

echo "==> 1/5  Abhängigkeiten installieren"
python3 -m pip install --upgrade -q aiohttp rich

echo "==> 2/5  State-Verzeichnis"
install -d -m 0755 /var/lib/tdf-tracker

echo "==> 3/5  systemd-Unit schreiben ($SERVICE_FILE)"
PYBIN="$(command -v python3 || echo /usr/bin/python3)"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=TdF Live Tracker (v3 aiohttp)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO
ExecStart=$PYBIN -m tdf.server --year 2026 --port $PORT
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=TDF_TOKEN=$TOKEN
Environment=TDF_JSONL_PATH=/var/lib/tdf-tracker/live.jsonl
Environment=TDF_SERVICE_NAME=tdf-tracker

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "$SERVICE_FILE"

echo "==> 4/5  Dienst aktivieren und starten"
systemctl daemon-reload
systemctl enable --now tdf-tracker

echo "==> 5/5  polkit-Hinweis"
cat <<EOF

---------------------------------------------------------------------------
Dienst läuft:  systemctl status tdf-tracker
Log verfolgen: journalctl -u tdf-tracker -f
Web-UI:       http://<server-ip>:$PORT/?k=$TOKEN
Token:        $TOKEN

WICHTIG – Steuerung vom Handy (Start/Stop/Restart) braucht polkit.
Lege /etc/polkit-1/rules.d/49-tdf-tracker.rules an mit:

  polkit.addRule(function(action, subject) {
      if (action.id == "org.freedesktop.systemd1.manage-units" &&
          subject.user == "root") {  // <- Dienst-User anpassen
          return polkit.Result.YES;
      }
  });

Siehe README.md „systemd & polkit".
---------------------------------------------------------------------------
EOF
