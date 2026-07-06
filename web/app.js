/* TdF Live – Frontend.
 *
 * Upgrade: Glassmorphism, Floating Cards, Partikel-Hintergrund, Staggered Typography.
 * Dark industrial sci-fi TdF-Ästhetik. Alle Features erhalten.
 *
 * Prinzipien (Performance/Mobil):
 *   • Primär WebSocket /ws, Fallback: setTimeout-Rekursion auf /state (kein setInterval).
 *   • DOM-Diff: bestehende Zeilen wiederverwenden, nur textContent mutieren.
 *   • requestAnimationFrame batcht alle DOM-Writes eines Updates.
 *   • IntersectionObserver: nur sichtbare GC-Zeilen rendern.
 *   • visibilitychange: im Hintergrund seltener pollen (10s), sonst 2s/WS.
 *
 * Features 6-10: Gap-Chart, Karten-Marker (Checkpoints), Restzeit-Anzeige,
 *   Berg/Sprint-Panel, Alarm-Panel.
 */

// ─── Partikel-Canvas Hintergrund ───
(function initParticles() {
  const canvas = document.getElementById('particle-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let particles = [];
  const COUNT = 60;

  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
  }
  resize();
  window.addEventListener('resize', resize);

  for (let i = 0; i < COUNT; i++) {
    particles.push({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.3,
      vy: (Math.random() - 0.5) * 0.3,
      r: Math.random() * 2 + 0.5,
      a: Math.random() * 0.4 + 0.05,
    });
  }

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (const p of particles) {
      p.x += p.vx;
      p.y += p.vy;
      if (p.x < 0) p.x = canvas.width;
      if (p.x > canvas.width) p.x = 0;
      if (p.y < 0) p.y = canvas.height;
      if (p.y > canvas.height) p.y = 0;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(253, 218, 36, ${p.a})`;
      ctx.fill();
    }
    // Verbindungslinien zwischen nahen Partikeln
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 150) {
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.strokeStyle = `rgba(253, 218, 36, ${0.03 * (1 - dist / 150)})`;
          ctx.lineWidth = 0.5;
          ctx.stroke();
        }
      }
    }
    requestAnimationFrame(draw);
  }
  draw();
})();

(() => {
"use strict";

const $ = (id) => document.getElementById(id);
const PAD = (n) => String(n).padStart(2, "0");

let ws = null;
let pollTimer = null;
let visible = !document.hidden;
let lastData = null;
let pendingFrame = null;
let controlAvailable = null;  // null = unbekannt, true/false nach erstem /api/control
let sseConnected = false;     // Verbindungsstatus für UI

// -----------------------------------------------------------------
// Token-Handling
// -----------------------------------------------------------------
function getToken() {
  const fromUrl = new URLSearchParams(location.search).get("k");
  if (fromUrl) {
    localStorage.setItem("tdf_token", fromUrl);
    return fromUrl;
  }
  return localStorage.getItem("tdf_token") || "";
}

function authHeaders() {
  const t = getToken();
  return t ? { "Authorization": `Bearer ${t}` } : {};
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

// Leaflet
let map = null;
const markerGroups = { group: {}, rider: {}, checkpoint: [] };

// GC-Zeilen-Cache für DOM-Diff: bib -> {tr, cells}
const gcRows = new Map();
// Top-Card-Cache: bib -> {card, fields}
const topCards = new Map();

// ----------------------------------------------------------------- #
// Status Quo Header – immer sichtbar
// ----------------------------------------------------------------- #
function renderStatusQuo(data) {
  const badge = $("sq-pill");
  const badgeContainer = $("sq-status-badge");

  // Stage
  $("sq-stage").textContent = data.stage != null
    ? `${data.year ?? "?"} · Etappe ${data.stage}`
    : "Kein Rennen aktiv";

  // Status-Pill im Quo-Header
  const status = data.status || "IDLE";
  badge.textContent = status;
  badge.className = "sq-pill";
  if (status === "LIVE") {
    badge.classList.add("sq-pill--live");
    badgeContainer.className = "sq-status-badge sq-status-badge--live";
  } else if (status === "STALE") {
    badge.classList.add("sq-pill--stale");
    badgeContainer.className = "sq-status-badge sq-status-badge--stale";
  } else {
    badge.classList.add("sq-pill--idle");
    badgeContainer.className = "sq-status-badge sq-status-badge--idle";
  }

  // Auch die Topbar-Pille aktualisieren
  const topPill = $("status-pill");
  topPill.textContent = status;
  topPill.className = "pill";
  if (status === "LIVE") topPill.classList.add("pill--live");
  else if (status === "STALE") topPill.classList.add("pill--stale");
  else topPill.classList.add("pill--idle");

  // Freshness
  const freshness = data.freshness || "";
  $("sq-freshness").textContent = freshness;
  $("freshness").textContent = freshness;

  // SSE/WS-Verbindungsstatus
  const sseDot = $("sse-dot");
  if (sseConnected) {
    sseDot.className = "sse-dot sse-dot--on";
    sseDot.title = "Verbunden ✅";
  } else {
    sseDot.className = "sse-dot sse-dot--off";
    sseDot.title = "Getrennt / Polling ❌";
  }

  // Nächste Etappe
  const nextEl = $("sq-next");
  if (data.next_stage != null) {
    nextEl.textContent = `Etappe ${data.next_stage} · ${data.next_stage_date ?? "?"}`;
  } else {
    nextEl.textContent = status === "LIVE" ? "Läuft gerade" : "–";
  }

  // Mini-Statistiken
  const groups = data.groups || [];
  const gc = data.gc || [];
  let riderCount = 0;
  for (const g of groups) {
    riderCount += g.size || 0;
  }

  $("sq-rider-count").textContent = riderCount || "–";
  $("sq-group-count").textContent = groups.length || "0";

  // GC-Führer
  const leader = gc.length > 0 ? gc[0] : null;
  if (leader) {
    $("sq-gc-leader").textContent = leader.name;
    $("sq-gc-gap").textContent = leader.rel_s != null && leader.rel_s <= 0
      ? "Führung"
      : (leader.rel_s != null ? fmtGap(leader.rel_s) : "–");
  } else {
    $("sq-gc-leader").textContent = "–";
    $("sq-gc-gap").textContent = "–";
  }
}

function setSSEConnected(connected) {
  sseConnected = connected;
  if (lastData) {
    renderStatusQuo(lastData);
  }
}

// ----------------------------------------------------------------- #
// Status-Header (Topbar)
// ----------------------------------------------------------------- #
function setStatus(status, freshness) {
  const pill = $("status-pill");
  pill.textContent = status;
  pill.className = "pill";
  if (status === "LIVE") pill.classList.add("pill--live");
  else if (status === "STALE") pill.classList.add("pill--stale");
  else pill.classList.add("pill--idle");
  $("freshness").textContent = freshness || "";
}

function setLastUpdate() {
  const d = new Date();
  $("last-update").textContent = `Aktualisiert ${PAD(d.getHours())}:${PAD(d.getMinutes())}:${PAD(d.getSeconds())}`;
}

// ----------------------------------------------------------------- #
// Feature 8: Restzeit-Anzeige (backend: data.predictions[])
// ----------------------------------------------------------------- #
function renderRestzeit(data) {
  const container = $("restzeit");
  if (container && !container.classList.contains("glass")) container.classList.add("glass");
  const el = $("restzeit");
  const preds = data.predictions || [];

  // Nimm die erste Gruppen-Vorhersage (Führungsgruppe).
  const leadPred = preds.find(p => p.type === "group" && p.id === 0) ||
                   preds.find(p => p.type === "group");

  if (!leadPred) {
    el.hidden = true;
    return;
  }
  el.hidden = false;

  const eta = leadPred.eta_seconds;
  const abs = leadPred.eta_absolute || "";
  if (eta != null) {
    const totalSec = Math.round(eta);
    const hh = Math.floor(totalSec / 3600);
    const mm = Math.floor((totalSec % 3600) / 60);
    const ss = totalSec % 60;
    const timeStr = hh > 0
      ? `${hh}h ${PAD(mm)}m ${PAD(ss)}s`
      : `${PAD(mm)}m ${PAD(ss)}s`;
    $("restzeit-value").textContent = timeStr;
  } else {
    $("restzeit-value").textContent = "–";
  }
  $("restzeit-sub").textContent = abs
    ? `${Number(leadPred.remaining_km).toFixed(1)} km @ ${Number(leadPred.speed_kph).toFixed(1)} km/h  ·  Ziel ca. ${abs}`
    : `${Number(leadPred.remaining_km).toFixed(1)} km @ ${Number(leadPred.speed_kph).toFixed(1)} km/h`;
}

// ----------------------------------------------------------------- #
// Feature 6: Gap-Chart (backend: data.groups[].gap_s)
// ----------------------------------------------------------------- #
function renderGapChart(data) {
  const container = $("gap-chart");
  if (container && !container.classList.contains("glass")) container.classList.add("glass");
  const header = $("gap-chart-h");
  const info = $("gap-chart-groups-info");
  const groups = data.groups || [];

  if (groups.length < 2) {
    header.hidden = true;
    container.hidden = true;
    return;
  }
  header.hidden = false;
  container.hidden = false;

  const gaps = groups.map(g => g.gap_s != null ? g.gap_s : 0);
  const maxGap = Math.max(...gaps) || 1;

  const speeds = groups.map(g => g.speed_kph).filter(s => s != null);
  const avgSpeed = speeds.length ? (speeds.reduce((a, b) => a + b, 0) / speeds.length) : null;
  info.textContent = avgSpeed != null
    ? `  ·  ⌀ ${Number(avgSpeed).toFixed(1)} km/h`
    : "";

  const seen = new Set();
  for (const g of groups) {
    const key = g.order;
    seen.add(key);
    let bar = container.querySelector(`[data-gap-order="${key}"]`);
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "gap-bar";
      bar.dataset.gapOrder = key;
      bar.innerHTML =
        '<span class="gap-bar__label"></span>' +
        '<span class="gap-bar__track"><span class="gap-bar__fill"></span></span>' +
        '<span class="gap-bar__value"></span>';
      container.appendChild(bar);
    }
    const gap = g.gap_s != null ? Math.max(0, g.gap_s) : 0;
    const pct = maxGap > 0 ? (gap / maxGap) * 100 : 0;
    bar.querySelector(".gap-bar__label").textContent = g.name;
    bar.querySelector(".gap-bar__fill").style.width = `${Math.max(1, pct)}%`;
    bar.querySelector(".gap-bar__value").textContent =
      g.gap_s != null ? `${Number(g.gap_s).toFixed(0)}s` : "–";
    const fill = bar.querySelector(".gap-bar__fill");
    if (g.order === 0) fill.style.background = "var(--green)";
    else if (g.order === groups.length - 1) fill.style.background = "var(--polka)";
    else fill.style.background = "var(--accent)";
  }
  for (const el of container.querySelectorAll(".gap-bar")) {
    if (!seen.has(Number(el.dataset.gapOrder))) el.remove();
  }
}

// ----------------------------------------------------------------- #
// Feature 9: Berg/Sprint-Panel (backend: data.classifications)
// ----------------------------------------------------------------- #
function renderBergSprintPanel(data) {
  const panel = $("cps");
  if (panel && !panel.classList.contains("glass")) panel.classList.add("glass");
  const list = $("cp-list");
  const info = $("cp-info");
  const cls = data.classifications || {};
  const mountains = cls.mountains || [];
  const sprints = cls.sprints || [];

  if (!mountains.length && !sprints.length) {
    // Fallback: checkpoints aus dem Snapshot verwenden.
    const cps = data.checkpoints || [];
    if (!cps.length) { panel.hidden = true; return; }
    // Render from raw checkpoints.
    panel.hidden = false;
    info.textContent = `(${cps.length} Wegpunkte)`;
    const seen = new Set();
    for (const cp of cps) {
      const key = cp.index;
      seen.add(key);
      let el = list.querySelector(`[data-cp="${key}"]`);
      if (!el) {
        el = document.createElement("div");
        el.className = "cp";
        el.dataset.cp = key;
        el.innerHTML =
          '<span class="cp__icon"></span>' +
          '<div class="cp__body">' +
            '<span class="cp__place"></span>' +
            '<span class="cp__road"></span>' +
          '</div>';
        list.appendChild(el);
      }
      const icon = el.querySelector(".cp__icon");
      if (cp.kind === "mountain") {
        icon.textContent = "⛰️";
        el.className = "cp cp--mountain";
      } else if (cp.kind === "sprint") {
        icon.textContent = "🏁";
        el.className = "cp cp--sprint";
      } else {
        icon.textContent = "📍";
        el.className = "cp cp--other";
      }
      el.querySelector(".cp__place").textContent = cp.place || "?";
      el.querySelector(".cp__road").textContent = cp.road || "";
    }
    for (const el of list.querySelectorAll(".cp")) {
      if (!seen.has(Number(el.dataset.cp))) el.remove();
    }
    return;
  }

  panel.hidden = false;
  info.textContent = `(${mountains.length} Berg(e), ${sprints.length} Sprint(s))`;

  const all = [];
  for (const m of mountains) {
    const cat = m.category_label || m.category || "";
    all.push({
      index: m.index,
      icon: "⛰️",
      cls: "cp--mountain",
      place: `${m.name}${cat ? " (" + cat + ")" : ""}`,
      road: m.length_km != null ? `${Number(m.length_km).toFixed(1)} km @ ${m.avg_gradient != null ? Number(m.avg_gradient).toFixed(1) : "?"}%` : "",
    });
  }
  for (const s of sprints) {
    all.push({
      index: s.index || all.length,
      icon: "🏁",
      cls: "cp--sprint",
      place: s.place || "Zwischensprint",
      road: "",
    });
  }
  all.sort((a, b) => (a.index || 0) - (b.index || 0));

  const seen = new Set();
  for (const item of all) {
    const key = item.index;
    seen.add(key);
    let el = list.querySelector(`[data-cp="${key}"]`);
    if (!el) {
      el = document.createElement("div");
      el.className = "cp";
      el.dataset.cp = key;
      el.innerHTML =
        '<span class="cp__icon"></span>' +
        '<div class="cp__body">' +
          '<span class="cp__place"></span>' +
          '<span class="cp__road"></span>' +
        '</div>';
      list.appendChild(el);
    }
    el.querySelector(".cp__icon").textContent = item.icon;
    el.className = "cp " + item.cls;
    el.querySelector(".cp__place").textContent = item.place;
    el.querySelector(".cp__road").textContent = item.road;
  }
  for (const el of list.querySelectorAll(".cp")) {
    if (!seen.has(Number(el.dataset.cp))) el.remove();
  }
}

// ----------------------------------------------------------------- #
// Feature 7: Karten-Marker (backend: data.map / data.checkpoints)
// ----------------------------------------------------------------- #
function renderMapCheckpoints(data) {
  if (!map) return;

  // Alte Marker entfernen.
  for (const m of markerGroups.checkpoint) {
    map.removeLayer(m);
  }
  markerGroups.checkpoint = [];

  // Bevorzugt backend map_data, sonst raw checkpoints.
  const mapData = data.map || {};
  let markers = mapData.mountain_markers || mapData.sprint_markers || [];
  const hasBackendMarkers = (mapData.mountain_markers || []).length > 0 ||
                            (mapData.sprint_markers || []).length > 0;

  if (hasBackendMarkers) {
    for (const m of (mapData.mountain_markers || [])) {
      if (m.lat == null || m.lon == null) continue;
      const leafletM = L.circleMarker([m.lat, m.lon], {
        radius: 9, color: "#d4392f", weight: 2,
        fillColor: "#d4392f", fillOpacity: 0.5,
      }).addTo(map);
      leafletM.bindPopup(`<b>⛰️ ${m.name || "Berg"}</b><br>${m.category_label || m.category || ""}${m.length_km ? " · " + Number(m.length_km).toFixed(1) + " km" : ""}${m.avg_gradient ? " · " + Number(m.avg_gradient).toFixed(1) + "%" : ""}`);
      markerGroups.checkpoint.push(leafletM);
    }
    for (const m of (mapData.sprint_markers || [])) {
      if (m.lat == null || m.lon == null) continue;
      const leafletM = L.circleMarker([m.lat, m.lon], {
        radius: 8, color: "#2da44e", weight: 2,
        fillColor: "#2da44e", fillOpacity: 0.5,
      }).addTo(map);
      leafletM.bindPopup(`<b>🏁 ${m.place || "Sprint"}</b>`);
      markerGroups.checkpoint.push(leafletM);
    }
    return;
  }

  // Raw checkpoint markers (Fallback).
  const cps = data.checkpoints || [];
  for (const cp of cps) {
    if (cp.lat == null || cp.lon == null) continue;
    let color, fillColor, radius, iconText, label;
    if (cp.kind === "mountain") {
      color = "#d4392f"; fillColor = "#d4392f"; radius = 9;
      iconText = "⛰️"; label = "Berg";
    } else if (cp.kind === "sprint") {
      color = "#2da44e"; fillColor = "#2da44e"; radius = 8;
      iconText = "🏁"; label = "Sprint";
    } else {
      color = "#8b949e"; fillColor = "#8b949e"; radius = 6;
      iconText = "📍"; label = "Wegpunkt";
    }
    const m = L.circleMarker([cp.lat, cp.lon], {
      radius, color, weight: 2,
      fillColor, fillOpacity: 0.5,
    }).addTo(map);
    const road = cp.road ? ` · ${cp.road}` : "";
    m.bindPopup(`<b>${iconText} ${cp.place || "?"}</b>${road}<br>${label}`);
    markerGroups.checkpoint.push(m);
  }
}

// ----------------------------------------------------------------- #
// Feature 10: Alarm-Panel (backend: data.alarms[])
// ----------------------------------------------------------------- #
function renderAlarms(data) {
  const panel = $("alarms");
  if (panel && !panel.classList.contains("glass")) panel.classList.add("glass");
  const list = $("alarm-list");
  const alarms = data.alarms || [];

  if (!alarms.length) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;

  const seen = new Set();
  for (const a of alarms) {
    const id = a.id || `${a.kind}-${a.ts || Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    seen.add(id);
    let el = list.querySelector(`[data-alarm-id="${id}"]`);
    if (!el) {
      el = document.createElement("div");
      el.className = "alarm";
      el.dataset.alarmId = id;
      el.innerHTML = '<span class="alarm__text"></span><span class="alarm__ago"></span>';
      list.insertBefore(el, list.firstChild);
      let cls = "alarm";
      if (a.kind === "withdrawal") cls += " alarm--withdrawal";
      else if (a.kind === "gap_change") cls += " alarm--gap";
      else cls += " alarm--other";
      el.className = cls;
    }
    el.querySelector(".alarm__text").textContent = a.text;
    const ts = a.ts ? a.ts / 1000 : 0;
    const ago = ts ? Math.round((Date.now() / 1000) - ts) : 0;
    el.querySelector(".alarm__ago").textContent = ago < 60 ? `${ago}s` : `${Math.floor(ago / 60)}m`;
  }
  for (const el of list.querySelectorAll(".alarm")) {
    if (!seen.has(el.dataset.alarmId)) el.remove();
  }
}

// ----------------------------------------------------------------- #
// Karte (Leaflet)
// ----------------------------------------------------------------- #
function initMap() {
  if (map) return;
  map = L.map("map", { zoomControl: true, attributionControl: true }).setView([46.6, 2.4], 5);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 14,
    attribution: "© OpenStreetMap",
  }).addTo(map);
}

function renderMap(data) {
  if (!map) initMap();

  // Gruppen-Marker.
  const seenGroups = new Set();
  for (const g of data.groups) {
    if (g.lat == null || g.lon == null) continue;
    seenGroups.add(g.order);
    let m = markerGroups.group[g.order];
    if (!m) {
      m = L.circleMarker([g.lat, g.lon], {
        radius: 12, color: "#fdda24", weight: 2,
        fillColor: "#fdda24", fillOpacity: 0.35,
      }).addTo(map);
      m.bindPopup("");
      markerGroups.group[g.order] = m;
    }
    m.setLatLng([g.lat, g.lon]);
    const lines = [`<b>${g.name}</b>  (${g.size} F.)`,
                   fmtGap(g.gap_s),
                   g.speed_kph != null ? `${Number(g.speed_kph).toFixed(1)} km/h` : "",
                   g.remaining_km != null ? `${Number(g.remaining_km).toFixed(1)} km` : ""];
    m.setPopupContent(lines.filter(Boolean).join("<br>"));
  }
  for (const [order, m] of Object.entries(markerGroups.group)) {
    if (!seenGroups.has(Number(order))) {
      map.removeLayer(m);
      delete markerGroups.group[order];
    }
  }

  // Top-Rider-Marker.
  const seenRiders = new Set();
  for (const r of data.top_n) {
    const pos = r.pos || null;
    if (!pos) continue;
    seenRiders.add(r.bib);
    let m = markerGroups.rider[r.bib];
    if (!m) {
      m = L.circleMarker([pos[0], pos[1]], {
        radius: 7, color: "#1a1a1a", weight: 2,
        fillColor: "#2da44e", fillOpacity: 0.95,
      }).addTo(map);
      m.bindPopup("");
      markerGroups.rider[r.bib] = m;
    }
    m.setLatLng([pos[0], pos[1]]);
    const lines = [`<b>${r.name}</b>`, r.team,
                   r.kph != null ? `${Number(r.kph).toFixed(1)} km/h` : "",
                   r.rank ? `Virtual-GC: #${r.rank}` : ""];
    m.setPopupContent(lines.filter(Boolean).join("<br>"));
  }
  for (const [bib, m] of Object.entries(markerGroups.rider)) {
    if (!seenRiders.has(Number(bib))) {
      map.removeLayer(m);
      delete markerGroups.rider[bib];
    }
  }

  // Checkpoint-Marker (Feature 7).
  renderMapCheckpoints(data);

  // Auto-Fit einmalig.
  if (!data._fitDone && (Object.keys(markerGroups.group).length
                         || Object.keys(markerGroups.rider).length)) {
    const pts = [];
    for (const m of Object.values(markerGroups.group)) pts.push(m.getLatLng());
    for (const m of Object.values(markerGroups.rider)) pts.push(m.getLatLng());
    if (pts.length) {
      map.fitBounds(L.latLngBounds(pts).pad(0.2), { maxZoom: 11 });
      data._fitDone = true;
    }
  }
}

// ----------------------------------------------------------------- #
// Top-Cards
// ----------------------------------------------------------------- #
function renderTopCards(top) {
  const container = $("top-cards");
  const seen = new Set();
  for (const r of top) {
    seen.add(r.bib);
    let entry = topCards.get(r.bib);
    if (!entry) {
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML =
        '<div class="card__rank"></div>' +
        '<div class="card__name"></div>' +
        '<div class="card__stats">' +
          '<div class="card__stat"><span>km/h</span><b class="kph">–</b></div>' +
          '<div class="card__stat"><span>km → Ziel</span><b class="ktf">–</b></div>' +
          '<div class="card__stat"><span>Steigung</span><b class="grad">–</b></div>' +
        '</div>';
      container.appendChild(card);
      entry = { card, name: card.querySelector(".card__name"),
                rank: card.querySelector(".card__rank"),
                kph: card.querySelector(".kph"),
                ktf: card.querySelector(".ktf"),
                grad: card.querySelector(".grad") };
      topCards.set(r.bib, entry);
    }
    entry.name.textContent = r.name;
    entry.name.style.animation = 'none';
    requestAnimationFrame(() => {
      entry.name.style.animation = 'gcFadeIn 0.5s ease forwards';
    });
    entry.rank.textContent = r.team + (r.rank ? `  ·  #${r.rank}` : "");
    entry.kph.textContent = r.kph != null ? Number(r.kph).toFixed(1) : "–";
    entry.ktf.textContent = r.km_to_finish != null ? Number(r.km_to_finish).toFixed(1) : "–";
    entry.grad.textContent = r.gradient != null ? `${Number(r.gradient).toFixed(0)}%` : "–";
  }
  for (const [bib, entry] of topCards) {
    if (!seen.has(bib)) {
      entry.card.remove();
      topCards.delete(bib);
    }
  }
}

// -----------------------------------------------------------------
// Trikots
// -----------------------------------------------------------------
function renderJerseys(jerseys) {
  const container = $("jerseys");
  const seen = new Set();
  const ORDER = ["Y", "G", "P", "W"];
  for (const code of ORDER) {
    const j = jerseys[code];
    if (!j) continue;
    seen.add(code);
    let el = container.querySelector(`[data-jersey="${code}"]`);
    if (!el) {
      el = document.createElement("div");
      el.className = "jersey";
      el.dataset.jersey = code;
      el.innerHTML =
        `<span class="jersey__swatch jersey__swatch--${code}"></span>` +
        '<div class="jersey__body">' +
          '<div class="jersey__label"></div>' +
          '<div class="jersey__name"></div>' +
        '</div>';
      container.appendChild(el);
    }
    el.querySelector(".jersey__label").textContent = j.label;
    el.querySelector(".jersey__name").textContent = j.name;
  }
  for (const el of container.querySelectorAll(".jersey")) {
    if (!seen.has(el.dataset.jersey)) el.remove();
  }
}

// ----------------------------------------------------------------- #
// GC-Tabelle (DOM-Diff)
// ----------------------------------------------------------------- #
function fmtGap(s) {
  if (s == null) return "–";
  s = Number(s);
  if (s <= 0) return "leader";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  return h > 0 ? `+${h}:${PAD(m)}:${PAD(ss)}` : `+${PAD(m)}:${PAD(ss)}`;
}

function renderGC(gc) {
  const body = $("gc-body");
  const seen = new Set();
  gc.forEach((e, idx) => {
    seen.add(e.bib);
    let entry = gcRows.get(e.bib);
    if (!entry) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        '<td class="num pos"></td>' +
        '<td class="num bib"></td>' +
        '<td class="name"></td>' +
        '<td class="team"></td>' +
        '<td class="num gap"></td>';
      body.appendChild(tr);
      entry = { tr, pos: tr.querySelector(".pos"), bib: tr.querySelector(".bib"),
                name: tr.querySelector(".name"), team: tr.querySelector(".team"),
                gap: tr.querySelector(".gap") };
      gcRows.set(e.bib, entry);
    }
    if (entry.tr.sectionRowIndex !== idx) {
      body.insertBefore(entry.tr, body.children[idx] || null);
    }
    entry.pos.textContent = e.pos;
    entry.pos.classList.toggle("gc-pos-leader", e.pos === 1);
    entry.bib.textContent = e.bib;
    // Staggered Typography: Fade-In für neue GC-Namen
    if (entry.name.textContent !== e.name) {
      entry.name.style.animation = 'none';
      entry.name.textContent = e.name;
      requestAnimationFrame(() => {
        // Staggered delay based on position
        const delay = Math.min(idx * 0.05, 0.5);
        entry.name.style.animation = `gcFadeIn 0.5s ease ${delay}s forwards`;
      });
    } else {
      entry.name.textContent = e.name;
    }
    entry.team.textContent = e.team;
    entry.gap.textContent = fmtGap(e.rel_s);
  });
  for (const [bib, entry] of gcRows) {
    if (!seen.has(bib)) {
      entry.tr.remove();
      gcRows.delete(bib);
    }
  }
}

// ----------------------------------------------------------------- #
// Gruppen
// ----------------------------------------------------------------- #
function renderGroups(groups) {
  const container = $("groups");
  const seen = new Set();
  for (const g of groups) {
    seen.add(g.order);
    let el = container.querySelector(`[data-order="${g.order}"]`);
    if (!el) {
      el = document.createElement("div");
      el.className = "group";
      el.dataset.order = g.order;
      el.innerHTML =
        '<div class="group__head"><span class="group__name"></span>' +
        '<span class="group__gap"></span></div>' +
        '<div class="group__meta"></div>';
      container.appendChild(el);
    }
    el.querySelector(".group__name").textContent =
      `${g.name}  (${g.size} F.)`;
    el.querySelector(".group__gap").textContent = fmtGap(g.gap_s);
    const meta = [];
    if (g.speed_kph != null) meta.push(`${Number(g.speed_kph).toFixed(1)} km/h`);
    if (g.remaining_km != null) meta.push(`${Number(g.remaining_km).toFixed(1)} km zu Ziel`);
    if (g.sample_names && g.sample_names.length)
      meta.push(g.sample_names.join(", ") + (g.extra ? `  +${g.extra}` : ""));
    el.querySelector(".group__meta").textContent = meta.join("  ·  ");
  }
  for (const el of container.querySelectorAll(".group")) {
    if (!seen.has(Number(el.dataset.order))) el.remove();
  }
}

// ----------------------------------------------------------------- #
// Technische Features (6-8): Power, Survival, Time-Cut
// ----------------------------------------------------------------- #
function renderPower(d) {
  const panel = $("power-panel");
  const power = d.power;
  if (!power || !power.riders || power.riders.length === 0) {
    if (panel) panel.hidden = true;
    return;
  }
  if (panel) panel.hidden = false;
  const gradEl = $("power-gradient");
  if (gradEl) {
    const g = power.gradient_pct ?? 0;
    gradEl.textContent = `· ${g.toFixed(1)} % Steigung`;
  }
  const list = $("wkg-list");
  if (!list) return;
  // Top 8 nach W/kg
  const top = power.riders.slice(0, 8);
  list.innerHTML = top.map((r, i) => {
    const wkgClass = r.w_per_kg >= 6 ? "wkg-wperkg--high" : "wkg-wperkg--med";
    return `<div class="wkg-row">
      <span class="wkg-rank">${i + 1}</span>
      <span class="wkg-name" title="${r.label}">${r.label}</span>
      <span class="wkg-watts">${Math.round(r.watts)} W</span>
      <span class="wkg-wperkg ${wkgClass}">${r.w_per_kg.toFixed(1)}</span>
    </div>`;
  }).join("");
}

function renderSurvival(d) {
  const panel = $("survival-panel");
  const surv = d.breakaway_survival;
  if (!surv || surv.available === false) {
    if (panel) panel.hidden = true;
    return;
  }
  if (panel) panel.hidden = false;
  const pct = surv.survival_pct ?? 0;
  const fill = $("survival-fill");
  const pctEl = $("survival-pct");
  if (fill) fill.style.width = `${pct}%`;
  if (pctEl) pctEl.textContent = `${pct.toFixed(0)} %`;
  const meta = $("survival-meta");
  if (meta) {
    const conf = surv.confidence || "?";
    const brk = surv.breakaway_name || "?";
    const brkSize = surv.breakaway_size ?? "?";
    const gap = surv.gap_s ? formatGap(surv.gap_s) : "?";
    meta.innerHTML = `${brk} (${brkSize} F., ${gap} Vorsprung)
      · Konfidenz: <strong>${conf}</strong>`;
  }
}

function renderTimeCut(d) {
  const panel = $("timecut-panel");
  const tc = d.time_cut;
  if (!tc || !tc.groups || tc.groups.length === 0) {
    if (panel) panel.hidden = true;
    return;
  }
  if (panel) panel.hidden = false;
  const thr = $("timecut-threshold");
  if (thr) thr.textContent = `· ${tc.threshold_pct}% Cut`;
  const list = $("timecut-list");
  if (!list) return;
  // Nur gefährdete + warning zeigen, safe ausblenden (zu viel Noise)
  const relevant = tc.groups.filter(g => g.status !== "safe").slice(0, 8);
  if (relevant.length === 0) {
    list.innerHTML = `<div class="timecut-row">
      <span>Alle Gruppen sicher</span>
      <span class="timecut-status timecut-status--safe">SAFE</span>
      <span></span>
    </div>`;
    return;
  }
  list.innerHTML = relevant.map(g => {
    const margin = g.margin_s != null ? formatGap(Math.abs(g.margin_s)) : "?";
    const marginLabel = g.margin_s >= 0 ? `+${margin}` : `über Cut`;
    return `<div class="timecut-row timecut-row--${g.status}">
      <span>${g.name}</span>
      <span class="timecut-margin">${marginLabel}</span>
      <span class="timecut-status timecut-status--${g.status}">${g.status.toUpperCase()}</span>
    </div>`;
  }).join("");
}

// Hilfsfunktion: Sekunden -> "Xm Ys" oder "X.Xh"
function formatGap(s) {
  if (s == null) return "?";
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
  return `${(s / 3600).toFixed(1)}h`;
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
    renderStatusQuo(d);           // ← Status Quo Header immer rendern
    renderJerseys(d.jerseys || {});
    renderTopCards(d.top_n || []);
    renderRestzeit(d);
    renderGapChart(d);
    renderGC(d.gc || []);
    renderGroups(d.groups || []);
    renderMap(d);
    renderBergSprintPanel(d);
    renderAlarms(d);
    renderPower(d);
    renderSurvival(d);
    renderTimeCut(d);
    setLastUpdate();
  });
}

// -----------------------------------------------------------------
// Anbindung: WebSocket primär, /state-Fallback
// -----------------------------------------------------------------
function connectWS() {
  if (ws) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  try {
    ws = new WebSocket(`${proto}://${location.host}/ws${authQuery()}`);
  } catch (e) {
    setSSEConnected(false);
    schedulePoll();
    return;
  }
  ws.onopen = () => {
    setSSEConnected(true);
  };
  ws.onmessage = (ev) => {
    try { applyUpdate(JSON.parse(ev.data)); } catch (_) {}
  };
  ws.onclose = () => {
    ws = null;
    setSSEConnected(false);
    schedulePoll();
  };
  ws.onerror = () => {
    setSSEConnected(false);
    try { ws.close(); } catch (_) {}
  };
}

function pollOnce() {
  fetch(`/state${authQuery()}`, { headers: authHeaders() })
    .then((r) => {
      if (r.status === 401) { showAuthBar(); throw new Error("401"); }
      return r.json();
    })
    .then(applyUpdate)
    .catch(() => {});
}

function schedulePoll() {
  if (pollTimer) clearTimeout(pollTimer);
  const interval = visible ? 2000 : 10000;
  pollTimer = setTimeout(() => {
    if (!ws) pollOnce();
    if (!ws) schedulePoll();
  }, interval);
}

document.addEventListener("visibilitychange", () => {
  visible = !document.hidden;
  if (visible && !ws) {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    connectWS();
  }
});

// -----------------------------------------------------------------
// Steuerung (Start/Stop/Restart via /api/control)
// -----------------------------------------------------------------
async function refreshControlState() {
  const el = $("ctrl-state");
  try {
    const r = await fetch(`/api/control${authQuery()}`, { headers: authHeaders() });
    if (r.status === 401) { $("ctrlbar").hidden = true; return; }
    if (r.status === 503) {
      $("ctrlbar").hidden = true;
      controlAvailable = false;
      return;
    }
    controlAvailable = true;
    $("ctrlbar").hidden = false;
    const d = await r.json();
    el.className = "ctrl-state " + (d.active ? "ok" : "bad");
    el.textContent = `● ${d.active ? "aktiv" : (d.state || "inaktiv")}`;
  } catch (_) {
    el.className = "ctrl-state bad";
    el.textContent = "● Status unbekannt";
  }
}

async function controlAction(action) {
  const el = $("ctrl-state");
  el.className = "ctrl-state";
  el.textContent = `● ${action} …`;
  try {
    const r = await fetch(`/api/control?action=${action}${authQuerySep("&")}`,
                          { method: "POST", headers: authHeaders() });
    const d = await r.json();
    setTimeout(refreshControlState, action === "restart" ? 4000 : 1000);
  } catch (_) {
    el.className = "ctrl-state bad";
    el.textContent = "● Fehler";
  }
}

function initControl() {
  const bar = $("ctrlbar");
  if (!bar) return;
  bar.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === "stop" || action === "restart") {
      if (!confirm(`${action} wirklich ausführen?`)) return;
    }
    controlAction(action);
  });
  refreshControlState();
  setInterval(() => { if (visible) refreshControlState(); }, 10000);
}

// Start
pollOnce();
connectWS();
initControl();

})();
