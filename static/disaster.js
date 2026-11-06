/* DISASTER RESPONSE · REAL-TIME EVENTS — renders into #disaster-mount.
   Data: GET /api/disaster (disaster.py). Every item names its source and fetch time;
   a feed that can't be reached shows "unavailable"/"stale", never invented values.
   Interactions: stat tiles + type chips filter the list; every event (list row or map
   point) opens a detail view with its source, timestamps, distance, a SHOW ON MAP action
   (window.GlobalMap.focus when globalmap.js is loaded) and the matching preparedness steps.
   High-severity alerts arrive over the WebSocket as "log" messages (app.js logEvent). */
(function () {
  "use strict";
  const mount = document.getElementById("disaster-mount");
  if (!mount) return;

  const W = 720, H = 360;                       // equirectangular: x=(lon+180)*2, y=(90-lat)*2
  const SEV_CLASS = ["info", "low", "med", "high", "crit"];
  const ABBR = { earthquake: "EQ", cyclone: "TC", storm: "ST", flood: "FL", rain: "RN", thunderstorm: "LT",
    wildfire: "WF", volcano: "VO", drought: "DR", tsunami: "TSU", coastal: "CW", heat: "HT", landslide: "LS",
    ice: "ICE", dust: "DU", snow: "SN", vulnerability: "CVE", service: "SVC", outage: "NET", other: "··" };
  let data = null, filter = { k: "all" }, prepSel = null, tab = "services", landDrawn = false, busy = false;
  // view prefs (filter, tab, preparedness pick) survive a reload; per-browser only, never server state
  function pref(k, v) {
    try {
      if (v === undefined) return JSON.parse(localStorage.getItem("dis." + k));
      localStorage.setItem("dis." + k, JSON.stringify(v));
    } catch (e) { return null; }
  }
  {
    const f = pref("filter");
    if (f && typeof f.k === "string" && ["all", "cat", "near", "sev", "type"].includes(f.k)) filter = { k: f.k, v: f.v };
    const t = pref("tab"); if (["services", "prep", "sources"].includes(t)) tab = t;
    const p = pref("prep"); if (typeof p === "string") prepSel = p;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  // Some feeds arrive UTF-8-decoded-as-cp1252 ("Â·", "â€”"). Repair those on display only.
  const CP1252 = { "€": 0x80, "‚": 0x82, "ƒ": 0x83, "„": 0x84, "…": 0x85, "†": 0x86, "‡": 0x87, "ˆ": 0x88, "‰": 0x89,
    "Š": 0x8A, "‹": 0x8B, "Œ": 0x8C, "Ž": 0x8E, "‘": 0x91, "’": 0x92, "“": 0x93, "”": 0x94, "•": 0x95, "–": 0x96,
    "—": 0x97, "˜": 0x98, "™": 0x99, "š": 0x9A, "›": 0x9B, "œ": 0x9C, "ž": 0x9E, "Ÿ": 0x9F };
  function fix(s) {
    s = String(s == null ? "" : s);
    if (!/[ÂÃâ][\u0080-ÿ€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ]/.test(s)) return s;
    try {
      const bytes = [];
      for (const ch of s) {
        const c = ch.codePointAt(0);
        if (c < 0x100) bytes.push(c); else if (CP1252[ch] != null) bytes.push(CP1252[ch]); else return s;
      }
      return new TextDecoder("utf-8", { fatal: true }).decode(new Uint8Array(bytes));
    } catch (e) { return s; }
  }
  function safeUrl(u) { return typeof u === "string" && /^https?:\/\/[^\s"'<>]+$/.test(u) ? u : null; }
  function ago(ts) {
    if (!ts) return "—";
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 90) return Math.round(s) + "s ago";
    if (s < 5400) return Math.round(s / 60) + "m ago";
    if (s < 172800) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }
  function clock(ts) { return ts ? new Date(ts * 1000).toTimeString().slice(0, 5) : "—"; }
  function xy(lat, lon) { return [(lon + 180) * (W / 360), (90 - lat) * (H / 180)]; }

  mount.innerHTML = `
  <div class="bs-card dis-card ft-card" id="dis-card">
    <div class="ft-head">
      <div class="ft-head-txt">
        <div class="ft-kicker" id="dis-kicker">LIVE · PUBLIC HAZARD &amp; OUTAGE FEEDS</div>
        <div class="bs-title">DISASTER RESPONSE</div>
        <div class="bs-sub" id="dis-sub">USGS · GDACS · NASA EONET · NDMA SACHET · CISA KEV · STATUSPAGE · IODA · loading…</div>
      </div>
      <div class="ft-actions">
        <button class="cmd-btn" id="dis-refresh" type="button" title="Re-fetch every feed now (max once a minute)">↻ REFRESH</button>
      </div>
    </div>
    <div class="ft-stats" id="dis-counts"><div class="ft-skel"></div><div class="ft-skel"></div><div class="ft-skel"></div><div class="ft-skel"></div></div>
    <div class="dis-types" id="dis-types"></div>
    <div class="dis-main">
      <div class="dis-mapwrap">
        <div class="dis-mapbox">
          <canvas class="dis-land-cv" id="dis-land" width="${W * 2}" height="${H * 2}" aria-hidden="true"></canvas>
          <svg class="dis-map" id="dis-map" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="World map of current physical events: click a point for detail"></svg>
          <div class="dis-map-loc" id="dis-loc"></div>
        </div>
        <div class="dis-legend">
          <span><i class="sev-crit"></i>CRITICAL</span><span><i class="sev-high"></i>HIGH</span>
          <span><i class="sev-med"></i>MEDIUM</span><span><i class="sev-low"></i>LOW</span>
          <span><i class="dis-you"></i>YOU · <span id="dis-nearkm">500</span> KM</span>
        </div>
      </div>
      <div class="dis-tl">
        <div class="dis-tl-head">
          <span class="dis-tl-lab">EVENTS · <b id="dis-flab">ALL</b> · <span id="dis-fn">0</span></span>
          <button type="button" class="ft-link" id="dis-clear" hidden>CLEAR FILTER ✕</button>
        </div>
        <div class="dis-list" id="dis-list"><div class="tf-empty">waiting for feeds…</div></div>
      </div>
    </div>
    <div class="ft-tabs" role="tablist" id="dis-tabs">
      <button type="button" role="tab" data-tab="services" class="on">SERVICE STATUS</button>
      <button type="button" role="tab" data-tab="prep">PREPAREDNESS</button>
      <button type="button" role="tab" data-tab="sources">FEEDS <span id="dis-feeds-n"></span></button>
    </div>
    <div class="ft-pane" data-pane="services">
      <div class="dis-svcs" id="dis-svcs"><div class="tf-empty">—</div></div>
      <div class="dis-digi" id="dis-digi"></div>
    </div>
    <div class="ft-pane" data-pane="prep" hidden>
      <div class="dis-prep-tabs" id="dis-prep-tabs"></div>
      <ul class="dis-prep" id="dis-prep"><li class="tf-empty">guidance appears for active event types</li></ul>
      <div class="ft-note">Source: <span id="dis-prep-src">NDMA / CISA guidance</span></div>
    </div>
    <div class="ft-pane" data-pane="sources" hidden>
      <div class="dis-sources" id="dis-sources"></div>
    </div>
  </div>`;

  // ── map ───────────────────────────────────────────────
  // Land + graticule are painted ONCE onto a canvas (no SVG path markup in the DOM);
  // a data refresh only rewrites the small marker layer in the SVG on top.
  async function drawLand() {
    if (landDrawn) return true;
    const cv = document.getElementById("dis-land");
    if (!cv || typeof d3 === "undefined") return false;
    let feat = null;
    try {
      if (typeof topojson !== "undefined") {
        const w = await (await fetch("https://cdn.jsdelivr.net/npm/world-atlas@2/land-110m.json")).json();
        feat = topojson.feature(w, w.objects.land);
      }
    } catch (e) { feat = null; }
    try { if (!feat && typeof worldFeatures !== "undefined" && worldFeatures) feat = worldFeatures; } catch (e) {}
    const ctx = cv.getContext("2d"), k = cv.width / W;
    ctx.setTransform(k, 0, 0, k, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const css = getComputedStyle(cv);
    ctx.strokeStyle = css.getPropertyValue("--dis-grat").trim() || "rgba(0,217,255,0.12)";
    ctx.lineWidth = 0.5;
    ctx.beginPath();
    for (let lon = -150; lon <= 150; lon += 30) { const x = xy(0, lon)[0]; ctx.moveTo(x, 0); ctx.lineTo(x, H); }
    for (let lat = -60; lat <= 60; lat += 30) { const y = xy(lat, 0)[1]; ctx.moveTo(0, y); ctx.lineTo(W, y); }
    ctx.stroke();
    if (!feat) return false;
    const proj = d3.geoEquirectangular().scale(W / (2 * Math.PI)).translate([W / 2, H / 2]);
    const path = d3.geoPath(proj, ctx);
    ctx.beginPath(); path(feat);
    ctx.fillStyle = css.getPropertyValue("--dis-land").trim() || "rgba(20,60,90,0.9)";
    ctx.fill();
    ctx.strokeStyle = css.getPropertyValue("--dis-coast").trim() || "rgba(0,217,255,0.35)";
    ctx.lineWidth = 0.35; ctx.stroke();
    landDrawn = true;
    return true;
  }
  function renderMap() {
    const svg = document.getElementById("dis-map"); if (!svg || !data) return;
    let html = "";
    const loc = data.location;
    if (loc) {
      const [x, y] = xy(loc.lat, loc.lon), ry = data.near_km / 111 * (H / 180),
        rx = ry / Math.max(0.2, Math.cos(loc.lat * Math.PI / 180));
      html += `<ellipse class="dis-near-ring" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" rx="${rx.toFixed(1)}" ry="${ry.toFixed(1)}"/>` +
        `<circle class="dis-you-dot" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.5"><title>You · ${esc(loc.label)} (${esc(loc.source)})</title></circle>`;
    }
    const shown = new Set(filtered().map(i => i.id));
    const pts = data.items.filter(i => i.category === "physical" && i.lat != null && i.lon != null)
      .slice().sort((a, b) => a.severity - b.severity);   // severe drawn last (on top)
    for (const i of pts) {
      const [x, y] = xy(i.lat, i.lon), r = 2 + i.severity * 1.3, cx = x.toFixed(1), cy = y.toFixed(1);
      html += `<g class="dis-pt sev-${SEV_CLASS[i.severity]}${i.near ? " near" : ""}${shown.has(i.id) ? "" : " dim"}" data-id="${esc(i.id)}" tabindex="0" role="button">` +
        (i.severity >= 3 && shown.has(i.id) ? `<circle class="dis-pulse" cx="${cx}" cy="${cy}" r="${r + 3}"/>` : "") +
        `<circle class="dis-hit" cx="${cx}" cy="${cy}" r="${Math.max(7, r + 3)}"/>` +
        `<circle cx="${cx}" cy="${cy}" r="${r}"/><title>${esc(i.sev_label)} · ${esc(fix(i.title))}
${esc(i.source)}${i.distance_km != null ? " · " + i.distance_km.toLocaleString() + " km from you" : ""} · click for detail</title></g>`;
    }
    svg.innerHTML = html;
  }

  // ── filtering ─────────────────────────────────────────
  function filtered() {
    if (!data) return [];
    const f = filter;
    return data.items.filter(i =>
      f.k === "all" ? true : f.k === "cat" ? i.category === f.v : f.k === "near" ? i.near :
      f.k === "sev" ? i.severity === f.v : f.k === "type" ? i.type === f.v : true);
  }
  function filterLabel() {
    const f = filter;
    return f.k === "all" ? "ALL" : f.k === "near" ? "NEAR YOU" : f.k === "cat" ? f.v.toUpperCase() :
      f.k === "sev" ? (f.v === 4 ? "CRITICAL" : "HIGH") : f.k === "type" ? f.v.toUpperCase() : "ALL";
  }
  function setFilter(f) {
    const same = filter.k === f.k && filter.v === f.v;
    filter = same ? { k: "all" } : f;
    pref("filter", filter);
    if (!data) return;
    renderCounts(); renderList(); renderMap();
  }

  // ── panels ────────────────────────────────────────────
  function renderCounts() {
    const c = data.counts, sev = c.by_severity;
    const on = (k, v) => filter.k === k && filter.v === v ? " on" : "";
    const tile = (k, v, n, lab, cls, tip) =>
      `<button type="button" class="ft-stat${cls ? " " + cls : ""}${on(k, v)}" data-fk="${k}" data-fv="${v == null ? "" : v}" title="${esc(tip)}">
        <b>${n}</b><span>${lab}</span></button>`;
    document.getElementById("dis-counts").innerHTML =
      tile("all", undefined, c.total, "EVENTS", filter.k === "all" ? "on" : "", "Show every event") +
      tile("sev", 4, sev.CRITICAL, "CRITICAL", "crit", "Show critical events only") +
      tile("sev", 3, sev.HIGH, "HIGH", "high", "Show high-severity events only") +
      tile("near", undefined, c.near, `WITHIN ${data.near_km} KM`, "near", data.location ? "Events near your location" : "Location unknown") +
      tile("cat", "physical", c.physical, "PHYSICAL", "", "Natural hazards") +
      tile("cat", "digital", c.digital, "DIGITAL", "", "Exploited CVEs, service and internet outages");
    const types = Object.entries(c.by_type).sort((a, b) => b[1] - a[1]);
    document.getElementById("dis-types").innerHTML = types.map(([t, n]) =>
      `<button type="button" class="dis-chip${filter.k === "type" && filter.v === t ? " on" : ""}" data-type="${esc(t)}" title="Show only ${esc(t)} events">
        <em>${esc(ABBR[t] || "··")}</em>${esc(t)} <b>${n}</b></button>`).join("");
  }
  function renderList() {
    const host = document.getElementById("dis-list");
    const items = filtered();
    document.getElementById("dis-flab").textContent = filterLabel();
    document.getElementById("dis-fn").textContent = items.length;
    document.getElementById("dis-clear").hidden = filter.k === "all";
    if (!items.length) {
      host.innerHTML = `<div class="tf-empty">${filter.k === "near" ? (data.location ? "nothing within " + data.near_km + " km of you right now" : "location unknown: distance unavailable") : "no events match this filter from the reachable feeds"}</div>`;
      return;
    }
    host.innerHTML = items.slice(0, 80).map(i => {
      const dist = i.distance_km != null ? `<span class="dis-dist${i.near ? " near" : ""}">${i.near ? "NEAR · " : ""}${i.distance_km.toLocaleString()} km</span>` : "";
      return `<button type="button" class="dis-ev sev-${SEV_CLASS[i.severity]}${i.near ? " near" : ""}${i.stale ? " stale" : ""}" data-id="${esc(i.id)}" title="Open event detail">
        <span class="dis-ab" title="${esc(i.type)}">${esc(ABBR[i.type] || "··")}</span>
        <span class="dis-ev-body">
          <span class="dis-ev-t"><span class="dis-sev">${esc(i.sev_label)}</span>${esc(fix(i.title))}</span>
          <span class="dis-ev-m">${esc(i.source)}${safeUrl(i.link) ? " ↗" : ""} · ${esc(ago(i.time))}${i.stale ? " · STALE" : ""} ${dist}</span>
        </span>
        <span class="dis-go" aria-hidden="true">›</span></button>`;
    }).join("") + (items.length > 80 ? `<div class="tf-empty">+${items.length - 80} more (lower severity) · narrow with a filter</div>` : "");
  }
  function renderServices() {
    const host = document.getElementById("dis-svcs");
    const svcs = data.services || [];
    const src = data.sources.find(s => s.key === "status");
    if (!svcs.length) { host.innerHTML = `<div class="tf-empty">status pages ${src && src.status === "pending" ? "not fetched yet" : "unavailable"}</div>`; }
    else host.innerHTML = svcs.map(s => {
      const cls = s.indicator === "unavailable" ? "na" : s.indicator === "none" ? "ok" : s.severity >= 3 ? "bad" : "warn";
      const u = safeUrl(s.url);
      return `<a class="dis-svc ${cls}" ${u ? `href="${esc(u)}" target="_blank" rel="noopener noreferrer"` : ""} title="${esc(fix(s.description))}${u ? " · open status page ↗" : ""}">
        <span class="dot"></span><b>${esc(s.name)}</b><span>${esc(fix(s.description))}</span></a>`;
    }).join("");
    const kev = data.sources.find(s => s.key === "kev"), ioda = data.sources.find(s => s.key === "ioda");
    const kevN = data.items.filter(i => i.type === "vulnerability").length;
    const netN = data.items.filter(i => i.type === "outage").length;
    document.getElementById("dis-digi").innerHTML =
      `<button type="button" class="dis-digi-row" data-type="vulnerability"><b>CISA KEV</b> ${kev && kev.status !== "unavailable" && kev.status !== "pending"
        ? `${kevN} newest of ${esc((kev.meta && kev.meta.total) || "?")} actively-exploited CVEs · catalog ${esc((kev.meta && kev.meta.catalog) || "—")}`
        : esc(kev ? kev.status : "—")} ›</button>` +
      `<button type="button" class="dis-digi-row" data-type="outage"><b>IODA</b> ${ioda && ioda.status !== "unavailable" && ioda.status !== "pending"
        ? `${netN} countries with an ongoing internet-outage signal (24 h)` : esc(ioda ? ioda.status : "—")} ›</button>`;
  }
  function renderPrep() {
    const all = data.preparedness_all || {}, active = Object.keys(data.preparedness || {});
    const lead = (data.items.find(i => i.near && all[i.type]) || data.items.find(i => all[i.type]) || {}).type;
    if (!prepSel || !all[prepSel]) prepSel = lead || active[0] || Object.keys(all)[0];
    const keys = [...new Set([...active, ...Object.keys(all)])].filter(k => all[k] && (!["storm", "rain"].includes(k) || active.includes(k)));
    document.getElementById("dis-prep-tabs").innerHTML = keys.map(k =>
      `<button type="button" data-k="${esc(k)}" class="${k === prepSel ? "on" : ""}${active.includes(k) ? " live" : ""}" title="${active.includes(k) ? "active event type" : "reference"}">${esc(ABBR[k] || k)} · ${esc(k)}</button>`).join("");
    document.getElementById("dis-prep").innerHTML = prepSel ?
      `<li class="dis-prep-h">${esc(prepSel.toUpperCase())}${active.includes(prepSel) ? " · ACTIVE NOW" : " · REFERENCE"}</li>` +
      (all[prepSel] || []).map(t => `<li>${esc(fix(t))}</li>`).join("") : `<li class="tf-empty">no guidance loaded</li>`;
    document.getElementById("dis-prep-src").textContent = data.preparedness_source || "NDMA / CISA guidance";
  }
  function renderSources() {
    const okN = data.sources.filter(s => s.status === "ok").length;
    document.getElementById("dis-feeds-n").textContent = `${okN}/${data.sources.length}`;
    document.getElementById("dis-sources").innerHTML = data.sources.map(s => {
      const u = safeUrl(s.home);
      const when = s.last_ok ? "ok " + ago(s.last_ok) : "never";
      return `<a class="dis-src ${esc(s.status)}" ${u ? `href="${esc(u)}" target="_blank" rel="noopener noreferrer"` : ""} title="${esc(s.error || "open the feed's site ↗")}">
        <span class="dot"></span><b>${esc(s.label)}</b>
        <span class="dis-src-m">${esc(String(s.status).toUpperCase())} · ${s.count} items · ${esc(when)}${s.error ? " · " + esc(s.error) : ""}</span></a>`;
    }).join("");
  }
  function renderAll() {
    if (!data) return;
    const c = data.counts, loc = data.location;
    const okN = data.sources.filter(s => s.status === "ok").length;
    const card = document.getElementById("dis-card");
    card.classList.toggle("is-degraded", okN < data.sources.length);
    document.getElementById("dis-kicker").textContent =
      `${okN === data.sources.length ? "LIVE" : okN ? "PARTIAL" : "OFFLINE"} · ${okN}/${data.sources.length} FEEDS · UPDATED ${clock(data.generated)}`;
    document.getElementById("dis-sub").textContent = c.total
      ? `${c.total} events · ${c.by_severity.CRITICAL} critical · ${c.near} within ${data.near_km} km of ${loc ? loc.label : "you (location unknown)"}`
      : "No events reported by the reachable feeds right now.";
    document.getElementById("dis-nearkm").textContent = data.near_km;
    document.getElementById("dis-loc").textContent = loc ? `YOU · ${loc.label}` : "LOCATION UNKNOWN";
    renderCounts(); renderList(); renderServices(); renderPrep(); renderSources(); renderMap();
  }

  // ── detail view ───────────────────────────────────────
  function openEvent(id) {
    const i = data && data.items.find(x => x.id === id);
    if (!i) return;
    const u = safeUrl(i.link);
    const all = data.preparedness_all || {};
    const html = `<div class="mb-grid">
        <div class="mb-stat"><span class="lbl">SEVERITY</span><span class="val">${esc(i.sev_label)}</span></div>
        <div class="mb-stat"><span class="lbl">TYPE</span><span class="val">${esc(String(i.type).toUpperCase())}</span></div>
        <div class="mb-stat"><span class="lbl">EVENT TIME</span><span class="val">${i.time ? esc(new Date(i.time * 1000).toLocaleString()) : "—"}</span></div>
        <div class="mb-stat"><span class="lbl">DISTANCE</span><span class="val">${i.distance_km != null ? esc(i.distance_km.toLocaleString()) + " km" + (i.near ? " · NEAR" : "") : "—"}</span></div>
        ${i.lat != null ? `<div class="mb-stat"><span class="lbl">COORDINATES</span><span class="val">${(+i.lat).toFixed(2)}, ${(+i.lon).toFixed(2)}</span></div>` : ""}
        ${i.until ? `<div class="mb-stat"><span class="lbl">VALID UNTIL</span><span class="val">${esc(new Date(i.until * 1000).toLocaleString())}</span></div>` : ""}
      </div>
      <div class="mb-list">
        ${i.detail ? `<div class="mb-row">${esc(fix(i.detail))}</div>` : ""}
        ${i.stale ? `<div class="mb-row" style="color:var(--warn)">STALE: this feed was unreachable on the last fetch; showing its last known data.</div>` : ""}
      </div>
      <div class="mb-actions">
        ${i.lat != null ? `<button class="cmd-btn" type="button" data-map-lat="${+i.lat}" data-map-lon="${+i.lon}" data-map-id="${esc(i.id)}" data-map-layer="disaster" data-map-label="${esc(fix(i.title))}">SHOW ON MAP</button>` : ""}
        ${u ? `<a class="cmd-btn" href="${esc(u)}" target="_blank" rel="noopener noreferrer">OPEN SOURCE · ${esc(i.source)} ↗</a>` : ""}
        ${all[i.type] ? `<button class="cmd-btn" type="button" data-dis-prep="${esc(i.type)}">WHAT TO DO</button>` : ""}
        <button class="cmd-btn" type="button" data-dis-type="${esc(i.type)}">SIMILAR EVENTS</button>
      </div>
      <div class="si-src"><span class="lbl">DATA SOURCE</span> ${u ? `<a class="src-link" href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(i.source)} ↗</a>` : `${esc(i.source)} (no public page for this item)`} · fetched ${esc(clock(i.fetched_at))} (${esc(ago(i.fetched_at))}) <code>/api/disaster</code></div>`;
    if (typeof openCustomModal === "function") openCustomModal(fix(i.title), `DISASTER RESPONSE · ${String(i.category).toUpperCase()}`, null, html);
  }

  // ── data ──────────────────────────────────────────────
  function locQuery() {
    try {
      if (typeof userLat !== "undefined" && userLat != null && typeof userLon !== "undefined" && userLon != null)
        return `?lat=${Number(userLat).toFixed(4)}&lon=${Number(userLon).toFixed(4)}`;
    } catch (e) {}
    return "";
  }
  let warmups = 0;
  async function load() {
    try {
      const r = await fetch("/api/disaster" + locQuery());
      if (!r.ok) throw new Error("HTTP " + r.status);
      data = await r.json();
      document.getElementById("dis-card").classList.remove("is-error");
      renderAll();
      const pending = Object.values(data.sources || {}).some(s => s && (s.status === "pending" || s.status === undefined));
      if (pending && ++warmups < 24) setTimeout(load, 5000);
    } catch (e) {
      document.getElementById("dis-card").classList.add("is-error");
      document.getElementById("dis-kicker").textContent = "UNAVAILABLE";
      const s = document.getElementById("dis-sub");
      if (s) s.textContent = "Disaster feeds unavailable · " + (e && e.message || "error") + (data ? " · showing last data" : "");
      if (!data) document.getElementById("dis-list").innerHTML = `<div class="tf-empty">could not reach /api/disaster · retrying every minute</div>`;
    }
  }
  async function refresh() {
    if (busy) return; busy = true;
    const b = document.getElementById("dis-refresh"); b.disabled = true; b.textContent = "FETCHING…";
    try {
      const r = await fetch("/api/disaster/refresh" + locQuery(), { method: "POST" });
      const j = await r.json();
      if (r.status === 429) {
        try { logEvent(`<span class="tag">[disaster]</span> refresh limited: try again in ${esc(j.retry_after)} s`, "warn"); } catch (e) {}
        b.textContent = `WAIT ${j.retry_after}s`;
        setTimeout(() => { b.textContent = "↻ REFRESH"; }, 2500);
        return;
      } else if (r.ok) { data = j; renderAll(); }
      else { try { logEvent(`<span class="tag">[disaster]</span> refresh failed: ${esc(j.error || "HTTP " + r.status)}`, "warn"); } catch (e2) {} }
    } catch (e) {
      try { logEvent(`<span class="tag">[disaster]</span> refresh failed`, "warn"); } catch (e2) {}
    } finally { busy = false; b.disabled = false; if (b.textContent === "FETCHING…") b.textContent = "↻ REFRESH"; }
  }

  // ── events ────────────────────────────────────────────
  document.getElementById("dis-refresh").addEventListener("click", refresh);
  document.getElementById("dis-clear").addEventListener("click", () => setFilter({ k: "all" }));
  mount.addEventListener("click", (e) => {
    if (!data) return;
    const st = e.target.closest("[data-fk]");
    if (st) { const v = st.dataset.fv; setFilter({ k: st.dataset.fk, v: st.dataset.fk === "sev" ? +v : v || undefined }); return; }
    const ty = e.target.closest("[data-type]");
    if (ty) { setFilter({ k: "type", v: ty.dataset.type }); document.getElementById("dis-list").scrollTop = 0; return; }
    const ev = e.target.closest(".dis-ev[data-id], .dis-pt[data-id]");
    if (ev) { openEvent(ev.dataset.id); return; }
    const tb = e.target.closest("[data-tab]");
    if (tb) { showTab(tb.dataset.tab); return; }
    const pk = e.target.closest("#dis-prep-tabs button[data-k]");
    if (pk) { prepSel = pk.dataset.k; pref("prep", prepSel); renderPrep(); }
  });
  mount.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.matches(".dis-pt[data-id]")) { e.preventDefault(); openEvent(e.target.dataset.id); }
  });
  function showTab(t) {
    tab = t;
    pref("tab", t);
    mount.querySelectorAll("#dis-tabs [data-tab]").forEach(b => { b.classList.toggle("on", b.dataset.tab === t); b.setAttribute("aria-selected", b.dataset.tab === t); });
    mount.querySelectorAll(".ft-pane").forEach(p => { p.hidden = p.dataset.pane !== t; });
  }
  // actions inside the shared detail modal
  document.addEventListener("click", (e) => {
    const p = e.target.closest("#tool-modal [data-dis-prep]");
    const t = e.target.closest("#tool-modal [data-dis-type]");
    if (!p && !t) return;
    try { closeToolModal(); } catch (e2) {}
    if (p) { prepSel = p.dataset.disPrep; pref("prep", prepSel); showTab("prep"); renderPrep(); document.getElementById("dis-tabs").scrollIntoView({ behavior: "smooth", block: "center" }); }
    if (t) { setFilter({ k: "type", v: t.dataset.disType }); document.getElementById("dis-list").scrollIntoView({ behavior: "smooth", block: "center" }); }
  });

  showTab(tab);                                      // restore the remembered tab
  load();
  setInterval(load, 60000);                          // server caches; feeds refresh every ~5 min
  let tries = 0;
  (async function landLoop() { if (!(await drawLand()) && ++tries < 10) setTimeout(landLoop, 3000); })();
})();
