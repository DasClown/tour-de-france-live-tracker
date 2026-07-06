# HANDOFF — TDF-Tracker auf Server deployen

**Stand: 2026-07-05.** Vollständiger, in sich geschlossener Zustand des
Projekts. Dieses Dokument ersetzt keinen Chat-Verlauf — ein neuer Chat kann
damit direkt auf dem Server weiterarbeiten.

Lies zuerst dieses Dokument, dann `README.md`, dann `tdf/server.py`.

---

## Was das Projekt ist

Live-Tracker für die Tour de France. Zieht offizielle ASO-Daten von
`racecenter.letour.fr` (reverse-engineered, stabil), berechnet eine
Hybrid-GC und liefert sie als Echtzeit-Web-App + Terminal-Client.

**Drei Ebenen:**
- **v3 (Hauptsystem)** — aiohttp-Server mit SSE-Consumer, WebSocket,
  Token-Auth, Handy-Steuerung (systemctl), JSONL-Trail.
- **v2 (Fallback)** — `tdf_tracker.py`, reines CLI-Skript, `requests`.
- **Deployment** — systemd-Unit + `install.sh`.

## Verzeichnisstruktur (komplett)

```
tdf-tracker/
  tdf/
    __init__.py          Paket-Marker
    config.py            URLs, Header, Bind-Namen, Codes, ENV-Variablen
    static.py            Rider/Teams/Etappen via REST (async)
    bootstrap.py         REST-Bootstrap: GC, Trikots, Telemetrie
    state.py             State-Engine, Dispatcher, Hybrid-GC, Snapshot
    extrapolate.py       Haversine, Destination-Point, Dead-Reckon, Lerp
    profile.py           Best-Effort-Höhenprofil aus /profils/-CSV
    auth.py              Token-Middleware (?k= / Bearer)
    trail.py             JSONL-Writer + Leser
    control.py           systemctl-Wrapper (Start/Stop/Restart)
    server.py            aiohttp: SSE + HTTP + WS + /api/* + statisch
  web/
    index.html           Frontend (mobil)
    app.js               DOM-Diff, rAF, IntersectionObserver, Steuer-Leiste
    style.css            dunkel/hell, mobil-first
  deploy/
    tdf-tracker.service  systemd-Unit (Template)
    install.sh           Richtet den Dienst ein
  tdf_console.py         Terminal-Client (rich optional)
  tdf_tracker.py         v2-Fallback (unverändert)
  requirements.txt       aiohttp, rich, requests
  README.md              Vollständige Doku
  HANDOFF.md             Diese Datei
```

**~3900 Zeilen** insgesamt, 11 Python-Module + Frontend.

## Server-Endpunkte

| URL | Auth | Zweck |
|---|---|---|
| `GET /` | nein | Web-Frontend (mobil) |
| `GET /state[?top=N]` | ja | JSON-Snapshot (GC, Top-N, Gruppen, Trikots) |
| `GET /profile` | ja | Höhenprofil (Best-Effort) |
| `GET /ws` | ja | WebSocket-Push |
| `GET /api/health` | ja | `{ok, status, ts}` für Monitoring |
| `GET /api/control` | ja | systemd-Status (is-active) |
| `POST /api/control?action=start\|stop\|restart` | ja | Dienst steuern |
| `GET /api/trail?n=20` | ja | letzte N JSONL-Snapshots |

## Konfiguration (ENV)

| Variable | Default | Bedeutung |
|---|---|---|
| `TDF_TOKEN` | (leer) | Auth-Token. Leer = keine Auth (nur localhost). |
| `TDF_JSONL_PATH` | `/var/lib/tdf-tracker/live.jsonl` | JSONL-Trail-Pfad. |
| `TDF_SERVICE_NAME` | `tdf-tracker` | systemd-Dienstname für /api/control. |

CLI-Flags: `--year --stage --port --host --insecure --jsonl --log-level`

##Deployment auf den Server (Schritt-für-Schritt)

### Voraussetzungen auf dem Server
- Linux + systemd (für Autostart + Handy-Steuerung).
- Python ≥ 3.10 (getestet: 3.14).
- Port 8000 frei (oder anderen via `--port`).

### 1. Code übertragen (vom Mac aus)
```bash
rsync -avz --delete --exclude '__pycache__' --exclude '.git' \
  /Users/janickthum/ZCodeProject/tdf-tracker/ \
  root@<SERVER-IP>:/opt/tdf-tracker/
```

### 2. Dienst einrichten (auf dem Server)
Entweder automatisch (empfohlen):
```bash
ssh root@<SERVER-IP> 'bash /opt/tdf-tracker/deploy/install.sh /opt/tdf-tracker 8000'
```
`install.sh` macht: pip install, Verzeichnis anlegen, systemd-Unit mit Token
schreiben, Dienst aktivieren + starten, gibt am Ende **Token + URL** aus.

Oder manuell:
```bash
ssh root@<SERVER-IP>
cd /opt/tdf-tracker && pip install -r requirements.txt
install -d -m 0755 /var/lib/tdf-tracker
cp deploy/tdf-tracker.service /etc/systemd/system/
# TDF_TOKEN in der Unit setzen! Dann:
systemctl daemon-reload && systemctl enable --now tdf-tracker
journalctl -u tdf-tracker -f   # Log verfolgen
```

### 3. Handy
```
http://<SERVER-IP>:8000/?k=<TOKEN>
```
Token wird im Browser gespeichert. Steuer-Leiste (Restart/Stop/Start)
erscheint automatisch, sobald `/api/control` systemd meldet.

### 4. polkit (für Restart/Stop vom Handy)
Restart/Stop aus dem laufenden Dienst brauchen eine polkit-Regel.
`/etc/polkit-1/rules.d/49-tdf-tracker.rules` (User anpassen):
```javascript
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        subject.user == "root" &&
        action.lookup("unit") == "tdf-tracker.service") {
        return polkit.Result.YES;
    }
});
```
Start/Status funktionieren auch ohne polkit.

---

## Verifizierter Stand (2026-07-05)

### Live gegen ASO getestet
- Bootstrap: 184 Starter, 21 Etappen, echte GC (183 Fahrer).
- Virtual-GC korrekt (Etappe 2):
  - **1. Vingegaard** (leader, 4:01:48)
  - **2. Pogacar +6s** (6s Bonus)
  - **3. Evenepoel +15s** (4s Bonus)
  - **4. del Toro +16s** (10s Bonus)
  - **5. Ayuso +19s**
- Trikots: Gelb Vingegaard, Grün del Toro, Weiß Ayuso, Punkt Molenaar.
- SSE verbindet, pack-Bind liefert 5 Gruppen, 1 Aufgabe.
- Entspricht exakt der procyclingstats-Verifizierung.

### Steuerung getestet
- Auth-Matrix: ohne Token=401, `?k=`=200, Bearer=200, falsch=401. `/`=200.
- `/api/health`, `/api/control` (503 ohne systemd), `/api/trail` (liest JSONL).
- JSONL-Trail wird geschrieben (throttelt 2s), Heartbeat-Loop stellt
  Zuverlässigkeit auch post-race/ohne Browser sicher.
- Frontend lädt, Steuer-Leiste + Token-Eingabe im HTML.

---

## Architektur-Entscheidungen (fix)

1. **Gap-Quelle = pack-Bind** (`computedRelative`), nicht Polyline-Snap.
   Die `/profils/`-CSV ist nicht zuverlässig auflösbar. Dead-Reckoning nur
   zur Top-N-UI-Glättung zwischen SSE-Ticks.
2. **Virtual-GC = offizielles itg-Ranking**. Bonus/Penalty nur anzeigen,
   nicht in Sortierung einrechnen (ASO hat sie schon in `relative`).
3. **Top-N = dynamisch** (GC-Top-10), keine Fixliste.
4. **Start-GC = echte aktuelle GC** per REST-Bootstrap (nicht 0).
5. **Token-Auth** via `TDF_TOKEN` ENV (`?k=` oder Bearer).
6. **Steuerung via systemctl subprocess**, Server tötet sich nie selbst.
7. **JSONL-Trail** in v3 integriert, schreibt throttelt.

## Bewusst NICHT umgesetzt

- Time-Cut (post-race).
- Echtradius-Erkennung (nur grob via 50m-Snap-Fehler).
- Echtes Höhenprofil aus Polyline (nur CSV-Best-Effort/checkpoint-interpoliert).
- Konfiguration zur Laufzeit ändern (Etappe/Port) → Neustart nötig.
- Trail-Rotation (wächst; ggf. logrotate).

## Bekannte Daten-Caveats

- **Zeiten in `rankings` sind Millisekunden**, nicht Sekunden!
- **Trikotfarbe im `type`-Code** (`pmt`/`pmp`/`pmm`/`pmj`), kein eigenes Feld.
- **`absolute`-Einheit variiert**: ms bei pmt/pmj, Punkte bei pmp/pmm.
- **RaceStatus in `telemetryCompetitor`**, nicht im `pack`.
- Rider-Objekt hat **kein `code`**-Feld → Team via `$team`-Join oder `_origin`.
- Etappen-Array **unsortiert** → nach Feld `stage` filtern.
- Gruppen-Größe zeigt manchmal „999 F." (ASO-Dummy, nicht unser Bug).
- Jerseys-Bind trägt **n+1** als Suffix (nächste Etappe), nicht aktuelle.

## Verifizierte SSE-Bind-Schemata

| Bind | Inhalt |
|---|---|
| `telemetryCompetitor-<J>` | Riders[], RaceStatus, TimeStamp, StageIndex |
| `pack-<J>-<ET>` | Gruppen: bibs[], computedRelative, computedSpeed, remainingDistance |
| `rankingTypeArrival-<J>-<ET>` | type=itg(GC)/ite(Teams)/ete(Etappe), Zeiten in ms |
| `rankingTypeJerseys-<J>-<N>` | type=pmt(Y)/pmp(G)/pmm(P)/pmj(W), position 1 = Träger |
| `checkpoint-<J>-<ET>` | numerisch indizierte Wegpunkte |
| `stageWithdrawals-<J>-<ET>` | Aufgaben |
| `allCompetitors-<J>` | Bib→Rider (REST) |
| `team-<J>` | code→nameShort (REST) |
| `millesime-<J>` | Renn-Config (Profile-Hash etc.) |

## Quellen & Referenzen

- ASO Race Center: https://racecenter.letour.fr
- Community-Referenz: https://github.com/mullummer/racecenter (@velofacts)
  – Haversine + CSV-Schema übernommen; NICHT sein Dead-Reckoning/Virtual-GC
  (er macht das nicht — Recon hat das korrigiert).
- Ergebnis-Verifizierung: procyclingstats.com

## Erstkontakt für neuen Chat

Wenn ein neuer Chat das Projekt fortsetzt:

1. Lies `HANDOFF.md` (diese Datei), dann `README.md`, dann `tdf/server.py`.
2. Aktueller Stand: Code fertig + live verifiziert, aber **noch nicht auf
   dem Server deployed**. Nächster Schritt: Deployment (siehe oben).
3. Etappe 3 ist am 2026-07-06 (morgen). Server sollte vorher laufen.
4. Tatsächliche Daten aktuell: Etappe 2 zu Ende, RaceStatus=False.
5. Bekannte offene Punkte: polkit-Regel auf dem Server einrichten,
   Trail-Rotation, ggf. Profil-CSV-Hash-Auflösung verbessern.

## Schnell-Start im neuen Chat

```
„Lies HANDOFF.md. Deploye den TDF-Tracker auf meinem Server unter
<SERVER-IP>. Hilf mir bei der Einrichtung."
```

Das war's. Viel Erfolg morgen bei Etappe 3. 🚴
