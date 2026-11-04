/* GLOBAL ACTIVITY MAP · live planetary overview — renders into #globalmap-mount.
   Data: GET /api/globalmap (+ /layer/flights, /layer/satellites) from globalmap.py,
   /api/fleet and /api/geo from main.py. Every layer names its source and fetch time;
   a feed that cannot be reached is shown as stale/unavailable, never invented.
   2D TACTICAL: canvas + d3-geo (land cached to an offscreen bitmap, markers batched).
   3D GLOBE / ORBITAL: globe.gl (lazy, pinned) with satellites at their real altitude.
   Public API: window.GlobalMap = { focus({lat, lon, id, layer}), highlight(layer) } */
(function () {
  "use strict";
  const mount = document.getElementById("globalmap-mount");
  if (!mount) return;

  const CDN = {
    d3: "https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js",
    topo: "https://cdn.jsdelivr.net/npm/topojson-client@3.1.0/dist/topojson-client.min.js",
    globe: "https://cdn.jsdelivr.net/npm/globe.gl@2.46.2/dist/globe.gl.min.js",
    sat: "https://cdn.jsdelivr.net/npm/satellite.js@6.0.1/dist/satellite.min.js",
    countries: "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-50m.json",
    land50: "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/land-50m.json",
    land: "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/land-110m.json",
    india: "/static/geo/india_world.json",
  };
  const LAYERS = [
    ["quakes", "EARTHQUAKES", "Q"], ["disasters", "GDACS ALERTS", "G"], ["events", "NATURAL EVENTS", "E"],
    ["volcanoes", "VOLCANOES", "V"], ["wxalerts", "WEATHER ALERTS", "W"], ["outages", "INTERNET OUTAGES", "N"],
    ["flights", "AIR TRAFFIC", "A"], ["satellites", "SATELLITES", "S"], ["space", "SPACE WEATHER", "K"],
    ["daynight", "DAY / NIGHT", "D"], ["fleet", "BUCS FLEET", "F"],
  ];
  const LOCAL_LAYERS = { daynight: "computed locally from solar position", fleet: "/api/fleet · this BUCS install" };
  const LAYER_COLOR = { quakes: "#ffd84d", disasters: "#ff8c42", events: "#ff6b3d", volcanoes: "#ff3b5c",
    wxalerts: "#ffb000", outages: "#b07cff", flights: "#8fe3ff", satellites: "#d6a8ff", space: "#39ff9f",
    daynight: "#6b94b4", fleet: "#00d9ff" };
  const SEV = ["#6b94b4", "#4dd2ff", "#ffd84d", "#ff8c42", "#ff3b5c"];
  const SEV_NAME = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"];
  const NEAR_KM = 500, DAY = 86400;
  const reduced = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ───────── helpers
  const $ = (s, r = mount) => r.querySelector(s);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = u => (typeof u === "string" && /^https?:\/\/[^\s"'<>]+$/.test(u) ? u : null);
  const nowS = () => Date.now() / 1000;
  function ago(ts) {
    if (!ts) return "—";
    const s = Math.max(0, nowS() - ts);
    return s < 90 ? Math.round(s) + "s ago" : s < 5400 ? Math.round(s / 60) + "m ago" : s < 172800 ? Math.round(s / 3600) + "h ago" : Math.round(s / 86400) + "d ago";
  }
  const utc = ts => (ts ? new Date(ts * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC" : "—");
  const ll = (lat, lon) => `${Math.abs(lat).toFixed(2)}°${lat >= 0 ? "N" : "S"} ${Math.abs(lon).toFixed(2)}°${lon >= 0 ? "E" : "W"}`;
  function km(a1, o1, a2, o2) {
    const r = Math.PI / 180, dp = (a2 - a1) * r, dl = (o2 - o1) * r;
    const h = Math.sin(dp / 2) ** 2 + Math.cos(a1 * r) * Math.cos(a2 * r) * Math.sin(dl / 2) ** 2;
    return 2 * 6371 * Math.asin(Math.min(1, Math.sqrt(h)));
  }
  function store(k, v) { try { if (v === undefined) return JSON.parse(localStorage.getItem("gm." + k)); localStorage.setItem("gm." + k, JSON.stringify(v)); } catch (e) { return null; } }
  const loaded = {};
  function loadScript(url) {
    if (!loaded[url]) loaded[url] = new Promise((ok, bad) => {
      const s = document.createElement("script"); s.src = url; s.async = true; s.crossOrigin = "anonymous";
      s.onload = ok; s.onerror = () => { delete loaded[url]; bad(new Error("load failed: " + url)); };
      document.head.appendChild(s);
    });
    return loaded[url];
  }
  async function getJSON(url) { const r = await fetch(url, { cache: "no-store" }); if (!r.ok) throw new Error(url + " " + r.status); return r.json(); }
  const cssVar = (n, d) => (getComputedStyle(document.body).getPropertyValue(n).trim() || d);

  // ───────── state
  const saved = store("layers") || {};
  const S = {
    mode: "2d", data: null, flights: null, sats: null, satrecs: [], satPos: [], issTrack: [], fleet: null, op: null,
    on: Object.fromEntries(LAYERS.map(([k]) => [k, saved[k] !== undefined ? !!saved[k] : true])),
    replay: null, playing: false, selected: null, hl: null, hlUntil: 0, seen: null, fresh: new Map(),
    near: false, visible: true, accent: "#00d9ff", gen: 0,
  };

  // ───────── DOM
  mount.innerHTML = `
  <h2 class="panel-title">
    <span>GLOBAL ACTIVITY MAP <span class="map-sub">· LIVE PLANETARY OVERVIEW</span></span>
    <span class="title-badge" id="gm-badge">CONNECTING…</span>
  </h2>
  <div class="gm" id="gm-root">
    <div class="gm-bar">
      <div class="gm-modes" role="tablist" aria-label="Map mode">
        <button type="button" role="tab" data-mode="2d" class="on" aria-selected="true">2D TACTICAL</button>
        <button type="button" role="tab" data-mode="3d" aria-selected="false">3D GLOBE</button>
        <button type="button" role="tab" data-mode="orbit" aria-selected="false">ORBITAL</button>
      </div>
      <div class="gm-chips">
        <button type="button" class="gm-chip" id="gm-kp" title="NOAA SWPC planetary K-index">Kp —</button>
        <span class="gm-chip gm-utc" id="gm-utc">--:--:-- UTC</span>
      </div>
      <div class="gm-actions">
        <button type="button" class="gm-btn" id="gm-near" title="Focus on your location (${NEAR_KM} km ring)">◎ NEAR ME</button>
        <button type="button" class="gm-btn" id="gm-reset" title="Reset view">⟲</button>
        <button type="button" class="gm-btn" id="gm-full" title="Full screen" aria-label="Full screen">⤢</button>
      </div>
    </div>
    <div class="gm-body">
      <div class="gm-stage" id="gm-stage">
        <canvas class="gm-cv" id="gm-cv" aria-label="World map of live events" role="img"></canvas>
        <div class="gm-globe" id="gm-globe" hidden></div>
        <div class="gm-tip" id="gm-tip" hidden></div>
        <div class="gm-card" id="gm-card" hidden></div>
        <div class="gm-msg" id="gm-msg">loading world geometry…</div>
        <div class="gm-foot" id="gm-foot"></div>
      </div>
      <aside class="gm-layers" id="gm-layers" aria-label="Map layers"></aside>
    </div>
    <div class="gm-time">
      <button type="button" class="gm-btn" id="gm-play" title="Replay the last 24 h">▶</button>
      <div class="gm-scrub"><canvas id="gm-hist" aria-hidden="true"></canvas>
        <input type="range" id="gm-range" min="-1440" max="0" step="5" value="0" aria-label="Time: last 24 hours"></div>
      <button type="button" class="gm-btn gm-live on" id="gm-live">LIVE</button>
      <span class="gm-tlabel" id="gm-tlabel">LIVE</span>
    </div>
    <div class="gm-ticker" id="gm-ticker" aria-label="Latest events"><div class="gm-ticker-track" id="gm-track"><span class="gm-tick-empty">waiting for first fetch…</span></div></div>
  </div>`;
  mount.classList.add("gm-panel");
  const root = $("#gm-root"), stage = $("#gm-stage"), cv = $("#gm-cv"), ctx = cv.getContext("2d");
  const tip = $("#gm-tip"), card = $("#gm-card"), msg = $("#gm-msg");

  // ───────── layer panel
  function renderLayers() {
    const L = (S.data && S.data.layers) || {};
    $("#gm-layers").innerHTML = LAYERS.map(([k, label]) => {
      const info = L[k];
      let st = info ? info.status : (LOCAL_LAYERS[k] ? "ok" : "pending");
      let count = info ? info.count : "";
      let sub = info ? srcLine(info) : (k === "daynight" ? "solar position · local" : "");
      if (k === "fleet") {
        const f = S.fleet;
        st = f ? "ok" : "pending";
        count = f ? f.nodes.length : "";
        sub = f ? `${f.online} online · ${esc(f.city || "site")} (IP geo)` : "/api/fleet";
      }
      if (k === "flights" && !S.on.flights) sub = "off · no OpenSky calls";
      return `<button type="button" class="gm-lyr ${S.on[k] ? "on" : ""} st-${st} ${S.hl === k ? "hl" : ""}" data-l="${k}" aria-pressed="${S.on[k]}"
        title="${esc(info ? info.sources.join(", ") + " · " + st + (info.fetched_at ? " · fetched " + utc(info.fetched_at) : "") : (LOCAL_LAYERS[k] || ""))}">
        <i style="--c:${LAYER_COLOR[k]}"></i><span class="gm-ln">${label}</span><b>${count === "" ? "" : Number(count).toLocaleString()}</b>
        <span class="gm-st" aria-label="${st}"></span><small>${sub}</small></button>`;
    }).join("");
  }
  function srcLine(info) {
    const names = { usgs: "USGS", gdacs: "GDACS", eonet: "NASA EONET", gvp: "Smithsonian GVP", nws: "NWS", sachet: "NDMA SACHET",
      ioda: "IODA", swpc: "NOAA SWPC", ovation: "OVATION", opensky: "OpenSky", celestrak: "CelesTrak" };
    const prov = k => { const m = ((S.data && S.data.sources) || []).find(x => x.key === k); return m && m.meta && m.meta.provider; };
    const s = info.sources.map(k => (k === "celestrak" && /AMSAT/.test(prov(k) || "") ? "AMSAT (CelesTrak down)" : names[k] || k)).join(" + ");
    if (info.status === "pending") return s + " · fetching…";
    if (info.status === "unavailable") return s + " · unavailable";
    if (info.status === "limited") return s + " · rate-limited";
    return `${s} · ${ago(info.fetched_at)}${info.status === "stale" ? " · STALE" : info.degraded ? " · partial" : ""}`;
  }
  $("#gm-layers").addEventListener("click", e => {
    const b = e.target.closest(".gm-lyr"); if (!b) return;
    const k = b.dataset.l; S.on[k] = !S.on[k];
    store("layers", S.on);
    if (k === "flights" && S.on.flights) pollFlights();
    if (k === "satellites" && S.on.satellites && !S.sats) pollSats();
    renderLayers(); invalidate(true); if (G) syncGlobe();
  });

  // ───────── data
  function items() {
    const all = (S.data && S.data.items) || [];
    const T = S.replay == null ? null : nowS() + S.replay * 60;
    return all.filter(i => S.on[i.layer] && (T == null || !i.time || i.time <= T));
  }
  async function pollSummary() {
    if (document.hidden && S.data) return;
    try {
      const d = await getJSON("/api/globalmap");
      const ids = new Set(d.items.map(i => i.id));
      if (S.seen) for (const id of ids) if (!S.seen.has(id)) S.fresh.set(id, nowS());
      S.seen = ids;
      for (const [id, t] of S.fresh) if (nowS() - t > 900) S.fresh.delete(id);
      S.data = d;
      for (const i of d.items) {
        projItem(i);
        // GeoJSON (RFC 7946) rings are counter-clockwise; d3-geo reads those as the
        // complement (the whole world minus the polygon), so rewind when needed
        if (i.poly && typeof d3 !== "undefined") i.poly = i.poly.map(r => (d3.geoArea({ type: "Polygon", coordinates: [r] }) > 2 * Math.PI ? r.slice().reverse() : r));
      }
      prepAurora();
      renderLayers(); renderKp(); renderTicker(); renderHist(); renderBadge(); invalidate(true);
      if (G) syncGlobe();
      const pend = Object.values(d.layers).some(l => l.status === "pending");
      if (pend) setTimeout(pollSummary, 5000);
    } catch (e) {
      $("#gm-badge").textContent = "BACKEND OFFLINE";
    }
  }
  async function pollFlights() {
    if (!S.on.flights || (document.hidden && S.flights)) return;
    try {
      const d = await getJSON("/api/globalmap/layer/flights");
      if (!S.flights || !d.meta || !S.flights.meta || d.meta.time !== S.flights.meta.time) {
        S.flights = d; S.flightsAt = d.meta && d.meta.time;
        S.fl = { n: d.rows.length, lat: new Float32Array(d.rows.length), lon: new Float32Array(d.rows.length),
          x: new Float32Array(d.rows.length), y: new Float32Array(d.rows.length) };
        updateFlights(true);
      } else S.flights = Object.assign(S.flights, { status: d.status, source: d.source, gate: d.gate, count: d.count });
      if (d.status === "pending") setTimeout(pollFlights, 6000);
      renderLayers(); renderTicker();
    } catch (e) { /* summary shows the status */ }
  }
  async function pollSats() {
    if (!S.on.satellites) return;
    try {
      const d = await getJSON("/api/globalmap/layer/satellites");
      if (d.status === "pending") setTimeout(pollSats, 6000);
      if (!d.sats || !d.sats.length) { S.sats = S.sats || null; return; }
      await loadScript(CDN.sat);
      S.sats = d;
      S.satrecs = d.sats.map(s => { try { return { s, rec: satellite.twoline2satrec(s.l1, s.l2) }; } catch (e) { return null; } }).filter(Boolean);
      updateSats(true);
      renderLayers();
    } catch (e) { S.satErr = String(e.message || e); }
  }
  async function pollFleet() {
    try {
      const f = await getJSON("/api/fleet");
      const site = f.site || {};
      if (site.lat == null) return;
      const nodes = f.nodes || [];
      S.fleet = { nodes, online: nodes.filter(n => n.status === "online").length, lat: site.lat, lon: site.lon,
        city: [site.city, site.country].filter(Boolean).join(", ") };
      S.fleet.p = proj ? proj([site.lon, site.lat]) : null;
      renderLayers(); invalidate();
    } catch (e) { /* fleet optional */ }
  }
  async function locate() {
    let lat = null, lon = null, how = "";
    try { if (typeof userLat !== "undefined" && userLat != null && typeof userLon !== "undefined") { lat = userLat; lon = userLon; how = "dashboard location"; } } catch (e) { /* not exposed */ }
    if (lat == null) {
      try { const g = await getJSON("/api/geo"); if (g && g.lat != null) { lat = g.lat; lon = g.lon; how = g.source === "ip-geo" ? "public-IP geolocation" : "configured location"; } } catch (e) { /* none */ }
    }
    if (lat == null) return;
    if (!S.op || S.op.lat !== lat || S.op.lon !== lon) {
      S.op = { lat, lon, how, p: proj ? proj([lon, lat]) : null };
      invalidate(true); if (G) syncGlobe();
    }
  }

  // ───────── space weather chip
  function renderKp() {
    const sp = S.data && S.data.space, chip = $("#gm-kp");
    const st = S.data && S.data.layers.space;
    if (!sp || sp.kp == null) { chip.textContent = st && st.status === "unavailable" ? "Kp · SWPC unavailable" : "Kp —"; chip.className = "gm-chip"; return; }
    const g = sp.scales && sp.scales.G != null ? ` · G${sp.scales.G}` : "";
    chip.innerHTML = `<i></i>Kp ${sp.kp.toFixed(1)} · ${esc(sp.kp_label.toUpperCase())}${g}`;
    chip.className = "gm-chip kp " + (sp.kp >= 7 ? "sev4" : sp.kp >= 5 ? "sev3" : sp.kp >= 4 ? "sev2" : "sev1");
    const w = sp.wind;
    chip.title = `NOAA SWPC · Kp ${sp.kp} (${utc(sp.kp_time)})` + (w ? ` · solar wind ${w.speed_kms ?? "—"} km/s, Bt ${w.bt_nt ?? "—"} nT, Bz ${w.bz_nt ?? "—"} nT` : "");
  }
  function renderBadge() {
    const L = Object.values(S.data.layers);
    const live = L.filter(l => l.status === "ok").length;
    const b = $("#gm-badge");
    b.textContent = `${live}/${L.length} FEEDS LIVE`;
    b.title = L.map(l => `${l.label}: ${l.status}`).join("\n");
  }

  // ───────── ticker
  function tickerRows() {
    const rows = ((S.data && S.data.items) || []).filter(i => i.time && nowS() - i.time < DAY && (i.severity >= 2 || i.layer === "quakes"))
      .map(i => ({ t: i.time, sev: i.severity, text: i.title, layer: i.layer, id: i.id }));
    for (const a of ((S.data && S.data.space && S.data.space.alerts) || []).slice(0, 4))
      rows.push({ t: a.time, sev: a.severity, text: `SWPC ${a.kind}: ${a.title}`, layer: "space", id: a.id });
    for (const e of ((S.flights && S.flights.meta && S.flights.meta.emergencies) || []))
      rows.push({ t: S.flights.meta.time, sev: 3, text: `Squawk ${e.squawk} (${e.meaning}) reported by ${e.callsign || e.icao24}`, layer: "flights", lat: e.lat, lon: e.lon });
    const seen = new Set();
    return rows.sort((a, b) => b.t - a.t).filter(r => !seen.has(r.text) && seen.add(r.text)).slice(0, 28);
  }
  function renderTicker() {
    const rows = tickerRows(), tr = $("#gm-track");
    if (!rows.length) { tr.innerHTML = `<span class="gm-tick-empty">no events in the last 24 h from the active feeds</span>`; return; }
    const html = rows.map((r, n) => `<button type="button" class="gm-tick sev${r.sev}" data-n="${n}"><i style="--c:${LAYER_COLOR[r.layer]}"></i>
      <em>${new Date(r.t * 1000).toISOString().slice(11, 16)}Z</em>${esc(r.text)}</button>`).join("");
    tr.innerHTML = html + (reduced ? "" : `<span class="gm-dup" aria-hidden="true">${html}</span>`);
    tr.style.animationDuration = Math.max(40, rows.length * 6) + "s";
    tr._rows = rows;
  }
  $("#gm-track").addEventListener("click", e => {
    const b = e.target.closest(".gm-tick"); if (!b) return;
    const r = $("#gm-track")._rows[+b.dataset.n]; if (!r) return;
    if (r.layer === "space") return showSpaceCard();
    api.focus({ id: r.id, lat: r.lat, lon: r.lon, layer: r.layer });
  });

  // ───────── 2D projection / land
  let W = 0, H = 0, DPR = 1, proj = null, gpath = null, T = { k: 1, x: 0, y: 0 }, zoomer = null;
  let ovlT = null;
  let india = null, landBmp = null, landT = null, ovlBmp = null, ovlKey = "";
  let night = null, nightAt = 0;
  const grat = () => d3.geoGraticule10();

  // Geometry is projected ONCE into a fixed reference projection and kept as Path2D;
  // every redraw (resize, zoom, pan) is then a plain affine transform - no d3 work.
  const REF_SCALE = 1000;
  let refProj = null, refPath = null;
  const GEO = {};
  function P2(geo) { const d = geo && refPath(geo); return d ? new Path2D(d) : null; }
  async function initGeo() {
    if (typeof d3 === "undefined" || !d3.geoNaturalEarth1) await loadScript(CDN.d3);
    if (typeof topojson === "undefined") await loadScript(CDN.topo);
    refProj = d3.geoNaturalEarth1().scale(REF_SCALE).translate([0, 0]).precision(0.3);
    refPath = d3.geoPath(refProj);
    GEO.sphere = P2({ type: "Sphere" }); GEO.grat = P2(d3.geoGraticule10());
    const [l, ind] = await Promise.all([getJSON(CDN.land), getJSON(CDN.india).catch(() => null)]);
    india = ind;
    GEO.land = P2(topojson.feature(l, l.objects.land)); GEO.india = P2(india);
    msg.textContent = "";
    setTimeout(hiRes, 1500);             // sharper 1:50m coast + borders once the page is idle
  }
  async function hiRes() {
    try {
      const [w, l] = await Promise.all([getJSON(CDN.countries), getJSON(CDN.land50)]);
      const feats = topojson.feature(w, w.objects.countries).features;
      S.countryFeat = new Map(feats.map(f => [String(f.id), f]));
      if (!india) { india = feats.find(f => String(f.id) === "356"); GEO.india = P2(india); }
      const land = P2(topojson.feature(l, l.objects.land));
      const bp = new Path2D();
      for (const f of feats) { if (String(f.id) === "356") continue; const d = refPath(f); if (d) bp.addPath(new Path2D(d)); }
      GEO.land = land; GEO.borders = bp;
      landBmp = null; invalidate(true);
    } catch (e) { /* 1:110m land stays */ }
  }
  function withRef(c) {
    const s = proj.scale() / REF_SCALE, t = proj.translate(), k = T.k * s;
    c.setTransform(DPR * k, 0, 0, DPR * k, DPR * (T.k * t[0] + T.x), DPR * (T.k * t[1] + T.y));
    return k;
  }

  // The canvas never takes part in layout (absolute, 100% x 100%); its backing store is
  // sized from the stage's clientWidth/clientHeight, clamped, and debounced, so the map
  // can never feed its own size back into the dashboard grid.
  const MAX_W = 3840, MAX_H = 2160;
  let resizeT = 0;
  function scheduleResize() { clearTimeout(resizeT); resizeT = setTimeout(resize, 120); }
  function resize() {
    const cw = Math.min(MAX_W, Math.max(0, Math.floor(stage.clientWidth)));
    const ch = Math.min(MAX_H, Math.max(0, Math.floor(stage.clientHeight)));
    if (!cw || !ch) return;
    DPR = Math.min(2, window.devicePixelRatio || 1);
    if (cw === W && ch === H && cv.width === Math.round(W * DPR)) return;
    W = cw; H = ch;
    cv.width = Math.round(W * DPR); cv.height = Math.round(H * DPR);
    if (typeof d3 === "undefined" || !d3.geoNaturalEarth1) return;
    const band = { type: "MultiPoint", coordinates: [] };
    for (let lon = -180; lon <= 180; lon += 5) band.coordinates.push([lon, -58], [lon, 78]);
    proj = d3.geoNaturalEarth1().fitExtent([[6, 6], [W - 6, H - 6]], band);
    gpath = d3.geoPath(proj);
    if (!zoomer) {
      zoomer = d3.zoom().scaleExtent([1, 24]).on("zoom", e => { T = e.transform; hideTip(); invalidate(); scheduleCrisp(); })
        .filter(e => (!e.ctrlKey || e.type === "wheel") && !e.button);
      d3.select(cv).call(zoomer).on("dblclick.zoom", null);
    }
    zoomer.translateExtent([[-W * 0.1, -H * 0.1], [W * 1.1, H * 1.1]]).extent([[0, 0], [W, H]]);
    reproject(); landBmp = null; S.gen++; invalidate(true);
    if (G) G.width(W).height(H);
  }
  function reproject() {
    if (!proj) return;
    for (const i of ((S.data && S.data.items) || [])) projItem(i);
    if (S.op) S.op.p = proj([S.op.lon, S.op.lat]);
    if (S.fleet) S.fleet.p = proj([S.fleet.lon, S.fleet.lat]);
    updateFlights(true); updateSats(true); prepAurora();
  }
  function projItem(i) { i._p = proj ? proj([i.lon, i.lat]) : null; }
  let crispTimer = 0;
  function scheduleCrisp() { clearTimeout(crispTimer); crispTimer = setTimeout(() => { landBmp = null; S.gen++; invalidate(); }, 160); }

  function withT(c, t) { c.setTransform(DPR * t.k, 0, 0, DPR * t.k, DPR * t.x, DPR * t.y); }
  function buildLand() {
    const b = landBmp || document.createElement("canvas");
    b.width = cv.width; b.height = cv.height;
    const c = b.getContext("2d");
    c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, b.width, b.height);
    if (GEO.sphere) {
      const k = withRef(c);
      c.fillStyle = "rgba(3,14,26,0.85)"; c.fill(GEO.sphere);
      c.lineWidth = 0.5 / k; c.strokeStyle = "rgba(0,217,255,0.08)"; c.stroke(GEO.grat);
      if (GEO.land) {
        c.fillStyle = "#0f2d44"; c.fill(GEO.land);
        if (GEO.borders) { c.lineWidth = 0.45 / k; c.strokeStyle = "rgba(90,170,220,0.32)"; c.stroke(GEO.borders); }
        if (GEO.india) {        // official boundary, drawn opaque over any neighbouring line
          c.fillStyle = "#0f2d44"; c.fill(GEO.india);
          c.lineWidth = 0.9 / k; c.strokeStyle = "rgba(0,217,255,0.55)"; c.stroke(GEO.india);
        }
        c.lineWidth = 0.7 / k; c.strokeStyle = "rgba(0,217,255,0.45)"; c.stroke(GEO.land);
      }
      c.lineWidth = 1 / k; c.strokeStyle = "rgba(0,217,255,0.35)"; c.stroke(GEO.sphere);
    }
    landBmp = b; landT = { k: T.k, x: T.x, y: T.y };
  }

  // solar position → night hemisphere (real, computed locally)
  function subsolar(t) {
    const d = new Date(t * 1000), jd = t / 86400 + 2440587.5, n = jd - 2451545.0;
    const L = (280.46 + 0.9856474 * n) % 360, g = ((357.528 + 0.9856003 * n) % 360) * Math.PI / 180;
    const lam = (L + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * Math.PI / 180, eps = (23.439 - 4e-7 * n) * Math.PI / 180;
    const dec = Math.asin(Math.sin(eps) * Math.sin(lam));
    const ra = Math.atan2(Math.cos(eps) * Math.sin(lam), Math.cos(lam));
    const gmst = (280.46061837 + 360.98564736629 * n) % 360;
    let lon = (ra * 180 / Math.PI - gmst) % 360; if (lon < -180) lon += 360; if (lon > 180) lon -= 360;
    return [lon, dec * 180 / Math.PI, d];
  }
  function nightShape(t) {
    const [lon, lat] = subsolar(t);
    return d3.geoCircle().center([lon + 180, -lat]).radius(90)();
  }

  function prepAurora() {
    const a = S.data && S.data.aurora;
    S.aur = null;
    if (!a || !proj) return;
    S.aur = { cells: a.cells.map(c => { const p = proj([c[0], c[1]]); return p ? [p[0], p[1], c[2]] : null; }).filter(Boolean),
      north: a.north, south: a.south };
  }

  function buildOverlay(t) {
    const key = [S.gen, W, H, Math.floor(t / 60), S.on.daynight, S.on.space, S.on.wxalerts, S.on.outages,
      S.data && S.data.generated, S.hl].join("|");
    if (key === ovlKey && ovlBmp) return false;
    ovlKey = key;
    const b = ovlBmp || document.createElement("canvas");
    b.width = cv.width; b.height = cv.height;
    const c = b.getContext("2d");
    c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, b.width, b.height);
    const k = withRef(c);
    if (S.on.outages && S.countryFeat) {
      c.globalAlpha = dim("outages");
      for (const i of items().filter(i => i.layer === "outages")) {
        const f = S.countryFeat.get(String(i.iso_num)); if (!f) continue;
        const cp = f._p2 || (f._p2 = P2(f)); if (!cp) continue;
        c.fillStyle = "rgba(176,124,255,0.22)"; c.fill(cp);
        c.lineWidth = 0.8 / k; c.strokeStyle = "rgba(176,124,255,0.7)"; c.stroke(cp);
      }
    }
    if (S.on.daynight) {
      c.globalAlpha = 1;
      const np = P2(nightShape(t));
      c.fillStyle = "rgba(0,4,12,0.42)"; c.fill(np);
      c.lineWidth = 1 / k; c.strokeStyle = "rgba(255,200,90,0.18)"; c.stroke(np);
    }
    if (S.on.space && S.aur) {
      c.globalAlpha = dim("space");
      c.save(); withT(c, T);
      const s = Math.max(1.4, 2.2 * Math.min(T.k, 3)) / T.k;
      for (const [x, y, pr] of S.aur.cells) { c.fillStyle = `rgba(57,255,159,${Math.min(0.5, pr / 90)})`; c.fillRect(x - s, y - s, 2 * s, 2 * s); }
      c.restore();
      c.lineWidth = 1.1 / k; c.strokeStyle = "rgba(57,255,159,0.75)"; c.setLineDash([4 / k, 3 / k]);
      for (const edge of [S.aur.north, S.aur.south]) {
        if (edge.length < 2) continue;
        if (!edge._p2) edge._p2 = P2({ type: "LineString", coordinates: edge });
        c.stroke(edge._p2);
      }
      c.setLineDash([]);
    }
    if (S.on.wxalerts) {
      c.globalAlpha = dim("wxalerts");
      for (const i of items().filter(i => i.poly)) {
        if (!i._p2) i._p2 = P2({ type: "MultiPolygon", coordinates: i.poly.map(r => [r]) });
        if (!i._p2) continue;
        c.fillStyle = hexA(SEV[i.severity], 0.22); c.fill(i._p2);
        c.lineWidth = 0.8 / k; c.strokeStyle = hexA(SEV[i.severity], 0.8); c.stroke(i._p2);
      }
    }
    c.globalAlpha = 1;
    ovlBmp = b; ovlT = { k: T.k, x: T.x, y: T.y };
    return true;
  }
  function hexA(h, a) { const n = parseInt(h.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; }
  const dim = l => (S.hl && S.hlUntil > nowS() && S.hl !== l ? 0.22 : 1);

  // ───────── flights (dead reckoning between OpenSky reports, ≤10 min)
  function updateFlights(force) {
    const f = S.flights, fl = S.fl;
    if (!f || !fl || !proj) return;
    const dtBase = Math.min(600, Math.max(0, nowS() - (f.meta && f.meta.time || nowS())));
    for (let n = 0; n < fl.n; n++) {
      const r = f.rows[n];
      let lat = r[4], lon = r[3];
      if (r[6] && r[7] != null) {
        const d = r[6] * dtBase, a = r[7] * Math.PI / 180;
        lat += d * Math.cos(a) / 111320;
        lon += d * Math.sin(a) / (111320 * Math.max(0.2, Math.cos(lat * Math.PI / 180)));
        if (lon > 180) lon -= 360; if (lon < -180) lon += 360;
      }
      fl.lat[n] = lat; fl.lon[n] = lon;
      const p = proj([lon, lat]); fl.x[n] = p ? p[0] : NaN; fl.y[n] = p ? p[1] : NaN;
    }
    fl.dt = dtBase;
    if (G && S.mode !== "2d" && !force) syncGlobeFlights();
  }

  // ───────── satellites (SGP4 via satellite.js, propagated client-side)
  function satAt(rec, date) {
    const pv = satellite.propagate(rec, date);
    if (!pv || !pv.position || typeof pv.position !== "object") return null;
    const g = satellite.eciToGeodetic(pv.position, satellite.gstime(date));
    const v = pv.velocity ? Math.hypot(pv.velocity.x, pv.velocity.y, pv.velocity.z) : null;
    const lat = satellite.degreesLat(g.latitude), lon = satellite.degreesLong(g.longitude);
    if (!isFinite(lat) || !isFinite(lon) || !isFinite(g.height)) return null;
    return { lat, lon, alt: g.height, v };
  }
  function satTime() { return new Date((S.replay == null ? nowS() : nowS() + S.replay * 60) * 1000); }
  function updateSats(force) {
    if (!S.satrecs.length || typeof satellite === "undefined") return;
    const date = satTime();
    S.satPos = S.satrecs.map(o => {
      const q = satAt(o.rec, date); if (!q) return null;
      q.s = o.s; q.rec = o.rec; q.p = proj ? proj([q.lon, q.lat]) : null;
      q.iss = o.s.id === 25544; return q;
    }).filter(Boolean);
    const iss = S.satrecs.find(o => o.s.id === 25544);
    if (iss && (force || !S.issAt || Math.abs(date - S.issAt) > 30000)) {
      S.issAt = date; S.issTrack = [];
      for (let m = -50; m <= 95; m += 1) {
        const q = satAt(iss.rec, new Date(+date + m * 60000)); if (q) S.issTrack.push([q.lon, q.lat, q.alt, m]);
      }
    }
    if (G && S.mode !== "2d") syncGlobeSats();
  }

  // ───────── 2D draw
  let dirty = true, lastDraw = 0, raf = 0, frameMs = 0;
  function invalidate(ovl) { if (ovl) ovlKey = ""; dirty = true; kickLoop(); }
  function animating() {
    if (reduced) return false;
    if (S.playing) return true;
    return items().some(i => i.severity >= 4 || S.fresh.has(i.id)) || !!S.selected;
  }
  function kickLoop() { if (!raf && S.visible && S.mode === "2d") raf = requestAnimationFrame(loop); }
  let lastSlow = 0;
  function loop(ts) {
    raf = 0;
    if (!S.visible || S.mode !== "2d") return;
    if (ts - lastSlow > 1000) { lastSlow = ts; updateFlights(); updateSats(); dirty = true; tickClock(); }
    const anim = animating();
    if (dirty || (anim && ts - lastDraw > 33)) { draw(ts); lastDraw = ts; dirty = false; }
    raf = requestAnimationFrame(loop);
  }
  function draw(ts) {
    if (!proj || !W) return;
    const t0 = performance.now(), k = T.k;
    const tNow = S.replay == null ? nowS() : nowS() + S.replay * 60;
    let tA = performance.now();
    if (!landBmp) { buildLand(); S.landMs = performance.now() - tA; }
    tA = performance.now();
    if (buildOverlay(tNow)) S.ovlMs = performance.now() - tA;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, cv.width, cv.height);
    for (const [bmp, bt] of [[landBmp, landT], [ovlBmp, ovlT]]) {
      const r = T.k / bt.k;
      ctx.setTransform(r, 0, 0, r, DPR * (T.x - bt.x * r), DPR * (T.y - bt.y * r));
      ctx.drawImage(bmp, 0, 0);
    }
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    const X = p => T.x + T.k * p[0], Y = p => T.y + T.k * p[1];
    const zs = Math.min(2.2, 0.8 + Math.log2(k) * 0.35);
    // flights
    if (S.on.flights && S.fl && S.replay == null) {
      ctx.globalAlpha = 0.75 * dim("flights");
      const fl = S.fl;
      ctx.fillStyle = LAYER_COLOR.flights;
      const sz = k > 3 ? 2 : 1.4;
      ctx.beginPath();
      for (let n = 0; n < fl.n; n++) {
        const x = T.x + k * fl.x[n], y = T.y + k * fl.y[n];
        if (x < -4 || y < -4 || x > W + 4 || y > H + 4 || x !== x) continue;
        ctx.rect(x - sz / 2, y - sz / 2, sz, sz);
      }
      ctx.fill();
      if (k > 3) {                         // heading ticks when zoomed in
        ctx.strokeStyle = LAYER_COLOR.flights; ctx.lineWidth = 1; ctx.beginPath();
        for (let n = 0; n < fl.n; n++) {
          const tr = S.flights.rows[n][7]; if (tr == null) continue;
          const x = T.x + k * fl.x[n], y = T.y + k * fl.y[n];
          if (x < 0 || y < 0 || x > W || y > H) continue;
          const a = tr * Math.PI / 180; ctx.moveTo(x, y); ctx.lineTo(x + 6 * Math.sin(a), y - 6 * Math.cos(a));
        }
        ctx.stroke();
      }
      for (const e of ((S.flights.meta && S.flights.meta.emergencies) || [])) {
        const p = proj([e.lon, e.lat]); if (!p) continue;
        ring(X(p), Y(p), 7, "#ff3b5c", 1.5);
      }
      ctx.globalAlpha = 1;
    }
    // ISS ground track + satellites
    if (S.on.satellites && S.satPos.length) {
      ctx.globalAlpha = dim("satellites");
      if (S.issTrack.length) {
        ctx.lineWidth = 1.2; ctx.setLineDash([5, 4]);
        let prev = null;
        ctx.strokeStyle = "rgba(255,255,255,0.55)"; ctx.beginPath();
        for (const [lo, la] of S.issTrack) {
          const p = proj([lo, la]); if (!p) { prev = null; continue; }
          const x = X(p), y = Y(p);
          if (prev && Math.abs(lo - prev) < 90) ctx.lineTo(x, y); else ctx.moveTo(x, y);
          prev = lo;
        }
        ctx.stroke(); ctx.setLineDash([]);
      }
      ctx.fillStyle = LAYER_COLOR.satellites; ctx.beginPath();
      for (const q of S.satPos) { if (!q.p || q.iss) continue; const x = X(q.p), y = Y(q.p); ctx.moveTo(x + 1.5, y); ctx.arc(x, y, 1.5, 0, 6.283); }
      ctx.fill();
      const iss = S.satPos.find(q => q.iss);
      if (iss && iss.p) {
        const x = X(iss.p), y = Y(iss.p);
        ctx.shadowColor = "#fff"; ctx.shadowBlur = 10; ctx.fillStyle = "#fff";
        ctx.fillRect(x - 3, y - 3, 6, 6); ctx.shadowBlur = 0;
        ctx.strokeStyle = "#fff"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x - 9, y); ctx.lineTo(x + 9, y); ctx.stroke();
        label("ISS", x + 8, y - 6, "#fff");
      }
      ctx.globalAlpha = 1;
    }
    // events (batched: rare enough to draw individually)
    const list = items().filter(i => i._p);
    const pulse = ts ? (ts % 1800) / 1800 : 0;
    for (const i of list) {
      if (i.poly && S.on.wxalerts) continue;          // drawn as polygon in the overlay
      const x = X(i._p), y = Y(i._p);
      if (x < -10 || y < -10 || x > W + 10 || y > H + 10) continue;
      ctx.globalAlpha = dim(i.layer) * (i.stale ? 0.55 : 1);
      glyph(i, x, y, zs);
      if (!reduced && (i.severity >= 4 || S.fresh.has(i.id))) ring(x, y, 4 + 14 * pulse, SEV[Math.max(2, i.severity)], 1.4, 1 - pulse);
    }
    ctx.globalAlpha = 1;
    // fleet
    if (S.on.fleet && S.fleet && S.fleet.p) {
      const x = X(S.fleet.p), y = Y(S.fleet.p);
      hex(x, y, 6, S.accent); label(`FLEET ${S.fleet.online}/${S.fleet.nodes.length}`, x + 9, y + 12, S.accent);
    }
    // operator
    if (S.op && S.op.p) {
      const x = X(S.op.p), y = Y(S.op.p);
      if (S.near) {
        ctx.save(); withT(ctx, T); ctx.beginPath(); gpathCtx()(d3.geoCircle().center([S.op.lon, S.op.lat]).radius(NEAR_KM / 111.2)());
        ctx.fillStyle = hexA("#00d9ff", 0.06); ctx.fill(); ctx.setLineDash([4 / k, 4 / k]); ctx.lineWidth = 1 / k; ctx.strokeStyle = S.accent; ctx.stroke(); ctx.restore();
        ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
      }
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.arc(x, y, 5, 0, 6.283); ctx.moveTo(x - 10, y); ctx.lineTo(x - 6, y); ctx.moveTo(x + 6, y); ctx.lineTo(x + 10, y);
      ctx.moveTo(x, y - 10); ctx.lineTo(x, y - 6); ctx.moveTo(x, y + 6); ctx.lineTo(x, y + 10); ctx.stroke();
      ctx.fillStyle = S.accent; ctx.beginPath(); ctx.arc(x, y, 2, 0, 6.283); ctx.fill();
      label("YOU", x + 9, y - 7, "#fff");
    }
    // selection
    const sel = S.selected && selPos();
    if (sel) ring(X(sel), Y(sel), 11 + (reduced ? 0 : 3 * Math.sin((ts || 0) / 200)), "#fff", 1.5);
    frameMs = performance.now() - t0;
    S.frameMs = frameMs;
  }
  let gctxPath = null;
  function gpathCtx() { if (!gctxPath) gctxPath = d3.geoPath(proj, ctx); else gctxPath.projection(proj); return gctxPath; }
  function ring(x, y, r, col, w, a) { ctx.save(); ctx.globalAlpha *= (a == null ? 1 : a); ctx.strokeStyle = col; ctx.lineWidth = w; ctx.beginPath(); ctx.arc(x, y, r, 0, 6.283); ctx.stroke(); ctx.restore(); }
  function label(t, x, y, col) { ctx.font = "600 9px 'Share Tech Mono', monospace"; ctx.fillStyle = "rgba(0,0,0,0.6)"; ctx.fillText(t, x + 1, y + 1); ctx.fillStyle = col; ctx.fillText(t, x, y); }
  function hex(x, y, r, col) {
    ctx.beginPath(); for (let n = 0; n < 6; n++) { const a = Math.PI / 3 * n + Math.PI / 6; ctx[n ? "lineTo" : "moveTo"](x + r * Math.cos(a), y + r * Math.sin(a)); }
    ctx.closePath(); ctx.fillStyle = hexA("#00d9ff", 0.25); ctx.fill(); ctx.strokeStyle = col; ctx.lineWidth = 1.4; ctx.stroke();
  }
  function glyph(i, x, y, zs) {
    const col = i.layer === "outages" ? LAYER_COLOR.outages : SEV[i.severity];
    ctx.fillStyle = col; ctx.strokeStyle = "rgba(0,0,0,0.7)"; ctx.lineWidth = 0.8;
    ctx.beginPath();
    if (i.layer === "quakes") {
      const r = (2 + Math.max(0, (i.mag || 2.5) - 2.5) * 1.5) * zs; ctx.arc(x, y, r, 0, 6.283);
      ctx.globalAlpha *= 0.85; ctx.fill(); ctx.stroke(); return;
    }
    const r = 3.6 * zs;
    if (i.layer === "volcanoes" || i.type === "wildfire") { ctx.moveTo(x, y - r * 1.2); ctx.lineTo(x + r, y + r * 0.8); ctx.lineTo(x - r, y + r * 0.8); ctx.closePath(); }
    else if (i.layer === "disasters") { ctx.moveTo(x, y - r * 1.2); ctx.lineTo(x + r, y); ctx.lineTo(x, y + r * 1.2); ctx.lineTo(x - r, y); ctx.closePath(); }
    else if (i.layer === "outages") { ctx.arc(x, y, r * 1.2, 0, 6.283); ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.stroke(); return; }
    else if (i.layer === "wxalerts") { ctx.rect(x - r * 0.85, y - r * 0.85, r * 1.7, r * 1.7); }
    else ctx.arc(x, y, r, 0, 6.283);
    ctx.fill(); ctx.stroke();
    if (i.approx) { ctx.setLineDash([2, 2]); ctx.strokeStyle = col; ctx.beginPath(); ctx.arc(x, y, r * 2, 0, 6.283); ctx.stroke(); ctx.setLineDash([]); }
  }
  function selPos() {
    const s = S.selected;
    if (s.kind === "item") return s.it._p;
    if (s.kind === "flight" && S.fl) return [S.fl.x[s.n], S.fl.y[s.n]];
    if (s.kind === "sat") { const q = S.satPos.find(q => q.s.id === s.id); return q && q.p; }
    return null;
  }

  // ───────── hit test, tooltip, detail card
  function hitAt(mx, my) {
    let best = null, bd = 1e9;
    const X = p => T.x + T.k * p[0], Y = p => T.y + T.k * p[1];
    const consider = (d, o, lim) => { if (d < lim && d < bd) { bd = d; best = o; } };
    for (const i of items()) if (i._p) consider(Math.hypot(X(i._p) - mx, Y(i._p) - my), { kind: "item", it: i }, 10);
    if (best) return best;
    if (S.op && S.op.p) consider(Math.hypot(X(S.op.p) - mx, Y(S.op.p) - my), { kind: "op" }, 9);
    if (S.on.fleet && S.fleet && S.fleet.p) consider(Math.hypot(X(S.fleet.p) - mx, Y(S.fleet.p) - my), { kind: "fleet" }, 9);
    if (S.on.satellites) for (const q of S.satPos) if (q.p) consider(Math.hypot(X(q.p) - mx, Y(q.p) - my), { kind: "sat", id: q.s.id }, 7);
    if (best) return best;
    if (S.on.flights && S.fl && S.replay == null) {
      const fl = S.fl;
      for (let n = 0; n < fl.n; n++) { const dx = T.x + T.k * fl.x[n] - mx; if (dx > 6 || dx < -6) continue; consider(Math.hypot(dx, T.y + T.k * fl.y[n] - my), { kind: "flight", n }, 6); }
    }
    return best;
  }
  function describe(o) {
    if (o.kind === "item") { const i = o.it; return { title: i.title, sub: `${layerName(i.layer)} · ${SEV_NAME[i.severity]} · ${ago(i.time)}`, col: SEV[i.severity] }; }
    if (o.kind === "flight") { const r = S.flights.rows[o.n]; return { title: r[1] || r[0], sub: `AIRCRAFT · ${r[2]} · ${r[5] != null ? Math.round(r[5] * 3.281).toLocaleString() + " ft" : "alt —"}`, col: LAYER_COLOR.flights }; }
    if (o.kind === "sat") { const q = S.satPos.find(q => q.s.id === o.id); return q ? { title: q.s.name, sub: `SATELLITE · ${Math.round(q.alt)} km · ${q.s.group}`, col: LAYER_COLOR.satellites } : null; }
    if (o.kind === "fleet") return { title: `BUCS fleet · ${S.fleet.nodes.length} node(s)`, sub: `${S.fleet.online} online · ${S.fleet.city}`, col: S.accent };
    if (o.kind === "op") return { title: "Your location", sub: S.op.how, col: "#fff" };
    return null;
  }
  const layerName = l => (LAYERS.find(x => x[0] === l) || [l, l.toUpperCase()])[1];
  function showTip(o, mx, my) {
    const d = o && describe(o);
    if (!d) return hideTip();
    tip.innerHTML = `<b style="color:${d.col}">${esc(d.title)}</b><span>${esc(d.sub)}</span>`;
    tip.hidden = false;
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    tip.style.left = Math.min(W - tw - 6, mx + 12) + "px"; tip.style.top = Math.max(4, Math.min(H - th - 4, my - th - 8)) + "px";
  }
  function hideTip() { tip.hidden = true; }
  let moveRaf = 0;
  cv.addEventListener("pointermove", e => {
    if (moveRaf) return;
    const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    moveRaf = requestAnimationFrame(() => { moveRaf = 0; const o = hitAt(mx, my); cv.style.cursor = o ? "pointer" : "grab"; showTip(o, mx, my); });
  });
  cv.addEventListener("pointerleave", hideTip);
  cv.addEventListener("click", e => {
    const r = cv.getBoundingClientRect(), o = hitAt(e.clientX - r.left, e.clientY - r.top);
    if (o) select(o); else closeCard();
  });

  function row(k, v) { return v == null || v === "" ? "" : `<div><dt>${k}</dt><dd>${v}</dd></div>`; }
  function distRow(lat, lon) { return S.op ? row("FROM YOU", Math.round(km(S.op.lat, S.op.lon, lat, lon)).toLocaleString() + " km") : ""; }
  function select(o) {
    S.selected = o; hideTip();
    let h = "";
    if (o.kind === "item") {
      const i = o.it, src = (S.data.sources || []).find(s => s.key === srcKey(i));
      h = `<div class="gm-card-h" style="--c:${SEV[i.severity]}"><span>${layerName(i.layer)}</span><em>${SEV_NAME[i.severity]}</em></div>
        <h3>${esc(i.title)}</h3>${i.detail ? `<p>${esc(i.detail)}</p>` : ""}
        <dl>${row("TIME", i.time ? `${utc(i.time)} · ${ago(i.time)}` : "")}${i.until ? row("UNTIL", utc(i.until)) : ""}
        ${i.mag != null ? row("MAGNITUDE", "M" + Number(i.mag).toFixed(1)) : ""}
        ${row("POSITION", ll(i.lat, i.lon) + (i.approx ? " · approx (area centroid)" : ""))}${distRow(i.lat, i.lon)}
        ${row("SOURCE", esc(i.source) + (src ? ` · fetched ${ago(src.last_ok)}` : ""))}${i.stale ? row("STATUS", "STALE — source unreachable, last good data") : ""}</dl>
        ${safeUrl(i.link) ? `<a href="${esc(safeUrl(i.link))}" target="_blank" rel="noopener noreferrer">OPEN SOURCE ↗</a>` : ""}`;
    } else if (o.kind === "flight") {
      const r = S.flights.rows[o.n], m = S.flights.meta || {};
      const sq = r[10] && { 7500: "unlawful interference", 7600: "radio failure", 7700: "general emergency" }[r[10]];
      h = `<div class="gm-card-h" style="--c:${LAYER_COLOR.flights}"><span>AIR TRAFFIC</span><em>${sq ? "SQUAWK " + r[10] : "AIRBORNE"}</em></div>
        <h3>${esc(r[1] || "(no callsign)")} <small>${esc(r[0])}</small></h3>
        <dl>${row("REGISTERED", esc(r[2]))}${row("ALTITUDE", r[5] != null ? `${Math.round(r[5] * 3.281).toLocaleString()} ft · ${r[5].toLocaleString()} m` : "—")}
        ${row("SPEED", r[6] != null ? `${Math.round(r[6] * 1.944)} kt` : "—")}${row("HEADING", r[7] != null ? r[7] + "°" : "—")}
        ${row("CLIMB", r[8] != null ? r[8] + " m/s" : "")}${sq ? row("SQUAWK", `${r[10]} · ${sq} (as transmitted)`) : ""}
        ${row("POSITION", ll(S.fl.lat[o.n], S.fl.lon[o.n]))}${distRow(S.fl.lat[o.n], S.fl.lon[o.n])}
        ${row("REPORTED", `${utc(m.time)} · position extrapolated ${Math.round(S.fl.dt || 0)} s from heading & speed`)}
        ${row("SOURCE", "OpenSky Network (anonymous)")}</dl>
        <a href="https://opensky-network.org/aircraft-profile?icao24=${encodeURIComponent(r[0])}" target="_blank" rel="noopener noreferrer">OPENSKY PROFILE ↗</a>`;
    } else if (o.kind === "sat") {
      const q = S.satPos.find(q => q.s.id === o.id); if (!q) return;
      const mm = parseFloat(q.s.l2.slice(52, 63)), period = mm ? 1440 / mm : null, inc = parseFloat(q.s.l2.slice(8, 16));
      h = `<div class="gm-card-h" style="--c:${LAYER_COLOR.satellites}"><span>SATELLITE · ${esc(q.s.group.toUpperCase())}</span><em>NORAD ${q.s.id}</em></div>
        <h3>${esc(q.s.name)}</h3>
        <dl>${row("ALTITUDE", Math.round(q.alt).toLocaleString() + " km")}${row("VELOCITY", q.v ? q.v.toFixed(2) + " km/s" : "")}
        ${row("PERIOD", period ? period.toFixed(1) + " min" : "")}${row("INCLINATION", isFinite(inc) ? inc.toFixed(1) + "°" : "")}
        ${row("SUB-POINT", ll(q.lat, q.lon))}${distRow(q.lat, q.lon)}
        ${row("SOURCE", esc((S.sats.meta && S.sats.meta.provider) || "CelesTrak") + " element set · SGP4 propagated in your browser" + (S.sats.source ? " · fetched " + ago(S.sats.source.last_ok) : ""))}</dl>
        <a href="https://celestrak.org/NORAD/elements/gp.php?CATNR=${q.s.id}&FORMAT=tle" target="_blank" rel="noopener noreferrer">ELEMENT SET ↗</a>`;
    } else if (o.kind === "fleet") {
      const f = S.fleet;
      h = `<div class="gm-card-h" style="--c:${S.accent}"><span>BUCS FLEET</span><em>${f.online}/${f.nodes.length} ONLINE</em></div>
        <h3>${esc(f.city || "Fleet site")}</h3>
        <dl>${f.nodes.slice(0, 8).map(n => row(esc((n.name || n.node_id || "node").toUpperCase()), `${esc(n.status)}${n.cpu != null ? ` · CPU ${Math.round(n.cpu)}%` : ""}`)).join("")}
        ${row("LOCATION", "host public-IP geolocation (approximate)")}</dl>`;
    } else if (o.kind === "op") {
      h = `<div class="gm-card-h" style="--c:#fff"><span>OPERATOR</span><em>YOU</em></div><h3>${ll(S.op.lat, S.op.lon)}</h3>
        <dl>${row("FROM", esc(S.op.how))}${row(`WITHIN ${NEAR_KM} KM`, nearCount() + " event(s)")}</dl>`;
    }
    openCard(h); invalidate();
  }
  function srcKey(i) {
    const id = String(i.id || ""); const p = id.split(":")[0].replace("-zone", "");
    return p === "nws" ? "nws" : p;
  }
  function openCard(h) {
    card.innerHTML = `<button type="button" class="gm-x" aria-label="Close">✕</button>${h}`;
    card.hidden = false;
  }
  function closeCard() { card.hidden = true; S.selected = null; invalidate(); if (G) syncGlobeRings(); }
  card.addEventListener("click", e => { if (e.target.closest(".gm-x")) closeCard(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !card.hidden) closeCard(); });
  function nearCount() { return S.op ? items().filter(i => km(S.op.lat, S.op.lon, i.lat, i.lon) <= NEAR_KM).length : 0; }

  function showSpaceCard() {
    const sp = S.data && S.data.space, a = S.data && S.data.aurora, st = S.data && S.data.layers.space;
    if (!sp) { openCard(`<div class="gm-card-h"><span>SPACE WEATHER</span><em>${esc(st ? st.status.toUpperCase() : "—")}</em></div><p>NOAA SWPC data not available yet.</p>`); return; }
    const sc = sp.scales || {}, w = sp.wind || {};
    const bars = (sp.kp_3h || []).map(h => `<i title="${utc(h.time)} Kp ${h.kp}" style="height:${Math.max(6, h.kp / 9 * 100)}%;--c:${h.kp >= 5 ? SEV[3] : h.kp >= 4 ? SEV[2] : "#39ff9f"}"></i>`).join("");
    openCard(`<div class="gm-card-h" style="--c:#39ff9f"><span>SPACE WEATHER · NOAA SWPC</span><em>${esc(sp.kp_label.toUpperCase())}</em></div>
      <h3>Kp ${sp.kp != null ? sp.kp.toFixed(2) : "—"} <small>${ago(sp.kp_time)}</small></h3>
      <div class="gm-kpbars">${bars}</div>
      <dl>${row("NOAA SCALES", `G${sc.G ?? "—"} · S${sc.S ?? "—"} · R${sc.R ?? "—"}`)}${row("SOLAR WIND", w.speed_kms != null ? `${w.speed_kms} km/s` : "—")}
      ${row("IMF", w.bt_nt != null ? `Bt ${w.bt_nt} nT · Bz ${w.bz_nt} nT` : "—")}
      ${a ? row("AURORA OVAL", `equatorward edge of ≥${a.threshold}% probability · OVATION ${utc(a.forecast)}`) : ""}</dl>
      ${(sp.alerts || []).slice(0, 4).map(x => `<p class="gm-al">${utc(x.time)} · <b>${esc(x.kind)}</b> ${esc(x.title)}</p>`).join("") || "<p>No SWPC alerts in the last 24 h.</p>"}
      <a href="https://www.swpc.noaa.gov/" target="_blank" rel="noopener noreferrer">SWPC ↗</a>`);
  }
  $("#gm-kp").addEventListener("click", () => { api.highlight("space"); showSpaceCard(); });

  // ───────── view control
  function zoomTo(lat, lon, k, ms) {
    if (S.mode !== "2d") { if (G) G.pointOfView({ lat, lng: lon, altitude: S.mode === "orbit" ? 2.6 : 1.4 }, ms || 1000); return; }
    const p = proj && proj([lon, lat]); if (!p || !zoomer) return;
    const t = d3.zoomIdentity.translate(W / 2 - k * p[0], H / 2 - k * p[1]).scale(k);
    const sel = d3.select(cv);
    (reduced ? sel : sel.transition().duration(ms || 900)).call(zoomer.transform, t);
  }
  $("#gm-near").addEventListener("click", async () => {
    await locate();
    if (!S.op) { msg.textContent = "location unavailable (no GPS / IP geolocation)"; return; }
    S.near = !S.near; $("#gm-near").classList.toggle("on", S.near);
    if (S.near) { zoomTo(S.op.lat, S.op.lon, 5); select({ kind: "op" }); } else invalidate();
  });
  $("#gm-reset").addEventListener("click", () => {
    S.near = false; $("#gm-near").classList.remove("on"); closeCard();
    if (S.mode === "2d" && zoomer) d3.select(cv).transition().duration(reduced ? 0 : 700).call(zoomer.transform, d3.zoomIdentity);
    else if (G) G.pointOfView({ lat: 20, lng: S.op ? S.op.lon : 0, altitude: S.mode === "orbit" ? 3.4 : 1.75 }, 900);
  });
  $("#gm-full").addEventListener("click", () => {
    const on = document.fullscreenElement === mount || mount.classList.contains("gm-fs");
    if (on) { if (document.fullscreenElement) document.exitFullscreen(); mount.classList.remove("gm-fs"); }
    else if (mount.requestFullscreen) mount.requestFullscreen().catch(() => mount.classList.add("gm-fs"));
    else mount.classList.add("gm-fs");
  });
  document.addEventListener("fullscreenchange", scheduleResize);

  // ───────── timeline (24 h replay)
  function renderHist() {
    const c = $("#gm-hist"), cw = Math.min(MAX_W, c.clientWidth), ch = Math.min(64, c.clientHeight); if (!cw || !ch) return;
    c.width = Math.round(cw * DPR); c.height = Math.round(ch * DPR);
    const g = c.getContext("2d"), bins = new Array(48).fill(0), now = nowS();
    for (const i of ((S.data && S.data.items) || [])) if (i.time && now - i.time < DAY) bins[Math.min(47, Math.floor((DAY - (now - i.time)) / 1800))]++;
    const mx = Math.max(1, ...bins), bw = c.width / 48;
    g.clearRect(0, 0, c.width, c.height); g.fillStyle = hexA("#00d9ff", 0.35);
    bins.forEach((b, n) => { const h = (b / mx) * c.height; g.fillRect(n * bw + 1, c.height - h, bw - 2, h); });
  }
  function setReplay(min) {
    S.replay = min >= 0 ? null : min;
    $("#gm-range").value = S.replay == null ? 0 : min;
    $("#gm-live").classList.toggle("on", S.replay == null);
    $("#gm-tlabel").textContent = S.replay == null ? "LIVE" : `${utc(nowS() + min * 60).slice(5)} · −${Math.floor(-min / 60)}h${String(-min % 60).padStart(2, "0")} · replay (aircraft hidden: no history)`;
    updateSats(); invalidate(true); if (G) syncGlobe();
  }
  $("#gm-range").addEventListener("input", e => { stopPlay(); setReplay(+e.target.value); });
  $("#gm-live").addEventListener("click", () => { stopPlay(); setReplay(0); });
  let playT = 0;
  function stopPlay() { S.playing = false; clearInterval(playT); $("#gm-play").textContent = "▶"; }
  $("#gm-play").addEventListener("click", () => {
    if (S.playing) return stopPlay();
    S.playing = true; $("#gm-play").textContent = "❚❚";
    let m = S.replay == null || S.replay > -30 ? -1440 : S.replay;
    setReplay(m);
    playT = setInterval(() => { m += 10; if (m >= 0) { stopPlay(); setReplay(0); } else setReplay(m); }, reduced ? 400 : 120);
  });

  // ───────── 3D globe / orbital (globe.gl, lazy)
  let G = null, landFeats = null;
  async function initGlobe() {
    msg.textContent = "loading 3D engine…";
    await Promise.all([loadScript(CDN.globe), S.on.satellites ? loadScript(CDN.sat) : null]);
    if (!landFeats) {
      const l = await getJSON(CDN.land);
      const f = topojson.feature(l, l.objects.land);
      landFeats = (f.features || [f]).flatMap(ft => ft.geometry.type === "MultiPolygon"
        ? ft.geometry.coordinates.map(c => ({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: c } })) : [ft]);
      if (india) landFeats.push(Object.assign({}, india, { properties: { india: true } }));
    }
    const el = $("#gm-globe");
    let g;
    try { g = new Globe(el, { animateIn: false }); } catch (e) { g = Globe({ animateIn: false })(el); }
    g.width(W).height(H).backgroundColor("rgba(0,0,0,0)").showAtmosphere(true).atmosphereColor(S.accent).atmosphereAltitude(0.17)
      .showGraticules(true)
      .polygonsData(landFeats).polygonCapColor(f => f.properties.india ? "rgba(30,96,132,0.96)" : "rgba(24,80,114,0.94)")
      .polygonSideColor(() => "rgba(0,0,0,0)").polygonStrokeColor(f => f.properties.india ? "rgba(0,217,255,0.95)" : "rgba(80,200,255,0.55)")
      .polygonAltitude(f => (f.properties.india ? 0.0065 : 0.005)).polygonsTransitionDuration(0)
      .pointLat("lat").pointLng("lon").pointColor(d => d.col).pointAltitude(d => d.alt).pointRadius(d => d.r).pointsMerge(false).pointsTransitionDuration(0)
      .pointLabel(() => "").onPointHover(d => globeTip(d && d.o)).onPointClick(d => d && select(d.o))
      .ringLat("lat").ringLng("lon").ringColor(d => t => hexA(d.col, 1 - t)).ringMaxRadius(d => d.max || 4).ringPropagationSpeed(2.5).ringRepeatPeriod(1400)
      .particleLat("lat").particleLng("lon").particleAltitude("alt").particlesSize(d => d.size).particlesColor(d => d.col).particlesSizeAttenuation(false)
      .onParticleHover(d => globeTip(d && d.o)).onParticleClick(d => d && select(d.o))
      .pathPoints("pts").pathPointLat(p => p[1]).pathPointLng(p => p[0]).pathPointAlt(p => p[2]).pathColor(d => d.col).pathStroke(d => d.w || 1)
      .pathDashLength(d => d.dash || 1).pathDashGap(d => d.gap || 0).pathTransitionDuration(0)
      .labelLat("lat").labelLng("lon").labelText("text").labelColor(d => d.col).labelSize(0.55).labelDotRadius(0.25).labelAltitude(0.012).labelResolution(2)
      .onGlobeClick(() => closeCard());
    const mat = g.globeMaterial(); if (mat && mat.color) { mat.color.set("#04121f"); if (mat.emissive) mat.emissive.set("#020c16"); }
    const ctl = g.controls(); ctl.autoRotate = !reduced; ctl.autoRotateSpeed = 0.35; ctl.enableDamping = true;
    ctl.addEventListener("start", () => { ctl.autoRotate = false; });
    G = g;
    msg.textContent = "";
    syncGlobe();
  }
  function globeTip(o) {
    if (!o) return hideTip();
    const d = describe(o); if (!d) return hideTip();
    tip.innerHTML = `<b style="color:${d.col}">${esc(d.title)}</b><span>${esc(d.sub)}</span>`;
    tip.hidden = false; tip.style.left = "12px"; tip.style.top = "12px";
  }
  function syncGlobe() {
    if (!G || S.mode === "2d") return;
    const pts = items().map(i => ({
      lat: i.lat, lon: i.lon, o: { kind: "item", it: i },
      col: i.layer === "outages" ? LAYER_COLOR.outages : SEV[i.severity],
      alt: i.layer === "quakes" ? 0.01 + Math.max(0, (i.mag || 2.5) - 2.5) * 0.02 : 0.012 + i.severity * 0.012,
      r: i.layer === "quakes" ? 0.18 + Math.max(0, (i.mag || 2.5) - 2.5) * 0.09 : 0.32,
    }));
    if (S.op) pts.push({ lat: S.op.lat, lon: S.op.lon, col: "#ffffff", alt: 0.02, r: 0.35, o: { kind: "op" } });
    if (S.on.fleet && S.fleet) pts.push({ lat: S.fleet.lat, lon: S.fleet.lon, col: S.accent, alt: 0.05, r: 0.3, o: { kind: "fleet" } });
    G.pointsData(pts);
    const labels = [];
    if (S.op) labels.push({ lat: S.op.lat, lon: S.op.lon, text: "YOU", col: "#ffffff" });
    G.labelsData(labels);
    syncGlobeRings(); syncGlobeSats(); syncGlobeFlights(); syncGlobePaths();
  }
  function syncGlobeRings() {
    if (!G) return;
    const r = reduced ? [] : items().filter(i => i.severity >= 4 || S.fresh.has(i.id)).map(i => ({ lat: i.lat, lon: i.lon, col: SEV[Math.max(2, i.severity)] }));
    const s = S.selected && S.selected.kind === "item" ? S.selected.it : null;
    if (s) r.push({ lat: s.lat, lon: s.lon, col: "#ffffff", max: 2.5 });
    G.ringsData(r);
  }
  let partSats = [], partFlights = [];
  function syncGlobeSats() {
    if (!G) return;
    const orbit = S.mode === "orbit";
    partSats = S.on.satellites ? S.satPos.map(q => ({ lat: q.lat, lon: q.lon, alt: q.alt / 6371, o: { kind: "sat", id: q.s.id },
      col: q.iss ? "#ffffff" : LAYER_COLOR.satellites, size: q.iss ? 7 : orbit ? 3 : 2.2 })) : [];
    pushParticles();
  }
  function syncGlobeFlights() {
    if (!G) return;
    partFlights = [];
    if (S.on.flights && S.fl && S.replay == null && S.mode === "3d") {
      for (let n = 0; n < S.fl.n; n++) {
        const a = S.flights.rows[n][5] || 0;
        partFlights.push({ lat: S.fl.lat[n], lon: S.fl.lon[n], alt: 0.006 + (a / 6371000) * 3, o: { kind: "flight", n } });
      }
    }
    pushParticles();
  }
  function pushParticles() {
    const sets = [];
    if (partFlights.length) { partFlights.col = LAYER_COLOR.flights; partFlights.size = 1.6; sets.push(partFlights); }
    const iss = partSats.filter(p => p.size >= 7), rest = partSats.filter(p => p.size < 7);
    if (rest.length) { rest.col = LAYER_COLOR.satellites; rest.size = rest[0].size; sets.push(rest); }
    if (iss.length) { iss.col = "#ffffff"; iss.size = 7; sets.push(iss); }
    G.particlesData(sets).particlesColor(s => s.col).particlesSize(s => s.size);
  }
  function syncGlobePaths() {
    if (!G) return;
    const paths = [];
    if (S.on.satellites && S.issTrack.length) {
      const pts = S.issTrack.map(([lo, la, h]) => [lo, la, S.mode === "orbit" ? h / 6371 : 0.004]);
      paths.push({ pts, col: "rgba(255,255,255,0.6)", w: S.mode === "orbit" ? 0.6 : 0.4, dash: 0.02, gap: 0.01 });
    }
    if (S.on.space && S.data && S.data.aurora) for (const e of [S.data.aurora.north, S.data.aurora.south]) {
      if (e.length > 2) paths.push({ pts: e.map(([lo, la]) => [lo, la, 0.01]), col: "rgba(57,255,159,0.8)", w: 0.5 });
    }
    if (S.selected && S.selected.kind === "sat" && S.mode === "orbit") {
      const o = S.satrecs.find(o => o.s.id === S.selected.id);
      if (o) {
        const mm = parseFloat(o.s.l2.slice(52, 63)) || 15, per = 1440 / mm, d = satTime(), pts = [];
        for (let m = 0; m <= per; m += per / 120) { const q = satAt(o.rec, new Date(+d + m * 60000)); if (q) pts.push([q.lon, q.lat, q.alt / 6371]); }
        paths.push({ pts, col: "rgba(214,168,255,0.9)", w: 0.5 });
      }
    }
    G.pathsData(paths);
  }

  async function setMode(m) {
    if (m === S.mode) return;
    S.mode = m;
    if (["2d", "3d", "orbit"].includes(m)) store("mode", m);   // view pref: restored on reload
    mount.querySelectorAll(".gm-modes button").forEach(b => { const on = b.dataset.mode === m; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); });
    root.dataset.mode = m;
    hideTip();
    if (m === "2d") {
      $("#gm-globe").hidden = true; cv.hidden = false;
      if (G) G.pauseAnimation();
      invalidate(true); kickLoop(); return;
    }
    cv.hidden = true; $("#gm-globe").hidden = false;
    try {
      if (!G) await initGlobe();
      G.resumeAnimation();
      G.width(W).height(H);
      syncGlobe();
      G.pointOfView({ lat: S.op ? S.op.lat : 20, lng: S.op ? S.op.lon : 0, altitude: m === "orbit" ? 3.4 : 1.75 }, reduced ? 0 : 1200);
    } catch (e) {
      msg.textContent = "3D engine unavailable (" + (e.message || e) + ") — staying in 2D";
      setTimeout(() => { msg.textContent = ""; }, 6000);
      S.mode = "x"; setMode("2d");
    }
  }
  mount.querySelector(".gm-modes").addEventListener("click", e => { const b = e.target.closest("button[data-mode]"); if (b) setMode(b.dataset.mode); });
  setInterval(() => { if (G && S.mode !== "2d" && S.visible) { updateSats(); syncGlobePaths(); tickClock(); } }, 2000);
  setInterval(() => { if (G && S.mode === "3d" && S.visible) { updateFlights(); } }, 10000);

  // ───────── clock / footer / visibility
  function tickClock() {
    $("#gm-utc").textContent = new Date().toISOString().slice(11, 19) + " UTC";
    const d = S.data; if (!d) return;
    const fl = S.flights, gate = fl && fl.gate;
    let f = `updated ${new Date(d.generated * 1000).toISOString().slice(11, 19)}Z`;
    if (S.on.flights && fl && fl.meta) f += ` · aircraft: ${fl.meta.shown.toLocaleString()} airborne${fl.meta.sampled ? " (sample of " + fl.meta.airborne.toLocaleString() + ")" : ""}, OpenSky ${ago(fl.meta.time)}${gate ? `, next ≥ ${Math.max(0, Math.round((gate.next_at - nowS()) / 60))} min (anon. budget)` : ""}`;
    if (S.mode === "2d" && S.frameMs != null) f += ` · ${S.frameMs.toFixed(1)} ms/frame`;
    $("#gm-foot").textContent = f;
  }
  if ("IntersectionObserver" in window) new IntersectionObserver(es => {
    S.visible = es[0].isIntersecting && !document.hidden; onVis();
  }, { threshold: 0.02 }).observe(mount);
  document.addEventListener("visibilitychange", () => {
    S.visible = !document.hidden && (mount.getBoundingClientRect().bottom > 0 && mount.getBoundingClientRect().top < innerHeight);
    onVis();
    if (!document.hidden) { pollSummary(); pollFlights(); }
  });
  function onVis() {
    if (S.visible) { kickLoop(); if (G && S.mode !== "2d") G.resumeAnimation(); }
    else { if (raf) cancelAnimationFrame(raf); raf = 0; if (G) G.pauseAnimation(); }
  }
  if ("ResizeObserver" in window) new ResizeObserver(() => { scheduleResize(); scheduleHist(); }).observe(stage);
  else window.addEventListener("resize", scheduleResize);
  let histT = 0;
  function scheduleHist() { clearTimeout(histT); histT = setTimeout(renderHist, 150); }

  // ───────── public API
  const api = {
    focus(o) {
      o = o || {};
      let it = null;
      if (o.id && S.data) it = S.data.items.find(i => i.id === o.id || i.id === "usgs:" + o.id || String(i.id).endsWith(":" + o.id));
      // callers outside the map (disaster "SHOW ON MAP", threat cards) pass their own layer names
      // ("disaster", "threats"): only a real map layer may narrow the proximity match
      const known = !!o.layer && LAYERS.some(l => l[0] === o.layer);
      if (!it && o.lat != null && o.lon != null && S.data) {
        it = S.data.items.filter(i => (!known || i.layer === o.layer) && km(i.lat, i.lon, o.lat, o.lon) < 30)
          .sort((a, b) => km(a.lat, a.lon, o.lat, o.lon) - km(b.lat, b.lon, o.lat, o.lon))[0] || null;
      }
      const lat = it ? it.lat : +o.lat, lon = it ? it.lon : +o.lon;
      if (it && !S.on[it.layer]) { S.on[it.layer] = true; store("layers", S.on); renderLayers(); invalidate(true); }
      const r = mount.getBoundingClientRect();
      if (r.bottom < 0 || r.top > innerHeight) mount.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
      if (S.replay != null) setReplay(0);
      if (isFinite(lat) && isFinite(lon)) zoomTo(lat, lon, 4.5);
      if (it) select({ kind: "item", it }); else if (known) api.highlight(o.layer);
      if (G) syncGlobe();
      return !!it || (isFinite(lat) && isFinite(lon));
    },
    highlight(layer) {
      if (!LAYERS.some(l => l[0] === layer)) return false;
      if (!S.on[layer]) { S.on[layer] = true; store("layers", S.on); if (layer === "flights") pollFlights(); if (layer === "satellites") pollSats(); }
      S.hl = layer; S.hlUntil = nowS() + 6;
      renderLayers(); invalidate(true);
      setTimeout(() => { if (S.hlUntil <= nowS() + 0.1) { S.hl = null; renderLayers(); invalidate(true); } }, 6100);
      return true;
    },
    _bench(n = 20) {          // diagnostics: synchronous frame timings (works while the tab is hidden)
      if (!proj) return null;
      landBmp = null; ovlKey = "";
      draw(performance.now());
      const first = { land: S.landMs, overlay: S.ovlMs, frame: S.frameMs };
      let tot = 0, mx = 0;
      for (let i = 0; i < n; i++) { draw(performance.now() + i * 33); tot += S.frameMs; mx = Math.max(mx, S.frameMs); }
      return { rebuild: first, steadyAvgMs: +(tot / n).toFixed(2), steadyMaxMs: +mx.toFixed(2), flights: S.fl ? S.fl.n : 0, sats: S.satPos.length, items: items().length, W, H, k: T.k };
    },
    get state() { return { mode: S.mode, layers: Object.assign({}, S.on), frameMs: S.frameMs, landMs: S.landMs, ovlMs: S.ovlMs, replay: S.replay }; },
  };
  window.GlobalMap = api;

  // ───────── boot
  renderLayers();
  (async () => {
    try { await initGeo(); } catch (e) { msg.textContent = "world geometry unavailable (CDN) — markers only"; }
    S.accent = cssVar("--accent", "#00d9ff");
    resize();
    await Promise.all([pollSummary(), locate(), pollFleet()]);
    pollFlights(); pollSats();
    renderHist(); invalidate(true);
    const m = store("mode");                       // remembered 2D / 3D / ORBITAL view
    if (m === "3d" || m === "orbit") setMode(m);
  })();
  setInterval(pollSummary, 60000);
  setInterval(pollFlights, 60000);
  setInterval(pollSats, 3600000);
  setInterval(pollFleet, 60000);
  setInterval(locate, 30000);
  setInterval(() => { S.accent = cssVar("--accent", "#00d9ff"); renderLayers(); }, 30000);
})();
