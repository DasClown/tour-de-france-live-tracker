"""aiohttp-Server: SSE-Consumer + HTTP /state + WebSocket /ws + statische web/.

Architektur (approved):
    ASO /live-stream  ──►  SSE-Consumer-Task
                              │
                              ▼
                          State (state.py)
                              │
                ┌─────────────┼──────────────┐
                ▼             ▼              ▼
            HTTP /state   WebSocket /ws   Extrapolation-Loop
                              │              (Top-N-Position glätten)
                              ▼
                         Web-Frontend (mobil)

Startup:
  1. Statische Rider/Teams/Etappen laden.
  2. REST-Bootstrap (GC, Jerseys, Telemetrie, Pack) – startet „wo wir gerade sind".
  3. Profil-CSV laden (Best-Effort).
  4. Hintergrund-Tasks starten: SSE-Consumer + Extrapolation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

from . import bootstrap, config as cfg, profile as profile_mod
from . import state as st
from . import static
from . import extrapolate as ex
from . import auth as auth_mod, control as control_mod, trail as trail_mod
from . import gap_chart as gap_chart_mod
from . import prediction as prediction_mod
from . import alarms as alarms_mod
from . import classification as classification_mod
from . import map_data as map_data_mod

log = logging.getLogger("tdf.server")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


# --------------------------------------------------------------------------- #
# SSE-Consumer
# --------------------------------------------------------------------------- #
async def consume_sse(app: web.Application) -> None:
    """Dauerhafte SSE-Verbindung; Dispatch bei jedem 'update'-Event.

    Robustheit:
      - sock_read Timeout (30s): wenn ASO lange still bleibt, reconnecten.
        In Live-Phasen sendet ASO ~alle 1-5s Events; 30s Stille = Problem.
      - explizite app["stopping"]-Prüfung im Lese-Loop.
      - ausführliche Logs bei Verbindungsabbruch (vormals zu still).
    """
    state: st.State = app["state"]
    session: aiohttp.ClientSession = app["http_session"]
    year = state.year
    stage = state.stage
    next_stage = (stage + 1) if stage else None
    backoff = 1
    # sock_read: ASO schickt kontinuierlich Chunks (200+ in 0.1s während
    # Live-Phase). Ein Timeout hier ist kontraproduktiv — es würde die
    # stabile Verbindung kappen. Wir trusten dem Watchdog im heartbeat_loop,
    # der den Task neu startet, falls er doch mal stirbt.
    sse_read_timeout = cfg.SSE_READ_TIMEOUT_S  # Default: None (unendlich)

    while not app["stopping"]:
        try:
            timeout = aiohttp.ClientTimeout(
                total=None,
                connect=cfg.SSE_CONNECT_TIMEOUT_S,
                sock_read=sse_read_timeout,
            )
            async with session.get(cfg.LIVE_STREAM, headers=cfg.SSE_HEADERS,
                                   timeout=timeout) as resp:
                resp.raise_for_status()
                sock_read_str = (f"{sse_read_timeout:.0f}s"
                                 if sse_read_timeout is not None else "∞")
                log.info("SSE verbunden (HTTP %d, sock_read=%s)",
                         resp.status, sock_read_str)
                backoff = 1
                # ⚠️ WICHTIG: aiohttp liefert resp.content als Byte-Stream in
                # kleinen Chunks (oft nur 9-70 Bytes), NICHT als Zeilen.
                # Eine einzelne SSE-`data:`-Zeile kann 45 KB groß sein (alle
                # Riders eines telemetryCompetitor-Events) und über tausende
                # Chunks verteilt sein. Deshalb: Chunks in einem Puffer
                # sammeln und auf Leerzeile (Event-Grenze) splitten.
                buffer = ""
                event_name: str | None = None
                event_data_lines: list[str] = []
                async for raw in resp.content:
                    if app["stopping"]:
                        return
                    if not raw:
                        continue
                    buffer += raw.decode("utf-8", errors="replace")
                    # Vollständige Zeilen aus dem Puffer extrahieren.
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.rstrip("\r")
                        if line == "":
                            # Event-Grenze: auswerten, falls update.
                            if event_name == "update" and event_data_lines:
                                data_str = "\n".join(event_data_lines)
                                try:
                                    msg = json.loads(
                                        data_str[5:].lstrip()
                                        if data_str.startswith("data:")
                                        else data_str)
                                except json.JSONDecodeError:
                                    pass
                                else:
                                    changed = False
                                    async with state.lock:
                                        if (msg.get("bind") == cfg.bind_telemetry(year)
                                                and state.telemetry.bootstrapped):
                                            state.telemetry.bootstrapped = False
                                        changed = st.dispatch(
                                            msg, state, year=year,
                                            stage=stage, next_stage=next_stage)
                                        if changed and msg.get("bind") == cfg.bind_telemetry(year):
                                            ex.reanchor_on_tick(
                                                state.extrapolated_pos,
                                                state.telemetry.riders,
                                                state.top_n)
                                    if changed:
                                        await state.notify()
                            # Puffer für nächstes Event zurücksetzen.
                            event_name = None
                            event_data_lines = []
                            continue
                        if line.startswith("event:"):
                            event_name = line[6:].strip()
                        elif line.startswith("data:"):
                            event_data_lines.append(line)
                        elif line.startswith("id:"):
                            pass  # Last-Event-ID, derzeit nicht genutzt
        except asyncio.TimeoutError:
            if app["stopping"]:
                return
            # sock_read Timeout: ASO war 30s still. Reconnect mit kurzem Backoff
            # (kein exponentiell, weil das ein erwartetes „warmes" Reconnect ist).
            log.info("SSE sock_read Timeout (30s still); Reconnect in 1s")
            await asyncio.sleep(1)
            backoff = 1
        except (aiohttp.ClientError, ConnectionError) as e:
            if app["stopping"]:
                return
            log.warning("SSE-Verbindung verloren (%s: %s); Reconnect in %ds",
                        type(e).__name__, e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            if app["stopping"]:
                return
            log.exception("Unerwarteter Fehler im SSE-Consumer; Reconnect in %ds",
                          backoff)
            # Jitter hinzufügen, um Thundering-Herd zu vermeiden
            import random
            jitter = backoff * (0.5 + random.random())
            await asyncio.sleep(min(jitter, 30))
            backoff = min(backoff * 2, 30)


# --------------------------------------------------------------------------- #
# Extrapolations-Loop
# --------------------------------------------------------------------------- #
async def extrapolation_loop(app: web.Application) -> None:
    """Alle EXTRAPOLATE_INTERVAL_S Sekunden Top-N-Positionen glätten."""
    state: st.State = app["state"]
    while not app["stopping"]:
        try:
            await asyncio.sleep(cfg.EXTRAPOLATE_INTERVAL_S)
            async with state.lock:
                # Nur wenn Rennen aktiv ist – sonst eingefrorene Positionen.
                if state.telemetry.race_status is True and state.top_n:
                    ex.step_extrapolation(state.extrapolated_pos,
                                          state.telemetry.riders,
                                          state.top_n,
                                          cfg.EXTRAPOLATE_INTERVAL_S)
                    await state.notify()
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            log.exception("Extrapolations-Loop-Fehler")


async def heartbeat_loop(app: web.Application) -> None:
    """Periodischer Herzschlag: schreibt Trail + benachrichtigt WS-Clients,
    auch wenn gerade keine SSE-Updates kommen (post-race, Verbindungsstress).

    Zusätzlich: SSE-Watchdog mit Staleness-Erkennung.
      1. Task beendet (done)? -> neu starten.
      2. Task hängt (nicht done, aber keine Telemetrie seit >90s)? -> canceln
         und neu starten. Das ist der wichtige Fall: der Task kann im
         ``async for raw in resp.content`` blocken, ohne dass ``sock_read``
         greift (ASO schickt Keep-Alive-Pings, die den Read-Timeout resetten,
         aber keine echten Events). Wir trusten der Telemetrie-Aktualität
         als Staleness-Indikator.
    """
    state: st.State = app["state"]
    # Etwas seltener als der Trail-Flush, aber mindestens alle 10s.
    interval = max(cfg.JSONL_FLUSH_EVERY_S, 5.0)
    stale_threshold_s = 90.0  # keine Telemetrie seit >90s während Live-Rennen = Problem
    while not app["stopping"]:
        try:
            await asyncio.sleep(interval)
            sse_task = app.get("sse_task")

            # Fall 1: Task ist beendet (mit oder ohne Exception).
            if sse_task is not None and sse_task.done():
                exc = sse_task.exception() if not sse_task.cancelled() else None
                if exc:
                    log.error("SSE-Task gestorben (%s: %s) — starte neu",
                              type(exc).__name__, exc)
                else:
                    log.warning("SSE-Task beendet (ohne Exception) — starte neu")
                app["sse_task"] = asyncio.create_task(consume_sse(app))

            # Fall 2: Task hängt noch (nicht done), aber keine Telemetrie mehr.
            # Nur während live-Rennen relevant (vor Start gibt's legitimerweise
            # lange keine Updates).
            elif sse_task is not None and not sse_task.done():
                last_tel = state.telemetry.timestamp
                rs = state.telemetry.race_status
                if rs is True and last_tel:
                    age = time.time() - float(last_tel)
                    if age > stale_threshold_s:
                        log.warning(
                            "SSE stale: keine Telemetrie seit %.0fs (RaceStatus=True) "
                            "— cancelle Task und starte neu", age)
                        sse_task.cancel()
                        try:
                            await sse_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        app["sse_task"] = asyncio.create_task(consume_sse(app))

            async with state.lock:
                state.evaluate_features()
            await state.notify(force=True)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            log.exception("Heartbeat-Loop-Fehler")


# --------------------------------------------------------------------------- #
# HTTP-Handler
# --------------------------------------------------------------------------- #
async def handle_state(request: web.Request) -> web.Response:
    state: st.State = request.app["state"]
    top = request.query.get("top")
    top_int = int(top) if top and top.isdigit() else None
    async with state.lock:
        snap = st.to_snapshot(state, top_limit=top_int)
    return web.json_response(snap)


async def handle_profile(request: web.Request) -> web.Response:
    prof = request.app.get("profile")
    if prof is None:
        return web.json_response({"available": False})
    return web.json_response({"available": True, **profile_mod.to_json(prof)})


# --------------------------------------------------------------------------- #
# Steuerungs-Endpunkte (Auth via auth_middleware)
# --------------------------------------------------------------------------- #
async def handle_health(request: web.Request) -> web.Response:
    """Health-Endpoint für Monitoring/Uptime-Checks."""
    state: st.State = request.app["state"]
    return web.json_response({
        "ok": True,
        "status": state.telemetry.race_status,
        "ts": state.telemetry.timestamp,
        "stage": state.stage,
        "year": state.year,
    })


async def handle_control_get(request: web.Request) -> web.Response:
    """Status des systemd-Dienstes (is-active)."""
    if not control_mod.is_available():
        return web.json_response(control_mod.service_status(), status=503)
    return web.json_response(control_mod.service_status())


async def handle_control_post(request: web.Request) -> web.Response:
    """Aktion auslösen: ?action=start|stop|restart."""
    action = request.query.get("action", "").strip()
    if not action:
        return web.json_response({"ok": False, "error": "action fehlt"}, status=400)
    if not control_mod.is_available():
        return web.json_response(control_mod.service_action(action), status=503)
    res = control_mod.service_action(action)
    code = 200 if res.get("ok") else 500
    return web.json_response(res, status=code)


async def handle_trail(request: web.Request) -> web.Response:
    """Letzte N Snapshots aus dem JSONL-Trail."""
    n = request.query.get("n", "20")
    try:
        n_int = max(1, min(int(n), 500))
    except ValueError:
        n_int = 20
    path = request.app.get("jsonl_path") or cfg.JSONL_PATH
    snaps = trail_mod.read_last_n(path, n_int)
    return web.json_response({"n": len(snaps), "path": path, "snapshots": snaps})


async def handle_index(request: web.Request) -> web.Response:
    return web.FileResponse(WEB_DIR / "index.html")


# --------------------------------------------------------------------------- #
# Neue API-Endpunkte (Features 1-5)
# --------------------------------------------------------------------------- #
async def handle_predictions(request: web.Request) -> web.Response:
    """Feature 1: Restzeit-Vorhersage als eigenständiger Endpoint."""
    state: st.State = request.app["state"]
    async with state.lock:
        top_riders_data = [
            state.telemetry.riders.get(bib, {}) for bib in state.top_n
        ]
        predictions = prediction_mod.compute_predictions(
            groups=[st._group_json(g, state.meta) for g in state.groups],
            top_riders=top_riders_data,
        )
    return web.json_response({
        "predictions": predictions,
        "count": len(predictions),
    })


async def handle_gap_chart(request: web.Request) -> web.Response:
    """Feature 2: Gap-Chart-Zeitreihen."""
    state: st.State = request.app["state"]
    since = request.query.get("since")
    top_n = request.query.get("top_n", "20")
    try:
        top_n_int = max(1, min(int(top_n), 50))
    except ValueError:
        top_n_int = 20
    since_float: float | None = None
    if since:
        try:
            since_float = float(since)
        except ValueError:
            pass
    async with state.lock:
        chart = state.gap_history.get_history(
            top_n=top_n_int, since=since_float)
    return web.json_response(chart)


async def handle_classification(request: web.Request) -> web.Response:
    """Feature 3: Berg/Sprint-Klassifikation als eigenständiger Endpoint."""
    state: st.State = request.app["state"]
    async with state.lock:
        gc_list = state.virtual_gc or state.base_gc
        cls_state = classification_mod.parse_checkpoints_to_classifications(
            state.checkpoints, list(state.groups))
        cls_state.kom_ranking = classification_mod.extract_mountain_ranking(
            state.jerseys, state.meta, gc_list)
        cls_state.sprint_ranking = classification_mod.extract_sprint_ranking(
            state.jerseys, state.meta, gc_list)
        classifications = classification_mod.to_json(
            cls_state, state.meta, state.jerseys, gc_list)
    return web.json_response(classifications)


async def handle_alarms(request: web.Request) -> web.Response:
    """Feature 4: Alarme (letzte N)."""
    state: st.State = request.app["state"]
    limit = request.query.get("limit", "20")
    try:
        limit_int = max(1, min(int(limit), 100))
    except ValueError:
        limit_int = 20
    async with state.lock:
        alarms = state.alarm_system.get_alarms(limit=limit_int)
    return web.json_response({"alarms": alarms, "count": len(alarms)})


async def handle_map_data(request: web.Request) -> web.Response:
    """Feature 5: Karten-Koordinaten als eigenständiger Endpoint."""
    state: st.State = request.app["state"]
    profile = request.app.get("profile")
    async with state.lock:
        gc_list = state.virtual_gc or state.base_gc
        cls_state = classification_mod.parse_checkpoints_to_classifications(
            state.checkpoints, list(state.groups))
        classifications = classification_mod.to_json(
            cls_state, state.meta, state.jerseys, gc_list)
        top_riders_data = [
            state.telemetry.riders.get(bib, {}) for bib in state.top_n
        ]
        mdata = map_data_mod.build_map_data(
            groups=[st._group_json(g, state.meta) for g in state.groups],
            top_riders=top_riders_data,
            checkpoints=state.checkpoints,
            classifications=classifications,
            profile=profile,
        )
    return web.json_response(mdata)


# --------------------------------------------------------------------------- #
# Technische Features (6-9): Power, Time-Cut, Survival
# --------------------------------------------------------------------------- #
async def handle_power(request: web.Request) -> web.Response:
    """Feature 6: Watt + W/kg-Leaderboard für die Top-N-Rider."""
    state: st.State = request.app["state"]
    async with state.lock:
        snap = st.to_snapshot(state)
    return web.json_response(snap.get("power") or {"gradient_pct": 0, "riders": []})


async def handle_time_cut(request: web.Request) -> web.Response:
    """Feature 7: Time-Cut-Analyse aller Gruppen."""
    state: st.State = request.app["state"]
    async with state.lock:
        snap = st.to_snapshot(state)
    return web.json_response(snap.get("time_cut") or {"groups": []})


async def handle_breakaway_survival(request: web.Request) -> web.Response:
    """Feature 8: Ausreißer-Überlebenssimulation."""
    state: st.State = request.app["state"]
    async with state.lock:
        snap = st.to_snapshot(state)
    return web.json_response(snap.get("breakaway_survival") or {"available": False})


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #
async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    state: st.State = request.app["state"]
    log.info("WS-Client verbunden (%d gesamt)",
             len(request.app["ws_clients"]) + 1)
    request.app["ws_clients"].add(ws)

    # Snapshot direkt beim Verbinden.
    async with state.lock:
        snap = st.to_snapshot(state)
    try:
        await ws.send_json(snap)
    except ConnectionResetError:
        pass

    try:
        async for _ in ws:
            pass  # Wir empfangen nichts; reines Broadcast.
    finally:
        request.app["ws_clients"].discard(ws)
        log.info("WS-Client getrennt (%d verbleibend)",
                 len(request.app["ws_clients"]))
    return ws


async def broadcast(state: st.State) -> None:
    """Listener: schreibt Trail (immer) und broadcastet an WS-Clients (wenn welche).

    WIRD vom SSE-Consumer innerhalb von state.lock aufgerufen (via
    state.notify()). Daher dürfen wir das Lock hier NICHT erneut nehmen —
    asyncio.Lock ist nicht reentrant, das würde deadlocken. Wenn das Lock
    schon gehalten wird (was beim Aufruf aus consume_sse der Fall ist),
    rufen wir to_snapshot direkt auf.
    """
    app = state._app  # type: ignore[attr-defined]
    if state.lock.locked():
        # Lock schon gehalten (wir wurden aus einem Lock-Kontext aufgerufen):
        # Snapshot direkt nehmen, ohne erneut zu locken.
        snap = st.to_snapshot(state)
    else:
        async with state.lock:
            snap = st.to_snapshot(state)

    # 1) Trail mitschneiden (unabhängig von WS-Clients, throttelt).
    trail: trail_mod.JsonlTrail | None = app.get("trail")
    if trail is not None:
        trail.write(snap)

    # 2) WebSocket-Broadcast mit Timeout pro Client (langsame Clients blockieren nicht).
    if not app["ws_clients"]:
        return
    payload = json.dumps(snap, ensure_ascii=False)
    dead: list[web.WebSocketResponse] = []
    for ws in list(app["ws_clients"]):
        try:
            await asyncio.wait_for(ws.send_str(payload), timeout=5.0)
        except (ConnectionResetError, RuntimeError, asyncio.TimeoutError):
            dead.append(ws)
    for ws in dead:
        app["ws_clients"].discard(ws)


# --------------------------------------------------------------------------- #
# App-Lifecycle
# --------------------------------------------------------------------------- #
async def on_startup(app: web.Application) -> None:
    state: st.State = app["state"]
    connector = aiohttp.TCPConnector(ssl=not app.get("insecure", False))
    app["http_session"] = aiohttp.ClientSession(connector=connector)

    log.info("Startup: lade statische Daten für TdF %d ...", state.year)
    try:
        riders = await static.load_riders(app["http_session"], state.year)
        stages = await static.load_stages(app["http_session"], state.year)
        state.meta = riders
        log.info("%d Starter, %d Etappen geladen.", len(riders), len(stages))
        app["stages"] = stages
        if state.stage is None:
            state.stage = (static.today_stage_number(stages)
                           or (stages[0].get("stage") if stages else None))
            log.info("Heutige Etappe automatisch ermittelt: %s", state.stage)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.error("Statische Daten nicht ladbar: %s. Server startet ohne (SSE füllt nach).", e)
        app["stages"] = []

    if state.stage is not None:
        log.info("Bootstrap (REST) für Etappe %d ...", state.stage)
        try:
            await bootstrap.bootstrap_state(state, app["http_session"],
                                            year=state.year, stage=state.stage,
                                            next_stage=state.stage + 1)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.error("Bootstrap fehlgeschlagen: %s. Server startet ohne.", e)

    # Profil (Best-Effort).
    try:
        if state.stage is not None:
            app["profile"] = await profile_mod.try_load_profile(
                app["http_session"], state.year, state.stage)
        else:
            app["profile"] = None
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("Profil-Laden fehlgeschlagen: %s", e)
        app["profile"] = None

    # Profil-Punkte für Live-Gradient (Feature 6) in den State spiegeln.
    # Das erlaubt to_snapshot, die aktuelle Steigung ohne app-Zugriff zu
    # berechnen. Profile-Objekt hat .points (list[ProfilePoint]).
    prof = app.get("profile")
    if prof is not None and hasattr(prof, "points"):
        state.profile_points = [
            {"km_done": float(p.km_done), "alt": float(p.alt_m)}
            for p in prof.points
        ]
        log.info("Profil-Punkte für Live-Gradient: %d", len(state.profile_points))
    else:
        state.profile_points = []

    # Etappentyp für Time-Cut (Feature 7). Best-Effort aus dem
    # geladenen Stage-Objekt; Default 'flat' ist sicher.
    try:
        stages = app.get("stages") or []
        if stages and state.stage is not None:
            stage_obj = static.find_stage(stages, state.stage)
            if stage_obj is not None:
                stype = (stage_obj.get("stageType") or "").lower()
                # Mapping ASO -> unsere Typen.
                if "mountain" in stype or "high_mountain" in stype:
                    state.stage_type = "mountain"
                elif "medium" in stype or "hilly" in stype:
                    state.stage_type = "medium"
                elif "time_trial" in stype or stype == "itt":
                    state.stage_type = "itt"
                elif "team_time_trial" in stype or stype == "ttt":
                    state.stage_type = "ttt"
                else:
                    state.stage_type = "flat"
                log.info("Stage-Typ für Time-Cut: %s", state.stage_type)
    except Exception as e:
        log.warning("Stage-Typ-Bestimmung fehlgeschlagen: %s", e)

    # JSONL-Trail anlegen (wenn Pfad gesetzt).
    jsonl_path = app.get("jsonl_path") or cfg.JSONL_PATH
    if jsonl_path:
        app["trail"] = trail_mod.JsonlTrail(jsonl_path)
        log.info("JSONL-Trail nach %s (throttelt %.1fs)",
                 jsonl_path, cfg.JSONL_FLUSH_EVERY_S)
    else:
        app["trail"] = None

    # Auth-Status loggen (nur beim Start, einmalig).
    if cfg.TOKEN:
        log.info("Auth aktiviert (TDF_TOKEN gesetzt): /api/* und /ws brauchen Token.")
    else:
        log.info("Auth DEAKTIVIERT (TDF_TOKEN leer) – nur für localhost/Vertrauensnetz.")

    # Broadcast-Listener registrieren.
    state._app = app  # type: ignore[attr-defined]
    state.add_listener(broadcast)

    # Hintergrund-Tasks.
    app["stopping"] = False
    app["sse_task"] = asyncio.create_task(consume_sse(app))
    app["extrap_task"] = asyncio.create_task(extrapolation_loop(app))
    app["heartbeat_task"] = asyncio.create_task(heartbeat_loop(app))
    log.info("Server bereit.  http://localhost:%d  (Etappe %d, Jahr %d)",
             app["port"], state.stage, state.year)


async def on_cleanup(app: web.Application) -> None:
    app["stopping"] = True
    for key in ("sse_task", "extrap_task", "heartbeat_task"):
        t = app.get(key)
        if t:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
    session = app.get("http_session")
    if session:
        await session.close()
    for ws in list(app.get("ws_clients", set())):
        await ws.close()


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def build_app(*, year: int, stage: int | None, port: int,
              insecure: bool = False, jsonl_path: str | None = None) -> web.Application:
    state = st.State(year=year, stage=stage)
    app = web.Application(client_max_size=2 * 1024 * 1024,
                          middlewares=[auth_mod.auth_middleware])
    app["state"] = state
    app["port"] = port
    app["insecure"] = insecure
    app["ws_clients"] = set()
    app["jsonl_path"] = jsonl_path  # None = ENV-Default wird im Startup genutzt

    # Steuerung & Daten (Auth via Middleware auf /api/* und /ws).
    app.router.add_get("/state", handle_state)
    app.router.add_get("/profile", handle_profile)
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/control", handle_control_get)
    app.router.add_post("/api/control", handle_control_post)
    app.router.add_get("/api/trail", handle_trail)
    # Neue API-Endpunkte (Features 1-5)
    app.router.add_get("/api/prediction", handle_predictions)
    app.router.add_get("/api/gap-chart", handle_gap_chart)
    app.router.add_get("/api/classification", handle_classification)
    app.router.add_get("/api/alarms", handle_alarms)
    app.router.add_get("/api/map-data", handle_map_data)
    app.router.add_get("/api/power", handle_power)
    app.router.add_get("/api/time-cut", handle_time_cut)
    app.router.add_get("/api/breakaway-survival", handle_breakaway_survival)
    app.router.add_get("/ws", handle_ws)
    # Frontend (keine Auth – Token geht via JS in Fetch-Header).
    app.router.add_get("/", handle_index)
    if WEB_DIR.is_dir():
        app.router.add_static("/", path=str(WEB_DIR), show_index=False)

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m tdf.server",
                                description="TdF Live-Server (aiohttp)")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--stage", type=int, default=None,
                   help="Etappe (default: heutige, sonst erste)")
    p.add_argument("--port", type=int, default=cfg.DEFAULT_PORT)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--insecure", action="store_true",
                   help="SSL-Zertifikatsprüfung deaktivieren (nur für defekte "
                        "lokale CA-Bundles, z. B. frische python.org-Installs).")
    p.add_argument("--jsonl", default=None,
                   help="JSONL-Trail-Pfad (default: $TDF_JSONL_PATH oder "
                        "/var/lib/tdf-tracker/live.jsonl). Leerstring deaktiviert.")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    app = build_app(year=args.year, stage=args.stage, port=args.port,
                    insecure=args.insecure, jsonl_path=args.jsonl)
    web.run_app(app, host=args.host, port=args.port, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
