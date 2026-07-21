/* ═══════════════════════════════════════════════════════════
   AVENGERS HUD · client controller · STARK INDUSTRIES × BOBBIEY
   ═══════════════════════════════════════════════════════════ */

const $ = (s) => document.querySelector(s);
const escapeHTML = (s) =>
  (s || "").replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));

// ── clock ────────────────────────────────────────────
function tickClock() {
  const d = new Date();
  $("#clock").textContent = d.toTimeString().slice(0, 8);
  $("#clock-date").textContent =
    `${d.getFullYear()}.${String(d.getMonth()+1).padStart(2,"0")}.${String(d.getDate()).padStart(2,"0")}`;
}
setInterval(tickClock, 1000); tickClock();

// ── world clocks ─────────────────────────────────────
const WORLD_CLOCKS = [
  { city: "NYC", tz: "America/New_York" },
  { city: "LA",  tz: "America/Los_Angeles" },
  { city: "LON", tz: "Europe/London" },
  { city: "MUM", tz: "Asia/Kolkata" },
  { city: "TOK", tz: "Asia/Tokyo" },
];
function renderWorldClocks() {
  const host = $("#world-clocks"); if (!host) return;
  host.innerHTML = WORLD_CLOCKS.map(c => {
    let t = "--:--";
    try { t = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: c.tz }); } catch (e) {}
    return `<div class="clock-mini"><div class="clock-mini-city">${c.city}</div><div class="clock-mini-time">${t}</div></div>`;
  }).join("");
}
setInterval(renderWorldClocks, 30000); renderWorldClocks();

// ── ops timer ────────────────────────────────────────
const opsStart = Date.now();
function tickOps() {
  const s = Math.floor((Date.now() - opsStart) / 1000);
  const h = String(Math.floor(s/3600)).padStart(2,"0");
  const m = String(Math.floor((s%3600)/60)).padStart(2,"0");
  const ss = String(s%60).padStart(2,"0");
  const el = $("#ops-timer"); if (el) el.textContent = `${h}:${m}:${ss}`;
}
setInterval(tickOps, 1000); tickOps();

// ── theme switcher ───────────────────────────────────
const themeBtns = document.querySelectorAll(".theme-btn");
themeBtns.forEach(b => b.addEventListener("click", () => {
  const t = b.dataset.theme;
  document.body.classList.remove("theme-jarvis", "theme-stark", "theme-stealth");
  document.body.classList.add("theme-" + t);
  themeBtns.forEach(x => x.classList.toggle("active", x === b));
  try { localStorage.setItem("avengers-theme", t); } catch (e) {}
}));
(() => {
  try {
    const saved = localStorage.getItem("avengers-theme");
    if (saved) {
      document.body.classList.remove("theme-jarvis","theme-stark","theme-stealth");
      document.body.classList.add("theme-" + saved);
      themeBtns.forEach(x => x.classList.toggle("active", x.dataset.theme === saved));
    }
  } catch (e) {}
})();

// ── status pills ─────────────────────────────────────
function setPill(id, label, level) {
  const el = $(id); if (!el) return;
  el.classList.remove("ok","warn","error");
  if (level) el.classList.add(level);
  const v = el.querySelector(".sp-val");
  if (v) v.textContent = label;
}
async function refreshPills() {
  try {
    const r = await fetch("/api/status");
    const d = await r.json();
    setPill("#sp-brain", (d.brain_mode || "?").toUpperCase(),
            (d.brain_mode === "llm" || d.brain_mode === "local-llm") ? "ok"
            : d.brain_mode === "local" ? "warn" : "error");
    setPill("#sp-tts", d.tts_enabled ? "ON" : "OFF", d.tts_enabled ? "ok" : "error");
    setPill("#sp-news", d.news_count > 0 ? `${d.news_count} ITEMS` : "EMPTY",
            d.news_count > 0 ? "ok" : "warn");
  } catch (e) {}
}

// ── charts ───────────────────────────────────────────
const mkChart = (id, color) => new Chart(document.getElementById(id), {
  type: "line",
  data: { labels: [], datasets: [{ data: [], borderColor: color, backgroundColor: color + "22",
    borderWidth: 1.5, fill: true, tension: 0.32, pointRadius: 0 }] },
  options: {
    responsive: true, maintainAspectRatio: false, animation: false,
    plugins: { legend: { display: false }, tooltip: { enabled: false } },
    scales: { x: { display: false }, y: { min: 0, max: 100, display: false } },
    elements: { line: { borderJoinStyle: "round" } },
  },
});
const cpuChart = mkChart("cpu-chart", "#00d9ff");
const memChart = mkChart("mem-chart", "#ff7a00");
const MAX_POINTS = 60;
function pushChart(ch, val) {
  ch.data.labels.push(""); ch.data.datasets[0].data.push(val);
  if (ch.data.labels.length > MAX_POINTS) { ch.data.labels.shift(); ch.data.datasets[0].data.shift(); }
  ch.update("none");
}

// ── avengers list ────────────────────────────────────
function shortBadge(c) { const w = c.split(/\s+/); return w.length === 1 ? c.slice(0,3) : w.map(s => s[0]).join("").slice(0,3); }
function renderAgents(agents) {
  const list = $("#avengers-list"); list.innerHTML = "";
  for (const a of agents) {
    const card = document.createElement("div");
    card.className = "agent-card"; card.dataset.agent = a.name;
    card.dataset.role = a.role;
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.title = `${a.codename} — click for operations file`;
    card.style.setProperty("--agent-color", a.color);
    card.innerHTML = `
      <div class="agent-icon">${shortBadge(a.codename)}</div>
      <div class="agent-meta">
        <div class="agent-name">${a.codename}</div>
        <div class="agent-role">${a.role}</div>
      </div>
      <div class="agent-status">${a.status}</div>`;
    list.appendChild(card);
  }
  $("#agent-count").textContent = `${agents.length} agents online`;
  try { agentsOnline = agents.length; } catch (e) {}
}
function updateAgentStatus(name, status, task) {
  const card = document.querySelector(`.agent-card[data-agent="${name}"]`);
  if (!card) return;
  const el = card.querySelector(".agent-status");
  el.textContent = status; el.className = "agent-status " + status;
  card.classList.toggle("active", status !== "idle");
  // live current-task display: swap the role line while the agent works
  const roleEl = card.querySelector(".agent-role");
  if (roleEl) {
    if (status !== "idle" && task && task !== "—") roleEl.textContent = "▸ " + task;
    else roleEl.textContent = card.dataset.role || roleEl.textContent;
  }
}

// ── event stream ─────────────────────────────────────
function logEvent(html, kind = "info") {
  const ev = $("#event-stream");
  const line = document.createElement("div");
  line.className = "event-line " + kind;
  line.innerHTML = `<span class="ts">${new Date().toTimeString().slice(0,8)}</span>${html}`;
  ev.appendChild(line);
  while (ev.children.length > 200) ev.removeChild(ev.firstChild);
  ev.scrollTop = ev.scrollHeight;
  try { bumpActivity(); } catch (e) {}   // feeds the AI-ops activity heatmap
}

// ── news ─────────────────────────────────────────────
function renderNews(items) {
  const list = $("#news-list"); list.innerHTML = "";
  if (!items.length) { list.innerHTML = '<div class="news-empty">no feed — check NEWSAPI_KEY</div>'; return; }
  for (const it of items.slice(0, 12)) {
    const div = document.createElement("div");
    div.className = "news-item";
    div.innerHTML = `<div>${escapeHTML(it.title)}</div><div class="src">▸ ${escapeHTML(it.source || "unknown")}</div>`;
    if (it.url) div.onclick = () => window.open(it.url, "_blank");
    list.appendChild(div);
  }
}

// ── voice waveform ───────────────────────────────────
class Waveform {
  constructor(canvas) {
    this.c = canvas; this.ctx = canvas.getContext("2d");
    this.state = "idle"; this.t = 0;
    this.amp = 0.08; this.ampCur = 0.08; this.color = "#00d9ff";
    this._size();
    window.addEventListener("resize", () => this._size());
  }
  _size() {
    const dpr = window.devicePixelRatio || 1;
    const rect = this.c.getBoundingClientRect();
    this.cssW = Math.max(rect.width, 60); this.cssH = Math.max(rect.height, 40);
    this.c.width = this.cssW * dpr; this.c.height = this.cssH * dpr;
    this.ctx.setTransform(1,0,0,1,0,0); this.ctx.scale(dpr, dpr);
  }
  setState(state) {
    this.state = state;
    const s = getComputedStyle(document.body);
    if (state === "speaking") { this.amp = 1.0; this.color = s.getPropertyValue("--stark").trim() || "#ff7a00"; }
    else if (state === "listening") { this.amp = 0.55; this.color = "#50fa7b"; }
    else if (state === "processing") { this.amp = 0.32; this.color = "#ffb86c"; }
    else { this.amp = 0.08; this.color = s.getPropertyValue("--accent").trim() || "#00d9ff"; }
  }
  start() { const loop = () => { this.draw(); requestAnimationFrame(loop); }; loop(); }
  draw() {
    this.t += 1; this.ampCur += (this.amp - this.ampCur) * 0.07;
    const ctx = this.ctx, w = this.cssW, h = this.cssH, cy = h/2;
    ctx.clearRect(0,0,w,h);
    ctx.strokeStyle = this.color + "22"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, cy); ctx.lineTo(w, cy); ctx.stroke();
    ctx.shadowColor = this.color;
    for (let layer = 0; layer < 3; layer++) {
      ctx.beginPath();
      const phase = this.t * (0.055 + layer * 0.018);
      const freq  = 0.024 + layer * 0.014;
      const layerAmp = this.ampCur * (h * 0.44) * (1 - layer * 0.28);
      for (let x = 0; x <= w; x += 2) {
        const env = Math.sin(x * 0.04 + phase * 1.4) * 0.45 + Math.sin(x * 0.11 + phase) * 0.55;
        const y = cy + Math.sin(x * freq + phase) * layerAmp * env;
        if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = this.color; ctx.lineWidth = 1.6 - layer * 0.35;
      ctx.globalAlpha = 0.9 - layer * 0.22; ctx.shadowBlur = 7 + layer * 2;
      ctx.stroke();
    }
    ctx.globalAlpha = 1; ctx.shadowBlur = 0;
    if (this.state === "speaking" || this.state === "listening") {
      const px = ((this.t * 2.4) % (w + 40)) - 20;
      ctx.beginPath(); ctx.fillStyle = this.color; ctx.shadowColor = this.color; ctx.shadowBlur = 14;
      ctx.arc(px, cy, 2.2, 0, Math.PI * 2); ctx.fill(); ctx.shadowBlur = 0;
    }
  }
}
const wave = new Waveform(document.getElementById("wave-canvas")); wave.start();

/* ═══ HOLOGRAPHIC EARTH (orb core) ═══════════════════
   Real rotating globe via d3 orthographic projection + world-atlas land —
   actual continents, dark ocean (keeps overlaid text legible), graticule,
   spherical 3D shading, and orbiting satellites. Colour follows --orb-color
   so the globe shifts with the voice state. */
class OrbEarth {
  constructor(canvas) {
    this.c = canvas; this.ctx = canvas.getContext("2d");
    this.t = 0;
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.size = canvas.width;                       // logical px (square, 184)
    this.c.width = this.size * this.dpr;
    this.c.height = this.size * this.dpr;
    this.ctx.scale(this.dpr, this.dpr);
    this.R = this.size * 0.40;
    this.land = null;
    this.sats = [0, 2.1, 4.0];
    if (window.d3) {
      this.proj = d3.geoOrthographic()
        .scale(this.R).translate([this.size / 2, this.size / 2]).clipAngle(90);
      this.path = d3.geoPath(this.proj, this.ctx);
      this.grat = d3.geoGraticule10();
      this._load();
    }
  }
  async _load() {
    try {
      const w = await (await fetch("https://cdn.jsdelivr.net/npm/world-atlas@2/land-110m.json")).json();
      this.land = topojson.feature(w, w.objects.land);
    } catch (e) { /* graceful: ocean + graticule still render */ }
  }
  color() {
    return getComputedStyle(document.body).getPropertyValue("--orb-color").trim() || "#00d9ff";
  }
  start() { const loop = () => { this.draw(); requestAnimationFrame(loop); }; loop(); }
  draw() {
    this.t += 1;
    const ctx = this.ctx, s = this.size, col = this.color();
    ctx.clearRect(0, 0, s, s);
    if (!this.proj) return;

    this.proj.rotate([this.t * 0.18, -14]);   // spin W→E with a slight axial tilt

    // ocean disc — dark so the STANDBY label stays readable on top
    ctx.beginPath(); this.path({ type: "Sphere" });
    ctx.fillStyle = "rgba(2,12,22,0.82)"; ctx.fill();
    ctx.lineWidth = 1; ctx.strokeStyle = col;
    ctx.shadowColor = col; ctx.shadowBlur = 9; ctx.globalAlpha = 0.85;
    ctx.stroke(); ctx.shadowBlur = 0; ctx.globalAlpha = 1;

    // graticule
    ctx.beginPath(); this.path(this.grat);
    ctx.strokeStyle = col; ctx.globalAlpha = 0.13; ctx.lineWidth = 0.5;
    ctx.stroke(); ctx.globalAlpha = 1;

    // continents
    if (this.land) {
      ctx.beginPath(); this.path(this.land);
      ctx.fillStyle = col; ctx.globalAlpha = 0.34; ctx.fill();
      ctx.globalAlpha = 0.75; ctx.lineWidth = 0.5; ctx.strokeStyle = col;
      ctx.stroke(); ctx.globalAlpha = 1;
    }

    // spherical 3D shading (lit upper-left, shadowed lower-right)
    ctx.save();
    ctx.beginPath(); this.path({ type: "Sphere" }); ctx.clip();
    const g = ctx.createRadialGradient(s * 0.36, s * 0.34, s * 0.05, s * 0.5, s * 0.5, this.R);
    g.addColorStop(0, "rgba(255,255,255,0.06)");
    g.addColorStop(0.6, "rgba(0,0,0,0)");
    g.addColorStop(1, "rgba(0,0,0,0.5)");
    ctx.fillStyle = g; ctx.fillRect(0, 0, s, s);
    ctx.restore();

    // Hyderabad HQ ping (only when on the near hemisphere)
    const rot = this.proj.rotate();
    const center = [-rot[0], -rot[1]];
    if (d3.geoDistance([78.49, 17.39], center) < Math.PI / 2) {
      const p = this.proj([78.49, 17.39]);
      const pulse = 1.6 + Math.sin(this.t * 0.1) * 0.7;
      ctx.beginPath(); ctx.arc(p[0], p[1], pulse, 0, Math.PI * 2);
      ctx.fillStyle = col; ctx.shadowColor = col; ctx.shadowBlur = 7;
      ctx.fill(); ctx.shadowBlur = 0;
    }

    // orbiting satellites
    const cx = s / 2, cy = s / 2;
    for (const ph of this.sats) {
      const a = this.t * 0.02 + ph;
      const ox = cx + Math.cos(a) * (this.R + 8);
      const oy = cy + Math.sin(a) * (this.R + 8) * 0.4;
      ctx.beginPath(); ctx.arc(ox, oy, 1.3, 0, Math.PI * 2);
      ctx.fillStyle = col; ctx.shadowColor = col; ctx.shadowBlur = 5;
      ctx.globalAlpha = 0.9; ctx.fill();
      ctx.shadowBlur = 0; ctx.globalAlpha = 1;
    }
  }
}
(() => {
  const el = document.getElementById("orb-earth");
  if (el) { try { new OrbEarth(el).start(); } catch (e) { console.warn("globe:", e); } }
})();

// ── voice state ──────────────────────────────────────
function setVoiceState(state) {
  const orb = $("#voice-orb"); if (!orb) return;
  orb.classList.remove("listening","speaking","processing");
  if (state !== "idle" && state !== "standby") orb.classList.add(state);
  $("#voice-label").textContent = state === "idle" ? "STANDBY" : state.toUpperCase();
  wave.setState(state);
  const sub = $("#voice-sublabel");
  if (sub) {
    if (state === "speaking")        sub.textContent = "◉ TRANSMITTING";
    else if (state === "listening")  sub.textContent = "◉ LISTENING";
    else if (state === "processing") sub.textContent = "◉ PROCESSING";
    else                              sub.textContent = 'say "hey <agent>"';
  }
}
function setHeard(text, routeLabel) {
  const t = $("#heard-text"); const r = $("#heard-route");
  if (text) { t.classList.remove("empty"); t.textContent = text; }
  if (r) r.textContent = routeLabel || "";
}

/* ═══ WORLD MAP via D3 + TopoJSON (50m for better outlines) ═══ */

const CITIES = [
  { name: "HYDERABAD", lat: 17.385, lon: 78.4867, primary: true },
  { name: "NEW YORK",  lat: 40.7128, lon: -74.0060 },
  { name: "LONDON",    lat: 51.5074, lon: -0.1278 },
  { name: "TOKYO",     lat: 35.6762, lon: 139.6503 },
  { name: "SYDNEY",    lat: -33.8688, lon: 151.2093 },
  { name: "SAN FRAN",  lat: 37.7749, lon: -122.4194 },
];

let mapProjection = null;
let worldFeatures = null;

function fallbackProjection(lat, lon, w = 1000, h = 460) {
  return [(lon + 180) * w / 360, (90 - lat) * h / 180];
}

async function renderRealWorldMap() {
  const continents = document.getElementById("map-continents");
  if (!continents) return;

  if (typeof d3 === "undefined" || typeof topojson === "undefined") {
    console.warn("d3/topojson not loaded — using fallback shapes");
    renderFallbackContinents();
    renderArcs();
    renderCities();
    renderIndiaMap();
    renderTelanganaMap();
    return;
  }

  try {
    // Use 50m for sharper continent outlines (a bit larger payload, ~250KB)
    const r = await fetch("https://cdn.jsdelivr.net/npm/world-atlas@2/countries-50m.json");
    if (!r.ok) throw new Error("HTTP " + r.status);
    const world = await r.json();
    worldFeatures = topojson.feature(world, world.objects.countries);

    const w = 1000, h = 460;
    mapProjection = d3.geoNaturalEarth1().scale(190).translate([w / 2, h / 2 + 10]);
    const path = d3.geoPath(mapProjection);

    continents.innerHTML = "";
    worldFeatures.features.forEach(f => {
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", path(f));
      p.setAttribute("class", "country");
      continents.appendChild(p);
    });
  } catch (e) {
    console.warn("world map load failed", e);
    renderFallbackContinents();
  } finally {
    renderArcs();
    renderCities();
    renderIndiaMap();
    renderTelanganaMap();
  }
}

function renderFallbackContinents() {
  const host = document.getElementById("map-continents"); if (!host) return;
  const blobs = [
    { cx: 220, cy: 160, rx: 130, ry: 90 }, { cx: 320, cy: 330, rx: 60, ry: 100 },
    { cx: 510, cy: 145, rx: 55, ry: 45 },  { cx: 530, cy: 260, rx: 70, ry: 100 },
    { cx: 720, cy: 175, rx: 160, ry: 110 },{ cx: 830, cy: 345, rx: 65, ry: 40 },
  ];
  host.innerHTML = blobs.map(b =>
    `<ellipse cx="${b.cx}" cy="${b.cy}" rx="${b.rx}" ry="${b.ry}" class="country"/>`
  ).join("");
}
function projectCity(c) {
  if (mapProjection) { try { return mapProjection([c.lon, c.lat]); } catch (e) {} }
  return fallbackProjection(c.lat, c.lon);
}
function renderCities() {
  const host = document.getElementById("map-cities"); if (!host) return;
  host.innerHTML = "";
  for (const c of CITIES) {
    const [x, y] = projectCity(c);
    if (!isFinite(x) || !isFinite(y)) continue;
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.classList.add("city-marker"); if (c.primary) g.classList.add("primary");
    const r = c.primary ? 4 : 3;
    g.innerHTML = `
      <circle cx="${x}" cy="${y}" r="${r}" class="city-dot"/>
      <circle cx="${x}" cy="${y}" r="${r+2}" class="city-ping"/>
      <circle cx="${x}" cy="${y}" r="${r+2}" class="city-ping ping-2"/>
      <text x="${x + 9}" y="${y + 4}" class="city-label">${c.name}</text>`;
    host.appendChild(g);
  }
  // NOTE: #map-nodes badge is owned by the live fleet (refreshFleet), not the
  // static city list — don't overwrite it here.
}
function renderArcs() {
  const arcs = document.getElementById("map-arcs"); if (!arcs) return;
  arcs.innerHTML = "";
  const primary = CITIES.find(c => c.primary); if (!primary) return;
  const [px, py] = projectCity(primary);
  if (!isFinite(px) || !isFinite(py)) return;
  for (const c of CITIES) {
    if (c === primary) continue;
    const [cx, cy] = projectCity(c);
    if (!isFinite(cx) || !isFinite(cy)) continue;
    const mx = (px + cx) / 2;
    const my = Math.min(py, cy) - Math.abs(cx - px) * 0.18 - 30;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", `M ${px} ${py} Q ${mx} ${my} ${cx} ${cy}`);
    arcs.appendChild(path);
  }
}

/* ═══ INDIA MAP ═══════════════════════════════════════ */

const INDIA_CITIES = [
  { name: "DELHI",     lat: 28.61, lon: 77.21 },
  { name: "MUMBAI",    lat: 19.07, lon: 72.87 },
  { name: "KOLKATA",   lat: 22.57, lon: 88.36 },
  { name: "CHENNAI",   lat: 13.08, lon: 80.27 },
  { name: "BENGALURU", lat: 12.97, lon: 77.59 },
  { name: "HYDERABAD", lat: 17.385, lon: 78.4867, primary: true },
];
let indiaProjection = null;

function renderIndiaMap() {
  const host = document.getElementById("india-shape");
  const citiesHost = document.getElementById("india-cities");
  const arcsHost = document.getElementById("india-arcs");
  if (!host) return;

  // Default: leave the inline hand-traced fallback path in place.
  // If D3 + world data are available, replace with the real outline.
  if (typeof d3 !== "undefined" && worldFeatures) {
    const india = worldFeatures.features.find(
      f => String(f.id) === "356" || (f.properties && f.properties.name === "India")
    );
    if (india) {
      const w = 200, h = 240;
      indiaProjection = d3.geoMercator().fitExtent([[16, 20], [w - 16, h - 20]], india);
      const path = d3.geoPath(indiaProjection);
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", path(india));
      host.innerHTML = "";
      host.appendChild(p);
    } else {
      indiaProjection = null;
    }
  } else {
    indiaProjection = null;
  }

  // Telangana highlight overlaid on India (uses same projection so it lands inside India correctly)
  renderTelanganaHighlightOnIndia();

  if (citiesHost) {
    citiesHost.innerHTML = "";
    for (const c of INDIA_CITIES) {
      const proj = indiaProjection ? indiaProjection([c.lon, c.lat]) : null;
      const [x, y] = proj && isFinite(proj[0]) ? proj : indiaFallbackProject(c.lat, c.lon);
      const r = c.primary ? 3.5 : 2.6;
      const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.classList.add("region-marker"); if (c.primary) g.classList.add("primary");
      g.innerHTML = `
        <circle cx="${x}" cy="${y}" r="${r}" class="city-dot"/>
        <circle cx="${x}" cy="${y}" r="${r+1.5}" class="city-ping"/>
        <circle cx="${x}" cy="${y}" r="${r+1.5}" class="city-ping ping-2"/>
        <text x="${x + 6}" y="${y + 2}" class="city-label">${c.name}</text>`;
      citiesHost.appendChild(g);
    }
  }

  if (arcsHost) {
    arcsHost.innerHTML = "";
    const hyd = INDIA_CITIES.find(c => c.primary);
    const hp = indiaProjection ? indiaProjection([hyd.lon, hyd.lat]) : indiaFallbackProject(hyd.lat, hyd.lon);
    if (isFinite(hp[0])) {
      for (const c of INDIA_CITIES) {
        if (c === hyd) continue;
        const cp = indiaProjection ? indiaProjection([c.lon, c.lat]) : indiaFallbackProject(c.lat, c.lon);
        if (!isFinite(cp[0])) continue;
        const mx = (hp[0] + cp[0]) / 2;
        const my = Math.min(hp[1], cp[1]) - Math.abs(cp[0] - hp[0]) * 0.25 - 8;
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", `M ${hp[0]} ${hp[1]} Q ${mx} ${my} ${cp[0]} ${cp[1]}`);
        arcsHost.appendChild(path);
      }
    }
  }
}

function indiaFallbackProject(lat, lon) {
  // Bounds tuned to the inline hand-traced fallback path so cities land
  // on the actual rendered outline (lon 68.5–97, lat 8–37).
  const x = (lon - 68.5) * (160 / 28.5) + 20;
  const y = 240 - ((lat - 8) * (200 / 29) + 20);
  return [x, y];
}

// Telangana boundary in lat/lon, clockwise from NW. 30 points traced from the
// real state outline: Adilabad north cap → Godavari NE edge → Bhadradri SE
// tongue (easternmost ~81.05°E) → Khammam indent → Krishna south border →
// Gadwal south tail (~15.85°N) → smooth Karnataka west border → Nizamabad NW.
const TS_BOUNDARY_LATLON = [
  [19.65, 77.95], [19.92, 78.35], [19.85, 78.85], [19.70, 79.05],
  [19.45, 79.35], [19.30, 79.90], [18.85, 80.05], [18.75, 80.30],
  [18.40, 80.40], [18.10, 80.70], [17.80, 80.90], [17.55, 81.05],
  [17.20, 80.90], [16.95, 80.65], [16.75, 80.30], [16.85, 79.95],
  [16.55, 79.70], [16.40, 79.25], [16.25, 78.90], [15.95, 78.55],
  [15.85, 78.25], [16.10, 77.80], [16.40, 77.45], [16.95, 77.45],
  [17.35, 77.30], [17.85, 77.45], [18.20, 77.60], [18.45, 77.85],
  [18.85, 77.75], [19.25, 77.85]
];

function renderTelanganaHighlightOnIndia() {
  const host = document.getElementById("india-ts-highlight");
  if (!host) return;
  const project = (lat, lon) => {
    if (indiaProjection) {
      try { const r = indiaProjection([lon, lat]); if (isFinite(r[0])) return r; } catch (e) {}
    }
    return indiaFallbackProject(lat, lon);
  };
  const pts = TS_BOUNDARY_LATLON.map(([lat, lon]) => {
    const [x, y] = project(lat, lon);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  host.innerHTML = `<polygon points="${pts.join(' ')}"/>`;
}

/* ═══ TELANGANA — refined polygon + Hyderabad pin ═══ */

const TS_CITIES = [
  { name: "HYDERABAD",  lat: 17.385, lon: 78.4867, primary: true },
  { name: "WARANGAL",   lat: 18.00,  lon: 79.58 },
  { name: "NIZAMABAD",  lat: 18.67,  lon: 78.10 },
  { name: "KARIMNAGAR", lat: 18.43,  lon: 79.13 },
  { name: "KHAMMAM",    lat: 17.25,  lon: 80.15 },
];
function tsFallbackProject(lat, lon) {
  // Matches the inline Telangana path projection:
  // lon 77.27–81.05 → x 16–184, lat 15.83–19.92 → y 204–16.
  const x = (lon - 77.27) * 44.44 + 16;
  const y = 204 - (lat - 15.83) * 45.97;
  return [x, y];
}
function renderTelanganaMap() {
  const citiesHost = document.getElementById("ts-cities");
  if (!citiesHost) return;
  citiesHost.innerHTML = "";
  for (const c of TS_CITIES) {
    const [x, y] = tsFallbackProject(c.lat, c.lon);
    const r = c.primary ? 4 : 2.5;
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.classList.add("region-marker"); if (c.primary) g.classList.add("primary");
    g.innerHTML = `
      <circle cx="${x}" cy="${y}" r="${r}" class="city-dot"/>
      <circle cx="${x}" cy="${y}" r="${r+2}" class="city-ping"/>
      <circle cx="${x}" cy="${y}" r="${r+2}" class="city-ping ping-2"/>
      <text x="${x + 6}" y="${y + 2}" class="city-label">${c.name}</text>`;
    citiesHost.appendChild(g);
  }
}

renderRealWorldMap();

// ── weather ──────────────────────────────────────────
let userLat = null, userLon = null;
let weatherCity = "HYDERABAD";
// module-scope live-data stashes — declared HERE (early) so functions called
// during load (refreshWeather via initGeo, etc.) never hit a temporal-dead-zone
// ReferenceError, which would halt all remaining top-level init.
let lastAgenda = null, lastThreat = null, lastMemorySnap = null, lastWeatherOk = null;

function setWeatherState(state, message) {
  const card = $("#weather-card"); if (!card) return;
  card.classList.remove("loading", "error");
  if (state) card.classList.add(state);
  if (message != null) $("#weather-label").textContent = message;
}

async function refreshWeather(force = false) {
  setWeatherState("loading", "acquiring signal…");
  $("#weather-update").textContent = "FETCHING…";
  try {
    let url = "/api/weather";
    if (userLat != null && userLon != null) {
      url += `?lat=${userLat.toFixed(4)}&lon=${userLon.toFixed(4)}`;
    }
    const ctl = new AbortController();
    const timeout = setTimeout(() => ctl.abort(), 12000);
    const r = await fetch(url, { signal: ctl.signal, cache: force ? "no-store" : "default" });
    clearTimeout(timeout);
    const d = await r.json();
    if (!d || d.error) {
      lastWeatherOk = false;
      setWeatherState("error", "unavailable");
      $("#weather-update").textContent = "ERR · " + new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
      return;
    }
    lastWeatherOk = true;
    setWeatherState(null, (d.label || "").toUpperCase() || "—");
    $("#weather-glyph").textContent = d.glyph || "◐";
    $("#weather-temp").textContent  = d.temp_c != null ? Math.round(d.temp_c) : "--";
    $("#weather-humid").textContent = d.humidity ?? "--";
    $("#weather-wind").textContent  = d.wind_kmh != null ? Math.round(d.wind_kmh) : "--";
    $("#weather-feels").textContent = d.feels_c != null ? Math.round(d.feels_c) : "--";
    $("#weather-update").textContent = "OK · " + new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
    $("#hw-glyph").textContent = d.glyph || "◐";
    $("#hw-temp").textContent = d.temp_c != null ? `${Math.round(d.temp_c)}°` : "--°";
    if (d.city) {
      $("#weather-city").textContent = `${(weatherCity || d.city || "").toUpperCase()} · WEATHER`;
    }
  } catch (e) {
    setWeatherState("error", e.name === "AbortError" ? "timeout" : "fetch failed");
    $("#weather-update").textContent = "ERR";
    console.warn("weather fetch failed:", e);
  }
}
refreshWeather();
setInterval(() => refreshWeather(false), 5 * 60 * 1000);   // refresh every 5 min
$("#weather-refresh")?.addEventListener("click", () => refreshWeather(true));

// ── connectivity ─────────────────────────────────────
async function refreshConnectivity() {
  try {
    const r = await fetch("/api/connectivity"); const d = await r.json();
    const wifi = d.wifi || {};
    const link = d.link || null;
    const wifiRow = $("#conn-wifi"); const wifiVal = $("#wifi-val");
    const linkLabel = $("#conn-link-label");
    if (wifi.connected) {                          // on WiFi
      if (linkLabel) linkLabel.textContent = "WIFI";
      wifiVal.textContent = `${wifi.ssid || "WIFI"} · ${wifi.signal || "—"}`;
      wifiRow.classList.remove("bad"); wifiRow.classList.add("ok");
    } else if (link && link.up) {                  // WiFi down but wired/other uplink is live
      if (linkLabel) linkLabel.textContent = link.type === "wifi" ? "WIFI" : "ETHERNET";
      const spd = link.speed_mbps ? ` · ${link.speed_mbps} Mbps` : "";
      wifiVal.textContent = `${link.name.slice(0, 14)}${spd}`;
      wifiRow.classList.remove("bad"); wifiRow.classList.add("ok");
    } else {                                        // genuinely offline
      if (linkLabel) linkLabel.textContent = "LINK";
      wifiVal.textContent = "offline";
      wifiRow.classList.remove("ok"); wifiRow.classList.add("bad");
    }
    const ble = d.bluetooth || {};
    $("#ble-val").textContent = ble.count ? `${ble.count} paired` : "none";
    $("#conn-ble").classList.toggle("ok", ble.count > 0);

    const p = d.ping_ms ?? -1;
    // orb flank: real link latency + probe loss
    const fv = $("#fl-vector"); if (fv) fv.textContent = p >= 0 ? String(Math.min(999, p)) : "---";
    const fp = $("#fl-pkt"); if (fp) fp.textContent = p >= 0 ? "0%" : "100%";
    const pingRow = $("#conn-net");
    if (p < 0)        { $("#ping-val").textContent = "—";       pingRow.classList.add("bad");  pingRow.classList.remove("ok","warn"); }
    else if (p < 100) { $("#ping-val").textContent = `${p} ms`; pingRow.classList.add("ok");   pingRow.classList.remove("warn","bad"); }
    else if (p < 300) { $("#ping-val").textContent = `${p} ms`; pingRow.classList.add("warn"); pingRow.classList.remove("ok","bad"); }
    else              { $("#ping-val").textContent = `${p} ms`; pingRow.classList.add("bad");  pingRow.classList.remove("ok","warn"); }

    // wifi link speed takes precedence in the network tool-card when present
    const tcNet = $("#tc-net");
    if (tcNet && wifi.connected && wifi.speed_rx_mbps) {
      window._wifiSpeedShown = true;
      tcNet.textContent = `${Math.round(parseFloat(wifi.speed_rx_mbps))} Mbps · ${wifi.ssid || "WIFI"}`;
    } else {
      window._wifiSpeedShown = false;   // fall back to live throughput from metrics
    }
  } catch (e) {}
}
refreshConnectivity(); setInterval(refreshConnectivity, 30000);

// ── battery (real, from server psutil — browser getBattery is dead in Chrome) ─
async function refreshBattery() {
  try {
    const d = await (await fetch("/api/hardware")).json();
    const p = d.power || {};
    const row = $("#conn-bat"); const val = $("#bat-val");
    if (!row || !val) return;
    row.classList.remove("ok", "warn", "bad");
    if (!p.present) { val.textContent = "AC POWER ⚡"; row.classList.add("ok"); return; }
    const pct = p.percent;
    val.textContent = `${pct}%${p.plugged ? " ⚡" : ""}`;
    if (pct > 30 || p.plugged) row.classList.add("ok");
    else if (pct > 15) row.classList.add("warn");
    else row.classList.add("bad");
  } catch (e) {}
}
refreshBattery(); setInterval(refreshBattery, 30000);

// ── location: real IP geolocation baseline, refined by precise GPS ───
function updateLocation(text, coords) {
  if (text) $("#location-value").textContent = text;
  if (coords) $("#location-coords").textContent = coords;
}
function fmtCoords(lat, lon) {
  return `${Math.abs(lat).toFixed(2)}°${lat >= 0 ? "N" : "S"} · ${Math.abs(lon).toFixed(2)}°${lon >= 0 ? "E" : "W"}`;
}
async function initGeo() {
  try {
    const g = await (await fetch("/api/geo")).json();
    if (g && g.lat != null) {
      userLat = g.lat; userLon = g.lon;
      window._geo = g;
      const parts = [g.city, g.region, g.country].filter(Boolean);
      if (parts.length) updateLocation(parts.map(s => String(s).toUpperCase()).join(" · "));
      updateLocation(null, fmtCoords(g.lat, g.lon));
      if (g.city) {
        weatherCity = String(g.city).toUpperCase();
        const wc = $("#weather-city"); if (wc) wc.textContent = `${weatherCity} · WEATHER`;
      }
      const isp = $("#isp-value");
      if (isp) isp.textContent = g.isp ? `${g.isp}${g.ip ? " · " + g.ip : ""}`.slice(0, 38) : "—";
      refreshWeather(true);
    }
  } catch (e) {}
  detectLocation();   // refine with precise browser GPS if the user grants it
}
function detectLocation() {
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(async (pos) => {
    const { latitude, longitude } = pos.coords;
    userLat = latitude; userLon = longitude;
    updateLocation(null, `${latitude.toFixed(2)}°${latitude>=0?"N":"S"} · ${longitude.toFixed(2)}°${longitude>=0?"E":"W"}`);
    // re-fetch weather for the detected coords
    refreshWeather(true);
    try {
      const r = await fetch(`https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${latitude}&longitude=${longitude}&localityLanguage=en`);
      const d = await r.json();
      const parts = [d.locality, d.principalSubdivision, d.countryName].filter(Boolean);
      if (parts.length) {
        updateLocation(parts.map(s => s.toUpperCase()).join(" · "));
        weatherCity = (d.locality || d.principalSubdivision || "your area").toUpperCase();
        const wc = $("#weather-city");
        if (wc) wc.textContent = `${weatherCity} · WEATHER`;
      }
    } catch (e) {}
  }, (err) => {
    console.warn("geolocation denied/unavailable:", err && err.message);
  }, { timeout: 8000, maximumAge: 60_000 });
}
initGeo();

// ── agenda + inbox ───────────────────────────────────
function fmtTime(ts) { return new Date(ts * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false }); }
function fmtCountdown(mins) {
  if (mins < 0) return "NOW";
  if (mins < 1) return "<1 MIN";
  if (mins < 60) return `${Math.round(mins)} MIN`;
  return `${Math.floor(mins/60)}H ${Math.round(mins%60)}M`;
}
function renderAgenda(events, source) {
  const host = $("#agenda-list"); host.innerHTML = "";
  $("#agenda-badge").textContent = `${events.length} TODAY`;
  if (!events.length) {
    host.innerHTML = source === "google"
      ? '<div class="agenda-empty">calendar clear — no upcoming events</div>'
      : '<div class="agenda-empty">no data — connect Google Calendar via the <b>G</b> button</div>';
    return;
  }
  for (const e of events) {
    const div = document.createElement("div");
    div.className = "agenda-item" + (e.priority === "high" ? " high" : "") + (e.minutes_until <= 10 && e.minutes_until > 0 ? " imminent" : "");
    div.innerHTML = `
      <div class="agenda-time">
        ${fmtTime(e.start_ts)}
        <span class="agenda-countdown">${fmtCountdown(e.minutes_until)}</span>
      </div>
      <div class="agenda-body">
        <div class="agenda-title">${escapeHTML(e.title)}</div>
        <div class="agenda-meta">${escapeHTML((e.attendees || []).join(", ") || e.location || `${e.duration_min} min`)}</div>
      </div>`;
    host.appendChild(div);
  }
}
function renderInbox(emails, priorityCount, source) {
  const host = $("#inbox-list"); host.innerHTML = "";
  $("#inbox-badge").textContent = priorityCount ? `${priorityCount} PRIORITY` : `${emails.length} RECENT`;
  if (!emails.length) {
    host.innerHTML = source === "google"
      ? '<div class="inbox-empty">no recent mail</div>'
      : '<div class="inbox-empty">no data — connect Gmail via the <b>G</b> button</div>';
    return;
  }
  for (const m of emails) {
    const div = document.createElement("div");
    div.className = "inbox-item" + (m.priority === "priority" ? " priority" : "");
    div.innerHTML = `
      <div class="inbox-sender">${escapeHTML(m.sender)}</div>
      <div class="inbox-subject">${escapeHTML(m.subject)}</div>
      <div class="inbox-snippet">${escapeHTML(m.snippet || "")}</div>`;
    host.appendChild(div);
  }
}
async function refreshAgenda() {
  try {
    const r = await fetch("/api/agenda"); const d = await r.json();
    renderAgenda(d.events || [], d.source);
    renderInbox(d.emails || [], d.priority_unread || 0, d.source);
    try {
      pendingActions = (d.events || []).length + (d.priority_unread || 0);
      renderPriorityAlerts(d);
      renderCalIntel(d);
      if (d.source === "google") {
        const b = $("#agenda-badge");
        if (b) b.textContent = `${(d.events || []).length} · GOOGLE`;
      }
      lastAgenda = d; updateLiveOps();
    } catch (e) {}
  } catch (e) {}
}
refreshAgenda(); setInterval(refreshAgenda, 30000);

// ── resource bars ────────────────────────────────────
function updateResource(cpu, mem, disk) {
  if (cpu != null) { $("#res-cpu").textContent = `${Math.round(cpu)}%`; $("#res-cpu-bar").style.width = `${Math.min(100, cpu)}%`; }
  if (mem != null) { $("#res-mem").textContent = `${Math.round(mem)}%`; $("#res-mem-bar").style.width = `${Math.min(100, mem)}%`; }
  if (disk != null){ $("#res-disk2").textContent = `${Math.round(disk)}%`; $("#res-disk-bar").style.width = `${Math.min(100, disk)}%`; }
}

// ── AI gauge animation (r=36, circumference ≈ 226) ─
let aiGaugeBase = 0.92;
const AI_CIRC = 2 * Math.PI * 36;  // ≈ 226 (matches HTML circle r=36)
function animateAiGauge(active) {
  const arc = $("#ai-arc"); if (!arc) return;
  const target = active ? 0.78 : aiGaugeBase;
  arc.style.transition = "stroke-dashoffset 0.6s ease";
  arc.setAttribute("stroke-dashoffset", String(AI_CIRC * (1 - target)));
}
(function () {
  const arc = $("#ai-arc"); if (!arc) return;
  arc.setAttribute("stroke-dasharray", String(AI_CIRC.toFixed(1)));
  arc.setAttribute("stroke-dashoffset", String(AI_CIRC * (1 - aiGaugeBase)));
})();

// ── quick access buttons ─────────────────────────────
document.querySelectorAll(".quick-btn").forEach(btn => {
  btn.addEventListener("click", async () => {
    if (btn.classList.contains("busy")) return;
    btn.classList.add("busy");
    const action = btn.dataset.action;
    try {
      if (action === "worldmonitor") {
        await fetch("/api/browser/open", {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ url: "https://www.worldmonitor.app", fullscreen: false, app_mode: true }),
        });
        logEvent('<span class="tag">[quick]</span> launching worldmonitor.app');
      } else if (action === "refresh-news") {
        await fetch("/api/news/refresh", { method: "POST" });
        logEvent('<span class="tag">[quick]</span> world feed refresh queued');
      } else if (action === "system-scan") {
        const r = await fetch("/api/ask", {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ prompt: "Run a system scan and report any concerns in one sentence.", agent: "stark" }),
        });
        const d = await r.json();
        if (d.reply) logEvent(`<span class="tag">[stark]</span> ${escapeHTML(d.reply)}`);
      } else if (action === "briefing") {
        logEvent('<span class="tag">[quick]</span> compiling executive briefing…');
        const r = await fetch("/api/briefing", { method: "POST" });
        const d = await r.json();
        if (d.insight) logEvent(`<span class="tag">[briefing]</span> ${escapeHTML(d.insight)}`);
      } else if (action === "network-diag") {
        await refreshConnectivity();
        logEvent('<span class="tag">[quick]</span> network diagnostic complete');
      } else if (action === "security-sweep") {
        const r = await fetch("/api/ask", {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ prompt: "Security sweep: any anomalies on your end? One sentence.", agent: "hawkeye" }),
        });
        const d = await r.json();
        if (d.reply) logEvent(`<span class="tag">[hawkeye]</span> ${escapeHTML(d.reply)}`);
      }
    } catch (e) {
      logEvent("quick-action failed: " + escapeHTML(e.message), "error");
    } finally {
      setTimeout(() => btn.classList.remove("busy"), 800);
    }
  });
});

/* ═══ TOOL MODAL ═══════════════════════════════════ */

const TOOL_DATA = {
  "ai-diagnostics": {
    title: "AI DIAGNOSTICS",
    sub: "LIVE LLM & AGENT TELEMETRY",
    icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 3 L12 21 M3 12 L21 12"/><path d="M5 5 L19 19 M5 19 L19 5" opacity="0.45"/><circle cx="12" cy="12" r="2.6" fill="currentColor"/></svg>`,
    render: (state) => `
      <div class="mb-grid">
        <div class="mb-stat"><span class="lbl">MODEL</span><span class="val">${escapeHTML(state.brain_mode || "—")}</span></div>
        <div class="mb-stat"><span class="lbl">SUBSYSTEMS</span><span class="val ok">8 / 8 NOMINAL</span></div>
        <div class="mb-stat"><span class="lbl">ROUTING</span><span class="val ok">99.4%</span></div>
        <div class="mb-stat"><span class="lbl">AVG LATENCY</span><span class="val">0.32 s</span></div>
        <div class="mb-stat"><span class="lbl">CALLS TODAY</span><span class="val">1,247</span></div>
        <div class="mb-stat"><span class="lbl">TOKENS USED</span><span class="val">1.2 M / 10 M</span></div>
      </div>
      <div class="mb-list">
        <div class="mb-row">All 8 agents (Jarvis, Captain, Stark, Widow, Hawkeye, Hulk, Thor, Vision) responding within tolerance.
          <div class="mb-row-meta">LAST SWEEP · ${new Date().toLocaleTimeString("en-GB",{hour:"2-digit",minute:"2-digit",hour12:false})}</div></div>
      </div>`,
  },
  "satellite-feed": {
    title: "SATELLITE FEED",
    sub: "ORBITAL UPLINK · GEO + LEO",
    icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M 5 19 L 19 5"/><path d="M 4 14 A 10 10 0 0 0 14 4"/><circle cx="6" cy="18" r="2" fill="currentColor"/><path d="M 11 11 L 14 14"/></svg>`,
    render: () => `
      <div class="mb-grid">
        <div class="mb-stat"><span class="lbl">ACTIVE SATS</span><span class="val">14</span></div>
        <div class="mb-stat"><span class="lbl">COVERAGE</span><span class="val ok">87%</span></div>
        <div class="mb-stat"><span class="lbl">UPLINK</span><span class="val">12.3 Mbps</span></div>
        <div class="mb-stat"><span class="lbl">DOWNLINK</span><span class="val">48.6 Mbps</span></div>
        <div class="mb-stat"><span class="lbl">STRONGEST</span><span class="val">GEO · INDIAN OCEAN</span></div>
        <div class="mb-stat"><span class="lbl">NEXT PASS</span><span class="val warn">HST · 12 min</span></div>
      </div>
      <div class="mb-list">
        <div class="mb-row">Acquired handshake with GSAT-30 · signal 96% strength
          <div class="mb-row-meta">UTC · ${new Date().toUTCString().slice(17, 22)}</div></div>
        <div class="mb-row">Resync with Starlink mesh · 4 new satellites in window
          <div class="mb-row-meta">UTC · -4 min</div></div>
      </div>`,
  },
  "threat-scanner": {
    title: "THREAT SCANNER",
    sub: "ACTIVE PERIMETER MONITORING",
    icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M 12 2 L 20 5 V 12 Q 20 18 12 22 Q 4 18 4 12 V 5 Z"/><circle cx="12" cy="12" r="2.5" fill="currentColor"/></svg>`,
    render: () => `
      <div class="mb-grid">
        <div class="mb-stat"><span class="lbl">ACTIVE THREATS</span><span class="val ok">0</span></div>
        <div class="mb-stat"><span class="lbl">RESOLVED 24H</span><span class="val">7</span></div>
        <div class="mb-stat"><span class="lbl">FIREWALL BLOCKS</span><span class="val">142</span></div>
        <div class="mb-stat"><span class="lbl">INTRUSIONS</span><span class="val ok">0</span></div>
        <div class="mb-stat"><span class="lbl">VULNERABILITIES</span><span class="val warn">2 LOW</span></div>
        <div class="mb-stat"><span class="lbl">LAST FULL SCAN</span><span class="val">2 H AGO</span></div>
      </div>
      <div class="mb-list">
        <div class="mb-row">Perimeter integrity: GREEN. Encryption layer (AES-256) holding.<div class="mb-row-meta">SHIELD · ACTIVE</div></div>
        <div class="mb-row">2 low-priority CVEs queued for the next maintenance window.<div class="mb-row-meta">CVE-2025-7XXX · CVE-2025-8XXX</div></div>
      </div>`,
  },
  "mission-files": {
    title: "MISSION FILES",
    sub: "OPERATIONS ARCHIVE",
    icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M 3 8 V 19 H 21 V 8 H 13 L 11 6 H 3 Z"/><line x1="3" y1="12" x2="21" y2="12"/></svg>`,
    render: () => `
      <div class="mb-list">
        <div class="mb-row"><strong>Project Helios v4 — Core Specs</strong><div class="mb-row-meta">MODIFIED · 2 H AGO · classification: stark-1</div></div>
        <div class="mb-row"><strong>Stark Defense Grid — Phase II</strong><div class="mb-row-meta">MODIFIED · YESTERDAY · classification: stark-1</div></div>
        <div class="mb-row"><strong>Neural Net Upgrade — Architecture</strong><div class="mb-row-meta">MODIFIED · 4 DAYS AGO · classification: stark-2</div></div>
        <div class="mb-row"><strong>Hyderabad Lab — Operations Brief</strong><div class="mb-row-meta">MODIFIED · 1 WEEK AGO · classification: stark-3</div></div>
        <div class="mb-row"><strong>AISIN Joint Venture — Terms</strong><div class="mb-row-meta">MODIFIED · 2 WEEKS AGO · classification: stark-2</div></div>
      </div>`,
  },
  "security-logs": {
    title: "SECURITY LOGS",
    sub: "EVENT AUDIT TRAIL · LAST 24H",
    icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="10" rx="1"/><path d="M 8 11 V 7 A 4 4 0 0 1 16 7 V 11"/><circle cx="12" cy="15" r="1.6" fill="currentColor"/></svg>`,
    render: () => `
      <div class="mb-grid">
        <div class="mb-stat"><span class="lbl">EVENTS</span><span class="val">1,247</span></div>
        <div class="mb-stat"><span class="lbl">ALERTS</span><span class="val ok">0</span></div>
      </div>
      <div class="mb-list">
        <div class="mb-row">14:32 — Admin login · 127.0.0.1 · success<div class="mb-row-meta">SESSION · 0x4F12</div></div>
        <div class="mb-row">13:50 — Firewall rule updated · port 8765 allowed loopback<div class="mb-row-meta">RULE · WL-7</div></div>
        <div class="mb-row">11:15 — Encrypted backup completed · 2.3 GB<div class="mb-row-meta">VAULT · A-7</div></div>
        <div class="mb-row">09:40 — Security sweep run · 0 anomalies<div class="mb-row-meta">SWEEP · 4f12</div></div>
        <div class="mb-row">08:00 — Daily integrity check · pass<div class="mb-row-meta">HASH · OK</div></div>
      </div>`,
  },
  "network-activity": {
    title: "NETWORK ACTIVITY",
    sub: "LIVE TRAFFIC ANALYSIS",
    icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="18" r="2"/><circle cx="12" cy="12" r="2.5" fill="currentColor"/><path d="M 7.5 7.5 L 10.5 10.5 M 16.5 7.5 L 13.5 10.5 M 7.5 16.5 L 10.5 13.5 M 16.5 16.5 L 13.5 13.5"/></svg>`,
    render: (state) => `
      <div class="mb-grid">
        <div class="mb-stat"><span class="lbl">WAN SSID</span><span class="val">${escapeHTML(state.wifi_ssid || "—")}</span></div>
        <div class="mb-stat"><span class="lbl">LINK SPEED</span><span class="val">${escapeHTML(state.wifi_speed || "—")}</span></div>
        <div class="mb-stat"><span class="lbl">SIGNAL</span><span class="val ok">${escapeHTML(state.wifi_signal || "—")}</span></div>
        <div class="mb-stat"><span class="lbl">PING</span><span class="val">${escapeHTML(state.ping || "—")} ms</span></div>
        <div class="mb-stat"><span class="lbl">CONNECTIONS</span><span class="val">1,247</span></div>
        <div class="mb-stat"><span class="lbl">TOP APP</span><span class="val">chrome.exe · 32%</span></div>
      </div>
      <div class="mb-list">
        <div class="mb-row">Encryption: WPA2 · channel ${escapeHTML(state.wifi_channel || "—")}<div class="mb-row-meta">SECURE</div></div>
      </div>`,
  },
};

async function gatherNetworkSnapshot() {
  let wifi = {}, ping = "—";
  try {
    const r = await fetch("/api/connectivity"); const d = await r.json();
    wifi = d.wifi || {}; ping = d.ping_ms ?? "—";
  } catch (e) {}
  let brainMode = "—";
  try {
    const r = await fetch("/api/status"); const d = await r.json();
    brainMode = (d.brain_mode || "—").toUpperCase();
  } catch (e) {}
  return {
    brain_mode: brainMode,
    wifi_ssid: wifi.ssid || "—",
    wifi_speed: wifi.speed_rx_mbps ? `${wifi.speed_rx_mbps} Mbps` : "—",
    wifi_signal: wifi.signal || "—",
    wifi_channel: wifi.channel || "—",
    ping: String(ping),
  };
}

async function openToolModal(toolKey) {
  const def = TOOL_DATA[toolKey]; if (!def) return;
  const modal = $("#tool-modal"); if (!modal) return;
  $("#modal-title").textContent = def.title;
  $("#modal-sub").textContent = def.sub;
  $("#modal-icon").innerHTML = def.icon;
  $("#modal-body").innerHTML = '<div style="color:var(--text-dim);font-style:italic">acquiring live data…</div>';
  modal.hidden = false;

  const state = await gatherNetworkSnapshot();
  try {
    $("#modal-body").innerHTML = def.render(state);
  } catch (e) {
    $("#modal-body").innerHTML = `<div style="color:var(--error)">render error: ${escapeHTML(e.message)}</div>`;
  }
}
function closeToolModal() {
  const modal = $("#tool-modal"); if (modal) modal.hidden = true;
}

document.querySelectorAll(".tool-card").forEach(btn => {
  btn.addEventListener("click", () => {
    btn.classList.add("flash");
    setTimeout(() => btn.classList.remove("flash"), 700);
    openToolModal(btn.dataset.tool);
  });
});
$("#modal-close")?.addEventListener("click", closeToolModal);
$("#modal-backdrop")?.addEventListener("click", closeToolModal);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeToolModal(); });

// keep AI tool-card stat in sync with live brain mode
async function refreshToolCardStats() {
  try {
    const r = await fetch("/api/status"); const d = await r.json();
    const ai = $("#tc-ai");
    if (ai) ai.textContent = `BRAIN · ${(d.brain_mode || "?").toUpperCase()} · 8/8`;
  } catch (e) {}
}
refreshToolCardStats();
setInterval(refreshToolCardStats, 60000);

// ── operator camera ──────────────────────────────────
const camRoot   = $("#operator-cam");
const camVideo  = $("#cam-video");
const camStatus = $("#cam-status");
const camToggle = $("#cam-toggle");
const camResEl  = $("#cam-res");
let camStream   = null;

/* Phase 3 · multi-camera situational awareness */
let camDeviceId = "";            // "" = default camera
function camLabel() {
  const sel = $("#cam-select");
  const opt = sel && sel.selectedOptions[0];
  return (opt && opt.value ? opt.textContent : "primary").slice(0, 40);
}
async function enumerateCameras() {
  try {
    const devs = await navigator.mediaDevices.enumerateDevices();
    const cams = devs.filter(d => d.kind === "videoinput");
    const sel = $("#cam-select"); if (!sel) return;
    const cur = camDeviceId;
    sel.innerHTML = cams.map((c, i) =>
      `<option value="${escapeHTML(c.deviceId)}"${c.deviceId === cur ? " selected" : ""}>` +
      `${escapeHTML((c.label || `CAMERA ${i + 1}`).toUpperCase().slice(0, 28))}</option>`).join("")
      || '<option value="">DEFAULT CAMERA</option>';
    if (cams.length > 1)
      logEvent(`<span class="tag">[cam]</span> ${cams.length} cameras available — multi-camera awareness armed`);
  } catch (e) {}
}
$("#cam-select")?.addEventListener("change", async () => {
  camDeviceId = $("#cam-select").value || "";
  if (camStream) {          // hot-switch: restart the stream on the new device
    stopCamera();
    await startCamera();
    logEvent(`<span class="tag">[cam]</span> switched to ${escapeHTML(camLabel())}`);
  }
});

async function startCamera() {
  if (camStream) return;
  logEvent('<span class="tag">[cam]</span> requesting camera access…');

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    camStatus.textContent = "✕ UNSUPPORTED";
    camRoot.classList.add("error");
    logEvent('<span class="tag">[cam]</span> camera API not available — http:// origin may block it. Try http://127.0.0.1:8765 (not 0.0.0.0 / LAN ip)', "warn");
    return;
  }
  camRoot.classList.remove("error");
  camRoot.classList.add("requesting");
  camStatus.textContent = "◌ REQUESTING…";
  try {
    const vidConstraints = {
      width:  { ideal: 480 },
      height: { ideal: 360 },
    };
    if (camDeviceId) vidConstraints.deviceId = { exact: camDeviceId };
    else vidConstraints.facingMode = "user";
    const stream = await navigator.mediaDevices.getUserMedia({
      video: vidConstraints,
      audio: false,
    });
    camStream = stream;
    camVideo.srcObject = stream;
    enumerateCameras();   // labels become available once permission is granted

    // Explicit play() — autoplay can silently fail on display:none or unfocused tabs.
    try {
      await camVideo.play();
    } catch (playErr) {
      console.warn("[cam] play() failed:", playErr);
      logEvent(`<span class="tag">[cam]</span> play blocked: ${escapeHTML(playErr.message || playErr.name)}`, "warn");
    }

    camRoot.classList.remove("requesting");
    camRoot.classList.add("live");
    camStatus.textContent = "◉ LIVE";

    // Read actual stream dimensions once metadata arrives
    const showRes = () => {
      const w = camVideo.videoWidth, h = camVideo.videoHeight;
      if (w && h && camResEl) camResEl.textContent = `${w}×${h}`;
    };
    if (camVideo.readyState >= 1) showRes();
    else camVideo.addEventListener("loadedmetadata", showRes, { once: true });

    logEvent('<span class="tag">[cam]</span> operator view online');
    try { Presence.start(); } catch (e) { console.warn("[presence] start failed:", e); }

    // If the user revokes permission or the device disappears, clean up.
    stream.getVideoTracks().forEach(t => {
      t.addEventListener("ended", () => stopCamera());
    });
  } catch (e) {
    console.error("[cam] getUserMedia failed:", e);
    camRoot.classList.remove("requesting", "live");
    camRoot.classList.add("error");
    const msg = e.name === "NotAllowedError" ? "PERMISSION DENIED"
              : e.name === "NotFoundError"   ? "NO CAMERA FOUND"
              : e.name === "NotReadableError"? "CAMERA IN USE"
              : e.name === "OverconstrainedError" ? "CONSTRAINTS UNMET"
              : e.name === "SecurityError"   ? "BLOCKED · USE HTTPS OR 127.0.0.1"
              : (e.name || "ERROR");
    camStatus.textContent = "✕ " + msg;
    logEvent(`<span class="tag">[cam]</span> ${escapeHTML(msg)} · ${escapeHTML(e.message || "")}`, "warn");
  }
}

function stopCamera() {
  if (camStream) {
    try { camStream.getTracks().forEach(t => t.stop()); } catch (e) {}
    camStream = null;
  }
  if (camVideo) camVideo.srcObject = null;
  camRoot.classList.remove("live", "requesting", "error");
  camStatus.textContent = "◌ OFFLINE";
  if (camResEl) camResEl.textContent = "--×--";
  try { Presence.stop(); } catch (e) {}
  try { if (VisionAI.on) VisionAI.toggle(); } catch (e) {}   // disable AI vision with the camera
  logEvent('<span class="tag">[cam]</span> operator view offline');
}

camToggle?.addEventListener("click", () => {
  if (camStream) stopCamera(); else startCamera();
});

/* ═══ AI VISION — Claude describes the webcam feed ═══════════
   Opt-in (protects Claude usage): when ON and the camera is live, a frame is
   captured every VISION_INTERVAL and sent to /api/vision/analyze; Claude's
   one-line observation is displayed + spoken. Also fires on demand (voice
   "what do you see", the AI button, or the manual analyze). */
const VisionAI = {
  on: false, timer: null, busy: false,
  interval: 45000,              // 45 s between auto-analyses — frequent operator comments
  canvas: document.createElement("canvas"),

  toggle() {
    this.on = !this.on;
    const btn = $("#cam-ai-toggle");
    if (btn) btn.classList.toggle("on", this.on);
    try { localStorage.setItem("vision-ai", this.on ? "1" : "0"); } catch (e) {}
    if (this.on) {
      if (!camStream) { logEvent('<span class="tag">[vision-ai]</span> enable the camera first', "warn"); }
      else { logEvent('<span class="tag">[vision-ai]</span> Claude vision ON — commenting every 45s'); this.analyze(); }
      this.timer = setInterval(() => { if (camStream) this.analyze(); }, this.interval);
    } else {
      if (this.timer) { clearInterval(this.timer); this.timer = null; }
      logEvent('<span class="tag">[vision-ai]</span> Claude vision OFF');
    }
  },

  async analyze(force = false) {
    if (this.busy) return;
    if (!camStream || camVideo.readyState < 2) {
      if (force) logEvent('<span class="tag">[vision-ai]</span> camera not ready', "warn");
      return;
    }
    this.busy = true;
    const btn = $("#cam-ai-toggle"); if (btn) btn.classList.add("busy");
    const vo = $("#vision-obs"); if (vo) vo.textContent = "◍ Claude is analysing the feed…";
    try {
      this.canvas.width = 320; this.canvas.height = 240;
      this.canvas.getContext("2d").drawImage(camVideo, 0, 0, 320, 240);
      const frame = this.canvas.toDataURL("image/jpeg", 0.6);
      const r = await fetch("/api/vision/analyze", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ frame, speak: true, camera: camLabel() }),
      });
      const d = await r.json();
      if (!d.ok) {
        if (vo) vo.textContent = d.error === "rate-limited"
          ? `◍ cooling down (${d.retry_in}s)…` : `◍ ${d.error}`;
      }
      // success arrives via WS "vision-obs" + "insight"
    } catch (e) {
      if (vo) vo.textContent = "◍ vision request failed";
    } finally {
      this.busy = false;
      if (btn) btn.classList.remove("busy");
    }
  },
};
$("#cam-ai-toggle")?.addEventListener("click", () => VisionAI.toggle());

// Tidy up if the user navigates away
window.addEventListener("beforeunload", () => { if (camStream) stopCamera(); });

/* ═══ PRESENCE MONITOR — motion-based, identity-free ═══
   Samples the live video onto a 64×48 canvas every 450 ms, computes
   frame-to-frame luminance difference (motion) and scene variance
   (signal check). No face detection, no identification, no frames
   leave the machine — only an abstract state string is reported. */

function setChip(sel, base, text, cls) {
  const el = $(sel); if (!el) return;
  el.textContent = text;
  el.className = base + (cls ? " " + cls : "");
}

const Presence = {
  canvas: document.createElement("canvas"),
  ctx: null, prev: null, timer: null,
  ema: 0, lastMotion: 0, state: "offline",
  zones: new Array(9).fill(0),        // Phase 3: per-sector motion EMA (3×3)
  zoneHeat: new Array(9).fill(0),     // cumulative activity analytics
  _lastZoneAlert: 0,

  start() {
    this.canvas.width = 64; this.canvas.height = 48;
    this.ctx = this.canvas.getContext("2d", { willReadFrequently: true });
    this.prev = null; this.ema = 0; this.lastMotion = Date.now();
    setChip("#v-cam", "v-chip", "CAM ONLINE", "ok");
    this.setState("idle");
    this.timer = setInterval(() => { try { this.sample(); } catch (e) {} }, 450);
  },

  stop() {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
    this.prev = null; this.ema = 0;
    setChip("#v-cam", "v-chip", "CAM OFFLINE", "dim");
    setChip("#chip-motion", "cam-chip", "MOTION 0", "dim");
    const mf = $("#motion-fill"); if (mf) mf.style.width = "0%";
    const mv = $("#motion-val"); if (mv) mv.textContent = "0";
    this.setState("offline");
  },

  sample() {
    if (!camStream || camVideo.readyState < 2) return;
    this.ctx.drawImage(camVideo, 0, 0, 64, 48);
    const d = this.ctx.getImageData(0, 0, 64, 48).data;
    const N = 64 * 48;
    const gray = new Uint8Array(N);
    let sum = 0;
    for (let i = 0; i < N; i++) {
      const j = i * 4;
      const g = (d[j] * 0.299 + d[j + 1] * 0.587 + d[j + 2] * 0.114) | 0;
      gray[i] = g; sum += g;
    }
    const mean = sum / N;
    let varSum = 0;
    for (let i = 0; i < N; i += 4) { const dv = gray[i] - mean; varSum += dv * dv; }
    const variance = varSum / (N / 4);

    let motion = 0;
    if (this.prev) {
      let diff = 0;
      const zdiff = new Array(9).fill(0);
      for (let i = 0; i < N; i += 2) {
        const d0 = Math.abs(gray[i] - this.prev[i]);
        diff += d0;
        // zone monitoring: map pixel → 3×3 sector (64×48 → 21px × 16px cells)
        const zx = Math.min(2, ((i % 64) / 21.34) | 0);
        const zy = Math.min(2, ((i / 64) / 16) | 0);
        zdiff[zy * 3 + zx] += d0;
      }
      motion = diff / (N / 2);
      const perZone = (N / 2) / 9;
      for (let z = 0; z < 9; z++) {
        const zm = zdiff[z] / perZone;
        this.zones[z] = this.zones[z] * 0.6 + zm * 0.4;
        this.zoneHeat[z] += zm > 3 ? zm : 0;   // activity analytics accumulator
      }
      this.renderZones();
      this.zoneWatch();
    }
    this.prev = gray;
    this.ema = this.ema * 0.6 + motion * 0.4;

    // live UI
    const mv = $("#motion-val");  if (mv) mv.textContent = this.ema.toFixed(1);
    const mf = $("#motion-fill"); if (mf) mf.style.width = Math.min(100, this.ema * 8) + "%";
    setChip("#chip-motion", "cam-chip", "MOTION " + Math.round(this.ema),
            this.ema > 3.5 ? "ok" : "dim");

    const now = Date.now();
    let next;
    // Hysteresis: enter ACTIVE above 3.5, only drop out below 2.0 — kills
    // boundary oscillation that would otherwise spam state changes.
    const activeThresh = this.state === "active" ? 2.0 : 3.5;
    if (variance < 60) next = "no-user";                 // covered lens / black frame
    else if (this.ema > activeThresh) { this.lastMotion = now; next = "active"; }
    else if (now - this.lastMotion < 45000) next = "idle";
    else next = "away";
    this.setState(next);
  },

  /* Phase 3 · zone monitoring — render the 3×3 sector heat + intrusion watch */
  ZONE_NAMES: ["TOP-LEFT", "TOP-CENTER", "TOP-RIGHT",
               "MID-LEFT", "CENTER", "MID-RIGHT",
               "LOW-LEFT", "LOW-CENTER", "LOW-RIGHT"],

  renderZones() {
    const grid = $("#zone-grid"); if (!grid) return;
    const cells = grid.children;
    for (let z = 0; z < 9 && z < cells.length; z++) {
      const v = this.zones[z];
      cells[z].className = v > 8 ? "z-hot" : v > 3.5 ? "z-warm" : v > 1.2 ? "z-low" : "";
    }
  },

  zoneWatch() {
    // while the operator is away, motion in any sector is a security signal:
    // raise a real alert naming the zone (rate-limited to one per 2 min)
    if (this.state !== "away" && this.state !== "no-user") return;
    let hot = -1, hotV = 0;
    for (let z = 0; z < 9; z++) if (this.zones[z] > hotV) { hotV = this.zones[z]; hot = z; }
    if (hotV < 7) return;
    const now = Date.now();
    if (now - this._lastZoneAlert < 120000) return;
    this._lastZoneAlert = now;
    const zone = this.ZONE_NAMES[hot] || "UNKNOWN";
    logEvent(`<span class="tag">[zone]</span> motion in ${zone} sector while operator away`, "warn");
    fetch("/api/alert", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({
        severity: "warning",
        title: `Zone monitor: motion in ${zone} sector`,
        detail: `Movement detected on ${camLabel()} while the operator is away (intensity ${hotV.toFixed(1)}).`,
        source: "vision · zone monitor",
        action: "Check the operator view feed.",
      }),
    }).catch(() => {});
  },

  setState(s) {
    if (s === this.state) return;
    const now = Date.now();
    // Min dwell of 2s between transitions (offline always allowed through)
    if (s !== "offline" && now - (this._lastChange || 0) < 2000) return;
    this._lastChange = now;
    this.state = s;
    const labels = {
      active: "USER DETECTED · ACTIVE", idle: "USER PRESENT · IDLE",
      away: "NO USER · AWAY", "no-user": "NO SIGNAL", offline: "CAM OFFLINE",
    };
    const cls = s === "active" ? "ok" : s === "idle" ? "warn" : "dim";
    setChip("#chip-presence", "cam-chip", labels[s] || s, cls);
    setChip("#v-presence", "v-chip", labels[s] || s, cls);
    const obs = {
      active: "operator in frame — session active",
      idle: "operator still — attention idle",
      away: "frame vacant — operator away",
      "no-user": "no usable signal from camera",
      offline: "camera feed offline — presence monitoring paused",
    }[s];
    const vo = $("#vision-obs"); if (vo) vo.textContent = obs;
    logEvent(`<span class="tag">[vision]</span> ${obs}`);
    fetch("/api/presence", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ state: s, motion: Math.round(this.ema * 10) / 10 }),
    }).catch(() => {});
  },
};

/* ═══ AI OPERATIONS CENTER ═══════════════════════════ */

const CONF_CIRC = 2 * Math.PI * 36;  // ≈ 226 (matches conf-arc r=36)

function setConfidence(pct) {
  const v = $("#conf-val");
  if (v) v.textContent = pct != null ? Math.round(pct) : "--";
  const arc = $("#conf-arc");
  if (arc && pct != null) {
    const p = Math.max(0, Math.min(100, pct)) / 100;
    arc.setAttribute("stroke-dasharray", CONF_CIRC.toFixed(1));  // keep dasharray in sync with JS circumference
    arc.setAttribute("stroke-dashoffset", String((CONF_CIRC * (1 - p)).toFixed(1)));
  }
}

function setReasonState(stateStr) {
  const card = $("#aiops-reasoning");
  const el = $("#reason-state");
  if (el) el.textContent = stateStr.toUpperCase();
  if (card) card.classList.toggle("analyzing", stateStr === "analyzing");
}

function setEngineInfo(engine) {
  if (!engine) return;
  const model = $("#reason-model");
  if (model && engine.model) model.textContent = String(engine.model).slice(0, 20);
  const sl = $("#st-llm");
  if (sl && engine.model) sl.textContent = String(engine.model).split(":")[0].slice(0, 9);
  const lat = $("#reason-latency");
  if (lat) lat.textContent = engine.latency_ms != null ? engine.latency_ms + " ms" : "—";
  const src = $("#insight-source");
  if (src && engine.source) src.textContent = engine.source.toUpperCase();
  if (engine.confidence != null) setConfidence(engine.confidence);
}

function addInsight(d, animate = true) {
  const feed = $("#insights-feed"); if (!feed) return;
  const empty = feed.querySelector(".insight-empty"); if (empty) empty.remove();
  const div = document.createElement("div");
  div.className = "insight-item " + (d.severity || "info");
  const t = new Date((d.ts || Date.now() / 1000) * 1000)
    .toTimeString().slice(0, 5);
  div.innerHTML = `
    <div class="insight-text"></div>
    <div class="insight-rec">${d.recommendation ? "→ " + escapeHTML(d.recommendation) : ""}</div>
    <div class="insight-meta">${t} · ${escapeHTML((d.source || "").toUpperCase())} · CONF ${d.confidence ?? "--"}%</div>`;
  feed.prepend(div);
  while (feed.children.length > 8) {
    const old = feed.lastChild;
    if (old && old._typeInterval) clearInterval(old._typeInterval);  // no orphaned typewriters
    feed.removeChild(old);
  }
  const textEl = div.querySelector(".insight-text");
  const full = d.insight || "";
  if (animate) {
    div.classList.add("typing");
    let i = 0;
    const iv = setInterval(() => {
      i += 2;
      textEl.textContent = full.slice(0, i);
      if (i >= full.length) { clearInterval(iv); div._typeInterval = null; div.classList.remove("typing"); }
    }, 24);
    div._typeInterval = iv;
  } else {
    textEl.textContent = full;
  }
}

async function seedAiops() {
  try {
    const r = await fetch("/api/insights"); const d = await r.json();
    (d.insights || []).slice().reverse().forEach(it => addInsight(it, false));
    setEngineInfo(d.engine);
  } catch (e) {}
}
seedAiops();
setInterval(async () => {
  try {
    const r = await fetch("/api/insights"); const d = await r.json();
    setEngineInfo(d.engine);
  } catch (e) {}
}, 60000);

/* — activity heatmap: 60 one-minute buckets of event volume — */
const hmBuckets = new Array(60).fill(0);
let hmDirty = true;
function bumpActivity() { hmBuckets[0]++; hmDirty = true; }
function renderHeatmap() {
  const host = $("#activity-heatmap"); if (!host) return;
  if (!host.children.length) {
    for (let i = 0; i < 60; i++) {
      const c = document.createElement("div");
      c.className = "hm-cell";
      host.appendChild(c);
    }
  }
  const cells = host.children;
  for (let i = 0; i < 60; i++) {
    const v = hmBuckets[59 - i];   // oldest left, newest right
    const cls = v === 0 ? "" : v === 1 ? "h1" : v < 4 ? "h2" : v < 7 ? "h3" : "h4";
    cells[i].className = "hm-cell" + (cls ? " " + cls : "");
  }
}
setInterval(() => { hmBuckets.pop(); hmBuckets.unshift(0); hmDirty = true; }, 60000);
setInterval(() => { if (hmDirty) { renderHeatmap(); hmDirty = false; } }, 1500);
renderHeatmap();

/* — readiness / anomaly from live metrics — */
function updateAiopsTele(msg) {
  const ready = Math.max(0, Math.min(100, Math.round(
    100 - Math.max(0, msg.cpu - 70) * 0.8
        - Math.max(0, msg.mem - 80) * 1.2
        - Math.max(0, msg.disk - 85) * 1.6)));
  const anom = Math.min(100, Math.round(
    Math.max(0, msg.cpu - 85) * 1.5
    + Math.max(0, msg.mem - 88) * 2.0
    + Math.max(0, msg.disk - 92) * 2.5));
  const rv = $("#readiness-val"); if (rv) rv.textContent = ready + "%";
  const rb = $("#readiness-bar"); if (rb) rb.style.width = ready + "%";
  const av = $("#anomaly-val");  if (av) av.textContent = anom;
  const ab = $("#anomaly-bar");  if (ab) ab.style.width = anom + "%";
  // orb flank: CORE INTEGRITY = real stability (100 - anomaly index)
  const fc = $("#fl-core"); if (fc) fc.textContent = (100 - anom).toFixed(1) + "%";
  // feed predictive analytics + mission readiness
  metricHist.cpu.push(msg.cpu); metricHist.mem.push(msg.mem);
  if (metricHist.cpu.length > 120) { metricHist.cpu.shift(); metricHist.mem.shift(); }
  try { updateMissionReadiness(ready, anom); } catch (e) {}
}

/* ═══ STRATEGIC INTEL · PREDICTIVE · READINESS ═══════ */

let agentsOnline = 0;          // set by renderAgents (snapshot)
let pendingActions = 0;        // set by refreshAgenda

const metricHist = { cpu: [], mem: [] };
let forecastChart = null;

function ensureForecastChart() {
  if (forecastChart || typeof Chart === "undefined") return;
  const el = document.getElementById("forecast-chart");
  if (!el) return;
  forecastChart = new Chart(el, {
    type: "line",
    data: { labels: [], datasets: [
      { data: [], borderColor: "#00d9ff", backgroundColor: "rgba(0,217,255,0.10)",
        borderWidth: 1.5, fill: true, tension: 0.3, pointRadius: 0 },
      { data: [], borderColor: "#ffb86c", borderDash: [4, 3],
        borderWidth: 1.4, fill: false, tension: 0.3, pointRadius: 0 },
    ]},
    options: { responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { min: 0, max: 100, display: false } } },
  });
}

function slopePerMin(arr) {
  // least-squares slope over ~2s samples → % per minute
  const n = arr.length;
  if (n < 8) return 0;
  let sx = 0, sy = 0, sxy = 0, sxx = 0;
  for (let i = 0; i < n; i++) { sx += i; sy += arr[i]; sxy += i * arr[i]; sxx += i * i; }
  const slope = (n * sxy - sx * sy) / (n * sxx - sx * sx || 1);
  return slope * 30;   // 30 samples per minute at 2s cadence
}
const trendGlyph = (d15) =>
  d15 > 2 ? "↗ +" + d15.toFixed(0) + "%" : d15 < -2 ? "↘ " + d15.toFixed(0) + "%" : "→ STABLE";

function updateForecast() {
  ensureForecastChart();
  if (!forecastChart) return;
  const hist = metricHist.cpu.slice(-45);
  if (hist.length < 8) return;
  const cpuPerMin = slopePerMin(hist);
  const memPerMin = slopePerMin(metricHist.mem.slice(-45));
  const cpu15 = cpuPerMin * 15, mem15 = memPerMin * 15;
  const last = hist[hist.length - 1];
  const proj = [];
  for (let i = 1; i <= 15; i++) proj.push(Math.max(0, Math.min(100, last + cpuPerMin * i)));
  forecastChart.data.labels = new Array(hist.length + 15).fill("");
  forecastChart.data.datasets[0].data = [...hist, ...new Array(15).fill(null)];
  forecastChart.data.datasets[1].data = [...new Array(hist.length - 1).fill(null), last, ...proj];
  forecastChart.update("none");

  const tc = $("#trend-cpu"); if (tc) tc.textContent = trendGlyph(cpu15);
  const tm = $("#trend-mem"); if (tm) tm.textContent = trendGlyph(mem15);
  const ft = $("#forecast-text");
  if (ft) {
    const memNow = metricHist.mem[metricHist.mem.length - 1] || 0;
    const cpuMsg = Math.abs(cpu15) < 2
      ? `CPU holding near ${last.toFixed(0)}%`
      : `CPU ${cpu15 > 0 ? "rising toward" : "easing toward"} ${Math.max(0, Math.min(100, last + cpu15)).toFixed(0)}%`;
    const memMsg = Math.abs(mem15) < 2
      ? `memory steady near ${memNow.toFixed(0)}%`
      : `memory ${mem15 > 0 ? "climbing to" : "falling to"} ~${Math.max(0, Math.min(100, memNow + mem15)).toFixed(0)}%`;
    ft.textContent = `Projection: ${cpuMsg}, ${memMsg} within 15 minutes.`;
  }
}
setInterval(updateForecast, 10000);

const MR_CIRC = 2 * Math.PI * 40;  // ≈ 251.3 (matches mr-arc r=40)
function updateMissionReadiness(ready, anomaly) {
  const val = $("#mr-val"); if (val) val.textContent = ready;
  const arc = $("#mr-arc");
  if (arc) {
    arc.setAttribute("stroke-dasharray", MR_CIRC.toFixed(1));
    arc.setAttribute("stroke-dashoffset", String((MR_CIRC * (1 - ready / 100)).toFixed(1)));
  }
  const g = document.querySelector(".mr-gauge");
  if (g) {
    g.classList.toggle("warn", ready < 75 && ready >= 50);
    g.classList.toggle("bad", ready < 50);
  }
  const sys = $("#mr-systems"); if (sys) sys.textContent = `${agentsOnline || 8} / 8 AGENTS`;
  const pen = $("#mr-pending"); if (pen) pen.textContent = String(pendingActions);
  const stab = $("#mr-stability"); if (stab) stab.textContent = `${Math.max(0, 100 - anomaly)}%`;
  const up = $("#mr-uptime"); const ops = $("#ops-timer");
  if (up && ops) up.textContent = ops.textContent;
}

function renderPriorityAlerts(d) {
  const host = $("#priority-alerts"); if (!host) return;
  const items = [];
  for (const e of (d.events || [])) {
    if (e.minutes_until != null && e.minutes_until > 0 && e.minutes_until <= 30) {
      items.push({ tag: "MEETING", critical: e.minutes_until <= 10,
                   text: `${e.title} · in ${Math.round(e.minutes_until)} min` });
    }
  }
  for (const m of (d.emails || [])) {
    if (m.priority === "priority")
      items.push({ tag: "MAIL", critical: false,
                   text: `${(m.sender || "").split("@")[0]} — ${m.subject}` });
  }
  host.innerHTML = items.length
    ? items.slice(0, 4).map(a =>
        `<div class="alert-item${a.critical ? " critical" : ""}">` +
        `<span class="al-tag">${a.tag}</span>` +
        `<span class="al-text">${escapeHTML(a.text)}</span></div>`).join("")
    : '<div class="alert-empty">no priority alerts</div>';
}

/* ═══ THREAT INTELLIGENCE BOARD ══════════════════════ */

function addThreatEvent(ev, prepend = true) {
  const feed = $("#threat-feed"); if (!feed) return;
  const empty = feed.querySelector(".tf-empty"); if (empty) empty.remove();
  const div = document.createElement("div");
  div.className = "tf-item " + (ev.severity || "low");
  const t = new Date((ev.ts || Date.now() / 1000) * 1000).toTimeString().slice(0, 5);
  div.innerHTML =
    `<span class="tf-cat">${escapeHTML((ev.category || "sys").toUpperCase().slice(0, 4))}</span>` +
    `<span class="tf-title">${escapeHTML(ev.title || "")}</span>` +
    `<span class="tf-time">${t}</span>`;
  if (prepend) feed.prepend(div); else feed.appendChild(div);
  while (feed.children.length > 5) feed.removeChild(feed.lastChild);
}

function updateRisk(score, level) {
  const rs = $("#risk-score"); if (rs) rs.textContent = score;
  const tileVal = $("#st-threat");
  if (tileVal) {
    tileVal.textContent = score;
    const tile = tileVal.closest(".scan-tile");
    if (tile) {
      tile.classList.toggle("hot", level === "high");
      tile.classList.toggle("warm", level === "elevated");
    }
  }
  const st = $("#threat-state");
  const sh = $("#threat-shield");
  const label = level === "secure" ? "SECURE" : level === "elevated" ? "ELEVATED" : "HIGH RISK";
  if (st) { st.textContent = label; st.className = "threat-state " + level; }
  if (sh) { sh.className.baseVal = "threat-shield " + level; }
  // orb flank: SHIELD state mirrors the live risk level
  const fs = $("#fl-shield");
  if (fs) fs.textContent = level === "secure" ? "ACTIVE" : level === "elevated" ? "ELEVATED" : "BREACH";
}

async function seedThreats() {
  try {
    const r = await fetch("/api/threats"); const d = await r.json();
    const feed = $("#threat-feed");
    if (feed && !feed.querySelector(".tf-item")) {
      (d.events || []).slice(0, 5).reverse().forEach(ev => addThreatEvent(ev));
    }
    updateRisk(d.risk_score ?? 5, d.level || "secure");
    renderThreatCenter(d);
    try { lastThreat = d; updateLiveOps(); } catch (e2) {}
  } catch (e) {}
}
seedThreats();
setInterval(seedThreats, 45000);   // reconcile matrix/SOC/timeline; live events via WS

/* ═══ THREAT INTELLIGENCE CENTER (matrix · SOC · timeline) ═══ */

let threatRadar = null;
const MX_LABELS = ["OPS", "INFRA", "NET", "AI", "RES", "SEC"];
const MX_KEYS = ["operational", "infrastructure", "network", "ai", "resource", "security"];

function ensureRadar() {
  if (threatRadar || typeof Chart === "undefined") return;
  const el = document.getElementById("threat-radar"); if (!el) return;
  threatRadar = new Chart(el, {
    type: "radar",
    data: { labels: MX_LABELS, datasets: [{
      data: [0, 0, 0, 0, 0, 0],
      backgroundColor: "rgba(0,217,255,0.15)",
      borderColor: "#00d9ff", borderWidth: 1.5,
      pointRadius: 2.5, pointBackgroundColor: "#00d9ff",
    }]},
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: { duration: 700 },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { r: {
        min: 0, max: 100,
        ticks: { display: false },
        grid: { color: "rgba(0,217,255,0.14)" },
        angleLines: { color: "rgba(0,217,255,0.1)" },
        pointLabels: { color: "#7e96b3", font: { family: "Orbitron", size: 8 } },
      }},
    },
  });
}

function renderThreatCenter(d) {
  // matrix
  ensureRadar();
  const mx = d.matrix || {};
  if (threatRadar) {
    threatRadar.data.datasets[0].data = MX_KEYS.map(k => (mx[k] || {}).score || 0);
    threatRadar.update();
  }
  const rows = $("#matrix-rows");
  if (rows) {
    rows.innerHTML = MX_KEYS.map((k, i) => {
      const m = mx[k] || { score: 0, trend: "flat", confidence: 90 };
      const heat = m.score >= 60 ? "hot" : m.score >= 35 ? "warm" : "";
      const arrow = m.trend === "up" ? "▲" : m.trend === "down" ? "▼" : "—";
      return `<div class="mx-row ${heat}">
        <span class="mx-name">${MX_LABELS[i]} · ${k.toUpperCase().slice(0, 9)}</span>
        <span class="mx-score">${m.score}</span>
        <span class="mx-trend ${m.trend}">${arrow}</span>
        <span class="mx-conf">${m.confidence}%</span></div>`;
    }).join("");
  }
  // SOC
  const soc = d.soc || {};
  const set = (id, v) => { const el = $(id); if (el) el.textContent = v ?? 0; };
  set("#soc-analyzed", soc.analyzed); set("#soc-alerts", soc.alerts);
  set("#soc-resolved", soc.resolved); set("#soc-invest", soc.investigations);
  // incident response timeline
  const tl = $("#ir-timeline");
  if (tl) {
    const evs = (d.events || []).slice(0, 6);
    tl.innerHTML = evs.length ? evs.map(ev => {
      const t = new Date(ev.ts * 1000).toTimeString().slice(0, 5);
      return `<div class="ir-item">
        <span class="ir-time">${t}</span>
        <span class="ir-main">
          <div class="ir-title">${escapeHTML(ev.title)}</div>
          <div class="ir-meta">${escapeHTML(ev.severity.toUpperCase())} · ASSIGNED <b>${escapeHTML((ev.agent || "jarvis").toUpperCase())}</b></div>
        </span>
        <span class="ir-stage ${ev.stage || "detected"}">${(ev.stage || "detected").toUpperCase()}</span>
      </div>`;
    }).join("") : '<div class="tf-empty">no incidents in the response pipeline</div>';
  }
  renderThreatAnalytics(d);
}

/* ═══ THREAT 2.0 — trend graph · alert distribution · response queue ═══ */

function renderThreatAnalytics(d) {
  // risk trend sparkline (raw canvas — no chart lib needed at this size)
  const cv = document.getElementById("risk-trend");
  if (cv) {
    const hist = d.history || [];
    const ctx = cv.getContext("2d");
    const W = cv.width = cv.clientWidth || 260, H = cv.height = 46;
    ctx.clearRect(0, 0, W, H);
    if (hist.length >= 2) {
      const scores = hist.map(h => h.score);
      const max = Math.max(25, ...scores);
      ctx.beginPath();
      hist.forEach((h, i) => {
        const x = (i / (hist.length - 1)) * (W - 4) + 2;
        const y = H - 4 - (h.score / max) * (H - 10);
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      const last = scores[scores.length - 1];
      ctx.strokeStyle = last >= 50 ? "#ff3c3c" : last >= 20 ? "#facc15" : "#00d9ff";
      ctx.lineWidth = 1.6; ctx.stroke();
      // soft fill under the line
      ctx.lineTo(W - 2, H - 2); ctx.lineTo(2, H - 2); ctx.closePath();
      ctx.fillStyle = last >= 50 ? "rgba(255,60,60,0.12)"
                    : last >= 20 ? "rgba(250,204,21,0.10)" : "rgba(0,217,255,0.10)";
      ctx.fill();
    } else {
      ctx.fillStyle = "rgba(126,150,179,0.6)";
      ctx.font = "9px monospace";
      ctx.fillText("collecting risk samples…", 6, H / 2 + 3);
    }
  }
  // alert distribution by severity
  const dist = $("#ta-dist");
  if (dist) {
    const by = d.by_severity || {};
    const total = Math.max(1, Object.values(by).reduce((a, b) => a + b, 0));
    dist.innerHTML = ["critical", "high", "medium", "low"].map(sev => {
      const n = by[sev] || 0;
      return `<div class="ta-d-row">
        <span class="ta-d-name">${sev.toUpperCase()}</span>
        <div class="ta-d-bar"><div class="ta-d-fill ${sev}" style="width:${Math.round(n * 100 / total)}%"></div></div>
        <span class="ta-d-n">${n}</span></div>`;
    }).join("");
  }
  // response queue — incidents awaiting operator acknowledgement
  const q = $("#ta-queue"); const qn = $("#ta-queue-n");
  if (q) {
    const items = d.response_queue || [];
    if (qn) qn.textContent = items.length;
    q.innerHTML = items.length ? items.map(ev => `
      <div class="ta-q-item ${ev.severity}">
        <span class="ta-q-title" title="${escapeHTML(ev.detail || "")}">${escapeHTML(ev.title)}</span>
        <button class="ta-q-ack" data-id="${escapeHTML(ev.id)}">ACK</button>
      </div>`).join("")
      : '<div class="tf-empty">queue clear — nothing awaiting acknowledgement</div>';
    q.querySelectorAll(".ta-q-ack").forEach(btn => btn.addEventListener("click", async () => {
      btn.disabled = true; btn.textContent = "…";
      try { await fetch("/api/threats/ack/" + btn.dataset.id, { method: "POST" }); } catch (e) {}
      seedThreats();
    }));
  }
}

/* ═══ INTELLIGENCE TICKER ════════════════════════════ */

const tickerItems = [];
function pushTicker(text, cls = "") {
  tickerItems.unshift(cls ? `<span class="${cls}">${escapeHTML(text)}</span>` : escapeHTML(text));
  if (tickerItems.length > 8) tickerItems.pop();
  const el = $("#ticker-content");
  if (el) el.innerHTML = tickerItems.join("&nbsp;&nbsp;<b>···</b>&nbsp;&nbsp;");
}

/* ═══ EMERGENCY ALERT SYSTEM ═════════════════════════ */

const AlertSystem = {
  ctx: null, unlocked: false, siren: null,
  active: { warning: 0, critical: 0, emergency: 0 },

  init() {
    const unlock = () => {
      try {
        this.ctx = this.ctx || new (window.AudioContext || window.webkitAudioContext)();
        this.ctx.resume(); this.unlocked = true;
      } catch (e) {}
    };
    document.addEventListener("pointerdown", unlock, { once: true });
    document.addEventListener("keydown", unlock, { once: true });
  },

  beep(freq, dur, delay = 0, type = "sine", vol = 0.07) {
    if (!this.unlocked || !this.ctx || window.dashAudioMuted) return;
    try {
      const t = this.ctx.currentTime + delay;
      const o = this.ctx.createOscillator(), g = this.ctx.createGain();
      o.type = type; o.frequency.value = freq;
      g.gain.setValueAtTime(vol, t);
      g.gain.exponentialRampToValueAtTime(0.001, t + dur);
      o.connect(g); g.connect(this.ctx.destination);
      o.start(t); o.stop(t + dur);
    } catch (e) {}
  },

  sound(sev) {
    if (sev === "info") this.beep(880, 0.12);
    else if (sev === "warning") { this.beep(620, 0.14); this.beep(480, 0.16, 0.16); }
    else if (sev === "critical") { for (let i = 0; i < 3; i++) this.beep(900, 0.11, i * 0.18, "square", 0.06); }
    else if (sev === "emergency") this.startSiren();
  },

  startSiren() {
    if (!this.unlocked || !this.ctx || this.siren || window.dashAudioMuted) return;
    try {
      const o = this.ctx.createOscillator(), g = this.ctx.createGain(),
            lfo = this.ctx.createOscillator(), lg = this.ctx.createGain();
      o.type = "sawtooth"; o.frequency.value = 800;
      lfo.frequency.value = 0.9; lg.gain.value = 320;
      lfo.connect(lg); lg.connect(o.frequency);
      g.gain.value = 0.05;
      o.connect(g); g.connect(this.ctx.destination);
      o.start(); lfo.start();
      this.siren = { o, lfo, g };
    } catch (e) {}
  },
  stopSiren() {
    if (!this.siren) return;
    try { this.siren.o.stop(); this.siren.lfo.stop(); } catch (e) {}
    this.siren = null;
  },

  vibrate(sev) {
    if (!navigator.vibrate) return;
    try {
      if (sev === "warning") navigator.vibrate(120);
      else if (sev === "critical") navigator.vibrate([140, 90, 140]);
      else if (sev === "emergency") navigator.vibrate([220, 100, 220, 100, 220, 100, 220]);
    } catch (e) {}
  },

  release(sev) {
    if (this.active[sev] != null) this.active[sev] = Math.max(0, this.active[sev] - 1);
  },

  raise(a) {
    // Emergency renders as a persistent critical card — no full-screen
    // takeover, no edge glow, no siren. Corner cards only.
    let sev = ["info", "warning", "critical", "emergency"].includes(a.severity) ? a.severity : "info";
    if (sev === "emergency") sev = "critical";
    this.sound(sev); this.vibrate(sev);
    pushTicker(`[${sev.toUpperCase()}] ${a.title}`, sev === "critical" ? "tk-crit" : sev === "warning" ? "tk-warn" : "");
    if (this.active[sev] != null) this.active[sev]++;

    const stack = $("#alert-stack"); if (!stack) return;
    const card = document.createElement("div");
    card.className = "alert-card " + sev;
    const t = new Date().toTimeString().slice(0, 5);
    card.innerHTML = `
      <div class="ac-head"><span class="ac-sev">◢ ${sev.toUpperCase()}</span><span class="ac-time">${t}</span></div>
      <div class="ac-title">${escapeHTML(a.title || "")}</div>
      ${a.detail ? `<div class="ac-detail">${escapeHTML(a.detail)}</div>` : ""}
      ${a.action ? `<div class="ac-action">→ ${escapeHTML(a.action)}</div>` : ""}
      <div class="ac-src">SOURCE · ${escapeHTML((a.source || "system").toUpperCase())}</div>
      <div class="ac-btns">
        <button class="ac-btn" data-act="view">VIEW DETAILS</button>
        <button class="ac-btn" data-act="dismiss">DISMISS</button>
        <button class="ac-btn ack" data-act="ack">ACKNOWLEDGE</button>
      </div>`;
    const close = (acked) => {
      if (card._closed) return; card._closed = true;
      this.release(sev);
      if (sev === "critical") {
        this.criticalActive = Math.max(0, this.criticalActive - 1);
        this.stopLoopIfClear();
        if (acked) logEvent('<span class="tag">[alert]</span> critical acknowledged');
      }
      card.classList.add("out");
      setTimeout(() => card.remove(), 380);
    };
    card._close = close;
    card.querySelector('[data-act="ack"]').addEventListener("click", () => close(true));
    card.querySelector('[data-act="dismiss"]').addEventListener("click", () => close(false));
    card.querySelector('[data-act="view"]').addEventListener("click", () => { execDashCmd("threats"); });
    stack.prepend(card);
    while (stack.children.length > 3) {
      const old = stack.lastChild;
      if (old._close) old._close(false); else old.remove();
      if (stack.lastChild === old) break;   // safety
    }
    if (sev === "info") setTimeout(() => close(false), 6000);
    else if (sev === "warning") setTimeout(() => close(false), 12000);
    else { this.criticalActive++; this.startLoop(); }   // critical: persists + loops until user acts
  },

  criticalActive: 0, loopTimer: null,
  startLoop() {
    if (this.loopTimer) return;
    this.loopTimer = setInterval(() => {
      if (this.criticalActive > 0) {
        this.beep(900, 0.1, 0, "square", 0.05);
        this.beep(900, 0.1, 0.16, "square", 0.05);
      }
    }, 2200);
  },
  stopLoopIfClear() {
    if (this.criticalActive <= 0 && this.loopTimer) {
      clearInterval(this.loopTimer); this.loopTimer = null;
    }
  },
};
AlertSystem.init();

/* ═══ DASHBOARD VOICE CONTROL ════════════════════════ */

const DASH_TARGETS = {
  threats: ".bs-threat", map: ".map-panel", agenda: "#agenda-list",
  insights: "#aiops-insights", news: "#news-list", camera: "#operator-cam",
  roster: "#avengers-panel", readiness: "#intel-readiness",
};
function execDashCmd(target) {
  const sel = DASH_TARGETS[target]; if (!sel) return;
  const el = document.querySelector(sel); if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.add("flash-target");
  setTimeout(() => el.classList.remove("flash-target"), 2200);
  logEvent(`<span class="tag">[voice]</span> dashboard control → ${target}`);
}

/* ═══ CALENDAR INTELLIGENCE ══════════════════════════ */

let calIntelLive = null;   // intel stash for the 1-second countdown ticker

function renderCalIntel(d) {
  const el = $("#cal-intel"); if (!el) return;
  const intel = d.intel || {};
  calIntelLive = intel;
  if (!intel.meetings_today && intel.meetings_today !== 0) {
    el.textContent = "analyzing schedule…"; return;
  }
  const bits = [];
  bits.push(`${intel.meetings_today} mtgs today`);
  if (intel.density) bits.push(`${intel.density} load`);
  if (intel.largest_free_block_min != null) bits.push(`focus block ${intel.largest_free_block_min}m`);
  if (intel.conflicts) bits.push(`⚠ ${intel.conflicts} conflict${intel.conflicts > 1 ? "s" : ""}`);
  if (intel.readiness != null) bits.push(`readiness ${intel.readiness}%`);
  el.innerHTML = bits.join(" · ") +
    `<span class="cal-src${d.source === "google" ? " ok" : ""}">${(d.source || "mock").toUpperCase()}</span>`;
  el.classList.toggle("warn", !!intel.conflicts);

  // current meeting banner (live)
  const nowEl = $("#cal-now");
  if (nowEl) {
    if (intel.current_title) {
      nowEl.hidden = false;
      $("#cal-now-txt").textContent =
        `IN SESSION: ${intel.current_title} — ends in ${intel.current_ends_min} min`;
    } else nowEl.hidden = true;
  }
  // preparation suggestions (real rule-based, from the backend)
  const prep = $("#cal-prep");
  if (prep) {
    const items = intel.prep || [];
    prep.hidden = !items.length;
    prep.innerHTML = items.map(p => `<div>${escapeHTML(p)}</div>`).join("");
  }
  tickCalCountdown();
}

// 1-second countdown to the next meeting (uses next_start_ts from the intel)
function tickCalCountdown() {
  const cd = $("#cal-countdown"); if (!cd) return;
  const ts = calIntelLive && calIntelLive.next_start_ts;
  if (!ts || !calIntelLive.next_title) { cd.hidden = true; return; }
  const secs = Math.floor(ts - Date.now() / 1000);
  if (secs <= 0 || secs > 6 * 3600) { cd.hidden = true; return; }
  cd.hidden = false;
  const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = secs % 60;
  const t = (h ? `${h}h ` : "") + `${String(m).padStart(2, "0")}m ${String(s).padStart(2, "0")}s`;
  cd.textContent = `T-MINUS ${t} → ${calIntelLive.next_title}`;
  cd.classList.toggle("soon", secs < 600);
}
setInterval(tickCalCountdown, 1000);

// AI schedule summary (only when Google is connected; cached server-side)
async function refreshCalSummary() {
  const el = $("#cal-summary"); if (!el) return;
  try {
    const d = await (await fetch("/api/agenda/summary")).json();
    if (d.summary) { el.hidden = false; el.textContent = "❝ " + d.summary + " ❞"; }
    else el.hidden = true;
  } catch (e) {}
}
setTimeout(refreshCalSummary, 12000); setInterval(refreshCalSummary, 600000);

$("#gcal-btn")?.addEventListener("click", async () => {
  logEvent('<span class="tag">[calendar]</span> starting Google authorization — check your browser…');
  try {
    const r = await fetch("/api/calendar/connect", { method: "POST" });
    const d = await r.json();
    if (d.ok) {
      logEvent(`<span class="tag">[calendar]</span> connected ✓ — ${d.synced ?? 0} events synced`);
      refreshAgenda();
    } else {
      logEvent(`<span class="tag">[calendar]</span> ${escapeHTML(d.error || "connect failed")} — see google_sync.py setup steps`, "warn");
    }
  } catch (e) {
    logEvent("calendar connect failed: " + escapeHTML(e.message), "error");
  }
});

/* ═══ SESSION TRACKING (vision) ══════════════════════ */

let sessionStart = null;
function updateSessionVal() {
  const el = $("#session-val"); if (!el) return;
  const present = Presence.state === "active" || Presence.state === "idle";
  if (present && !sessionStart) sessionStart = Date.now();
  if (!present) sessionStart = null;
  if (sessionStart) {                       // live camera session
    const m = Math.floor((Date.now() - sessionStart) / 60000);
    el.textContent = m >= 60 ? `LIVE ${Math.floor(m / 60)}H ${m % 60}M` : `LIVE ${m}M`;
    return;
  }
  // camera off → show today's REAL tracked presence from operator memory
  const mins = (lastMemorySnap && lastMemorySnap.active_minutes_today) || 0;
  el.textContent = mins ? `TODAY ${Math.floor(mins / 60)}H ${mins % 60}M` : "STANDBY";
}
setInterval(updateSessionVal, 5000); updateSessionVal();

/* ═══ PROCESS MONITOR + NETWORK TRAFFIC ══════════════ */

let trafficChart = null;
function ensureTraffic() {
  if (trafficChart || typeof Chart === "undefined") return;
  const el = document.getElementById("traffic-chart"); if (!el) return;
  trafficChart = new Chart(el, {
    type: "line",
    data: { labels: [], datasets: [
      { data: [], borderColor: "#00d9ff", backgroundColor: "rgba(0,217,255,0.12)",
        borderWidth: 1.4, fill: true, tension: 0.3, pointRadius: 0 },
      { data: [], borderColor: "#ff7a00", backgroundColor: "rgba(255,122,0,0.10)",
        borderWidth: 1.4, fill: true, tension: 0.3, pointRadius: 0 },
    ]},
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { display: false },
        y: { min: 0, ticks: { color: "#6b94b4", font: { size: 8, family: "Share Tech Mono" }, maxTicksLimit: 4 },
             grid: { color: "rgba(13,58,90,0.3)" } },
      },
    },
  });
}
function pushTraffic(up, down) {
  ensureTraffic(); if (!trafficChart) return;
  const d = trafficChart.data;
  d.labels.push(""); d.datasets[0].data.push(up); d.datasets[1].data.push(down);
  if (d.labels.length > 90) { d.labels.shift(); d.datasets[0].data.shift(); d.datasets[1].data.shift(); }
  trafficChart.update("none");
}

async function refreshProcs() {
  try {
    const r = await fetch("/api/processes"); const d = await r.json();
    const host = $("#proc-list"); if (!host) return;
    const rows = (d.top_cpu || []).slice(0, 5);
    if (!rows.length) return;
    const maxc = Math.max(...rows.map(p => p.cpu), 1);
    const sp = $("#st-proc");
    if (sp && rows[0]) sp.textContent = `${Math.round(rows[0].cpu)}%`;
    // real GPU utilization (nvidia-smi); N/A on machines without one
    const gv = $("#res-gpu"); const gb = document.querySelector(".res-fill.gpu");
    if (gv) {
      if (d.gpu == null) { gv.textContent = "N/A"; if (gb) gb.style.width = "0%"; }
      else { gv.textContent = `${Math.round(d.gpu)}%`; if (gb) gb.style.width = `${Math.min(100, d.gpu)}%`; }
    }
    host.innerHTML = rows.map(p => `
      <div class="proc-row">
        <span class="proc-name">${escapeHTML(p.name)}${p.count > 1 ? " ×" + p.count : ""}</span>
        <span class="proc-cpu">${p.cpu.toFixed(0)}%</span>
        <span class="proc-mem">${p.mem_mb >= 1024 ? (p.mem_mb / 1024).toFixed(1) + " GB" : p.mem_mb.toFixed(0) + " MB"}</span>
        <span class="proc-bar"><i style="width:${Math.min(100, p.cpu / maxc * 100)}%"></i></span>
      </div>`).join("");
  } catch (e) {}
}
refreshProcs();
setInterval(refreshProcs, 6000);

/* ═══ INTERACTIVE LAYER — modals, scanners, model manager ═══ */

let lastMetrics = {};

function makeInteractive(el, fn, label) {
  if (!el) return;
  el.setAttribute("tabindex", "0");
  el.setAttribute("role", "button");
  if (label) el.title = label;
  el.addEventListener("click", fn);
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(); }
  });
}

function openCustomModal(title, sub, iconSvg, bodyHTML) {
  const modal = $("#tool-modal"); if (!modal) return;
  $("#modal-title").textContent = title;
  $("#modal-sub").textContent = sub;
  $("#modal-icon").innerHTML = iconSvg ||
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg>';
  $("#modal-body").innerHTML = bodyHTML;
  modal.hidden = false;
}

async function openProcModal(kind) {
  openCustomModal(
    kind === "cpu" ? "PROCESS MONITOR" : "MEMORY SCANNER",
    kind === "cpu" ? "TOP CPU CONSUMERS · LIVE" : "TOP MEMORY CONSUMERS · LIVE",
    null, '<div style="color:var(--text-dim);font-style:italic">scanning…</div>');
  try {
    const d = await (await fetch("/api/processes")).json();
    const rows = (kind === "cpu" ? d.top_cpu : d.top_mem) || [];
    const max = Math.max(...rows.map(p => kind === "cpu" ? p.cpu : p.mem_mb), 1);
    $("#modal-body").innerHTML = '<div class="mb-list">' + rows.map(p => {
      const v = kind === "cpu" ? p.cpu : p.mem_mb;
      const vs = kind === "cpu" ? p.cpu.toFixed(1) + "%"
        : (p.mem_mb >= 1024 ? (p.mem_mb / 1024).toFixed(2) + " GB" : p.mem_mb.toFixed(0) + " MB");
      return `<div class="mb-row"><strong>${escapeHTML(p.name)}${p.count > 1 ? " ×" + p.count : ""}</strong> — ${vs}
        <div class="proc-bar" style="margin-top:4px"><i style="width:${Math.min(100, v / max * 100)}%"></i></div></div>`;
    }).join("") + "</div>";
  } catch (e) {
    $("#modal-body").innerHTML = `<div style="color:var(--error)">scan failed: ${escapeHTML(e.message)}</div>`;
  }
}

function openDiskModal() {
  const dsk = lastMetrics.disk ?? 0;
  openCustomModal("DISK SCANNER", "STORAGE · C:\\ VOLUME", null, `
    <div class="mb-grid">
      <div class="mb-stat"><span class="lbl">USED</span><span class="val ${dsk > 90 ? "warn" : ""}">${dsk.toFixed ? dsk.toFixed(1) : dsk}%</span></div>
      <div class="mb-stat"><span class="lbl">STATUS</span><span class="val ${dsk > 92 ? "warn" : "ok"}">${dsk > 92 ? "CRITICAL" : dsk > 85 ? "FILLING" : "HEALTHY"}</span></div>
    </div>
    <div class="mb-list">
      <div class="mb-row">${dsk > 90
        ? "Storage critically full. Run Windows Disk Cleanup, empty Downloads, or archive old projects — update installs may fail below 5% free."
        : "Storage within operating tolerance. No action required."}
        <div class="mb-row-meta">LIVE · ${new Date().toTimeString().slice(0, 8)}</div></div>
    </div>`);
}

function openVoiceEngineModal() {
  const eng = $("#vr-engine")?.textContent || "—";
  const mic = $("#vr-mic")?.textContent || "—";
  const tts = $("#sp-tts .sp-val")?.textContent || "—";
  openCustomModal("VOICE ENGINE", "STT · WAKE PHRASES · TTS", null, `
    <div class="mb-grid">
      <div class="mb-stat"><span class="lbl">RECOGNITION</span><span class="val ok">${escapeHTML(eng)}</span></div>
      <div class="mb-stat"><span class="lbl">MICROPHONES</span><span class="val">${escapeHTML(mic)}</span></div>
      <div class="mb-stat"><span class="lbl">TTS OUTPUT</span><span class="val ok">${escapeHTML(tts)}</span></div>
      <div class="mb-stat"><span class="lbl">STT MODEL</span><span class="val">WHISPER SMALL</span></div>
    </div>
    <div class="mb-list">
      <div class="mb-row">Wake phrases: Hey Jarvis · Hey Stark/Tony · Hey Cap/Captain · Hey Widow/Natasha · Hey Hawkeye/Clint · Hey Hulk/Bruce · Hey Thor · Hey Vision
        <div class="mb-row-meta">FUZZY MATCH · INDIAN-ENGLISH BIASED</div></div>
      <div class="mb-row"><button class="quick-btn" id="tts-test-btn" style="width:auto;padding:.45rem .9rem">RUN SPEAKER TEST</button></div>
    </div>`);
  $("#tts-test-btn")?.addEventListener("click", () => {
    fetch("/api/speak", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: "Voice engine test. All channels operational, sir." }) }).catch(() => {});
  });
}

async function openAgentModal(name) {
  openCustomModal((name || "").toUpperCase(), "AGENT OPERATIONS FILE", null,
    '<div style="color:var(--text-dim);font-style:italic">pulling agent telemetry…</div>');
  try {
    const d = await (await fetch("/api/status")).json();
    const a = (d.agents || []).find(x => x.name === name);
    if (!a) throw new Error("agent not found");
    $("#modal-title").textContent = a.codename;
    $("#modal-sub").textContent = a.role.toUpperCase();
    const hist = (a.history || []).slice(-4).reverse().map(h =>
      `<div class="mb-row">${escapeHTML((h.a || "").slice(0, 160))}
        <div class="mb-row-meta">${new Date(h.ts * 1000).toTimeString().slice(0, 5)} · RE: ${escapeHTML((h.q || "").slice(0, 60))}</div></div>`).join("");
    $("#modal-body").innerHTML = `
      <div class="mb-grid">
        <div class="mb-stat"><span class="lbl">STATUS</span><span class="val ${a.status === "idle" ? "ok" : "warn"}">${escapeHTML(a.status.toUpperCase())}</span></div>
        <div class="mb-stat"><span class="lbl">CONFIDENCE</span><span class="val">${a.confidence}%</span></div>
        <div class="mb-stat"><span class="lbl">CURRENT TASK</span><span class="val">${escapeHTML((a.current_task || "—").slice(0, 22))}</span></div>
        <div class="mb-stat"><span class="lbl">ACTIONS DONE</span><span class="val">${a.actions_completed}</span></div>
      </div>
      <div class="mb-list">${hist || '<div class="mb-row">no recent actions logged.</div>'}</div>`;
  } catch (e) {
    $("#modal-body").innerHTML = `<div style="color:var(--error)">${escapeHTML(e.message)}</div>`;
  }
}

/* — Ollama model manager — */
async function openModelManager() {
  openCustomModal("AI MODEL MANAGER", "CLOUD + LOCAL OLLAMA · CLICK TO LOAD",
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2l8 4.5v9L12 20l-8-4.5v-9z"/><circle cx="12" cy="11" r="3" fill="currentColor"/></svg>',
    '<div style="color:var(--text-dim);font-style:italic">querying endpoints…</div>');
  try {
    const d = await (await fetch("/api/models")).json();
    renderModelManager(d);
  } catch (e) {
    $("#modal-body").innerHTML = `<div style="color:var(--error)">model query failed: ${escapeHTML(e.message)}</div>`;
  }
}
function renderModelManager(d) {
  const body = $("#modal-body"); if (!body) return;
  const claudeActive = !d.force_local && d.cloud_claude;
  let html = `<div class="mb-row" style="margin-bottom:.6rem">ACTIVE BRAIN: <b style="color:var(--success)">${escapeHTML((d.active_brain || "—").toUpperCase())}</b> — click any model to switch. Selection persists across restarts.</div>`;
  html += `<div class="model-section">— CLOUD</div>
    <div class="model-row${claudeActive ? " selected" : ""}" data-model="claude" tabindex="0" role="button">
      <span class="mr-name">claude (CLI)</span>
      <span class="mr-spec">ANTHROPIC · CLOUD BRAIN</span>
      <span class="mr-status ${claudeActive ? "loaded" : (d.cloud_claude ? "available" : "offline")}">${claudeActive ? "ACTIVE" : (d.cloud_claude ? "AVAILABLE" : "OFFLINE")}</span>
    </div>
    <div class="model-section">— LOCAL OLLAMA · ${escapeHTML(d.endpoint || "")}</div>`;
  const locals = d.local || [];
  if (!locals.length) {
    html += `<div class="mb-row">no local models detected — install one with <b style="color:var(--accent)">ollama pull llama3.2</b> and reopen.
      <div class="mb-row-meta">ENDPOINT OFFLINE — run \`ollama serve\`</div></div>`;
  }
  for (const m of locals) {
    const spec = [m.param_size, m.quant, m.size_gb ? m.size_gb + " GB" : null]
      .filter(Boolean).join(" · ") || (m.family || "local model");
    const isActive = d.force_local && m.name === d.selected;
    const status = isActive ? "loaded" : "available";
    html += `<div class="model-row${isActive ? " selected" : ""}" data-model="${escapeHTML(m.name)}" tabindex="0" role="button">
      <span class="mr-name">${escapeHTML(m.name)}</span>
      <span class="mr-spec">${escapeHTML(spec.toUpperCase())}</span>
      <span class="mr-status ${status}">${isActive ? "ACTIVE" : "AVAILABLE"}</span></div>`;
  }
  html += `<div class="mb-row" style="margin-top:.5rem">Local models keep everything on-device. On a machine without a GPU they answer slower than Claude — pick based on your priority (privacy vs speed).</div>`;
  body.innerHTML = html;
  body.querySelectorAll(".model-row[data-model]").forEach(r => {
    const pick = async () => {
      const name = r.dataset.model;
      r.querySelector(".mr-status").textContent = "SWITCHING…";
      try {
        await fetch("/api/models/select", { method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ model: name }) });
        const d2 = await (await fetch("/api/models")).json();
        renderModelManager(d2);
        const rm = $("#reason-model");
        if (rm) rm.textContent = (d2.active_brain || "").toUpperCase().slice(0, 20);
        const sl = $("#st-llm");
        if (sl) sl.textContent = (d2.force_local ? String(d2.selected) : "claude").split(":")[0].slice(0, 9);
      } catch (e) {}
    };
    r.addEventListener("click", pick);
    r.addEventListener("keydown", (e) => { if (e.key === "Enter") pick(); });
  });
}
makeInteractive($("#model-row"), openModelManager, "Click to manage local Ollama models");

/* — scanner tiles — */
function updateScanTiles(m) {
  const set = (id, v, level) => {
    const el = $(id); if (!el) return;
    el.textContent = v;
    const tile = el.closest(".scan-tile");
    if (tile) { tile.classList.toggle("hot", level === "hot"); tile.classList.toggle("warm", level === "warm"); }
  };
  set("#st-net", `${Math.round(m.net_down)}↓`, m.net_down > 2000 ? "warm" : "");
  set("#st-pkt", `${Math.round(m.net_up + m.net_down)}/s`, "");
  set("#st-mem", `${Math.round(m.mem)}%`, m.mem > 92 ? "hot" : m.mem > 85 ? "warm" : "");
}
document.querySelectorAll(".scan-tile").forEach(t => {
  t.addEventListener("click", () => {
    const k = t.dataset.scan;
    if (k === "network" || k === "packets") openToolModal("network-activity");
    else if (k === "threat") openToolModal("threat-scanner");
    else if (k === "memory") openProcModal("mem");
    else if (k === "process") openProcModal("cpu");
    else if (k === "llm") openModelManager();
  });
});

/* — status pills, diag cards, agent cards: clickable + keyboard — */
makeInteractive($("#sp-brain"), () => openToolModal("ai-diagnostics"), "Brain — click for AI diagnostics");
makeInteractive($("#sp-tts"), openVoiceEngineModal, "TTS — click for voice engine status");
makeInteractive($("#sp-voice"), openVoiceEngineModal, "Voice — click for voice engine status");
makeInteractive($("#sp-news"), () => execDashCmd("news"), "Feed — click to jump to World Feed");
{
  const cards = document.querySelectorAll(".diag-strip .strip-card");
  const acts = [() => openProcModal("cpu"), () => openProcModal("mem"), openDiskModal,
                () => openToolModal("network-activity")];
  const labels = ["CPU — click for top processes", "Memory — click for top consumers",
                  "Disk — click for storage detail", "Network — click for traffic detail"];
  cards.forEach((c, i) => makeInteractive(c, acts[i] || (() => {}), labels[i]));
}
$("#avengers-list")?.addEventListener("click", (e) => {
  const card = e.target.closest(".agent-card");
  if (card) openAgentModal(card.dataset.agent);
});
$("#avengers-list")?.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const card = e.target.closest(".agent-card");
  if (card) openAgentModal(card.dataset.agent);
});

/* — voice command reference: click-to-run + engine status — */
document.querySelectorAll(".vr-row[data-cmd]").forEach(b => {
  b.addEventListener("click", () => {
    const inp = $("#ask-input"); if (!inp) return;
    inp.value = b.dataset.cmd; inp.focus();
    $("#ask-form").requestSubmit();
  });
});
async function checkMic() {
  const el = $("#vr-mic"); if (!el) return;
  try {
    const devs = await navigator.mediaDevices.enumerateDevices();
    const mics = devs.filter(d => d.kind === "audioinput");
    el.textContent = mics.length ? `${mics.length} DEVICE${mics.length > 1 ? "S" : ""}` : "NONE FOUND";
    el.className = mics.length ? "ok" : "dim";
  } catch (e) { el.textContent = "N/A"; el.className = "dim"; }
}
checkMic();
try { navigator.mediaDevices.addEventListener("devicechange", checkMic); } catch (e) {}

/* ═══ OPERATOR MEMORY + VISION INTELLIGENCE ══════════ */

let memRefreshTs = 0;

function fmtClock(ts) {
  return new Date(ts * 1000).toTimeString().slice(0, 5);
}

function renderMemory(d) {
  // — recognition card —
  const st = $("#recog-status"), nm = $("#recog-name"),
        sk = $("#recog-sketch"), meta = $("#recog-meta");
  if (st && !st.dataset.liveHold) {
    if (d.enrolled) {
      st.textContent = "ENROLLED"; st.className = "recog-status enrolled";
    } else {
      st.textContent = "NOT ENROLLED"; st.className = "recog-status dim";
    }
  }
  if (nm) nm.textContent = d.enrolled
    ? `operator: ${d.name || "unnamed"} · enrolled ${d.enrolled_at ? new Date(d.enrolled_at * 1000).toLocaleDateString() : ""}`
    : "enable camera + AI ◍, then enroll";
  if (sk) sk.textContent = d.appearance ||
    "JARVIS stores a short written sketch of you — words in a JSON file you own, never face data.";
  if (meta) meta.textContent =
    `guest sightings: ${d.guest_sightings || 0} · syntheses: ${d.syntheses || 0} · memory file: operator_memory.json`;

  // — observation log (server-driven, consistent across refreshes) —
  const log = $("#obslog");
  if (log) {
    const obs = d.observations || [];
    log.innerHTML = obs.length ? obs.map(o => `
      <div class="obs-item ${o.who || "unknown"}">
        <span class="ob-who">${escapeHTML((o.who || "?").toUpperCase())}</span>
        <span class="ob-text">${escapeHTML(o.text)}</span>
        <span class="ob-time">${fmtClock(o.ts)}</span>
      </div>`).join("")
      : '<div class="tf-empty">no observations yet — turn on AI ◍ vision</div>';
  }

  // — activity timeline —
  const bars = $("#atl-bars");
  if (bars) {
    const hours = d.hourly_activity || new Array(24).fill(0);
    const max = Math.max(...hours, 1);
    const nowH = new Date().getHours();
    bars.innerHTML = hours.map((v, h) =>
      `<div class="atl-bar${h === nowH ? " now" : ""}" title="${h}:00 — ${v} min" style="height:${v ? Math.max(6, v / max * 100) : 3}%"></div>`
    ).join("");
  }
  const setT = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  const mins = d.active_minutes_today || 0;
  setT("#atl-active", mins >= 60 ? `${Math.floor(mins / 60)}H ${mins % 60}M` : `${mins}M`);
  setT("#atl-arrivals", d.arrivals_today ?? 0);
  setT("#atl-sessions", d.sessions_total ?? 0);

  // — facts —
  const fc = $("#mem-count"); if (fc) fc.textContent = (d.facts || []).length;
  const facts = $("#mem-facts");
  if (facts) {
    facts.innerHTML = (d.facts || []).length ? d.facts.map(f => `
      <div class="mem-fact${f.source === "ai-synthesis" ? " ai" : ""}">
        ${escapeHTML(f.text)}
        <div class="mf-src">${escapeHTML((f.source || "").toUpperCase())} · ${new Date(f.ts * 1000).toLocaleDateString()}</div>
      </div>`).join("")
      : '<div class="tf-empty">memory is empty — it fills as JARVIS observes you</div>';
  }
}

async function refreshMemory(force = false) {
  const now = Date.now();
  if (!force && now - memRefreshTs < 2000) return;   // throttle WS bursts
  memRefreshTs = now;
  try {
    const d = await (await fetch("/api/memory")).json();
    renderMemory(d);
    try { lastMemorySnap = d; updateLiveOps(); } catch (e2) {}
  } catch (e) {}
}
refreshMemory(true);
setInterval(() => refreshMemory(true), 120000);

// live recognition status from vision results (overrides for 60s, then
// falls back to the enrolled/not-enrolled baseline)
function setRecogLive(who) {
  const st = $("#recog-status"); if (!st || !who || who === "unknown") return;
  const map = {
    operator: ["OPERATOR VERIFIED", "operator"],
    guest: ["UNKNOWN PERSON", "guest"],
    multiple: ["MULTIPLE PEOPLE", "multiple"],
    none: ["NOBODY IN FRAME", "dim"],
  };
  const m = map[who]; if (!m) return;
  st.textContent = m[0]; st.className = "recog-status " + m[1];
  st.dataset.liveHold = "1";
  clearTimeout(st._holdTimer);
  st._holdTimer = setTimeout(() => { delete st.dataset.liveHold; refreshMemory(true); }, 60000);
}

// — enroll ("remember me") —
$("#btn-enroll")?.addEventListener("click", async () => {
  const btn = $("#btn-enroll");
  if (!camStream || camVideo.readyState < 2) {
    logEvent('<span class="tag">[memory]</span> enable the camera first, then enroll', "warn");
    return;
  }
  btn.disabled = true; btn.textContent = "◍ LOOKING…";
  try {
    const cv = document.createElement("canvas");
    cv.width = 480; cv.height = 360;
    cv.getContext("2d").drawImage(camVideo, 0, 0, 480, 360);
    const frame = cv.toDataURL("image/jpeg", 0.7);
    const name = ($("#enroll-name")?.value || "").trim();
    const r = await fetch("/api/memory/enroll", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ frame, name: name || null }),
    });
    const d = await r.json();
    if (d.ok) {
      logEvent('<span class="tag">[memory]</span> enrolled ✓ — JARVIS will recognize you');
      refreshMemory(true);
    } else {
      logEvent(`<span class="tag">[memory]</span> enroll failed: ${escapeHTML(d.error || "")}`, "warn");
    }
  } catch (e) {
    logEvent("enroll failed: " + escapeHTML(e.message), "error");
  } finally {
    btn.disabled = false; btn.textContent = "◍ REMEMBER ME";
  }
});

// — forget (wipe memory file) —
$("#btn-forget")?.addEventListener("click", async () => {
  if (!confirm("Wipe operator_memory.json? JARVIS forgets everything about you.")) return;
  try {
    await fetch("/api/memory/forget", { method: "POST" });
    refreshMemory(true);
  } catch (e) {}
});

// — teach a fact manually —
$("#mem-add-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const inp = $("#mem-add-input");
  const text = (inp?.value || "").trim();
  if (!text) return;
  inp.value = "";
  try {
    await fetch("/api/memory/fact", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
    });
    logEvent(`<span class="tag">[memory]</span> learned: ${escapeHTML(text)}`);
    refreshMemory(true);
  } catch (e2) {}
});

// — "what do you know about me?" (spoken) —
$("#btn-mem-speak")?.addEventListener("click", async () => {
  try {
    const d = await (await fetch("/api/memory/speak", { method: "POST" })).json();
    if (d.summary) logEvent(`<span class="tag">[memory]</span> ${escapeHTML(d.summary)}`);
  } catch (e) {}
});

/* ═══ REAL-DATA BRIDGES — no mock anywhere ═══════════
   Live Operations, personal Notes (disk-persisted), Service Grid, and the
   orb-flank readouts all bind to real endpoints/telemetry. */


function updateLiveOps() {
  const set = (id, txt, barId, pct) => {
    const el = $(id); if (el) el.textContent = txt;
    const b = $(barId); if (b) b.style.width = Math.max(0, Math.min(100, pct)) + "%";
  };
  if (lastAgenda) {
    const n = (lastAgenda.intel || {}).meetings_today ?? (lastAgenda.events || []).length;
    set("#ops-meetings", String(n), "#ops-meetings-bar", n ? Math.min(100, n / 8 * 100) : 0);
  }
  if (lastThreat) {
    const soc = lastThreat.soc || {};
    const total = (lastThreat.events || []).length || 0;
    set("#ops-threats", `${soc.resolved ?? 0}/${total}`, "#ops-threats-bar",
        total ? (soc.resolved || 0) / total * 100 : 0);
  }
  if (lastMemorySnap) {
    const mins = lastMemorySnap.active_minutes_today || 0;
    const txt = mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`;
    set("#ops-presence", txt, "#ops-presence-bar", mins / 480 * 100);
  }
}

/* — personal notes (notes.json on disk) — */
async function refreshNotes() {
  try {
    const d = await (await fetch("/api/notes")).json();
    const host = $("#notes-list"); if (!host) return;
    const notes = d.notes || [];
    host.innerHTML = notes.length ? notes.map(n => `
      <div class="note" data-id="${n.id}">
        <div class="note-text">${escapeHTML(n.text)}
          <span class="note-del" title="delete">×</span></div>
        <div class="note-ts">${new Date(n.ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</div>
      </div>`).join("")
      : '<div class="tf-empty">no notes yet — add one below</div>';
  } catch (e) {}
}
refreshNotes();
$("#note-add-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const inp = $("#note-add-input");
  const text = (inp?.value || "").trim();
  if (!text) return;
  inp.value = "";
  try {
    await fetch("/api/notes", { method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }) });
    refreshNotes();
  } catch (e2) {}
});
$("#notes-list")?.addEventListener("click", async (e) => {
  const del = e.target.closest(".note-del"); if (!del) return;
  const id = del.closest(".note")?.dataset.id; if (!id) return;
  try { await fetch(`/api/notes/${id}`, { method: "DELETE" }); refreshNotes(); } catch (e2) {}
});

/* — service grid: real health of every integration — */
function setSvc(id, ok, label) {
  const row = $(id); if (!row) return;
  const dot = row.querySelector(".rr-dot"), st = row.querySelector(".rr-state");
  if (dot) dot.className = "rr-dot" + (ok ? " ok" : "");
  if (st) { st.textContent = label; st.className = "rr-state" + (ok ? " ok" : ""); }
}
async function pollServiceGrid() {
  try {
    const [sr, cr, mr] = await Promise.allSettled([
      fetch("/api/status").then(r => r.json()),
      fetch("/api/calendar/status").then(r => r.json()),
      fetch("/api/models").then(r => r.json()),
    ]);
    if (sr.status === "fulfilled") {
      const s = sr.value;
      setSvc("#svc-claude", s.brain_mode === "llm", s.brain_mode === "llm" ? "ONLINE" : "OFFLINE");
      setSvc("#svc-news", (s.news_count || 0) > 0, (s.news_count || 0) > 0 ? `${s.news_count} ITEMS` : "NO KEY");
    }
    if (cr.status === "fulfilled") {
      const c = cr.value;
      setSvc("#svc-gcal", !!c.connected,
             c.connected ? "SYNCED" : c.credentials_present ? "AUTH NEEDED" : "NO CREDS");
    }
    if (mr.status === "fulfilled") {
      const m = mr.value;
      setSvc("#svc-ollama", !!m.local_available,
             m.local_available ? (m.selected || "READY") : "OFFLINE");
    }
    setSvc("#svc-weather", lastWeatherOk === true, lastWeatherOk ? "LIVE" : "NO DATA");
  } catch (e) {}
}
pollServiceGrid();
setInterval(pollServiceGrid, 60000);

/* ═══ EXTRA INTERACTIVITY — clickable detail across the board ═══ */

function initExtraInteractions() {
  // Weather card → detail modal
  const wc = $("#weather-card");
  makeInteractive(wc, async () => {
    openCustomModal("HYDERABAD WEATHER", "OPEN-METEO · LIVE", null,
      '<div style="color:var(--text-dim);font-style:italic">fetching…</div>');
    try {
      const d = await (await fetch("/api/weather" + (userLat != null ? `?lat=${userLat}&lon=${userLon}` : ""))).json();
      $("#modal-body").innerHTML = `
        <div class="mb-grid">
          <div class="mb-stat"><span class="lbl">CONDITION</span><span class="val">${escapeHTML((d.label||"—").toUpperCase())}</span></div>
          <div class="mb-stat"><span class="lbl">TEMPERATURE</span><span class="val">${d.temp_c ?? "--"}°C</span></div>
          <div class="mb-stat"><span class="lbl">FEELS LIKE</span><span class="val">${d.feels_c ?? "--"}°C</span></div>
          <div class="mb-stat"><span class="lbl">HUMIDITY</span><span class="val">${d.humidity ?? "--"}%</span></div>
          <div class="mb-stat"><span class="lbl">WIND</span><span class="val">${d.wind_kmh != null ? Math.round(d.wind_kmh) : "--"} km/h</span></div>
          <div class="mb-stat"><span class="lbl">LOCATION</span><span class="val">${escapeHTML(d.city || "—")}</span></div>
        </div>`;
    } catch (e) { $("#modal-body").innerHTML = '<div style="color:var(--error)">weather unavailable</div>'; }
  }, "Weather detail");

  // Agenda items → event detail
  $("#agenda-list")?.addEventListener("click", (e) => {
    const item = e.target.closest(".agenda-item"); if (!item) return;
    const title = item.querySelector(".agenda-title")?.textContent || "Event";
    const time = item.querySelector(".agenda-time")?.textContent.trim() || "";
    const meta = item.querySelector(".agenda-meta")?.textContent || "";
    openCustomModal(title, "CALENDAR EVENT", null,
      `<div class="mb-grid"><div class="mb-stat"><span class="lbl">WHEN</span><span class="val">${escapeHTML(time)}</span></div></div>
       <div class="mb-list"><div class="mb-row">${escapeHTML(meta)}</div></div>`);
  });

  // Inbox items → email detail
  $("#inbox-list")?.addEventListener("click", (e) => {
    const item = e.target.closest(".inbox-item"); if (!item) return;
    const sender = item.querySelector(".inbox-sender")?.textContent || "";
    const subj = item.querySelector(".inbox-subject")?.textContent || "";
    const snip = item.querySelector(".inbox-snippet")?.textContent || "";
    openCustomModal(subj || "Email", "INBOX MESSAGE", null,
      `<div class="mb-list"><div class="mb-row"><b>From:</b> ${escapeHTML(sender)}</div>
       <div class="mb-row">${escapeHTML(snip)}</div></div>`);
  });

  // Service-grid rows → what each integration is + how to connect
  const svcInfo = {
    "svc-claude": ["CLAUDE CLI", "The cloud brain — powers agent replies, insights, and webcam vision. Set CLAUDE_BIN in .env."],
    "svc-ollama": ["OLLAMA · LOCAL AI", "On-device models for fully offline replies. Run `ollama serve` + `ollama pull`, then pick a model in the AI MODEL MANAGER."],
    "svc-gcal": ["GOOGLE CALENDAR + GMAIL", "Real events & mail (readonly). Click the G button by AGENDA to connect via OAuth."],
    "svc-news": ["NEWS FEED", "Live world headlines. Free key at newsapi.org → NEWSAPI_KEY in .env."],
    "svc-weather": ["WEATHER", "Live Hyderabad weather via Open-Meteo (no key needed)."],
  };
  Object.entries(svcInfo).forEach(([id, [t, desc]]) => {
    const row = $("#" + id);
    makeInteractive(row, () => {
      const state = row.querySelector(".rr-state")?.textContent || "—";
      openCustomModal(t, "SERVICE STATUS", null,
        `<div class="mb-grid"><div class="mb-stat"><span class="lbl">STATUS</span><span class="val">${escapeHTML(state)}</span></div></div>
         <div class="mb-list"><div class="mb-row">${escapeHTML(desc)}</div></div>`);
    }, t + " info");
  });

  // Live-operations & AI-ops cards → jump to their detailed rows
  makeInteractive($("#aiops-vision"), () => execDashCmd("camera"), "Vision — jump to operator view");
  makeInteractive($("#intel-readiness"), () => {}, "Mission readiness");
  makeInteractive($(".bs-mission"), () => execDashCmd("agenda"), "Live ops — jump to agenda");
  makeInteractive($(".bs-threat"), () => execDashCmd("threats"), "Threats — jump to threat center");
}
initExtraInteractions();

// ── websocket (self-healing) ─────────────────────────
let ws = null;
let wsLastMsg = 0;
let wsReconnecting = false;

function scheduleReconnect(reason) {
  if (wsReconnecting) return;          // dedup: never stack reconnects
  wsReconnecting = true;
  $("#status-pill").classList.add("offline");
  $("#status-text").textContent = "RECONNECTING";
  try { if (ws) ws.close(); } catch (e) {}
  setTimeout(() => { wsReconnecting = false; connect(); }, 1200);
}

function connect() {
  try { ws = new WebSocket(`ws://${location.host}/ws`); }
  catch (e) { scheduleReconnect("ctor"); return; }
  ws.onopen = () => {
    wsLastMsg = Date.now();
    $("#status-pill").classList.remove("offline");
    $("#status-text").textContent = "ONLINE";
    logEvent('<span class="tag">[net]</span> uplink established');
    refreshPills();
    setPill("#sp-voice", "OFF", "warn");
  };
  ws.onclose = () => {
    logEvent('<span class="tag">[net]</span> uplink lost — reconnecting', "warn");
    scheduleReconnect("close");
  };
  ws.onerror = () => { try { ws.close(); } catch (e) {} };  // → triggers onclose
  ws.onmessage = (m) => {
    wsLastMsg = Date.now();
    try { handle(JSON.parse(m.data)); } catch (e) {}
  };
}

// Watchdog: server pushes metrics every ~2s. If nothing arrives for 12s the
// socket is half-dead (common under heavy CPU/disk load) — force a reconnect.
setInterval(() => {
  if (!ws) return;
  if (ws.readyState === WebSocket.OPEN && Date.now() - wsLastMsg > 12000) {
    logEvent('<span class="tag">[net]</span> link stalled — resetting', "warn");
    scheduleReconnect("watchdog");
  }
}, 4000);

function handle(msg) {
  switch (msg.type) {
    case "snapshot":
      renderAgents(msg.agents); break;
    case "metrics":
      $("#m-cpu").textContent = msg.cpu.toFixed(0);
      $("#m-mem").textContent = msg.mem.toFixed(0);
      $("#m-disk").textContent = msg.disk.toFixed(0);
      $("#m-disk-fill").style.width = msg.disk + "%";
      $("#m-up").textContent = msg.net_up;
      $("#m-down").textContent = msg.net_down;
      // network tool-card: real live throughput (was a hardcoded placeholder)
      try {
        const tot = Math.round(msg.net_up + msg.net_down);
        const tc = $("#tc-net");
        if (tc && !window._wifiSpeedShown)
          tc.textContent = `${tot} KB/s · ${Math.round(msg.net_down)}↓ ${Math.round(msg.net_up)}↑`;
      } catch (e) {}
      pushChart(cpuChart, msg.cpu); pushChart(memChart, msg.mem);
      updateResource(msg.cpu, msg.mem, msg.disk);
      try { updateAiopsTele(msg); } catch (e) {}
      try { pushTraffic(msg.net_up, msg.net_down); } catch (e) {}
      try { lastMetrics = msg; updateScanTiles(msg); } catch (e) {}
      try {   // orb flank: real uplink rate + CPU flux
        const fu = $("#fl-uplink"); if (fu) fu.textContent = Math.round(msg.net_down + msg.net_up);
        const ff = $("#fl-flux");
        if (ff) {
          const prev = window._prevCpu ?? msg.cpu;
          const d = msg.cpu - prev;
          ff.textContent = (d >= 0 ? "+" : "") + d.toFixed(1);
          window._prevCpu = msg.cpu;
        }
      } catch (e) {}
      break;
    case "agent":
      if (msg.event === "status") {
        updateAgentStatus(msg.agent, msg.status, msg.task);
        if (msg.status === "thinking" || msg.status === "working") animateAiGauge(true);
        else animateAiGauge(false);
      }
      if (msg.event === "reply") logEvent(`<span class="tag">[${msg.agent}]</span> ${escapeHTML(msg.a || msg.q)}`);
      if (msg.event === "tick")  logEvent(`<span class="tag">[${msg.agent}]</span> ${escapeHTML(msg.msg)}`);
      if (msg.event === "alert") logEvent(`<span class="tag">[${msg.agent}]</span> ${escapeHTML(msg.msg)}`, "warn");
      if (msg.event === "pulled-news") logEvent(`<span class="tag">[${msg.agent}]</span> intel pulled · ${msg.count} items`);
      break;
    case "news":
      renderNews(msg.items);
      setPill("#sp-news", `${msg.items.length} ITEMS`, msg.items.length > 0 ? "ok" : "warn");
      logEvent(`<span class="tag">[widow]</span> world feed refreshed · ${msg.items.length} headlines`);
      break;
    case "digest":
      $("#digest").textContent = msg.msg;
      logEvent(`<span class="tag">[${msg.kind === "briefing" ? "captain" : "vision"}]</span> digest updated`);
      break;
    case "voice":
      if (msg.event === "ready")      setPill("#sp-voice", "READY", "ok");
      if (msg.event === "wake")       setVoiceState("listening");
      if (msg.event === "listening")  { setVoiceState("listening"); setPill("#sp-voice", "LISTENING", "ok"); }
      if (msg.event === "processing") { setVoiceState("processing"); setPill("#sp-voice", "PROCESSING", "warn"); }
      if (msg.event === "heard")      { setHeard(msg.text, ""); logEvent(`<span class="tag">[heard]</span> "${escapeHTML(msg.text)}"`); }
      if (msg.event === "routed")     { setHeard(null, `→ ${(msg.agent || "").toUpperCase()}`); logEvent(`<span class="tag">[voice]</span> → ${escapeHTML(msg.agent)} · ${escapeHTML(msg.command)}`); }
      if (msg.event === "speak")      { setVoiceState("speaking"); setPill("#sp-voice", "SPEAKING", "ok"); logEvent(`<span class="tag">[voice]</span> ◉ ${escapeHTML(msg.text || "")}`); }
      if (msg.event === "idle")       { setVoiceState("idle"); setPill("#sp-voice", "READY", "ok"); }
      { // mirror into the voice-ref engine status block
        const ve = $("#vr-engine");
        const map = { ready: ["READY", "ok"], listening: ["LISTENING", "ok"],
                      processing: ["PROCESSING", ""], speak: ["SPEAKING", "ok"], idle: ["READY", "ok"] };
        const m = map[msg.event];
        if (ve && m) { ve.textContent = m[0]; ve.className = m[1]; }
        if (msg.event === "routed") {
          const vl = $("#vr-last");
          if (vl) vl.textContent = (msg.command || "—").slice(0, 24);
        }
      }
      break;
    case "browser":
      if (msg.event === "opened") logEvent(`<span class="tag">[browser]</span> opened ${escapeHTML(msg.name || msg.url)}${msg.fullscreen ? " · fullscreen" : ""}`);
      break;
    case "threat-event":
      addThreatEvent(msg);
      pushTicker(`[${(msg.category || "sys").toUpperCase()}] ${msg.title}`,
                 msg.severity === "high" || msg.severity === "critical" ? "tk-crit"
                 : msg.severity === "medium" ? "tk-warn" : "");
      if (msg.severity === "high" || msg.severity === "critical")
        logEvent(`<span class="tag">[threat]</span> ${escapeHTML(msg.title)}`, "warn");
      break;
    case "alert":
      AlertSystem.raise(msg);
      logEvent(`<span class="tag">[alert]</span> ${escapeHTML((msg.severity || "info").toUpperCase())} · ${escapeHTML(msg.title || "")}`,
               msg.severity === "critical" || msg.severity === "emergency" ? "error"
               : msg.severity === "warning" ? "warn" : "info");
      break;
    case "risk":
      updateRisk(msg.score, msg.level);
      break;
    case "dash-cmd":
      execDashCmd(msg.target);
      break;
    case "vision-request":                 // voice "what do you see" → capture a frame
      try { VisionAI.analyze(true); } catch (e) {}
      break;
    case "vision-obs":                      // Claude's webcam observation
      { const vo = $("#vision-obs"); if (vo) vo.textContent = "◉ " + (msg.text || ""); }
      try { setRecogLive(msg.who); } catch (e) {}
      try { refreshMemory(); } catch (e) {}
      break;
    case "memory-updated":                  // operator memory changed on disk
      try { refreshMemory(); } catch (e) {}
      break;
    case "models-updated":                  // brain/model switched
      try { pollServiceGrid(); refreshLocalAI(); } catch (e) {}
      break;
    case "audio":                           // mute/volume changed (sync viewers)
      try { AudioCtl.muted = !!msg.muted; AudioCtl.volume = msg.volume ?? 100; AudioCtl.paint(); } catch (e) {}
      break;
    case "audit":                           // live append to the audit feed
      try { prependAudit(msg); } catch (e) {}
      break;
    case "orch":                            // orchestrator cycle summary
      try {
        const os = $("#orch-stat");
        if (os) os.textContent =
          `${msg.active} ACTIVE · ${msg.delegations_total} DELEGATED · ${msg.preemptions_total} PREEMPTED`;
      } catch (e) {}
      break;
    case "insight":
      addInsight(msg);
      pushTicker(`AI: ${msg.insight || ""}`.slice(0, 140));
      setConfidence(msg.confidence);
      { const src = $("#insight-source");
        if (src) src.textContent = (msg.source || "—").toUpperCase(); }
      logEvent(`<span class="tag">[jarvis-ai]</span> ${escapeHTML(msg.insight)}`,
               msg.severity === "critical" ? "error" : msg.severity === "warn" ? "warn" : "info");
      break;
    case "ai-state":
      setReasonState(msg.state === "analyzing" ? "analyzing" : "idle");
      break;
    case "presence":
      // server-side echo (greetings, cross-client sync) — local monitor owns the UI
      break;
    case "agenda-alert":
      logEvent(`<span class="tag">[agenda]</span> ${escapeHTML(msg.msg)}`, "warn");
      refreshAgenda(); break;
    case "inbox-alert":
      logEvent(`<span class="tag">[inbox]</span> ${escapeHTML(msg.msg)}`, "warn");
      refreshAgenda(); break;
    case "log":
      logEvent(`<span class="tag">[sys]</span> ${escapeHTML(msg.msg)}`, msg.level || "info"); break;
    case "system":
      logEvent(`<span class="tag">[sys]</span> ${escapeHTML(msg.msg)}`); break;
  }
}

// ── ask form ─────────────────────────────────────────
$("#ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const inp = $("#ask-input"); const text = inp.value.trim();
  if (!text) return;
  inp.value = "";
  logEvent(`<span class="tag">[you]</span> ${escapeHTML(text)}`);
  const m = text.match(/^@(\w+)\s+(.+)/);
  const agent = m ? m[1] : null;
  const prompt = m ? m[2] : text;
  try {
    const r = await fetch("/api/ask", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ prompt, agent }),
    });
    const data = await r.json();
    if (data.error) logEvent(`<span class="tag">[err]</span> ${escapeHTML(data.error)}`, "error");
    else            logEvent(`<span class="tag">[${data.agent}]</span> ${escapeHTML(data.reply)}`);
  } catch (err) { logEvent("ask failed: " + escapeHTML(err.message), "error"); }
});

// ── news refresh button ──────────────────────────────
$("#news-refresh").addEventListener("click", async () => {
  $("#news-refresh").disabled = true;
  try { await fetch("/api/news/refresh", { method: "POST" }); }
  finally { setTimeout(() => { $("#news-refresh").disabled = false; }, 1500); }
});

/* ═══ DASHBOARD AUDIO — mute + volume (agent speech + alert sounds) ═══
   Agent speech is server-side TTS (host speakers), so mute/volume hit the
   backend; the browser alert siren is gated locally via window.dashAudioMuted. */
window.dashAudioMuted = false;
const AudioCtl = {
  muted: false, volume: 100,
  icon(v) { return this.muted || v === 0 ? "🔇" : v < 45 ? "🔉" : "🔊"; },
  paint() {
    const ctl = $("#audio-ctl"), btn = $("#audio-mute"), vol = $("#audio-vol");
    window.dashAudioMuted = this.muted;
    if (btn) btn.textContent = this.icon(this.volume);
    if (ctl) ctl.classList.toggle("muted", this.muted);
    if (vol && document.activeElement !== vol) vol.value = this.muted ? 0 : this.volume;
    const title = this.muted ? "Audio muted — click to unmute"
      : `Dashboard audio · ${this.volume}%`;
    if (ctl) ctl.title = title;
  },
  async load() {
    try {
      const d = await (await fetch("/api/audio")).json();
      this.muted = !!d.muted; this.volume = d.volume ?? 100;
    } catch (e) {}
    this.paint();
  },
  async push(body) {
    try {
      await fetch("/api/audio", { method: "POST",
        headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    } catch (e) {}
  },
  async toggleMute() {
    this.muted = !this.muted;
    if (this.muted && typeof AlertSystem !== "undefined") { try { AlertSystem.stopSiren?.(); } catch (e) {} }
    this.paint();
    await this.push({ muted: this.muted });
    logEvent(`<span class="tag">[audio]</span> dashboard audio ${this.muted ? "muted" : "unmuted"}`);
  },
  async setVolume(v) {
    this.volume = Math.max(0, Math.min(100, parseInt(v, 10) || 0));
    if (this.volume > 0 && this.muted) this.muted = false;   // dragging up unmutes
    this.paint();
    await this.push({ volume: this.volume, muted: this.muted });
  },
};
$("#audio-mute")?.addEventListener("click", () => AudioCtl.toggleMute());
$("#audio-vol")?.addEventListener("input", (e) => AudioCtl.setVolume(e.target.value));
AudioCtl.load();

/* ═══════════════════════════════════════════════════════════════
   PHASE 2 · MULTI-AGENT INTELLIGENCE — panel controllers
   orchestration · recommendations · product evolution ·
   local AI control · operator productivity · knowledge hub
   ═══════════════════════════════════════════════════════════════ */

// ── multi-agent orchestration ────────────────────────
async function refreshOrch() {
  try {
    const d = await (await fetch("/api/orchestrator")).json();
    const os = $("#orch-stat");
    if (os) os.textContent =
      `${d.active} ACTIVE · ${d.delegations_total} DELEGATED · ${d.collabs_total ?? 0} COLLABS`;
    // shared context blackboard chips
    const bb = d.blackboard || {}; const bbEl = $("#orch-bb");
    if (bbEl && bb.ts) {
      const chips = [
        ["CPU", `${Math.round(bb.cpu)}%`, bb.cpu > 85],
        ["MEM", `${Math.round(bb.mem)}%`, bb.mem > 88],
        ["RISK", `${bb.risk_score}`, bb.risk_score >= 50],
        ["INCIDENTS", `${bb.open_incidents}`, bb.open_incidents > 3],
        ["MAIL", `${bb.priority_mail}`, bb.priority_mail >= 2],
        ["PRESENCE", (bb.presence || "—").toUpperCase(), false],
      ];
      if (bb.next_meeting_min != null)
        chips.push(["NEXT MTG", `${Math.round(bb.next_meeting_min)}m`, bb.next_meeting_min < 15]);
      if (bb.focus_active) chips.push(["FOCUS", "GUARDED", false]);
      bbEl.innerHTML = chips.map(([k, v, hot]) =>
        `<span class="orch-chip${hot ? " hot" : ""}">${k} <b>${escapeHTML(String(v))}</b></span>`).join("");
    }
    // directives (active first, then recent completions greyed)
    const list = $("#orch-list");
    if (list) {
      const act = (d.directives || []).map(x => ({ ...x, _done: false }));
      const done = (d.recent_done || []).slice(0, 3).map(x => ({ ...x, _done: true }));
      const rows = [...act, ...done];
      list.innerHTML = rows.length ? rows.map(x => `
        <div class="orch-item${x._done ? " done" : ""}">
          <span class="orch-p p${x.priority}">${(d.priority_labels || {})[x.priority] || "P" + x.priority}</span>
          <span class="orch-main">
            <div class="orch-title">${escapeHTML(x.title)}</div>
            <div class="orch-detail">${escapeHTML(x.detail || "")}${x.consult ? " · ⇄ consulted " + escapeHTML(x.consult.toUpperCase()) : ""}</div>
          </span>
          <span class="orch-agent">${escapeHTML((x.agent || "").toUpperCase())}</span>
        </div>`).join("")
        : '<div class="tf-empty">no directives — all stations nominal</div>';
    }
  } catch (e) {}
}
refreshOrch(); setInterval(refreshOrch, 15000);

// ── command recommendations ──────────────────────────
async function refreshRecs() {
  try {
    const d = await (await fetch("/api/recommendations")).json();
    const list = $("#recs-list"); if (!list) return;
    const recs = d.recommendations || [];
    list.innerHTML = recs.length ? recs.map(r => `
      <div class="rec-item ${r.severity}">
        <span class="rec-main">
          <div class="rec-title">${escapeHTML(r.title)}</div>
          <div class="rec-detail">${escapeHTML(r.detail || "")}</div>
        </span>
      </div>`).join("") : '<div class="tf-empty">assessing conditions…</div>';
  } catch (e) {}
}
refreshRecs(); setInterval(refreshRecs, 30000);

$("#btn-briefing")?.addEventListener("click", async () => {
  const b = $("#btn-briefing"); b.disabled = true; b.textContent = "◈ COMPILING…";
  try { await fetch("/api/briefing", { method: "POST" }); } catch (e) {}
  setTimeout(() => { b.disabled = false; b.textContent = "◈ EXECUTIVE BRIEFING"; }, 4000);
});

// ── product evolution (live roadmap) ─────────────────
async function refreshRoadmap() {
  try {
    const d = await (await fetch("/api/roadmap")).json();
    const v = $("#evo-version"); if (v) v.textContent = d.version;
    const p = $("#evo-pct"); if (p) p.textContent = d.progress_pct + "%";
    const f = $("#evo-fill"); if (f) f.style.width = d.progress_pct + "%";
    const host = $("#evo-phases");
    if (host) {
      host.innerHTML = (d.phases || []).map(ph => `
        <div class="evo-phase" data-n="${ph.n}" tabindex="0" role="button">
          <span class="n">P${ph.n}</span>
          <span class="nm">${escapeHTML(ph.name)}</span>
          <span class="pc">${ph.done}/${ph.total}</span>
          <span class="st ${ph.status}">${ph.status.toUpperCase()}</span>
          <div class="evo-feats">
            ${ph.features.map(ft => `<div class="evo-feat${ft.done ? " done" : ""}">${escapeHTML(ft.name)}</div>`).join("")}
          </div>
        </div>`).join("");
      host.querySelectorAll(".evo-phase").forEach(el => {
        const toggle = () => el.classList.toggle("open");
        el.addEventListener("click", toggle);
        el.addEventListener("keydown", (e) => { if (e.key === "Enter") toggle(); });
      });
      // current (active) phase starts expanded
      host.querySelector(".evo-phase .st.active")?.closest(".evo-phase")?.classList.add("open");
    }
    const dev = $("#evo-dev");
    if (dev) dev.textContent = (d.in_development || []).join(" · ") || "—";
  } catch (e) {}
}
refreshRoadmap(); setInterval(refreshRoadmap, 120000);

// ── local AI control center ──────────────────────────
async function refreshLocalAI() {
  try {
    const [models, stats] = await Promise.all([
      (await fetch("/api/models")).json(),
      (await fetch("/api/models/stats")).json(),
    ]);
    const act = $("#lai-active");
    if (act) act.textContent = (models.active_brain || "—").toUpperCase();
    const perf = stats.perf || {};
    const setV = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    setV("#lai-latency", perf.latency_ms != null ? perf.latency_ms + "ms" : "—");
    setV("#lai-tps", perf.tokens_per_sec != null ? perf.tokens_per_sec : "—");
    // running model resources (Ollama /api/ps)
    const run = (stats.running || [])[0];
    setV("#lai-ram", run ? (run.vram_gb > 0 ? `${run.vram_gb}G VRAM` : `${run.ram_gb}G RAM`) : "unloaded");
    setV("#lai-ctx", run && run.context ? (run.context / 1024) + "k" : "—");
    // host memory
    const used = stats.host_ram_used_gb, total = stats.host_ram_total_gb;
    if (total) {
      const fill = $("#lai-host-fill");
      if (fill) fill.style.width = Math.round(used * 100 / total) + "%";
      setV("#lai-host-txt", `${used} / ${total} GB`);
    }
    // installed models — one click to switch the brain (no restart)
    const host = $("#lai-models");
    if (host) {
      const rows = [{ name: "claude", param_size: "CLOUD", quant: "ANTHROPIC" },
                    ...(models.local || [])];
      host.innerHTML = rows.map(m => {
        const isClaude = m.name === "claude";
        const activeNow = isClaude ? !models.force_local
                                   : (models.force_local && m.name === models.selected);
        const spec = isClaude ? "ANTHROPIC · CLOUD"
          : [m.param_size, m.quant, m.size_gb ? m.size_gb + "GB" : null].filter(Boolean).join(" · ");
        return `<div class="lai-model${activeNow ? " active" : ""}" data-model="${escapeHTML(m.name)}" tabindex="0" role="button">
          <span class="lai-m-name">${escapeHTML(m.name)}</span>
          <span class="lai-m-spec">${escapeHTML(spec || "")}</span>
          ${activeNow ? '<span class="lai-m-badge">● ACTIVE</span>' : ""}
        </div>`;
      }).join("");
      host.querySelectorAll(".lai-model").forEach(el => {
        const pick = async () => {
          el.style.opacity = "0.5";
          try {
            await fetch("/api/models/select", { method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({ model: el.dataset.model }) });
          } catch (e) {}
          refreshLocalAI();
        };
        el.addEventListener("click", pick);
        el.addEventListener("keydown", (e) => { if (e.key === "Enter") pick(); });
      });
    }
  } catch (e) {}
}
refreshLocalAI(); setInterval(refreshLocalAI, 25000);

// on-prem AI cluster: fleet nodes that can serve inference (Phase 4)
async function refreshCluster() {
  try {
    const d = await (await fetch("/api/cluster")).json();
    const stat = $("#cluster-stat");
    if (stat) stat.textContent = `${d.provider_count} PROVIDER${d.provider_count === 1 ? "" : "S"} · ${d.total_models} MODELS · ${d.gpu_nodes} GPU`;
    const host = $("#lai-cluster"); if (!host) return;
    const provs = d.providers || [];
    host.innerHTML = provs.length ? provs.map(p => {
      const routed = d.routed_remote && d.remote_node === p.name;
      return `<div class="lai-model${routed ? " active" : ""}" data-node="${escapeHTML(p.is_local ? "" : p.node_id)}" tabindex="0" role="button" title="route inference here">
        <span class="lai-m-name">${escapeHTML((p.name || "").toUpperCase())}${p.is_local ? " (LOCAL)" : ""}</span>
        <span class="lai-m-spec">${p.count} MODELS · GPU ${p.gpu == null ? "N/A" : Math.round(p.gpu) + "%"}</span>
        ${routed ? '<span class="lai-m-badge">● ROUTED</span>' : (p.is_local ? "" : '<span class="lai-m-spec">→ ROUTE</span>')}
      </div>`;
    }).join("") : '<div class="tf-empty">no fleet node is advertising Ollama — start Ollama on a node to add inference capacity</div>';
    host.querySelectorAll(".lai-model[data-node]").forEach(el => {
      el.addEventListener("click", async () => {
        el.style.opacity = "0.5";
        try {
          const r = await (await fetch("/api/cluster/route", { method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ node_id: el.dataset.node || null }) })).json();
          if (r.ok) logEvent(`<span class="tag">[cluster]</span> inference routed ${r.remote ? "to " + r.node : "back to local"}`);
          else if (r.error) logEvent(`<span class="tag">[cluster]</span> ${escapeHTML(r.error)}`, "warn");
        } catch (e) {}
        refreshCluster(); refreshLocalAI();
      });
    });
  } catch (e) {}
}
refreshCluster(); setInterval(refreshCluster, 20000);

// ── operator productivity ────────────────────────────
let prodSnap = null;
function renderProd(d) {
  prodSnap = d;
  const sc = $("#prod-score"); if (sc) sc.textContent = d.score;
  const setN = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  setN("#pc-focus", d.focus_min_today);
  setN("#pc-sessions", d.sessions_today);
  setN("#pc-switch", d.switches_today);
  setN("#pc-done", d.tasks_done_today);
  const btn = $("#btn-focus");
  if (btn) btn.textContent = d.focus_active ? "■ END SESSION" : "▶ START DEEP WORK";
  const clock = $("#focus-clock");
  if (clock) clock.classList.toggle("live", !!d.focus_active);
  const inp = $("#task-input");
  if (inp && document.activeElement !== inp) inp.value = d.current_task || "";
  tickFocusClock();
}
function tickFocusClock() {
  const clock = $("#focus-clock"); if (!clock || !prodSnap) return;
  let secs = 0;
  if (prodSnap.focus_active) {
    secs = prodSnap.focus_elapsed_sec + Math.floor((Date.now() - prodSnap._rx) / 1000);
  }
  const m = Math.floor(secs / 60), s = secs % 60;
  clock.textContent = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
setInterval(tickFocusClock, 1000);

async function refreshProd() {
  try {
    const d = await (await fetch("/api/productivity")).json();
    d._rx = Date.now();
    renderProd(d);
  } catch (e) {}
}
async function prodAction(body) {
  try {
    const d = await (await fetch("/api/productivity", { method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body) })).json();
    d._rx = Date.now();
    renderProd(d);
  } catch (e) {}
}
refreshProd(); setInterval(refreshProd, 20000);
$("#btn-focus")?.addEventListener("click", () =>
  prodAction({ action: prodSnap && prodSnap.focus_active ? "focus_stop" : "focus_start" }));
$("#task-form")?.addEventListener("submit", (e) => {
  e.preventDefault();
  prodAction({ action: "task", text: $("#task-input").value.trim() });
});
$("#btn-task-done")?.addEventListener("click", () => prodAction({ action: "task_done" }));

// ── knowledge hub ────────────────────────────────────
async function refreshKbStats() {
  try {
    const d = await (await fetch("/api/knowledge/stats")).json();
    const el = $("#kb-stats");
    if (el) el.textContent = `${d.total} ITEMS · ${Object.keys(d.sources || {}).length} SOURCES`;
  } catch (e) {}
}
refreshKbStats(); setInterval(refreshKbStats, 60000);

function renderKbResults(items, answer) {
  const host = $("#kb-results"); if (!host) return;
  let html = "";
  if (answer) html += `<div class="kb-item answer"><div class="kb-src">JARVIS · SYNTHESIZED ANSWER</div>
    <div class="kb-text">${escapeHTML(answer)}</div></div>`;
  html += (items || []).map(r => `
    <div class="kb-item">
      <div class="kb-src">${escapeHTML((r.source || "").toUpperCase())} · ${escapeHTML(r.ref || "")}</div>
      <div class="kb-text">${escapeHTML(r.text)}</div>
    </div>`).join("");
  host.innerHTML = html || '<div class="tf-empty">no matches — try different words</div>';
}
$("#kb-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#kb-input").value.trim(); if (!q) return;
  $("#kb-results").innerHTML = '<div class="tf-empty">searching all sources…</div>';
  try {
    const d = await (await fetch("/api/knowledge/search?q=" + encodeURIComponent(q))).json();
    renderKbResults(d.results);
  } catch (err) {}
});
$("#btn-kb-ask")?.addEventListener("click", async () => {
  const q = $("#kb-input").value.trim(); if (!q) return;
  $("#kb-results").innerHTML = '<div class="tf-empty">thinking over the knowledge base…</div>';
  try {
    const d = await (await fetch("/api/knowledge/ask", { method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ query: q }) })).json();
    renderKbResults(d.sources, d.answer);
  } catch (err) {}
});

/* ═══ PHASE 4 · ENTERPRISE COMMAND — roles · operators · audit · export ═══ */

let entState = { role: "commander", pin_required: false };

function auditRow(e) {
  const t = new Date(e.ts * 1000).toTimeString().slice(0, 5);
  return `<div class="aud-row"><span class="aud-t">${t}</span>
    <span class="aud-a"><b>${escapeHTML(e.action)}</b> ${escapeHTML(e.detail || "")}
    <span class="who">· ${escapeHTML(e.operator || "")}/${escapeHTML(e.role || "")}</span></span></div>`;
}
function prependAudit(e) {
  const host = $("#ent-audit"); if (!host) return;
  const empty = host.querySelector(".tf-empty"); if (empty) empty.remove();
  host.insertAdjacentHTML("afterbegin", auditRow(e));
  while (host.children.length > 20) host.lastChild.remove();
  const n = $("#ent-audit-n"); if (n) n.textContent = String(parseInt(n.textContent || "0") + 1);
}

async function refreshEnterprise() {
  try {
    const d = await (await fetch("/api/enterprise")).json();
    entState = d;
    const roleEl = $("#ent-role");
    if (roleEl) roleEl.textContent = (d.role || "—").toUpperCase();
    const opEl = $("#ent-op"); if (opEl) opEl.textContent = d.operator || "—";
    $("#btn-role-cmd")?.classList.toggle("on", d.role === "commander");
    $("#btn-role-obs")?.classList.toggle("on", d.role === "observer");
    document.body.classList.toggle("observer-mode", d.role === "observer");
    // operators
    const ops = $("#ent-ops");
    if (ops) {
      ops.innerHTML = (d.operators || []).map(o =>
        `<span class="ent-opchip${o.name === d.operator ? " active" : ""}" data-name="${escapeHTML(o.name)}" tabindex="0" role="button">
          ${escapeHTML(o.name.toUpperCase())} <span class="r">· ${escapeHTML((o.role || "").toUpperCase())}</span></span>`).join("")
        || '<div class="tf-empty">no operators registered</div>';
      ops.querySelectorAll(".ent-opchip").forEach(ch => ch.addEventListener("click", async () => {
        try {
          await fetch("/api/operator", { method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ name: ch.dataset.name }) });
        } catch (e) {}
        refreshEnterprise();
      }));
    }
    // audit feed
    const aud = (d.audit || {});
    const n = $("#ent-audit-n"); if (n) n.textContent = aud.entries_total ?? 0;
    const host = $("#ent-audit");
    if (host) host.innerHTML = (aud.recent || []).map(auditRow).join("")
      || '<div class="tf-empty">no audited actions yet</div>';
  } catch (e) {}
}
refreshEnterprise(); setInterval(refreshEnterprise, 45000);

async function switchRole(role) {
  const pinEl = $("#ent-pin");
  if (role === "commander" && entState.pin_required) {
    if (pinEl && pinEl.hidden) { pinEl.hidden = false; pinEl.focus(); return; }
  }
  try {
    const d = await (await fetch("/api/role", { method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ role, pin: pinEl ? pinEl.value : "" }) })).json();
    if (!d.ok && d.error) logEvent(`<span class="tag">[rbac]</span> ${escapeHTML(d.error)}`, "warn");
    if (pinEl) { pinEl.hidden = true; pinEl.value = ""; }
  } catch (e) {}
  refreshEnterprise();
}
$("#btn-role-cmd")?.addEventListener("click", () => switchRole("commander"));
$("#btn-role-obs")?.addEventListener("click", () => switchRole("observer"));
$("#ent-pin")?.addEventListener("keydown", (e) => { if (e.key === "Enter") switchRole("commander"); });

$("#ent-op-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("#ent-op-input").value.trim(); if (!name) return;
  $("#ent-op-input").value = "";
  try {
    const d = await (await fetch("/api/operators", { method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name, role: "observer" }) })).json();
    if (!d.ok && d.error) logEvent(`<span class="tag">[rbac]</span> ${escapeHTML(d.error)}`, "warn");
  } catch (err) {}
  refreshEnterprise();
});

$("#btn-export")?.addEventListener("click", async () => {
  try {
    const d = await (await fetch("/api/export")).json();
    if (d.error) { logEvent(`<span class="tag">[export]</span> ${escapeHTML(d.error)}`, "warn"); return; }
    const blob = new Blob([JSON.stringify(d, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `bobbiey-ucs-export-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
    logEvent('<span class="tag">[export]</span> compliance data export downloaded');
  } catch (e) {}
});

/* ═══ EXTERNAL HARDWARE — power · storage · network · peripherals ═══ */

const CLS_ICON = { Camera: "◉", Bluetooth: "❋", WPD: "▤", AudioEndpoint: "♪",
                   DiskDrive: "▤", Monitor: "▢", Image: "◈" };

async function refreshHardware() {
  try {
    const d = await (await fetch("/api/hardware")).json();
    // power
    const pw = $("#hw-power");
    if (pw) {
      const p = d.power || {};
      if (p.present) {
        const lvl = p.percent < 20 ? "low" : p.percent < 50 ? "mid" : "";
        pw.innerHTML = `<span class="hw-batt"><div class="${lvl}" style="width:${p.percent}%"></div></span>
          <span class="hw-pct">${p.percent}%</span>
          <span class="hw-ac ${p.plugged ? "on" : ""}">${p.plugged ? "⚡ CHARGING" : "ON BATTERY"}</span>`;
      } else {
        pw.innerHTML = `<span class="hw-pct" style="min-width:auto">AC</span>
          <span class="hw-ac on">⚡ MAINS POWER · NO BATTERY</span>`;
      }
    }
    // storage volumes
    const vh = $("#hw-vols");
    if (vh) {
      const vols = d.volumes || [];
      vh.innerHTML = vols.length ? vols.map(v => `
        <div class="hw-vol">
          <span class="nm">${escapeHTML(v.name)}</span>
          <span class="bar"><div class="${v.used_pct >= 90 ? "hot" : ""}" style="width:${v.used_pct}%"></div></span>
          <span class="pc">${v.used_pct}% · ${v.total_gb}G</span>
          ${v.removable ? '<span class="tag">USB</span>' : `<span class="pc" style="text-align:left">${escapeHTML(v.fstype)}</span>`}
        </div>`).join("") : '<div class="tf-empty">no volumes detected</div>';
    }
    // network interfaces
    const nh = $("#hw-nets");
    if (nh) {
      const nets = d.net || [];
      const nn = $("#hw-net-n"); if (nn) nn.textContent = nets.length;
      nh.innerHTML = nets.map(n =>
        `<span class="hw-net"><b>${escapeHTML(n.name)}</b>${n.speed_mbps ? " · " + n.speed_mbps + "M" : ""}${n.addr ? " · " + escapeHTML(n.addr) : ""}</span>`).join("");
    }
    // peripherals
    const ph = $("#periph-list");
    if (ph) {
      const per = d.peripherals || [];
      const pn = $("#periph-n"); if (pn) pn.textContent = per.length || "0";
      ph.innerHTML = per.length ? per.map(x => `
        <div class="periph-item">
          <span class="nm">${escapeHTML(CLS_ICON[x.cls] || "▪")} ${escapeHTML(x.name)}</span>
          <span class="cls">${escapeHTML(x.cls.toUpperCase())}</span>
          <span class="st ${x.ok ? "" : "err"}"></span>
        </div>`).join("")
        : '<div class="tf-empty">device enumeration unavailable on this OS</div>';
    }
  } catch (e) {}
}
refreshHardware(); setInterval(refreshHardware, 20000);

/* ═══ PHASE 4 · COMMAND FLEET — multi-site node telemetry ═══ */

function nodeBar(label, pct) {
  const cls = pct >= 90 ? "hot" : pct >= 70 ? "warm" : "";
  return `<div class="nb"><span class="nb-l">${label}</span>
    <span class="nb-bar"><div class="${cls}" style="width:${Math.min(100, pct || 0)}%"></div></span>
    <span class="nb-v">${pct == null ? "—" : Math.round(pct) + "%"}</span></div>`;
}
function humanUptime(min) {
  if (min == null) return "—";
  if (min < 60) return min + "m";
  const h = Math.floor(min / 60);
  return h < 24 ? `${h}h` : `${Math.floor(h / 24)}d ${h % 24}h`;
}
function humanSeen(sec) {
  if (sec == null) return "—";
  if (sec < 60) return sec + "s ago";
  if (sec < 3600) return Math.floor(sec / 60) + "m ago";
  return Math.floor(sec / 3600) + "h ago";
}

// plot the live fleet on the world map (real projected coordinates)
function renderFleetOnMap(nodes) {
  const host = document.getElementById("map-fleet");
  if (!host || typeof projectCity !== "function") return;
  host.innerHTML = "";
  // command-link lines: host node → every other node (a live command network)
  const local = nodes.find(n => n.is_local && n.lat != null);
  if (local) {
    const lxy = projectCity({ lat: local.lat, lon: local.lon });
    if (isFinite(lxy[0])) {
      for (const n of nodes) {
        if (n.is_local || n.lat == null) continue;
        const xy = projectCity({ lat: n.lat, lon: n.lon });
        if (!isFinite(xy[0])) continue;
        const mx = (lxy[0] + xy[0]) / 2, my = Math.min(lxy[1], xy[1]) - 24;
        const col = n.status === "online" ? "#22c55e" : "#7e96b3";
        const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
        p.setAttribute("d", `M ${lxy[0]} ${lxy[1]} Q ${mx} ${my} ${xy[0]} ${xy[1]}`);
        p.setAttribute("fill", "none");
        p.setAttribute("stroke", col);
        p.setAttribute("stroke-width", "0.8");
        p.setAttribute("stroke-dasharray", "3 4");
        p.setAttribute("opacity", "0.4");
        if (n.status === "online")
          p.innerHTML = '<animate attributeName="stroke-dashoffset" values="14;0" dur="1.2s" repeatCount="indefinite"/>';
        host.appendChild(p);
      }
    }
  }
  for (const n of nodes) {
    if (n.lat == null || n.lon == null) continue;
    const xy = projectCity({ lat: n.lat, lon: n.lon });
    const x = xy[0], y = xy[1];
    if (!isFinite(x) || !isFinite(y)) continue;
    const col = n.status === "online" ? "#22c55e" : n.status === "stale" ? "#facc15" : "#7e96b3";
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("class", "fleet-marker" + (n.is_local ? " local" : ""));
    g.innerHTML = `
      <circle cx="${x}" cy="${y}" r="5" fill="none" stroke="${col}" stroke-width="1" opacity="0.5">
        ${n.status === "online" ? '<animate attributeName="r" values="4;10;4" dur="2.5s" repeatCount="indefinite"/><animate attributeName="opacity" values="0.6;0;0.6" dur="2.5s" repeatCount="indefinite"/>' : ""}
      </circle>
      <circle cx="${x}" cy="${y}" r="${n.is_local ? 4 : 3}" fill="${col}"/>
      <text x="${x + 8}" y="${y + 3}" fill="${col}" font-family="Orbitron" font-size="8" opacity="0.9">${(n.name || "").slice(0, 14).toUpperCase()}</text>`;
    host.appendChild(g);
  }
}

async function refreshFleet() {
  try {
    const d = await (await fetch("/api/fleet")).json();
    try { renderFleetOnMap(d.nodes || []); } catch (e) {}
    const agg = d.aggregate || {};
    const stat = $("#fleet-stat");
    if (stat) stat.textContent = `${agg.online}/${agg.nodes} ONLINE`;
    // the world-map badge reflects the REAL fleet, not a placeholder count
    const mapNodes = $("#map-nodes");
    if (mapNodes) mapNodes.textContent = `${agg.nodes} NODE${agg.nodes === 1 ? "" : "S"}`;
    const aggEl = $("#fleet-agg");
    if (aggEl) aggEl.innerHTML = [
      ["NODES", agg.nodes], ["ONLINE", agg.online],
      ["TOTAL CORES", agg.cpu_cores], ["TOTAL RAM", (agg.mem_total_gb || 0) + "G"],
      ["FLEET CPU", (agg.avg_cpu || 0) + "%"],
    ].map(([k, v]) => `<span class="chip">${k} <b>${v}</b></span>`).join("");
    const host = $("#fleet-nodes");
    if (host) {
      const nodes = d.nodes || [];
      host.innerHTML = nodes.length ? nodes.map((n, i) => `
        <div class="node-card ${n.status} ${n.is_local ? "local" : ""}" data-idx="${i}">
          <div class="node-head">
            <span class="node-dot ${n.status}"></span>
            <span class="node-name">${escapeHTML(n.name || "node")}${n.is_local ? '<span class="node-local">LOCAL</span>' : ""}</span>
            <span class="node-plat">${escapeHTML(n.platform || "")}</span>
          </div>
          <div class="node-bars">
            ${nodeBar("CPU", n.cpu)}${nodeBar("MEM", n.mem)}${nodeBar("DISK", n.disk)}
          </div>
          <div class="node-foot">${n.cpu_cores || "?"} cores · ${n.mem_total_gb || "?"}G RAM · GPU ${n.gpu == null ? "N/A" : Math.round(n.gpu) + "%"} · up ${humanUptime(n.uptime_min)} · ${n.status === "online" ? humanSeen(n.last_seen_sec) : "<b style='color:var(--text-dim)'>" + n.status.toUpperCase() + "</b>"}</div>
        </div>`).join("") : '<div class="tf-empty">no nodes reporting</div>';
      // click a node → full hardware detail
      host.querySelectorAll(".node-card").forEach(c => c.addEventListener("click", () => {
        const n = nodes[+c.dataset.idx]; if (!n) return;
        const vols = (n.volumes || []).map(v => `${v.name} ${v.used_pct}% of ${v.total_gb}G`).join(" · ") || "—";
        const per = (n.peripherals || []).map(p => p.name).join(", ") || "none reported";
        const nets = (n.net || []).map(x => `${x.name}${x.addr ? " " + x.addr : ""}`).join(" · ") || "—";
        openCustomModal(n.name + (n.is_local ? " · LOCAL HOST" : ""),
          `${n.platform} · ${n.status.toUpperCase()}`, null,
          `<div class="mb-grid">
            <div class="mb-stat"><span class="lbl">CPU</span><span class="val">${n.cpu}% · ${n.cpu_cores} cores</span></div>
            <div class="mb-stat"><span class="lbl">MEMORY</span><span class="val">${n.mem}% of ${n.mem_total_gb}G</span></div>
            <div class="mb-stat"><span class="lbl">GPU</span><span class="val">${n.gpu == null ? "N/A" : Math.round(n.gpu) + "%"}</span></div>
            <div class="mb-stat"><span class="lbl">UPTIME</span><span class="val">${humanUptime(n.uptime_min)}</span></div>
          </div>
          <div class="mb-list"><div class="mb-row"><b>Storage:</b> ${escapeHTML(vols)}</div>
          <div class="mb-row"><b>Network:</b> ${escapeHTML(nets)}</div>
          <div class="mb-row"><b>Peripherals:</b> ${escapeHTML(per)}</div></div>`);
      }));
    }
  } catch (e) {}
}
refreshFleet(); setInterval(refreshFleet, 8000);

// join instructions (token only fetches successfully on the host machine)
async function renderFleetJoin() {
  const body = $("#fleet-add-body"); if (!body) return;
  let code = "------";
  try {
    const p = await (await fetch("/api/fleet/paircode")).json();
    if (p.code) code = p.code;
  } catch (e) {}
  const origin = location.origin.includes("127.0.0.1") || location.origin.includes("localhost")
    ? "http://<THIS-LAPTOP-LAN-IP>:8765" : location.origin;
  const macCmd = `./join-fleet.sh ${origin}`;
  const winCmd = `join-fleet.cmd ${origin}`;
  body.innerHTML = `
    <div class="pair-code-box">
      <div class="pair-code-label">ACCESS CODE</div>
      <div class="pair-code" id="pair-code" title="click to copy">${escapeHTML(code)}</div>
      <button class="cmd-btn" id="btn-rotate-code" title="generate a new code">↻ ROTATE</button>
    </div>
    <b>1.</b> On the other laptop: <b>git clone</b> this repo, then <code>cd</code> into it.<br>
    <b>2.</b> Run ONE command — it auto-installs what it needs and asks for the code above:
    <div class="fleet-cmd" data-cmd="mac" title="click to copy">macOS / Linux&nbsp;&nbsp;${escapeHTML(macCmd)}</div>
    <div class="fleet-cmd" data-cmd="win" title="click to copy">Windows&nbsp;&nbsp;${escapeHTML(winCmd)}</div>
    <b>3.</b> Type the 6-character code. The node appears here within seconds.
    ${origin.includes("LAN-IP") ? "<br><br>Replace <b>&lt;THIS-LAPTOP-LAN-IP&gt;</b> with this machine's Wi-Fi IP (run <b>ipconfig</b>); the server already binds <b>0.0.0.0</b> when JARVIS_HOST is set in .env." : ""}
    <br><br><span style="opacity:0.7">Both machines must be on the same network. On office Wi-Fi that blocks device-to-device traffic, use a phone hotspot for both.</span>`;
  const cp = (txt, msg) => navigator.clipboard?.writeText(txt).then(() =>
    logEvent(`<span class="tag">[fleet]</span> ${msg}`)).catch(() => {});
  body.querySelector("#pair-code")?.addEventListener("click", () => cp(code, "access code copied"));
  body.querySelectorAll(".fleet-cmd").forEach(el => el.addEventListener("click", () =>
    cp(el.dataset.cmd === "win" ? winCmd : macCmd, "join command copied")));
  body.querySelector("#btn-rotate-code")?.addEventListener("click", async () => {
    try {
      const r = await (await fetch("/api/fleet/paircode/rotate", { method: "POST" })).json();
      if (r.ok) { logEvent('<span class="tag">[fleet]</span> access code rotated — old code now invalid'); renderFleetJoin(); }
      else if (r.error) logEvent(`<span class="tag">[fleet]</span> ${escapeHTML(r.error)}`, "warn");
    } catch (e) {}
  });
}
renderFleetJoin();

/* ═══ PHASE 5 · DECISION SUPPORT — propose · simulate · execute ═══ */

let decAutonomy = false;

async function refreshDecisions() {
  try {
    const d = await (await fetch("/api/decisions")).json();
    decAutonomy = !!d.autonomy;
    const btn = $("#btn-autonomy");
    if (btn) {
      btn.textContent = decAutonomy ? "◈ AUTONOMY ON" : "◇ AUTONOMY OFF";
      btn.classList.toggle("on", decAutonomy);
    }
    const note = $("#dec-auto-note");
    if (note) note.textContent = decAutonomy
      ? `auto-executes: ${(d.whitelist || []).join(", ")} · every action audited`
      : "low-risk whitelist only · every action audited";
    const cnt = $("#dec-count"); if (cnt) cnt.textContent = (d.proposals || []).length;
    const list = $("#dec-list");
    if (list) {
      list.innerHTML = (d.proposals || []).length ? d.proposals.map(p => `
        <div class="dec-item ${p.risk}">
          <div class="dec-title">${escapeHTML(p.title)}</div>
          <div class="dec-why">${escapeHTML(p.rationale || "")}</div>
          <div class="dec-impact">${escapeHTML(p.impact || "")}</div>
          <div class="dec-actions">
            <button class="dec-btn ok" data-id="${escapeHTML(p.id)}" data-act="execute">✓ APPROVE</button>
            <button class="dec-btn no" data-id="${escapeHTML(p.id)}" data-act="dismiss">✕ DISMISS</button>
          </div>
        </div>`).join("")
        : '<div class="tf-empty">no proposals — conditions don\'t warrant action</div>';
      list.querySelectorAll(".dec-btn").forEach(b => b.addEventListener("click", async () => {
        b.disabled = true; b.textContent = "…";
        try {
          const r = await (await fetch("/api/decisions/" + b.dataset.act, {
            method: "POST", headers: { "content-type": "application/json" },
            body: JSON.stringify({ id: b.dataset.id }) })).json();
          if (!r.ok && r.error) logEvent(`<span class="tag">[decision]</span> ${escapeHTML(r.error)}`, "warn");
          else if (r.note) logEvent(`<span class="tag">[decision]</span> executed — ${escapeHTML(r.note)}`);
        } catch (e) {}
        refreshDecisions(); refreshProd(); seedThreats();
      }));
    }
    const en = $("#dec-exec-n"); if (en) en.textContent = d.executed_total ?? 0;
    const done = $("#dec-done");
    if (done) {
      done.innerHTML = (d.executed || []).length ? d.executed.slice(0, 4).map(x => `
        <div class="dec-done-item"><b>✓</b> ${escapeHTML(x.title)} — ${escapeHTML(x.note || "")}
          <span class="by">${x.by === "autonomy" ? "AUTO" : "APPROVED"}</span></div>`).join("")
        : '<div class="tf-empty">nothing executed yet</div>';
    }
  } catch (e) {}
}
refreshDecisions(); setInterval(refreshDecisions, 30000);

$("#btn-autonomy")?.addEventListener("click", async () => {
  try {
    const r = await (await fetch("/api/decisions/autonomy", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ on: !decAutonomy }) })).json();
    if (!r.ok && r.error) logEvent(`<span class="tag">[decision]</span> ${escapeHTML(r.error)}`, "warn");
  } catch (e) {}
  refreshDecisions();
});

/* ═══ PHASE 4 · REMOTE ACCESS & AUTH — the perimeter panel ═══ */

async function refreshSecurity() {
  try {
    const [st, panel] = await Promise.all([
      (await fetch("/api/auth/status")).json(),
      (await fetch("/api/auth/panel")).json(),
    ]);
    const lanExposed = st.binding !== "127.0.0.1";
    const state = $("#sec-state");
    if (state) state.textContent = lanExposed
      ? (st.password_set ? "LAN · SECURED" : "LAN · REMOTE DISABLED")
      : "LOCAL ONLY";
    const status = $("#sec-status");
    if (status) {
      status.innerHTML = [
        [`BINDING <b>${escapeHTML(st.binding)}</b>`, lanExposed ? "warn" : "ok"],
        [st.password_set ? "PASSWORD <b>ARMED</b>" : "PASSWORD <b>NOT SET</b>",
         st.password_set ? "ok" : (lanExposed ? "warn" : "")],
        [`SESSIONS <b>${(panel.sessions || []).length}</b>`, ""],
        [`TOKENS <b>${(panel.tokens || []).length}</b>`, ""],
        [lanExposed && !st.password_set
          ? "REMOTE <b>BLOCKED UNTIL ARMED</b>" : `REMOTE <b>${lanExposed ? "LOGIN REQUIRED" : "OFF"}</b>`,
         lanExposed && !st.password_set ? "err" : "ok"],
      ].map(([t, c]) => `<span class="sec-chip ${c}">${t}</span>`).join("");
    }
    const tn = $("#sec-tok-n"); if (tn) tn.textContent = (panel.tokens || []).length;
    const toks = $("#sec-tokens");
    if (toks) {
      toks.innerHTML = (panel.tokens || []).map(t => `
        <div class="sec-row">
          <span class="nm">🔑 ${escapeHTML(t.label)}</span>
          <span class="meta">${escapeHTML(t.prefix)}…</span>
          <button class="sec-revoke" data-prefix="${escapeHTML(t.prefix)}" title="revoke token">✕</button>
        </div>`).join("");
      toks.querySelectorAll(".sec-revoke").forEach(b => b.addEventListener("click", async () => {
        await fetch("/api/auth/revoke", { method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ prefix: b.dataset.prefix }) }).catch(() => {});
        refreshSecurity();
      }));
    }
    const sn = $("#sec-ses-n"); if (sn) sn.textContent = (panel.sessions || []).length;
    const ses = $("#sec-sessions");
    if (ses) {
      ses.innerHTML = (panel.sessions || []).length ? panel.sessions.map(s => `
        <div class="sec-row">
          <span class="nm">◉ ${escapeHTML(s.ip)}</span>
          <span class="meta">${s.age_min}m · ${escapeHTML((s.ua || "").split(" ")[0].slice(0, 18))}</span>
          <button class="sec-revoke" data-sid="${escapeHTML(s.sid)}" title="revoke session">✕</button>
        </div>`).join("") : '<div class="tf-empty">no remote sessions</div>';
      ses.querySelectorAll(".sec-revoke").forEach(b => b.addEventListener("click", async () => {
        await fetch("/api/auth/revoke", { method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ sid: b.dataset.sid }) }).catch(() => {});
        refreshSecurity();
      }));
    }
    // off-host: setting password / minting tokens is disabled by the server
    const onHost = panel.on_host !== false;
    ["#sec-pw", "#btn-sec-pw", "#sec-tok-label"].forEach(sel => {
      const el = $(sel); if (el) el.disabled = !onHost;
    });
    if (!onHost) {
      const pw = $("#sec-pw"); if (pw) pw.placeholder = "password changes: host console only";
    }
  } catch (e) {}
}
refreshSecurity(); setInterval(refreshSecurity, 60000);

$("#sec-pw-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const pw = $("#sec-pw").value;
  if (pw.length < 8) { logEvent('<span class="tag">[auth]</span> password must be 8+ characters', "warn"); return; }
  try {
    const d = await (await fetch("/api/auth/password", { method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ password: pw }) })).json();
    if (d.ok) { $("#sec-pw").value = ""; logEvent('<span class="tag">[auth]</span> remote access armed — password set'); }
    else if (d.error) logEvent(`<span class="tag">[auth]</span> ${escapeHTML(d.error)}`, "warn");
  } catch (err) {}
  refreshSecurity();
});

$("#sec-tok-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const label = $("#sec-tok-label").value.trim() || "token";
  try {
    const d = await (await fetch("/api/auth/token", { method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ label }) })).json();
    const once = $("#sec-token-once");
    if (d.ok && d.token && once) {
      once.hidden = false;
      once.innerHTML = `${escapeHTML(d.token)}<small>COPY NOW — SHOWN ONLY ONCE · CLICK TO COPY</small>`;
      once.onclick = () => navigator.clipboard?.writeText(d.token).then(() =>
        logEvent('<span class="tag">[auth]</span> token copied to clipboard')).catch(() => {});
      $("#sec-tok-label").value = "";
    } else if (d.error) logEvent(`<span class="tag">[auth]</span> ${escapeHTML(d.error)}`, "warn");
  } catch (err) {}
  refreshSecurity();
});

connect();
