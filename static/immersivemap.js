/* IMMERSIVE MAP ENGINE (v8.8) — the shared MapLibre engine behind the INDIA, TELANGANA and
   DISASTER RESPONSE maps, with the same imagery modes as the global map's satellite view:
     SATELLITE · HYBRID · 3D TERRAIN · 3D CITY · NIGHT · TODAY · TOPO · DARK · ◉ LIVE
   Every mode names its source. Archival imagery is labelled as archival; ◉ LIVE overlays the
   newest geostationary frame + RainViewer radar (/api/globalmap/live) with their capture times,
   and TODAY the newest NASA daily pass (/api/globalmap/today). Nothing archival is called live.
   Public API: window.ImmersiveMap.create(host, opts) -> controller (see bottom). */
(function () {
  "use strict";
  const ML_JS = "https://unpkg.com/maplibre-gl@6.4.1/dist/maplibre-gl.mjs";      // GHSA-jrc7-96c5-q579 fixed
  const ML_CSS = "https://unpkg.com/maplibre-gl@6.4.1/dist/maplibre-gl.css";
  const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/";
  const reduced = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const MODES = [
    ["sat", "SATELLITE", "SATELLITE · Esri World Imagery · archival, capture dates vary · not a live feed"],
    ["hybrid", "HYBRID", "HYBRID · Esri imagery + roads & places · archival imagery"],
    ["terrain", "3D TERRAIN", "3D TERRAIN · imagery on AWS Terrarium elevation ×1.5 · drag with right mouse / two fingers to tilt"],
    ["city", "3D CITY", "3D CITY · OpenStreetMap building heights (zoom ≥ 13) on archival imagery"],
    ["night", "NIGHT", "NIGHT LIGHTS · NASA Black Marble 2016 annual composite · not a live feed"],
    ["today", "TODAY", "TODAY · newest NASA daily true-colour pass (VIIRS ~375 m/px)"],
    ["topo", "TOPO", "TOPOGRAPHIC · Esri World Topographic Map"],
    ["dark", "DARK", "DARK TACTICAL · Esri dark canvas · live layers stand out"],
    ["live", "◉ LIVE", "◉ LIVE · newest geostationary frame + rain radar, each with its capture time"],
  ];
  const MODE_KEYS = MODES.map(m => m[0]);
  const SKY = { "sky-color": "#04162a", "horizon-color": "#1d4f73", "fog-color": "#0a2236", "sky-horizon-blend": 0.6,
    "horizon-fog-blend": 0.5, "fog-ground-blend": 0.35, "atmosphere-blend": 0.8 };
  const hhmm = t => { const d = typeof t === "number" ? new Date(t * 1000) : new Date(t); return isNaN(d) ? "?" : d.toISOString().slice(11, 16) + " UTC"; };
  const agoS = s => s == null || !isFinite(s) ? "" : s < 90 ? `${Math.max(0, Math.round(s))} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : `${(s / 3600).toFixed(1)} h ago`;
  function store(k, v) { try { if (v === undefined) return JSON.parse(localStorage.getItem("im." + k)); localStorage.setItem("im." + k, JSON.stringify(v)); } catch (e) { return null; } }
  async function getJSON(u) { const r = await fetch(u); if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); }

  let mlP = null;
  function loadML() {
    if (window.maplibregl && window.maplibregl.Map) return Promise.resolve(window.maplibregl);
    if (!document.querySelector(`link[href="${ML_CSS}"]`)) {
      const l = document.createElement("link"); l.rel = "stylesheet"; l.href = ML_CSS; document.head.appendChild(l);
    }
    if (!mlP) {
      mlP = import(ML_JS).then(m => { window.maplibregl = window.maplibregl && window.maplibregl.Map ? window.maplibregl : (m.default && m.default.Map ? m.default : m); return window.maplibregl; });
      mlP.catch(() => { mlP = null; });
    }
    return mlP;
  }
  let todayP = null, todayAt = 0;
  function today() { if (!todayP || Date.now() - todayAt > 30 * 60000) { todayAt = Date.now(); todayP = getJSON("/api/globalmap/today").catch(e => ({ ok: false, error: e.message })); } return todayP; }

  function style() {
    const ras = (tiles, attribution, maxzoom) => ({ type: "raster", tiles, tileSize: 256, maxzoom, attribution });
    return { version: 8, sky: SKY,
      sources: {
        esri: ras([ESRI + "World_Imagery/MapServer/tile/{z}/{y}/{x}"], "Imagery © Esri, Maxar, Earthstar Geographics", 19),
        places: ras([ESRI + "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"], "Places © Esri", 19),
        roads: ras([ESRI + "Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}"], "Roads © Esri", 19),
        night: ras(["https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_Black_Marble/default/2016-01-01/GoogleMapsCompatible_Level8/{z}/{y}/{x}.png"],
          "Night lights: NASA Black Marble 2016 via GIBS", 8),
        topo: ras([ESRI + "World_Topo_Map/MapServer/tile/{z}/{y}/{x}"], "Topographic © Esri", 19),
        dark: ras([ESRI + "Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"], "Dark canvas © Esri, HERE, Garmin, OSM", 16),
        darkRef: ras([ESRI + "Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}"], "© Esri", 16),
        dem: { type: "raster-dem", tiles: ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"],
          encoding: "terrarium", tileSize: 256, maxzoom: 15, attribution: "Terrain: Mapzen / AWS Open Data" },
      },
      layers: [
        { id: "b-bg", type: "background", paint: { "background-color": "#020a12" } },
        { id: "b-esri", type: "raster", source: "esri", layout: { visibility: "none" } },
        { id: "b-night", type: "raster", source: "night", layout: { visibility: "none" } },
        { id: "b-topo", type: "raster", source: "topo", layout: { visibility: "none" } },
        { id: "b-dark", type: "raster", source: "dark", layout: { visibility: "none" } },
        { id: "b-darkref", type: "raster", source: "darkRef", layout: { visibility: "none" } },
        { id: "b-roads", type: "raster", source: "roads", layout: { visibility: "none" }, paint: { "raster-opacity": 0.75 } },
        { id: "b-places", type: "raster", source: "places", layout: { visibility: "none" } },
        // everything a consumer adds goes above this anchor; live/today imagery is inserted below it
        { id: "im-anchor", type: "background", paint: { "background-opacity": 0 } },
      ] };
  }

  function create(host, o) {
    o = Object.assign({ key: host.id || "map", modes: MODE_KEYS, mode: "sat", compact: false, home: null, mask: null,
      cityHome: null, maxZoom: 18, minZoom: 1 }, o || {});
    const modes = MODES.filter(m => o.modes.includes(m[0]));
    let mode = store(o.key + ".mode");
    if (!o.modes.includes(mode)) mode = o.mode;
    host.classList.add("im-host");
    if (o.compact) host.classList.add("im-compact");
    host.innerHTML = `
      <div class="im-map"></div>
      <div class="im-top"><div class="im-modes" role="tablist" aria-label="Map imagery mode">${modes.map(([k, l]) =>
        `<button type="button" role="tab" data-im="${k}" class="${k === mode ? "on" : ""}" aria-selected="${k === mode}">${l}</button>`).join("")}</div></div>
      <div class="im-ctl" role="toolbar" aria-label="Map controls">
        <button type="button" data-ic="in" title="Zoom in" aria-label="Zoom in">+</button>
        <button type="button" data-ic="out" title="Zoom out" aria-label="Zoom out">−</button>
        <button type="button" data-ic="tilt" title="Tilt 3D / flat" aria-label="Tilt">3D</button>
        <button type="button" data-ic="north" title="North up" aria-label="North up"><span class="im-compass">➤</span></button>
        <button type="button" data-ic="orbit" title="Slow cinematic orbit (stops when you touch the map)" aria-pressed="false">⟳</button>
        <button type="button" data-ic="home" title="Back to the whole region" aria-label="Reset view">⌂</button>
        <button type="button" data-ic="fs" title="Full screen (Esc to exit)" aria-label="Full screen">⛶</button>
      </div>
      <div class="im-cap" aria-live="polite"></div>
      <div class="im-load">loading satellite map…</div>
      <div class="im-err" hidden></div>`;
    const $ = s => host.querySelector(s);
    const capEl = $(".im-cap"), errEl = $(".im-err");
    let M = null, ready = false, orbit = false, orbitRaf = 0, orbitLast = 0, liveInfo = null, liveTimer = 0, liveAt = null, started = false;
    const handlers = { mode: [], ready: [] };
    let resolveReady; const readyP = new Promise(r => { resolveReady = r; });
    const capOf = k => (MODES.find(m => m[0] === k) || [])[2] || "";
    let extraCap = "";
    function caption() { capEl.innerHTML = `<span class="im-cap-m">${esc(capOf(mode))}</span>${extraCap ? `<span class="im-cap-x">${extraCap}</span>` : ""}`; }
    caption();

    function vis(id, on) { if (M && M.getLayer(id)) M.setLayoutProperty(id, "visibility", on ? "visible" : "none"); }
    function raster(id, tiles, maxzoom, attribution, paint) {
      const src = "src-" + id, s = M.getSource(src);
      if (s && s._imTiles === tiles[0]) { vis(id, true); return; }
      if (M.getLayer(id)) M.removeLayer(id);
      if (s) M.removeSource(src);
      M.addSource(src, { type: "raster", tiles, tileSize: 256, maxzoom, attribution });
      M.getSource(src)._imTiles = tiles[0];
      M.addLayer({ id, type: "raster", source: src, paint }, "im-anchor");
    }
    function ensureBuildings() {
      if (M.getSource("ofm")) return;
      M.addSource("ofm", { type: "vector", url: "https://tiles.openfreemap.org/planet", attribution: "© OpenFreeMap © OpenMapTiles © OpenStreetMap contributors" });
      M.addLayer({ id: "b-bld", type: "fill-extrusion", source: "ofm", "source-layer": "building", minzoom: 13, layout: { visibility: "none" },
        paint: { "fill-extrusion-color": ["interpolate", ["linear"], ["coalesce", ["get", "render_height"], 0], 0, "#1e3a52", 30, "#3f7aa3", 120, "#8fdcff"],
          "fill-extrusion-height": ["coalesce", ["get", "render_height"], 0], "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
          "fill-extrusion-opacity": 0.85 } }, "im-anchor");
    }
    async function ensureToday() {
      const t = await today();
      if (mode !== "today" || !M) return;
      if (!t.ok) { extraCap = "TODAY unavailable · " + esc(t.error || "?"); caption(); return; }
      raster("l-today", t.tiles, t.maxzoom, esc(t.attribution), { "raster-opacity": ["interpolate", ["linear"], ["zoom"], 8.5, 1, 11, 0], "raster-fade-duration": 0 });
      extraCap = `pass dated <b>${esc(t.date)}</b> UTC · ${esc(t.name)}`; caption();
    }
    async function fetchLive() {
      if (!M || mode !== "live") return;
      const c = M.getCenter();
      try {
        const d = await getJSON(`/api/globalmap/live?lat=${c.lat.toFixed(3)}&lon=${c.lng.toFixed(3)}`);
        if (mode !== "live" || !M) return;
        liveInfo = { ...d, at: Date.now() }; liveAt = [c.lat, c.lng];
        if (d.satellite && d.satellite.ok) raster("l-sat", d.satellite.tiles, d.satellite.maxzoom, esc(d.satellite.attribution),
          { "raster-opacity": ["interpolate", ["linear"], ["zoom"], 2, 0.95, 6, 0.8, 9, 0.5, 13, 0.32], "raster-fade-duration": 0 });
        if (d.radar && d.radar.ok) raster("l-radar", d.radar.tiles, d.radar.maxzoom, esc(d.radar.attribution), { "raster-opacity": 0.75, "raster-fade-duration": 0 });
        liveCap();
      } catch (e) { extraCap = "live layers unavailable · " + esc(e.message); caption(); }
    }
    function liveCap() {
      const L = liveInfo; if (!L || mode !== "live") return;
      const drift = (Date.now() - L.at) / 1000, s = L.satellite, r = L.radar, bits = [];
      bits.push(s && s.ok ? `<b>${esc(s.name)}</b> frame ${hhmm(s.time)} · <b>${agoS(s.age_s + drift)}</b>` : `satellite unavailable · ${esc(s ? s.error : "?")}`);
      bits.push(r && r.ok ? `radar ${hhmm(r.time)} · ${agoS(r.age_s + drift)}` : `radar unavailable`);
      extraCap = bits.join(" · "); caption();
    }
    function setLive(on) {
      clearInterval(liveTimer); liveTimer = 0;
      vis("l-sat", on); vis("l-radar", on);
      if (!on) return;
      fetchLive();
      liveTimer = setInterval(() => { if (mode !== "live" || !visible) return; if (!liveInfo || Date.now() - liveInfo.at > 120000) fetchLive(); else liveCap(); }, 30000);
    }
    function setMode(m, fly) {
      if (!o.modes.includes(m)) return;
      mode = m; store(o.key + ".mode", m); extraCap = "";
      host.querySelectorAll("[data-im]").forEach(b => { const on = b.dataset.im === m; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); });
      caption();
      if (!ready) return;
      if (m === "city") ensureBuildings();
      vis("b-esri", !["night", "topo", "dark"].includes(m)); vis("b-night", m === "night"); vis("b-topo", m === "topo");
      vis("b-dark", m === "dark"); vis("b-darkref", m === "dark"); vis("b-places", ["hybrid", "live", "today"].includes(m));
      vis("b-roads", m === "hybrid"); vis("b-bld", m === "city");
      if (m === "today") ensureToday(); else vis("l-today", false);
      setLive(m === "live");
      try { M.setTerrain(m === "terrain" ? { source: "dem", exaggeration: 1.5 } : null); } catch (e) { /* no terrain support */ }
      M.setMaxZoom(m === "terrain" ? 12 : o.maxZoom);
      handlers.mode.forEach(f => { try { f(m); } catch (e) { console.warn(e); } });
      if (fly === false) return;
      const to = { pitch: m === "terrain" || m === "city" ? 58 : 0, duration: reduced ? 0 : 1300, essential: true };
      if (m === "city" && M.getZoom() < 13 && o.cityHome) Object.assign(to, { center: [o.cityHome.lon, o.cityHome.lat], zoom: 15.2, bearing: -20 });
      M.easeTo(to);
    }
    function maskGeo(outline) {
      const world = [[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]];
      const holes = outline.geometry.coordinates.map(p => p[0].slice().reverse());
      return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [world, ...holes] } };
    }
    function home(ms) {
      if (!M) return;
      stopOrbit();
      if (o.home && o.home.bounds) M.fitBounds(o.home.bounds, { padding: o.home.padding == null ? 18 : o.home.padding, pitch: 0, bearing: 0, duration: ms == null ? (reduced ? 0 : 1200) : ms });
      else M.easeTo({ center: [o.home ? o.home.lon : 0, o.home ? o.home.lat : 20], zoom: o.home ? o.home.zoom : 1.4, pitch: 0, bearing: 0, duration: reduced ? 0 : 1200 });
    }
    function stopOrbit() { orbit = false; cancelAnimationFrame(orbitRaf); orbitRaf = 0; const b = $('[data-ic="orbit"]'); if (b) { b.classList.remove("on"); b.setAttribute("aria-pressed", "false"); } }
    function orbitStep(ts) {
      if (!orbit || !M) return;
      const dt = orbitLast ? Math.min(100, ts - orbitLast) : 16; orbitLast = ts;
      M.setBearing(M.getBearing() + dt * 0.006);
      orbitRaf = requestAnimationFrame(orbitStep);
    }
    function toggleOrbit() {
      if (orbit) { stopOrbit(); return; }
      orbit = true; orbitLast = 0;
      const b = $('[data-ic="orbit"]'); b.classList.add("on"); b.setAttribute("aria-pressed", "true");
      if (M.getPitch() < 30) M.easeTo({ pitch: 50, duration: reduced ? 0 : 900 });
      orbitRaf = requestAnimationFrame(orbitStep);
    }
    function toggleFs() {
      const fsEl = document.fullscreenElement;
      if (fsEl === host) { document.exitFullscreen().catch(() => {}); return; }
      if (host.classList.contains("im-fs")) { host.classList.remove("im-fs"); setTimeout(() => M && M.resize(), 60); return; }
      if (host.requestFullscreen) host.requestFullscreen().catch(() => { host.classList.add("im-fs"); setTimeout(() => M && M.resize(), 60); });
      else { host.classList.add("im-fs"); setTimeout(() => M && M.resize(), 60); }
    }
    document.addEventListener("fullscreenchange", () => setTimeout(() => M && M.resize(), 80));
    document.addEventListener("keydown", e => { if (e.key === "Escape" && host.classList.contains("im-fs")) { host.classList.remove("im-fs"); setTimeout(() => M && M.resize(), 60); } });

    host.addEventListener("click", e => {
      const mb = e.target.closest("[data-im]");
      if (mb) { setMode(mb.dataset.im); return; }
      const cb = e.target.closest("[data-ic]"); if (!cb || !M) return;
      const a = cb.dataset.ic;
      if (a === "in") M.zoomIn(); else if (a === "out") M.zoomOut();
      else if (a === "tilt") M.easeTo({ pitch: M.getPitch() > 20 ? 0 : 58, duration: reduced ? 0 : 700 });
      else if (a === "north") M.easeTo({ bearing: 0, pitch: 0, duration: reduced ? 0 : 600 });
      else if (a === "orbit") toggleOrbit();
      else if (a === "home") home();
      else if (a === "fs") toggleFs();
      else if (a === "retry") { errEl.hidden = true; started = false; start(); }
    });

    let visible = false;
    async function start() {
      if (started) return; started = true;
      try {
        const ml = await loadML();
        const b = o.home && o.home.bounds;
        M = new ml.Map({ container: $(".im-map"), style: style(), attributionControl: { compact: true },
          bounds: b || undefined, fitBoundsOptions: b ? { padding: o.home.padding == null ? 18 : o.home.padding } : undefined,
          center: b ? undefined : [o.home ? o.home.lon : 0, o.home ? o.home.lat : 20], zoom: b ? undefined : (o.home ? o.home.zoom : 1.4),
          minZoom: o.minZoom, maxZoom: o.maxZoom, maxPitch: 75, dragRotate: true, renderWorldCopies: !o.home || !o.home.bounds,
          fadeDuration: 0, canvasContextAttributes: { antialias: true } });
        M.on("error", e => { const m = e && e.error && e.error.message || ""; if (/WebGL|context/i.test(m)) fail(e.error); });
        M.on("rotate", () => { const c = $(".im-compass"); if (c) c.style.transform = `rotateX(${M.getPitch() * 0.6}deg) rotate(${-90 - M.getBearing()}deg)`; });
        M.on("pitch", () => { const c = $(".im-compass"); if (c) c.style.transform = `rotateX(${M.getPitch() * 0.6}deg) rotate(${-90 - M.getBearing()}deg)`; });
        for (const ev of ["mousedown", "touchstart", "wheel"]) M.getCanvasContainer().addEventListener(ev, () => { if (orbit) stopOrbit(); }, { passive: true });
        M.on("moveend", () => {
          if (mode === "live" && liveAt) { const c = M.getCenter(); if (Math.abs(c.lng - liveAt[1]) > 12 || Math.abs(c.lat - liveAt[0]) > 12) fetchLive(); }
        });
        await new Promise((res, rej) => { M.once("load", res); setTimeout(() => rej(new Error("map style did not load in 25 s")), 25000); });
        if (o.mask) {
          M.addSource("im-mask", { type: "geojson", data: maskGeo(o.mask) });
          M.addLayer({ id: "im-mask", type: "fill", source: "im-mask", paint: { "fill-color": "#01060c", "fill-opacity": 0.55 } });
        }
        ready = true; $(".im-load").remove();
        const at = M.getContainer().querySelector(".maplibregl-ctrl-attrib"); if (at) at.classList.remove("maplibregl-compact-show");
        setMode(mode, false);
        resolveReady(M);
        handlers.ready.forEach(f => { try { f(M); } catch (e) { console.warn(e); } });
      } catch (e) { fail(e); }
    }
    function fail(e) {
      ready = false; started = false;
      try { if (M) M.remove(); } catch (x) { /* gone */ }
      M = null;
      const l = $(".im-load"); if (l) l.remove();
      errEl.innerHTML = `<b>IMMERSIVE MAP UNAVAILABLE</b><span>${esc(e && e.message || e)}</span><span>Needs WebGL and unpkg.com (MapLibre).</span>
        <button type="button" data-ic="retry">RETRY</button>`;
      errEl.hidden = false;
      if (o.onFail) try { o.onFail(e); } catch (x) { /* ignore */ }
    }
    // start only when the panel scrolls into view (saves GPU + network for panels never seen)
    const io = "IntersectionObserver" in window ? new IntersectionObserver(es => {
      visible = es.some(x => x.isIntersecting);
      if (visible) start();
      if (!visible && orbit) stopOrbit();
    }, { rootMargin: "200px" }) : null;
    if (io) io.observe(host); else { visible = true; start(); }
    document.addEventListener("visibilitychange", () => { if (document.hidden && orbit) stopOrbit(); });

    // gentle cinematic tour over a list of stops: [{lat, lon, zoom, onArrive}] — stops on any touch
    let tourT = 0, touring = false;
    function tour(stops, opt) {
      stopTour();
      if (!M || !stops.length) return;
      touring = true; let n = 0;
      const dwell = (opt && opt.dwell) || 5200;
      const stop = () => stopTour();
      M.getCanvasContainer().addEventListener("mousedown", stop, { once: true });
      M.getCanvasContainer().addEventListener("wheel", stop, { once: true, passive: true });
      const step = () => {
        if (!touring || !M) return;
        const s = stops[n % stops.length]; n++;
        M.flyTo({ center: [s.lon, s.lat], zoom: s.zoom || 9, pitch: 50, bearing: (n * 37) % 360 - 180, speed: 0.9, curve: 1.4, essential: true, ...(reduced ? { duration: 0 } : {}) });
        M.once("moveend", () => { if (!touring) return; try { s.onArrive && s.onArrive(s); } catch (e) { /* ignore */ } tourT = setTimeout(step, dwell); });
        if (opt && opt.onStep) opt.onStep(n, stops.length);
      };
      step();
    }
    function stopTour() { const was = touring; touring = false; clearTimeout(tourT); if (was && o.onTourEnd) o.onTourEnd(); }

    return {
      get map() { return M; }, get ready() { return ready; }, get mode() { return mode; }, whenReady: () => readyP,
      setMode, home, tour, stopTour, get touring() { return touring; },
      flyTo(lat, lon, zoom, opt) { if (!M) return; stopOrbit(); M.flyTo(Object.assign({ center: [lon, lat], zoom: zoom || 9, essential: true }, reduced ? { duration: 0 } : {}, opt || {})); },   /* never pass duration: undefined (breaks flyTo) */
      on(ev, f) { (handlers[ev] = handlers[ev] || []).push(f); if (ev === "ready" && ready) f(M); },
      setCaption(t) { extraCap = t; caption(); },
      popup(lngLat, html) {
        if (!M) return null;
        if (this._pop) this._pop.remove();
        this._pop = new window.maplibregl.Popup({ className: "im-pop", closeButton: true, maxWidth: "300px", offset: 10 }).setLngLat(lngLat).setHTML(html).addTo(M);
        return this._pop;
      },
      state() { return M ? { mode, lat: +M.getCenter().lat.toFixed(4), lon: +M.getCenter().lng.toFixed(4), zoom: +M.getZoom().toFixed(2), pitch: Math.round(M.getPitch()) } : { mode, ready: false }; },
    };
  }

  window.ImmersiveMap = { create, loadML, MODES, MODE_KEYS };
})();
