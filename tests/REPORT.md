# Test-Report — TDF-Tracker

**Stand: 2026-07-06.** Vollständige Test-Suite mit 280 Tests, alle grün.

## Übersicht

| Stufe | Modul | Tests | Was geprüft wird |
|---|---|---|---|
| 1 | `extrapolate` | 27 | Haversine, Destination-Point, Lerp, Dead-Reckon |
| 1 | `auth` | 17 | Token-Auth: ?k=, Bearer, 4 Pfade, Pfad-Logik |
| 1 | `config` | 25 | URLs, Bind-Namen, Codes, ENV-Defaults |
| 1 | `prediction` | 18 | Restzeit-Berechnung, ETA-Format |
| 1 | `gap_chart` | 13 | Ringpuffer, Throttling, Aging, since-Filter |
| 1 | `trail` | 17 | JSONL-Schreiben, Throttling, Rotation, Tail-Lesen |
| 1 | `alarms` | 19 | Ausreißer, Trikotwechsel, GC-Wechsel, Aufgaben |
| 1 | `classification` | 22 | Berg/Sprint-Parsing, Rankings, Serialisierung |
| 1 | `map_data` | 15 | BBox, Center, Polyline, Marker |
| 2 | `bootstrap` | 14 | GC/Jerseys/Telemetrie/Pack mit echten ASO-Fixtures |
| 2 | `static` | 16 | Rider/Teams/Stages, Team-Code-Auflösung |
| 2 | `profile` | 14 | CSV-Parsing, _safe_float, Serialisierung |
| 2 | `state` | 33 | Parser, Hybrid-GC, Dispatcher (alle 6 Binds) |
| 3 | `server_e2e` | 16 | Live-Server: Auth-Matrix, alle Endpunkte, Performance |
| | **Total** | **280** | |

Laufzeit: ~3 Sekunden (offline), +5s mit Live-ASO.

## Ausführung

```bash
cd /opt/tdf-tracker

# Alles
.venv/bin/python -m pytest tests/

# Nur Unit/Integration (ohne E2E)
.venv/bin/python -m pytest tests/ -m "not e2e"

# Nur E2E gegen laufenden Server
.venv/bin/python -m pytest tests/ -m e2e

# Ausführlich mit Coverage (braucht pytest-cov)
.venv/bin/python -m pytest tests/ -v --cov=tdf
```

## Fixtures

`tests/fixtures/` enthält Live-ASO-Antworten vom 2026-07-06 (Etappe 2):
- 184 Starter, 21 Etappen, 22 Teams
- GC mit 183 Fahrern (Vingegaard Spitze, Zeiten verifiziert)
- Jerseys (Grün/Weiß/Punkt/Gelb), Telemetrie, 5 Gruppen

Diese ermöglichen **deterministische Offline-Tests** ohne Internet.

---

## Gefundene echte Code-Quirks (keine Test-Bugs)

Beim Schreiben der Tests habe ich mehrere Verhaltensdetails entdeckt, die
**keine Crashes verursachen, aber subtil falsch oder überraschend** sind.
Sie sind in den Tests als `⚠️ BEKANNTER CODE-QUIRK` markiert.

### 1. ~~Withdrawal-Cooldown schluckt mehrere Aufgaben gleichzeitig~~ ✅ BEHOBEN

**Ort:** `tdf/alarms.py`, `_add()` / `evaluate()`

**Status:** Behoben am 2026-07-06. Pro-Bib-Cooldown-Tag
(`details["_tag"]=str(bib)`) eingeführt, parallel zu breakaway/caught.
Regression-Test `test_multiple_new_withdrawals_all_emit` sichert das ab.
Dienst neu gestartet, Fix ist live.

**Ursprünglicher Bug:** Withdrawals teilten sich den Tag `"withdrawal"`
ohne `_tag`. Bei zwei gleichzeitigen Aufgaben (Massensturz) kam nur
eine durch; die zweite wurde vom Cooldown blockiert.

### 2. `parse_groups` verliert Gap ohne `isComputedGap`-Flag

**Ort:** `tdf/state.py:240`

**Symptom:** Wenn eine Gruppe `computedRelative=0` (Peloton) liefert,
aber `isComputedGap` fehlt oder `False` ist, fällt der Code auf das
`relative`-Feld zurück — und wenn dieses `None` ist, geht die Gap-Info
komplett verloren.

**Ursache:**
```python
gap = g.get("computedRelative") if g.get("isComputedGap") else g.get("relative")
```
Das Flag `isComputedGap` ist nicht immer gesetzt. Die `computed*`-Felder
sind laut Handoff aber die zuverlässigen.

**Auswirkung:** Peloton-Gruppen mit `computedRelative=0` zeigen manchmal
keine Gap an. Gering bis mittel.

**Empfohlener Fix:** `computedRelative` bevorzugen, Fallback auf `relative`:
```python
gap = g.get("computedRelative")
if gap is None:
    gap = g.get("relative")
```

### 3. JSONL-Trail wächst unbegrenzt

**Ort:** `tdf/trail.py` + `tdf/server.py`

**Symptom:** Der Heartbeat-Loop schreibt alle 2s einen Snapshot, auch
wenn das Rennen lange beendet ist. Aktuell **131 MB** in einer Etappe.

**Status:** `trail.py` hat mittlerweile eine Rotation (256 MB Limit,
5 Archive) — das wurde in der Nacht offenbar ergänzt. Damit ist das
ursprüngliche „bewusst nicht umgesetzt" aus dem Handoff teilweise
behoben. Die Rotation greift aber erst bei 256 MB; der Heartbeat
selbst läuft ewig weiter.

**Auswirkung:** ~2,7 GB über 21 Etappen. Mittel.

### 4. `/state` vor Rennstart loggt als `ERROR`

**Ort:** `tdf/bootstrap.py:147`

**Symptom:** Wenn ASO für die heutige Etappe noch `204 No Content`
liefert (Rennen nicht gestartet), wird das als
`GC-Bootstrap fehlgeschlagen: 204 ...` ins Journal geschrieben.

**Ursache:** Die Exception ist ein `ContentTypeError`, der in die
`except (aiohttp.ClientError, ValueError)`-Klausel fällt und dort als
`log.error(...)` geloggt wird.

**Auswirkung:** Verwirrend im Log (sieht aus wie Fehler, ist aber
normaler Zustand vor Etappenstart). Rein kosmetisch.

**Empfohlener Fix:** 204 explizit abfangen und als INFO loggen:
```python
except aiohttp.ClientResponseError as e:
    if e.status == 204:
        log.info("Bootstrap: Etappe %d noch nicht verfügbar (204)", stage)
    else:
        log.error("GC-Bootstrap fehlgeschlagen: %s", e)
```

---

## Was nicht automatisiert testbar ist

- **Live-Renn-Dynamik** — abhängig davon, was ASO gerade liefert
  (Ausreißer, Gegenangriffe etc.). Vor Rennstart ist `race_status=False`.
- **WebSocket-Push** — würde einen dauerhaften WS-Client brauchen.
  Getestet nur auf HTTP-Ebene (Auth 401 ohne Token).
- **Frontend-Logik** (app.js, 32 KB) — braucht Headless-Browser-Tests
  (Playwright/Selenium). Nicht Teil dieser Suite.
- **systemctl-Steuerung** (`control.py`) — braucht echtes systemd.
  Über `/api/control` indirekt getestet (liefert `active`).
