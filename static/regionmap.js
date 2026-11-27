/* INDIA + TELANGANA IMMERSIVE MAPS (v8.8) — the two side maps beside the global activity map.
   Engine: static/immersivemap.js (all nine imagery modes incl. ◉ LIVE satellite + radar).
   Data:   GET /api/regionmap/{india|telangana} (regionmap.py) — live Open-Meteo weather and CAMS
           air quality at every state / district point, active hazards inside the official boundary
           (USGS · GDACS · NASA EONET · NDMA SACHET) and aircraft overhead (OpenSky), each with its
           source and fetch time. Boundaries: static/geo/india_ts_ll.json (Survey of India depiction).
   The SVG maps stay in the page as the fallback when WebGL / the CDN is unavailable. */
(function () {
  "use strict";
  if (!window.ImmersiveMap) return;
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ago = ts => { if (!ts) return "—"; const s = Math.max(0, Date.now() / 1000 - ts); return s < 90 ? Math.round(s) + " s ago" : s < 5400 ? Math.round(s / 60) + " min ago" : (s / 3600).toFixed(1) + " h ago"; };
  const SEV = ["#6b94b4", "#4dd2ff", "#ffd84d", "#ff8c42", "#ff3b5c"];
  const SEV_NAME = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"];
  const METRICS = {
    temp: { label: "TEMP", unit: "°C", get: p => p.wx && p.wx.t, fmt: v => Math.round(v) + "°",
      ramp: [-10, "#5b6cff", 0, "#39b8ff", 15, "#39ffb0", 24, "#ffd84d", 32, "#ff8c42", 40, "#ff3b5c"] },
    rain: { label: "RAIN", unit: "mm/h", get: p => p.wx && p.wx.rain, fmt: v => v > 0 ? (+v).toFixed(1) : "0",
      ramp: [0, "#1b3448", 0.1, "#4dd2ff", 2, "#2f7bff", 8, "#9a5bff"] },
    aqi: { label: "AIR", unit: "US AQI", get: p => p.aq && p.aq.aqi, fmt: v => String(Math.round(v)),
      ramp: [0, "#39ff9f", 50, "#39ff9f", 100, "#ffd84d", 150, "#ff8c42", 200, "#ff3b5c", 300, "#b03bff"] },
    wind: { label: "WIND", unit: "km/h", get: p => p.wx && p.wx.wind, fmt: v => Math.round(v) + "",
      ramp: [0, "#1e3a52", 8, "#4dd2ff", 20, "#ffd84d", 40, "#ff3b5c"] },
    alerts: { label: "ALERTS", unit: "active alerts", get: p => p._alerts || 0, fmt: v => v ? String(v) : "",
      ramp: [0, "#10202c", 1, "#ffd84d", 3, "#ff8c42", 6, "#ff3b5c"] },
  };
  function rampColor(ramp, v) {
    if (v == null || !isFinite(v)) return "#555";
    if (v <= ramp[0]) return ramp[1];
    for (let i = 2; i < ramp.length; i += 2) if (v <= ramp[i]) return ramp[i + 1];
    return ramp[ramp.length - 1];
  }
  function inRing(lon, lat, r) {
    let inside = false;
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
      const [xi, yi] = r[i], [xj, yj] = r[j];
      if ((yi > lat) !== (yj > lat) && lon < (xj - xi) * (lat - yi) / ((yj - yi) || 1e-12) + xi) inside = !inside;
    }
    return inside;
  }
  const inFeature = (f, lat, lon) => f.geometry.coordinates.some(p => inRing(lon, lat, p[0]));
  const bbox = fc => { let a = 180, b = 90, c = -180, d = -90; for (const f of fc) for (const p of f.geometry.coordinates) for (const [x, y] of p[0]) { if (x < a) a = x; if (y < b) b = y; if (x > c) c = x; if (y > d) d = y; } return [[a, b], [c, d]]; };
  const wdir = d => d == null ? "" : ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round(((d % 360) / 45)) % 8];
  let GEO = null;
  // retried: a single network blip (e.g. ERR_NETWORK_CHANGED on Wi-Fi roam) must not drop to the classic map
  async function fetchGeo() {
    for (let n = 0; n < 5; n++) {
      try { const r = await fetch("/static/geo/india_ts_ll.json"); if (r.ok) return (GEO = await r.json()); } catch (e) { /* retry */ }
      await new Promise(res => setTimeout(res, 1500 * (n + 1)));
    }
    return null;
  }
  const geoP = fetchGeo();

  const REGIONS = {
    india: { panel: ".india-panel", title: "INDIA · LIVE NATIONAL GRID", unitName: "states/UTs", polys: g => g.india.states.features,
      outline: g => g.india.outline, cityHome: { lat: 17.385, lon: 78.4867 }, maxBoundsPad: 12, chipKinds: ["city"], tourZoom: 9.5 },
    telangana: { panel: ".telangana-panel", title: "TELANGANA · LIVE STATE GRID", unitName: "districts", polys: g => g.telangana.districts.features,
      outline: g => g.telangana.outline, cityHome: { lat: 17.385, lon: 78.4867 }, maxBoundsPad: 3, chipKinds: ["district"], tourZoom: 10.5 },
  };

  function mountRegion(key) {
    const R = REGIONS[key];
    const panel = document.querySelector(R.panel);
    if (!panel) return null;
    const stage = panel.querySelector(".side-map-stage");
    const wrap = document.createElement("div");
    wrap.className = "rm-wrap";
    wrap.innerHTML = `
      <div class="rm-bar" role="toolbar" aria-label="${key} map data">
        <div class="rm-metrics">${Object.entries(METRICS).map(([k, m]) => `<button type="button" data-rm="${k}" title="Colour the map by ${m.unit}">${m.label}</button>`).join("")}</div>
        <div class="rm-tog">
          <button type="button" data-rt="air" aria-pressed="false" title="Aircraft over ${key === "india" ? "India" : "Telangana"} right now (OpenSky)">✈</button>
          <button type="button" data-rt="tour" aria-pressed="false" title="Cinematic fly-through with the live readings at each stop">▶ TOUR</button>
        </div>
      </div>
      <div class="rm-host" id="rm-${key}"></div>
      <div class="rm-tip" hidden></div>
      <div class="rm-read" aria-live="polite"><span class="rm-wait">connecting to live weather, air quality and hazard feeds…</span></div>`;
    stage.insertAdjacentElement("afterend", wrap);
    const host = wrap.querySelector(".rm-host"), tip = wrap.querySelector(".rm-tip"), read = wrap.querySelector(".rm-read");
    const badge = panel.querySelector(".title-badge"), titleSpan = panel.querySelector(".panel-title > span");
    let metric = (() => { try { const v = localStorage.getItem("rm." + key + ".metric"); return METRICS[v] ? v : "temp"; } catch (e) { return "temp"; } })();
    let data = null, polys = null, ctl = null, markers = [], evMarkers = [], airOn = false, airT = 0, pollT = 0, lastFlights = null;

    function setMetric(m) {
      metric = m; try { localStorage.setItem("rm." + key + ".metric", m); } catch (e) { /* private mode */ }
      wrap.querySelectorAll("[data-rm]").forEach(b => b.classList.toggle("on", b.dataset.rm === m));
      paint();
    }
    wrap.querySelectorAll("[data-rm]").forEach(b => b.classList.toggle("on", b.dataset.rm === metric));

    function pointFor(f) { return data && data.points.find(p => p.name === f.properties.name && p.kind !== "city"); }
    function paint() {
      if (!ctl || !ctl.ready || !polys) return;
      const M = ctl.map, mt = METRICS[metric];
      for (const f of polys) {
        const p = pointFor(f);
        const v = p ? mt.get(p) : null;
        f.properties.v = v == null ? null : +v;
        f.properties.c = v == null ? "rgba(0,0,0,0)" : rampColor(mt.ramp, +v);
      }
      M.getSource("rm-polys").setData({ type: "FeatureCollection", features: polys });
      // value chips at the info points
      markers.forEach(m => m.remove()); markers = [];
      if (!data) return;
      for (const p of data.points) {
        if (!R.chipKinds.includes(p.kind) && !(key === "telangana" && p.kind === "city")) continue;
        const v = mt.get(p);
        if (key === "telangana" && p.kind === "city") continue;      // Hyderabad district chip already sits there
        const el = document.createElement("button");
        el.type = "button";
        el.className = "rm-chip" + (p.name === "Hyderabad" ? " hq" : "");
        el.style.setProperty("--c", v == null ? "#557" : rampColor(mt.ramp, +v));
        el.innerHTML = `<i></i><b>${v == null ? "—" : esc(mt.fmt(+v))}</b>${key === "india" ? `<span>${esc(p.name.toUpperCase())}</span>` : ""}`;
        el.title = `${p.name} · ${mt.label} ${v == null ? "unavailable" : mt.fmt(+v) + " " + mt.unit} · click for the live card`;
        el.addEventListener("click", ev => { ev.stopPropagation(); openCard(p, [p.lon, p.lat]); });
        markers.push(new maplibregl.Marker({ element: el, anchor: "center" }).setLngLat([p.lon, p.lat]).addTo(M));
      }
      legend();
    }
    function legend() {
      const mt = METRICS[metric], r = mt.ramp;
      const stops = []; for (let i = 0; i < r.length; i += 2) stops.push(`<span style="--c:${r[i + 1]}">${r[i]}</span>`);
      const ex = data && data.extremes;
      const w = data && data.weather, a = data && data.air;
      const nIn = data ? data.events.items.filter(i => i.inside).length : 0, nNear = data ? data.events.items.length - nIn : 0;
      read.innerHTML = `
        <div class="rm-scale"><em>${esc(mt.label)} · ${esc(mt.unit)}</em>${stops.join("")}</div>
        ${ex ? `<div class="rm-ext">
          ${ex.hottest ? `<button type="button" data-go="${esc(ex.hottest.name)}">🔥 <b>${esc(ex.hottest.name)}</b> ${Math.round(ex.hottest.t)}°</button>` : ""}
          ${ex.coolest ? `<button type="button" data-go="${esc(ex.coolest.name)}">❄ <b>${esc(ex.coolest.name)}</b> ${Math.round(ex.coolest.t)}°</button>` : ""}
          ${ex.worst_air ? `<button type="button" data-go="${esc(ex.worst_air.name)}">◍ <b>${esc(ex.worst_air.name)}</b> AQI ${ex.worst_air.aqi}</button>` : ""}
          ${ex.wettest ? `<button type="button" data-go="${esc(ex.wettest.name)}">☂ <b>${esc(ex.wettest.name)}</b> ${ex.wettest.rain} mm</button>` : ""}
          <button type="button" data-go="__alerts" class="${nIn ? "hot" : ""}">⚠ <b>${nIn}</b> alert${nIn === 1 ? "" : "s"} inside${nNear ? ` · ${nNear} near` : ""}</button>
        </div>` : ""}
        <div class="rm-src">${w ? `<a href="${esc(w.url)}" target="_blank" rel="noopener">Open-Meteo</a> ${w.ok ? ago(w.fetched_at) : "unavailable"}${w.stale ? " · STALE" : ""}` : ""}
          ${a ? ` · <a href="${esc(a.url)}" target="_blank" rel="noopener">CAMS air</a> ${a.ok ? ago(a.fetched_at) : "unavailable"}` : ""}
          · hazards: USGS · GDACS · EONET · NDMA SACHET${airOn && lastFlights ? ` · ✈ ${lastFlights.rows.length} aircraft (OpenSky ${lastFlights.time ? ago(lastFlights.time) : "…"})` : ""}</div>`;
    }
    read.addEventListener("click", e => {
      const b = e.target.closest("[data-go]"); if (!b || !data || !ctl) return;
      if (b.dataset.go === "__alerts") {
        const top = data.events.items[0];
        if (top) { ctl.flyTo(top.lat, top.lon, key === "india" ? 7 : 10); openEvent(top); }
        else ctl.setCaption("no active hazard alerts inside or near the boundary right now");
        return;
      }
      const p = data.points.find(x => x.name === b.dataset.go);
      if (p) { ctl.flyTo(p.lat, p.lon, R.tourZoom, { pitch: 45 }); openCard(p, [p.lon, p.lat]); }
    });

    function cardHTML(p) {
      const w = p.wx, a = p.aq;
      const inside = data.events.items.filter(i => {
        const f = polys && polys.find(x => x.properties.name === p.name);
        return f ? inFeature(f, i.lat, i.lon) : false;
      });
      const kind = p.kind === "district" ? "DISTRICT · Telangana" : p.kind === "state" ? "STATE / UT · India" : "CITY";
      return `<div class="rm-card">
        <div class="rm-card-k">${kind}${p.kind !== "city" ? " · reading at the polygon's centre" : ""}</div>
        <div class="rm-card-t">${esc(p.name)}</div>
        ${w ? `<div class="rm-card-big"><b>${Math.round(w.t)}°C</b><span>${esc(w.desc)} · feels ${Math.round(w.feels)}°</span></div>
          <dl><div><dt>HUMIDITY</dt><dd>${w.rh}%</dd></div><div><dt>RAIN NOW</dt><dd>${w.rain} mm</dd></div>
          <div><dt>WIND</dt><dd>${Math.round(w.wind)} km/h ${wdir(w.wdir)}</dd></div><div><dt>CLOUD</dt><dd>${w.cloud}%</dd></div>
          ${a ? `<div><dt>AIR (US AQI)</dt><dd style="color:${rampColor(METRICS.aqi.ramp, a.aqi)}">${a.aqi} · ${esc(a.band)}</dd></div><div><dt>PM2.5 / PM10</dt><dd>${a.pm25} / ${a.pm10} µg/m³</dd></div>` : ""}
          </dl><div class="rm-card-s">Open-Meteo · observed model time ${esc(w.time || "?")} local${a ? " · CAMS air " + esc(a.time || "") : ""}</div>`
        : `<div class="rm-card-s">live weather unavailable for this point right now</div>`}
        ${inside.length ? `<div class="rm-card-ev"><b>${inside.length} ACTIVE ALERT${inside.length > 1 ? "S" : ""}</b>${inside.slice(0, 4).map(i =>
          `<div style="--c:${SEV[i.severity]}"><i></i>${esc(SEV_NAME[i.severity])} · ${esc(i.title)} <small>${esc(i.source)}</small></div>`).join("")}</div>` : ""}
        <div class="rm-card-act"><button type="button" data-ca="close" data-lat="${p.lat}" data-lon="${p.lon}">FLY CLOSER</button>
          <button type="button" data-ca="global" data-lat="${p.lat}" data-lon="${p.lon}" data-name="${esc(p.name)}">ON GLOBAL MAP</button></div>
      </div>`;
    }
    function openCard(p, ll) { if (ctl) ctl.popup(ll, cardHTML(p)); }
    function openEvent(i) {
      if (!ctl) return;
      ctl.popup([i.lon, i.lat], `<div class="rm-card"><div class="rm-card-k" style="color:${SEV[i.severity]}">${esc(SEV_NAME[i.severity])} · ${esc((i.type || "").toUpperCase())}${i.inside ? "" : ` · ${i.outside_km} km outside the boundary`}</div>
        <div class="rm-card-t">${esc(i.title)}</div><div class="rm-card-s">${esc(i.detail || "")}</div>
        <div class="rm-card-s">${esc(i.source)} · ${i.time ? ago(i.time) : "time not given"}${i.stale ? " · feed STALE" : ""}</div>
        <div class="rm-card-act">${i.link && /^https?:\/\//.test(i.link) ? `<a href="${esc(i.link)}" target="_blank" rel="noopener">SOURCE ↗</a>` : ""}
          <button type="button" data-ca="global" data-lat="${i.lat}" data-lon="${i.lon}" data-name="${esc(i.title)}">ON GLOBAL MAP</button></div></div>`);
    }
    host.addEventListener("click", e => {
      const b = e.target.closest("[data-ca]"); if (!b) return;
      const lat = +b.dataset.lat, lon = +b.dataset.lon;
      if (b.dataset.ca === "close") ctl.flyTo(lat, lon, Math.max(ctl.map.getZoom() + 2.5, key === "india" ? 8 : 11), { pitch: 50 });
      else if (window.bucsShowOnMap) window.bucsShowOnMap({ lat, lon, label: b.dataset.name });
    });

    function renderEvents() {
      evMarkers.forEach(m => m.remove()); evMarkers = [];
      if (!data || !ctl || !ctl.ready) return;
      for (const i of data.events.items.slice(0, 120)) {
        const el = document.createElement("button");
        el.type = "button";
        el.className = `rm-ev s${i.severity}${i.inside ? "" : " out"}`;
        el.style.setProperty("--c", SEV[i.severity]);
        el.title = `${SEV_NAME[i.severity]} · ${i.title} · ${i.source}`;
        el.addEventListener("click", ev => { ev.stopPropagation(); openEvent(i); });
        evMarkers.push(new maplibregl.Marker({ element: el }).setLngLat([i.lon, i.lat]).addTo(ctl.map));
      }
      // per-polygon alert counts (ALERTS metric) — point-in-polygon on the official outlines
      if (polys) for (const f of polys) {
        const p = pointFor(f); if (!p) continue;
        p._alerts = data.events.items.filter(i => i.inside && inFeature(f, i.lat, i.lon)).length;
      }
    }

    async function load() {
      try {
        const r = await fetch(`/api/regionmap/${key}${airOn ? "?flights=1" : ""}`);
        if (!r.ok) throw new Error("HTTP " + r.status);
        data = await r.json();
        if (airOn && data.flights) { lastFlights = data.flights; drawFlights(); }
        renderEvents(); paint();
        const n = data.events.items.filter(i => i.inside).length;
        if (badge) badge.textContent = `LIVE · ${data.points.filter(p => p.kind !== "city").length} ${R.unitName.toUpperCase()}${n ? ` · ⚠ ${n}` : ""}`;
      } catch (e) {
        read.innerHTML = `<span class="rm-wait bad">live region data unavailable · ${esc(e.message)} · retrying</span>`;
      }
    }
    function drawFlights() {
      if (!ctl || !ctl.ready) return;
      const f = lastFlights && airOn ? lastFlights.rows.map(r => ({ type: "Feature", properties: { cs: r[1] || r[0], alt: r[5], v: r[6], trk: r[7] || 0, icao: r[0], cty: r[2] },
        geometry: { type: "Point", coordinates: [r[3], r[4]] } })) : [];
      ctl.map.getSource("rm-air").setData({ type: "FeatureCollection", features: f });
      legend();
    }
    function setAir(on) {
      airOn = on;
      const b = wrap.querySelector('[data-rt="air"]'); b.classList.toggle("on", on); b.setAttribute("aria-pressed", on);
      clearInterval(airT);
      if (on) { load(); airT = setInterval(() => { if (!document.hidden) load(); }, 60000); } else { lastFlights = null; drawFlights(); }
    }
    function setTour(on) {
      const b = wrap.querySelector('[data-rt="tour"]'); b.classList.toggle("on", on); b.setAttribute("aria-pressed", on);
      if (!ctl || !data) return;
      if (!on) { ctl.stopTour(); return; }
      const pts = data.points.filter(p => p.kind === (key === "india" ? "city" : "district") && p.wx);
      ctl.tour(pts.map(p => ({ lat: p.lat, lon: p.lon, zoom: R.tourZoom, onArrive: () => openCard(p, [p.lon, p.lat]) })), { dwell: 5200 });
    }
    wrap.querySelector(".rm-bar").addEventListener("click", e => {
      const m = e.target.closest("[data-rm]"); if (m) { setMetric(m.dataset.rm); return; }
      const t = e.target.closest("[data-rt]"); if (!t) return;
      if (t.dataset.rt === "air") setAir(!airOn);
      if (t.dataset.rt === "tour") setTour(t.getAttribute("aria-pressed") !== "true");
    });

    geoP.then(g => {
      if (!g) { read.innerHTML = `<span class="rm-wait bad">boundary file unavailable · showing the classic map</span>`; wrap.classList.add("rm-off"); return; }
      polys = R.polys(g).map(f => ({ ...f, properties: { ...f.properties } }));
      const outline = R.outline(g), bb = bbox([outline]);
      const pad = R.maxBoundsPad;
      ctl = window.ImmersiveMap.create(host, {
        key: "rm-" + key, compact: true, mode: "dark", mask: outline, cityHome: R.cityHome,
        home: { bounds: bb, padding: 8 }, minZoom: key === "india" ? 2.5 : 5,
        onFail: () => { wrap.classList.add("rm-off"); stage.hidden = false; stage.style.display = ""; },
        onTourEnd: () => { const b = wrap.querySelector('[data-rt="tour"]'); b.classList.remove("on"); b.setAttribute("aria-pressed", "false"); },
      });
      ctl.on("ready", M => {
        stage.hidden = true; stage.style.display = "none";   // the immersive map replaces the classic SVG (CSS display:flex beats [hidden])
        if (titleSpan) titleSpan.textContent = R.title;
        M.setMaxBounds([[bb[0][0] - pad, bb[0][1] - pad], [bb[1][0] + pad, bb[1][1] + pad]]);
        M.addSource("rm-polys", { type: "geojson", data: { type: "FeatureCollection", features: polys }, promoteId: "name" });
        M.addSource("rm-outline", { type: "geojson", data: outline });
        M.addSource("rm-air", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        M.addLayer({ id: "rm-fill", type: "fill", source: "rm-polys", paint: { "fill-color": ["get", "c"],
          "fill-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 0.62, 0.36] } });
        M.addLayer({ id: "rm-line", type: "line", source: "rm-polys", paint: { "line-color": "rgba(140,230,255,0.55)",
          "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 2.2, 0.7] } });
        M.addLayer({ id: "rm-outline-glow", type: "line", source: "rm-outline", paint: { "line-color": "#00d9ff", "line-width": 6, "line-opacity": 0.18, "line-blur": 4 } });
        M.addLayer({ id: "rm-outline", type: "line", source: "rm-outline", paint: { "line-color": "#00e5ff", "line-width": 1.6 } });
        M.addLayer({ id: "rm-air", type: "circle", source: "rm-air", paint: { "circle-color": "#8fe3ff", "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 2, 8, 3.5, 12, 5],
          "circle-stroke-color": "rgba(0,0,0,0.7)", "circle-stroke-width": 0.8 } });
        let hov = null;
        M.on("mousemove", "rm-fill", e => {
          const f = e.features && e.features[0]; if (!f) return;
          if (hov !== f.id) { if (hov != null) M.setFeatureState({ source: "rm-polys", id: hov }, { hover: false }); hov = f.id; M.setFeatureState({ source: "rm-polys", id: hov }, { hover: true }); }
          const p = pointFor(f), mt = METRICS[metric], v = p ? mt.get(p) : null;
          tip.innerHTML = `<b>${esc(f.properties.name)}</b> · ${esc(mt.label)} ${v == null ? "—" : esc(mt.fmt(+v)) + " " + esc(mt.unit)}${p && p.wx ? ` · ${esc(p.wx.desc)}` : ""}`;
          tip.hidden = false;
          const r = wrap.getBoundingClientRect();
          tip.style.left = Math.min(r.width - 10, e.originalEvent.clientX - r.left + 12) + "px";
          tip.style.top = (e.originalEvent.clientY - r.top + 12) + "px";
          M.getCanvas().style.cursor = "pointer";
        });
        M.on("mouseleave", "rm-fill", () => { if (hov != null) M.setFeatureState({ source: "rm-polys", id: hov }, { hover: false }); hov = null; tip.hidden = true; M.getCanvas().style.cursor = ""; });
        M.on("click", "rm-fill", e => { const f = e.features && e.features[0]; const p = f && pointFor(f); if (p) openCard(p, e.lngLat); });
        M.on("click", "rm-air", e => {
          const f = e.features && e.features[0]; if (!f) return;
          const q = f.properties, link = /^[0-9a-f]{6}$/i.test(q.icao) ? `https://globe.adsbexchange.com/?icao=${q.icao}` : null;
          ctl.popup(e.lngLat, `<div class="rm-card"><div class="rm-card-k">AIRCRAFT · OpenSky live position</div><div class="rm-card-t">${esc(q.cs)}</div>
            <dl><div><dt>ALTITUDE</dt><dd>${q.alt != null ? Math.round(q.alt).toLocaleString() + " m" : "—"}</dd></div><div><dt>SPEED</dt><dd>${q.v != null ? Math.round(q.v * 3.6) + " km/h" : "—"}</dd></div>
            <div><dt>HEADING</dt><dd>${Math.round(q.trk)}°</dd></div><div><dt>REGISTERED</dt><dd>${esc(q.cty || "—")}</dd></div></dl>
            <div class="rm-card-act">${link ? `<a href="${link}" target="_blank" rel="noopener">TRACK ON ADS-B EXCHANGE ↗</a>` : ""}</div></div>`);
        });
        M.on("mouseenter", "rm-air", () => { M.getCanvas().style.cursor = "pointer"; });
        M.on("mouseleave", "rm-air", () => { M.getCanvas().style.cursor = ""; });
        renderEvents(); paint();
      });
      load();
      pollT = setInterval(() => { if (!document.hidden) load(); }, 5 * 60000);
    });
    return { key, get ctl() { return ctl; }, get data() { return data; }, setMetric, setAir, setTour, load };
  }

  const maps = {};
  function init() { for (const k of Object.keys(REGIONS)) maps[k] = mountRegion(k); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();

  // Voice / agent control (app.js DASH actions call these)
  window.RegionMaps = {
    get maps() { return maps; },
    mode(region, m) { const r = maps[region]; if (r && r.ctl) r.ctl.setMode(m); return !!(r && r.ctl); },
    metric(region, m) { const r = maps[region]; if (r && METRICS[m]) { r.setMetric(m); return true; } return false; },
    tour(region, on) { const r = maps[region]; if (r) r.setTour(on !== false); return !!r; },
    air(region, on) { const r = maps[region]; if (r) r.setAir(on !== false); return !!r; },
    state() { const o = {}; for (const [k, r] of Object.entries(maps)) o[k] = r && r.ctl ? r.ctl.state() : null; return o; },
  };
})();
