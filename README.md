# 🚴 Tour de France Live Tracker

[![Tests](https://img.shields.io/badge/tests-281%20passing-brightgreen)](tests/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![aiohttp](https://img.shields.io/badge/aiohttp-3.9%2B-orange)](https://docs.aiohttp.org/)

Real-time tracker for the Tour de France. Pulls live data from the official
ASO Race Center, computes a hybrid General Classification, and serves it as
a mobile-friendly web app + terminal client + JSON API.

<!-- Screenshot hier einfügen, sobald vorhanden:
![Tracker Screenshot](docs/screenshot.png)
-->

## ✨ Features

- **Live GC** — Hybrid General Classification from official `itg` ranking +
  live pack data (gap source = `computedRelative`, not polyline snap).
- **Real-time telemetry** — Rider positions, speed, group composition via SSE.
- **5 dynamic features** (added v3):
  - 🕐 **Predictions** — Estimated finish time per group/rider
  - 📊 **Gap chart** — Time-series of GC gaps (line chart data)
  - 🏔️ **Classifications** — KOM & sprint standings + categorized climbs
  - 🔔 **Alarms** — Breakaways, jersey changes, GC leader changes, withdrawals,
    race end
  - 🗺️ **Map data** — Route polyline, mountain/sprint markers, bounding box
- **Mobile control** — Start/stop/restart the service from your phone
  (systemctl via polkit).
- **JSONL trail** — Append-only live snapshots for post-race analysis.
- **Token auth** — `?k=<token>` or `Authorization: Bearer <token>`.
- **Terminal client** — `tdf_console.py` with `rich` tables (optional).

## 🚀 Quick start

### Run locally (dev)

```bash
git clone https://github.com/DasClown/tour-de-france-live-tracker.git
cd tour-de-france-live-tracker

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Ohne Token (nur localhost), Etappe 3, Jahr 2026:
python -m tdf.server --year 2026 --port 8000

# Öffne http://localhost:8000
```

### Deploy on a server (systemd)

See [`deploy/install.sh`](deploy/install.sh) for an automated setup, or the
manual steps in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md). Mobile URL:

```
http://<server-ip>:8000/?k=<TOKEN>
```

## 📡 API endpoints

| URL | Auth | Purpose |
|---|---|---|
| `GET /` | no | Web frontend (mobile) |
| `GET /state[?top=N]` | yes | JSON snapshot (GC, top-N, groups, jerseys, predictions, alarms, classifications, map) |
| `GET /profile` | yes | Elevation profile (best-effort from CSV) |
| `GET /api/health` | yes | `{ok, status, ts, stage, year}` |
| `GET /api/control` | yes | systemd status |
| `POST /api/control?action=start\|stop\|restart` | yes | Control the service |
| `GET /api/trail?n=20` | yes | Last N JSONL snapshots |
| `GET /api/gap-chart` | yes | Gap-chart time series |
| `GET /api/classification` | yes | KOM + sprint standings |
| `GET /api/map-data` | yes | Route polyline + markers |
| `GET /ws` | yes | WebSocket push |

Auth: `?k=<TDF_TOKEN>` query param or `Authorization: Bearer <TDF_TOKEN>` header.

## ⚙️ Configuration

All via environment variables (see [`.env.example`](.env.example)):

| Variable | Default | Meaning |
|---|---|---|
| `TDF_TOKEN` | (empty) | Auth token. Empty = no auth (localhost only). |
| `TDF_JSONL_PATH` | `/var/lib/tdf-tracker/live.jsonl` | JSONL trail path. |
| `TDF_SERVICE_NAME` | `tdf-tracker` | systemd service name for `/api/control`. |

CLI flags: `--year --stage --port --host --insecure --jsonl --log-level`

## 🧪 Tests

```bash
pip install -r requirements.txt
pip install pytest pytest-asyncio

pytest                       # all 281 tests (~3s)
pytest -m "not e2e"          # unit + integration only (no live server)
pytest -m e2e                # end-to-end against running server
```

See [`tests/REPORT.md`](tests/REPORT.md) for the full test report and
known code quirks. Coverage: extrapolate, auth, config, prediction,
gap_chart, alarms, classification, map_data, trail, bootstrap, static,
profile, state, and live server endpoints.

## 🏗️ Architecture

```
tdf/
  config.py        URLs, headers, bind names, codes, ENV vars
  static.py        Rider/teams/stages via REST (async)
  bootstrap.py     REST bootstrap: GC, jerseys, telemetry
  state.py         State engine, dispatcher, hybrid GC, snapshot
  extrapolate.py   Haversine, destination-point, dead-reckon, lerp
  profile.py       Best-effort elevation profile from /profils/ CSV
  auth.py          Token middleware (?k= / Bearer)
  trail.py         JSONL writer + reader (with rotation)
  control.py       systemctl wrapper (start/stop/restart)
  prediction.py    ETA predictions (Feature 1)
  gap_chart.py     Gap time-series ring buffer (Feature 2)
  classification.py Mountain/sprint standings (Feature 3)
  alarms.py        Event detection (Feature 4)
  map_data.py      Map aggregation (Feature 5)
  server.py        aiohttp: SSE consumer + HTTP + WS + /api/* + static
web/
  index.html, app.js, style.css    Mobile frontend
deploy/
  tdf-tracker.service              systemd unit (template)
  install.sh                       Automated setup
tdf_console.py    Terminal client (rich optional)
tdf_tracker.py    v2 fallback (pure CLI, requests)
```

The state engine consumes SSE binds from the ASO Race Center:

| Bind | Content |
|---|---|
| `telemetryCompetitor-<J>` | Riders[], RaceStatus, TimeStamp, StageIndex |
| `pack-<J>-<ET>` | Groups: bibs[], computedRelative, computedSpeed, remainingDistance |
| `rankingTypeArrival-<J>-<ET>` | type=itg(GC)/ite(teams)/ete(stage), times in **ms** |
| `rankingTypeJerseys-<J>-<N>` | type=pmt(Y)/pmp(G)/pmm(P)/pmj(W), position 1 = holder |
| `checkpoint-<J>-<ET>` | Numerically indexed waypoints |
| `stageWithdrawals-<J>-<ET>` | Withdrawals |

> ⚠️ **Times in `rankings` are milliseconds, not seconds!** The parser
> handles the conversion. See `tests/test_state.py::TestParseRankings`.

## 🤝 Contributing

Contributions welcome! See [`CONTRIBUTING.md`](CONTRIBUTING.md).

Areas that need love:
- Time-cut detection (post-race)
- Real elevation profile from polyline (currently CSV best-effort)
- Runtime config changes (stage/port) without restart
- Frontend tests (Playwright)

## ⚠️ Disclaimer

**This project is not affiliated with, endorsed by, or sponsored by ASO
(Amaury Sport Organisation), Le Tour de France, or any of their partners.**

"Tour de France" is a trademark of ASO. All race data (rankings, telemetry,
rider information) is fetched from the publicly accessible
[racecenter.letour.fr](https://racecenter.letour.fr) endpoint for personal,
non-commercial, informational use only. No data is redistributed or
cached for resale. If you are a rights holder and have concerns, please
open an issue and we'll address it promptly.

The reverse-engineering of the public Race Center API is done for
interoperability and personal use, consistent with how the official
website exposes this data to ordinary browsers.

## 📄 License

[MIT](LICENSE) — © Janick Thum
