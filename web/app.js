/* TdF Live Tracker — Sport-Tabellarisch Frontend
 * Information-dense dashboard: KPIs, Two-Column GC, W/kg leaderboard,
 * live groups with collective power, survival, time-cut, alarms.
 */

"use strict";

// ----------------------------------------------------------------- #
// Helpers
// ----------------------------------------------------------------- #
const $ = (id) => document.getElementById(id);
const PAD = (n) => String(n).padStart(2, "0");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));

let ws = null;
let pollTimer = null;
let visible = !document.hidden;
let lastData = null;
let pendingFrame = null;
let controlAvailable = null;
let wkgSortKey = "w_per_kg";  // Sortierung des W/kg-Boards

// ----------------------------------------------------------------- #
// Token-Handling
// ----------------------------------------------------------------- #
function getToken() {
  const fromUrl = new URLSearchParams(location.search).get("k");
  if (fromUrl) {
    localStorage.setItem("tdf_token", fromUrl);
    history.replaceState(null, "", location.pathname);
    return fromUrl;
  }
  return localStorage.getItem("tdf_token") || "";
}

function authQuery() {
  const t = getToken();
  return t ? `?k=${encodeURIComponent(t)}` : "";
}

function authQuerySep(sep = "?") {
  const t = getToken();
  return t ? `${sep}k=${encodeURIComponent(t)}` : "";
}

function showAuthBar() {
  const bar = $("authbar");
  if (!bar) return;
  bar.hidden = false;
  $("token-save").onclick = () => {
    const v = $("token-input").value.trim();
    if (v) {
      localStorage.setItem("tdf_token", v);
      history.replaceState(null, "", location.pathname);
      bar.hidden = true;
      location.reload();
    }
  };
}

// ----------------------------------------------------------------- #
// Formatierung
// ----------------------------------------------------------------- #
function fmtGap(s) {
  if (s == null) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}'${PAD(Math.round(s % 60))}"`;
  return `${(s / 3600).toFixed(1)}h`;
}

function fmtKm(km) {
  if (km == null) return "—";
  return `${km.toFixed(1)} km`;
}

// ----------------------------------------------------------------- #
// Render: Header + Status
// ----------------------------------------------------------------- #
function setStatus(status, freshness) {
  const pill = $("status-pill");
  const fresh = $("freshness");
  const map = {
    "LIVE": ["LIVE", "pill--live"],
    "STALE": ["STALE", "pill--stale"],
    "NO TELEMETRY": ["…", "pill--idle"],
    "UNKNOWN": ["?", "pill--unknown"],
  };
  const [text, cls] = map[status] || ["?", "pill--unknown"];
  pill.textContent = text;
  pill.className = `pill ${cls}`;
  if (fresh) fresh.textContent = freshness || "";
}

// ----------------------------------------------------------------- #
// Render: Pace Context Banner
// ----------------------------------------------------------------- #
function renderPaceContext(d) {
  const banner = $("pace-banner");
  const pc = d.pace_context;
  if (!pc || !pc.available) {
    if (banner) banner.hidden = true;
    return;
  }
  if (banner) {
    banner.hidden = false;
    banner.className = `pace-banner pace-banner--${pc.pressure_level || "medium"}`;
    $("pace-icon").textContent = pc.pressure_level === "high" ? "🔥" :
                                  pc.pressure_level === "low" ? "💨" : "⚡";
    $("pace-text").textContent = pc.pressure || pc.trend || "—";
  }
}

// ----------------------------------------------------------------- #
// Render: KPI Leiste
// ----------------------------------------------------------------- #
function renderKPIs(d) {
  const starters = d.prev_stage_gc?.length || d.gc?.length || 0;
  const active = d.power?.total_with_power || 0;
  const dnf = d.withdrawals?.length || 0;
  const groups = d.groups?.length || 0;
  const speed = d.pace_context?.avg_speed_kph;

  $("kpi-starters").textContent = starters;
  $("kpi-active").textContent = active;
  $("kpi-dnf").textContent = dnf;
  $("kpi-groups").textContent = groups;
  $("kpi-speed").textContent = speed ? speed.toFixed(0) : "—";
}

// ----------------------------------------------------------------- #
// Render: Two-Column GC (Vortag + Live)
// ----------------------------------------------------------------- #
function renderGC(d) {
  // Spalte 1: Vortag (prev_stage_gc)
  const prev = d.prev_stage_gc || [];
  const live = d.current_stage_gc || [];
  renderGCTable("gc-prev-body", prev.slice(0, 10), "Vortag");
  renderGCTable("gc-live-body", live.slice(0, 10), "Live");

  // Source-Label
  const src = $("gc-source");
  if (src) {
    if (live.length > 0) src.textContent = "Live (aktuelle Etappe)";
    else if (prev.length > 0) src.textContent = "Stand vom Vortag";
    else src.textContent = "—";
  }
  // Etappen-Nummern
  if ($("gc-prev-stage")) $("gc-prev-stage").textContent = `(Etappe ${(d.stage || 1) - 1})`;
  if ($("gc-live-stage")) $("gc-live-stage").textContent = `(Etappe ${d.stage})`;
}

function renderGCTable(tbodyId, entries, label) {
  const tb = $(tbodyId);
  if (!tb) return;
  if (!entries.length) {
    tb.innerHTML = `<tr><td colspan="4" class="empty">Noch keine Daten</td></tr>`;
    return;
  }
  tb.innerHTML = entries.map((e) => {
    const name = esc(e.name || "?");
    const team = esc(e.team || "");
    const rel = e.rel_s != null ? (e.rel_s === 0 ? "—" : `+${fmtGap(e.rel_s)}`) : "—";
    return `<tr>
      <td>${e.pos || "?"}</td>
      <td>${name}</td>
      <td class="muted">${team}</td>
      <td class="num">${rel}</td>
    </tr>`;
  }).join("");
}

// ----------------------------------------------------------------- #
// Render: W/kg Leaderboard (sortierbar)
// ----------------------------------------------------------------- #
function renderPower(d) {
  const p = d.power;
  const meta = $("wkg-meta");
  const tb = $("wkg-body");
  if (!p || !p.riders || !p.riders.length) {
    if (meta) meta.textContent = "—";
    if (tb) tb.innerHTML = `<tr><td colspan="5" class="empty">Keine Telemetrie</td></tr>`;
    return;
  }
  if (meta) {
    const grad = p.gradient_pct ?? 0;
    meta.textContent = `${p.total_with_power || 0} Rider` +
      (grad ? ` · ${grad}% Steigung` : "");
  }
  // Sortieren nach gewähltem Key
  const riders = [...p.riders].sort((a, b) => {
    const va = a[wkgSortKey] ?? 0;
    const vb = b[wkgSortKey] ?? 0;
    return vb - va;  // absteigend
  }).slice(0, 20);

  tb.innerHTML = riders.map((r, i) => {
    const wkg = r.w_per_kg ?? 0;
    const wkgClass = wkg >= 6 ? "wkg-val--high" : wkg >= 4 ? "wkg-val--med" : "wkg-val--low";
    return `<tr>
      <td>${i + 1}</td>
      <td>${esc(r.label || "?")}</td>
      <td class="num"><span class="wkg-val ${wkgClass}">${wkg.toFixed(1)}</span></td>
      <td class="num">${Math.round(r.watts || 0)}</td>
      <td class="num">${(r.speed_kph || 0).toFixed(1)}</td>
    </tr>`;
  }).join("");
}

// Sortier-Buttons initialisieren
function initWkgSort() {
  document.querySelectorAll("[data-sort]").forEach((btn) => {
    btn.addEventListener("click", () => {
      wkgSortKey = btn.dataset.sort;
      document.querySelectorAll("[data-sort]").forEach((b) =>
        b.classList.toggle("btn--active", b === btn));
      if (lastData) renderPower(lastData);
    });
  });
  // Default aktiv
  const def = document.querySelector('[data-sort="w_per_kg"]');
  if (def) def.classList.add("btn--active");
}

// ----------------------------------------------------------------- #
// Render: Live-Gruppen (Tabelle mit kollektivem W/kg)
// ----------------------------------------------------------------- #
function renderGroups(d) {
  const tb = $("groups-body");
  const info = $("groups-info");
  if (!tb) return;
  const groups = d.groups || [];
  if (!groups.length) {
    tb.innerHTML = `<tr><td colspan="7" class="empty">Keine Gruppen</td></tr>`;
    return;
  }
  if (info) info.textContent = `${groups.length} Gruppen`;

  // Größte Gruppe = Peloton
  const maxSize = Math.max(...groups.map((g) => g.size || 0));

  tb.innerHTML = groups.map((g) => {
    const cp = g.collective_power;
    const wkgCol = cp ? `${cp.w_per_kg} <span class="muted">(${cp.label})</span>` : "—";
    const isPeloton = (g.size || 0) === maxSize && (g.size || 0) > 10;
    const status = isPeloton
      ? `<span class="status-pill status-pill--peloton">PELOTON</span>`
      : (g.gap_s === 0 || g.gap_s == null)
        ? `<span class="status-pill status-pill--breakaway">SPITZE</span>`
        : `<span class="muted">Verfolgung</span>`;
    return `<tr>
      <td>${esc(g.name || "?")}</td>
      <td class="num">${g.size || "—"}</td>
      <td class="num">${g.gap_s != null ? fmtGap(g.gap_s) : "—"}</td>
      <td class="num">${g.speed_kph != null ? g.speed_kph.toFixed(1) : "—"}</td>
      <td class="num">${wkgCol}</td>
      <td class="num">${g.remaining_km != null ? g.remaining_km.toFixed(1) : "—"}</td>
      <td class="col-status">${status}</td>
    </tr>`;
  }).join("");
}

// ----------------------------------------------------------------- #
// Render: Survival
// ----------------------------------------------------------------- #
function renderSurvival(d) {
  const box = $("survival-box");
  const surv = d.breakaway_survival;
  if (!surv || surv.available === false) {
    if (box) box.style.opacity = "0.4";
    if ($("survival-pct")) $("survival-pct").textContent = "—";
    if ($("survival-meta")) $("survival-meta").textContent = "Keine Ausreißer";
    return;
  }
  if (box) box.style.opacity = "1";
  const pct = surv.survival_pct ?? 0;
  if ($("survival-fill")) $("survival-fill").style.width = `${pct}%`;
  if ($("survival-pct")) $("survival-pct").textContent = `${pct.toFixed(0)}%`;
  if ($("survival-meta")) {
    $("survival-meta").innerHTML =
      `${esc(surv.breakaway_name || "?")} (${surv.breakaway_size} F.)` +
      ` · ${fmtGap(surv.gap_s)} Vorsprung` +
      ` · Konfidenz: <strong>${surv.confidence}</strong>`;
  }
}

// ----------------------------------------------------------------- #
// Render: Time-Cut
// ----------------------------------------------------------------- #
function renderTimeCut(d) {
  const tc = d.time_cut;
  const list = $("tc-list");
  const thr = $("tc-threshold");
  if (!tc || !tc.groups) {
    if (list) list.innerHTML = `<div class="empty">Keine Time-Cut-Daten</div>`;
    return;
  }
  if (thr) thr.textContent = tc.threshold_pct ? `${tc.threshold_pct}% Cut` : "—";

  const relevant = tc.groups.filter((g) => g.status !== "safe").slice(0, 8);
  if (!list) return;
  if (!relevant.length) {
    list.innerHTML = `<div class="empty">Alle Gruppen sicher</div>`;
    return;
  }
  list.innerHTML = relevant.map((g) => {
    const margin = g.margin_s != null
      ? (g.margin_s >= 0 ? `+${fmtGap(g.margin_s)}` : "über Cut")
      : "—";
    return `<div class="tc-row tc-row--${g.status}">
      <span>${esc(g.name || "?")}</span>
      <span class="tc-row__margin">${margin}</span>
      <span class="status-pill status-pill--${g.status}">${g.status.toUpperCase()}</span>
    </div>`;
  }).join("");
}

// ----------------------------------------------------------------- #
// Render: Trikots
// ----------------------------------------------------------------- #
function renderJerseys(d) {
  const cont = $("jerseys");
  if (!cont) return;
  const jerseys = d.jerseys || {};
  const entries = Object.entries(jerseys);
  if (!entries.length) {
    cont.innerHTML = `<div class="empty">Keine Trikots</div>`;
    return;
  }
  const names = { Y: "Gelb", G: "Grün", P: "Berg", W: "Weiß" };
  cont.innerHTML = entries.map(([code, j]) => {
    const name = j.name || names[code] || code;
    const bib = j.bib || "?";
    return `<div class="jersey jersey--${code}">
      <span class="jersey__color"></span>
      <span class="jersey__name">${esc(name)}</span>
      <span class="jersey__bib">#${bib}</span>
    </div>`;
  }).join("");
}

// ----------------------------------------------------------------- #
// Render: Alarme
// ----------------------------------------------------------------- #
function renderAlarms(d) {
  const cont = $("alarms");
  if (!cont) return;
  const alarms = d.alarms || [];
  if (!alarms.length) {
    cont.innerHTML = `<div class="empty">Keine Alarme</div>`;
    return;
  }
  cont.innerHTML = alarms.slice(0, 15).map((a) => {
    const t = new Date((a.ts || 0) * 1000);
    const tStr = t.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    return `<div class="alarm alarm--${a.severity || "info"}">
      <span class="alarm__msg">${esc(a.message || a.kind)}</span>
      <span class="alarm__time">${tStr}</span>
    </div>`;
  }).join("");
}

// ----------------------------------------------------------------- #
// Render: Control Bar
// ----------------------------------------------------------------- #
async function pollControl() {
  try {
    const r = await fetch(`/api/control${authQuery()}`);
    if (!r.ok) return;
    const d = await r.json();
    controlAvailable = d.active != null ? true : d.available;
    const bar = $("ctrlbar");
    if (controlAvailable && bar) bar.hidden = false;
    const st = $("ctrl-state");
    if (st) st.textContent = d.active ? "● aktiv" : "● gestoppt";
  } catch (e) { /* still */ }
}

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;
  const action = btn.dataset.action;
  if (!confirm(`Dienst ${action}?`)) return;
  try {
    await fetch(`/api/control?action=${action}${authQuerySep("&")}`, { method: "POST" });
    setTimeout(pollControl, 2000);
  } catch (err) { alert("Fehler: " + err); }
});

// ----------------------------------------------------------------- #
// Footer
// ----------------------------------------------------------------- #
function setLastUpdate() {
  const el = $("last-update");
  if (el) el.textContent = `Update: ${new Date().toLocaleTimeString("de-DE")}`;
}

// ----------------------------------------------------------------- #
// Render-Dispatch (batched via rAF)
// ----------------------------------------------------------------- #
function applyUpdate(data) {
  lastData = data;
  if (pendingFrame) return;
  pendingFrame = requestAnimationFrame(() => {
    pendingFrame = null;
    if (!lastData) return;
    const d = lastData;
    $("stage-label").textContent = `TdF ${d.year} · Etappe ${d.stage ?? "?"}`;
    setStatus(d.status, d.freshness);
    renderPaceContext(d);
    renderKPIs(d);
    renderGC(d);
    renderPower(d);
    renderGroups(d);
    renderSurvival(d);
    renderTimeCut(d);
    renderJerseys(d);
    renderAlarms(d);
    setLastUpdate();
  });
}

// ----------------------------------------------------------------- #
// Anbindung: WebSocket primär, /state-Fallback
// ----------------------------------------------------------------- #
function connectWS() {
  const tok = getToken();
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws${authQuery()}`);

  ws.onmessage = (ev) => {
    try { applyUpdate(JSON.parse(ev.data)); } catch (e) { /* ignore */ }
  };
  ws.onclose = () => {
    ws = null;
    if (visible) setTimeout(connectWS, 2000);  // reconnect
  };
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
}

async function pollState() {
  if (ws && ws.readyState === WebSocket.OPEN) return;
  try {
    const r = await fetch(`/state${authQuery()}`);
    if (!r.ok) {
      if (r.status === 401) showAuthBar();
      return;
    }
    applyUpdate(await r.json());
  } catch (e) { /* Netzfehler, retry beim nächsten Tick */ }
}

// ----------------------------------------------------------------- #
// Init
// ----------------------------------------------------------------- #
function init() {
  // Token aus URL oder localStorage
  if (!getToken()) {
    showAuthBar();
  }
  initWkgSort();
  connectWS();
  // Polling als Fallback alle 5s, falls WS nicht funktioniert
  pollTimer = setInterval(pollState, 5000);
  pollState();
  pollControl();
  setInterval(pollControl, 15000);

  // Pause wenn Tab unsichtbar
  document.addEventListener("visibilitychange", () => {
    visible = !document.hidden;
    if (visible && !ws) connectWS();
  });
}

init();
