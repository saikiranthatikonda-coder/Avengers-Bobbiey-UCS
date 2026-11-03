/* DISASTER RESPONSE · REAL-TIME EVENTS — renders into #disaster-mount.
   Data: GET /api/disaster (disaster.py). Every item names its source and fetch time;
   a feed that can't be reached shows "unavailable"/"stale", never invented values.
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
  let data = null, filter = "all", prepSel = null, landDrawn = false, busy = false;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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
  <div class="bs-card dis-card" id="dis-card">
    <div class="bs-head dis-head">
      <div class="dis-head-txt">
        <div class="bs-title">DISASTER RESPONSE · REAL-TIME EVENTS</div>
        <div class="bs-sub" id="dis-sub">USGS · GDACS · NASA EONET · NDMA SACHET · CISA KEV · STATUSPAGE · IODA · loading…</div>
      </div>
      <button class="cmd-btn" id="dis-refresh" type="button" title="Re-fetch every feed now (max once a minute)">REFRESH</button>
    </div>
    <div class="dis-counts" id="dis-counts"><div class="tf-empty">first fetch runs ~10 s after start…</div></div>
    <div class="dis-main">
      <div class="dis-mapwrap">
        <div class="intel-divider">PHYSICAL EVENTS · WORLD PLOT <span id="dis-loc"></span></div>
        <div class="dis-mapbox">
          <canvas class="dis-land-cv" id="dis-land" width="${W * 2}" height="${H * 2}" aria-hidden="true"></canvas>
          <svg class="dis-map" id="dis-map" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="World map of current physical events"></svg>
        </div>
        <div class="dis-legend">
          <span><i class="sev-crit"></i>CRITICAL</span><span><i class="sev-high"></i>HIGH</span>
          <span><i class="sev-med"></i>MEDIUM</span><span><i class="sev-low"></i>LOW</span>
          <span><i class="dis-you"></i>YOU · <span id="dis-nearkm">500</span> KM RING</span>
        </div>
      </div>
      <div class="dis-tl">
        <div class="intel-divider dis-tl-head">UNIFIED TIMELINE · SEVERITY-SORTED
          <span class="dis-filters" id="dis-filters">
            <button type="button" data-f="all" class="on">ALL</button><button type="button" data-f="physical">PHYSICAL</button><button type="button" data-f="digital">DIGITAL</button><button type="button" data-f="near">NEAR YOU</button>
          </span>
        </div>
        <div class="dis-list" id="dis-list"><div class="tf-empty">waiting for feeds…</div></div>
      </div>
    </div>
    <div class="dis-lower">
      <div>
        <div class="intel-divider">DIGITAL EVENTS · SERVICE STATUS (PUBLIC STATUSPAGES)</div>
        <div class="dis-svcs" id="dis-svcs"><div class="tf-empty">—</div></div>
        <div class="dis-digi" id="dis-digi"></div>
      </div>
      <div>
        <div class="intel-divider">PREPAREDNESS · <span id="dis-prep-src">NDMA / CISA guidance</span></div>
        <div class="dis-prep-tabs" id="dis-prep-tabs"></div>
        <ul class="dis-prep" id="dis-prep"><li class="tf-empty">guidance appears for active event types</li></ul>
      </div>
    </div>
    <div class="intel-divider">SOURCES · FETCH STATUS</div>
    <div class="dis-sources" id="dis-sources"></div>
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
      if (typeof topojson !== "undefined") {   // same CDN asset the globe already loads (cached)
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
    const pts = data.items.filter(i => i.category === "physical" && i.lat != null && i.lon != null)
      .slice().sort((a, b) => a.severity - b.severity);   // severe drawn last (on top)
    for (const i of pts) {
      const [x, y] = xy(i.lat, i.lon), r = 2 + i.severity * 1.3, cx = x.toFixed(1), cy = y.toFixed(1);
      html += `<g class="dis-pt sev-${SEV_CLASS[i.severity]}${i.near ? " near" : ""}">` +
        (i.severity >= 3 ? `<circle class="dis-pulse" cx="${cx}" cy="${cy}" r="${r + 3}"/>` : "") +
        `<circle cx="${cx}" cy="${cy}" r="${r}"/><title>${esc(i.sev_label)} · ${esc(i.title)}
${esc(i.source)}` +
        `${i.distance_km != null ? " · " + i.distance_km.toLocaleString() + " km from you" : ""}</title></g>`;
    }
    svg.innerHTML = html;
  }

  // ── panels ────────────────────────────────────────────
  function renderCounts() {
    const c = data.counts, host = document.getElementById("dis-counts");
    const types = Object.entries(c.by_type).sort((a, b) => b[1] - a[1]);
    const sev = c.by_severity;
    host.innerHTML =
      `<div class="dis-stat"><b>${c.total}</b><span>EVENTS</span></div>` +
      `<div class="dis-stat crit"><b>${sev.CRITICAL}</b><span>CRITICAL</span></div>` +
      `<div class="dis-stat high"><b>${sev.HIGH}</b><span>HIGH</span></div>` +
      `<div class="dis-stat near"><b>${c.near}</b><span>NEAR YOU</span></div>` +
      `<div class="dis-stat"><b>${c.physical}</b><span>PHYSICAL</span></div>` +
      `<div class="dis-stat"><b>${c.digital}</b><span>DIGITAL</span></div>` +
      `<div class="dis-types">${types.map(([t, n]) =>
        `<span class="dis-chip" title="${esc(t)}"><em>${esc(ABBR[t] || "··")}</em>${esc(t)} ${n}</span>`).join("")}</div>`;
  }
  function renderList() {
    const host = document.getElementById("dis-list");
    let items = data.items;
    if (filter === "physical" || filter === "digital") items = items.filter(i => i.category === filter);
    else if (filter === "near") items = items.filter(i => i.near);
    if (!items.length) {
      host.innerHTML = `<div class="tf-empty">${filter === "near" ? (data.location ? "nothing within " + data.near_km + " km of you right now" : "location unknown — distance unavailable") : "no events from the reachable feeds"}</div>`;
      return;
    }
    host.innerHTML = items.slice(0, 80).map(i => {
      const u = safeUrl(i.link);
      const dist = i.distance_km != null ? `<span class="dis-dist${i.near ? " near" : ""}">${i.near ? "NEAR · " : ""}${i.distance_km.toLocaleString()} km</span>` : "";
      return `<div class="dis-ev sev-${SEV_CLASS[i.severity]}${i.near ? " near" : ""}${i.stale ? " stale" : ""}">
        <span class="dis-ab" title="${esc(i.type)}">${esc(ABBR[i.type] || "··")}</span>
        <div class="dis-ev-body">
          <div class="dis-ev-t"><span class="dis-sev">${esc(i.sev_label)}</span>${u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a>` : esc(i.title)}</div>
          ${i.detail ? `<div class="dis-ev-d">${esc(i.detail)}</div>` : ""}
          <div class="dis-ev-m">▸ ${esc(i.source)} · event ${esc(ago(i.time))} · fetched ${esc(clock(i.fetched_at))}${i.stale ? " · STALE (feed unreachable)" : ""} ${dist}</div>
        </div></div>`;
    }).join("") + (items.length > 80 ? `<div class="tf-empty">+${items.length - 80} more (lower severity)</div>` : "");
  }
  function renderServices() {
    const host = document.getElementById("dis-svcs");
    const svcs = data.services || [];
    const src = data.sources.find(s => s.key === "status");
    if (!svcs.length) { host.innerHTML = `<div class="tf-empty">status pages ${src && src.status === "pending" ? "not fetched yet" : "unavailable"}</div>`; }
    else host.innerHTML = svcs.map(s => {
      const cls = s.indicator === "unavailable" ? "na" : s.indicator === "none" ? "ok" : s.severity >= 3 ? "bad" : "warn";
      const u = safeUrl(s.url);
      const name = u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(s.name)}</a>` : esc(s.name);
      return `<div class="dis-svc ${cls}" title="${esc(s.description)}"><span class="dot"></span><b>${name}</b><span>${esc(s.description)}</span></div>`;
    }).join("");
    const kev = data.sources.find(s => s.key === "kev"), ioda = data.sources.find(s => s.key === "ioda");
    const kevN = data.items.filter(i => i.type === "vulnerability").length;
    const netN = data.items.filter(i => i.type === "outage").length;
    document.getElementById("dis-digi").innerHTML =
      `<div class="dis-digi-row"><b>CISA KEV</b> ${kev && kev.status !== "unavailable" && kev.status !== "pending"
        ? `${kevN} newest of ${esc((kev.meta && kev.meta.total) || "?")} actively-exploited CVEs · catalog ${esc((kev.meta && kev.meta.catalog) || "—")}`
        : esc(kev ? kev.status : "—")}</div>` +
      `<div class="dis-digi-row"><b>IODA</b> ${ioda && ioda.status !== "unavailable" && ioda.status !== "pending"
        ? `${netN} countries with an ongoing internet-outage signal (24 h)` : esc(ioda ? ioda.status : "—")}</div>`;
  }
  function renderPrep() {
    const all = data.preparedness_all || {}, active = Object.keys(data.preparedness || {});
    // default: the type of the most severe near event, else most severe overall
    const lead = (data.items.find(i => i.near && all[i.type]) || data.items.find(i => all[i.type]) || {}).type;
    if (!prepSel || !all[prepSel]) prepSel = lead || active[0] || Object.keys(all)[0];
    const keys = [...new Set([...active, ...Object.keys(all)])].filter(k => !["storm", "rain"].includes(k) || active.includes(k));
    document.getElementById("dis-prep-tabs").innerHTML = keys.map(k =>
      `<button type="button" data-k="${esc(k)}" class="${k === prepSel ? "on" : ""}${active.includes(k) ? " live" : ""}" title="${active.includes(k) ? "active event type" : "reference"}">${esc(ABBR[k] || k)}</button>`).join("");
    document.getElementById("dis-prep").innerHTML =
      `<li class="dis-prep-h">${esc(prepSel.toUpperCase())}${active.includes(prepSel) ? " · ACTIVE NOW" : ""}</li>` +
      (all[prepSel] || []).map(t => `<li>${esc(t)}</li>`).join("");
    document.getElementById("dis-prep-src").textContent = data.preparedness_source || "NDMA / CISA guidance";
  }
  function renderSources() {
    document.getElementById("dis-sources").innerHTML = data.sources.map(s => {
      const u = safeUrl(s.home);
      const name = u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(s.label)}</a>` : esc(s.label);
      const when = s.last_ok ? "ok " + ago(s.last_ok) : "never";
      return `<div class="dis-src ${esc(s.status)}" title="${esc(s.error || "")}"><span class="dot"></span>${name}
        <span class="dis-src-m">${esc(s.status.toUpperCase())} · ${s.count} · ${esc(when)}${s.error ? " · " + esc(s.error) : ""}</span></div>`;
    }).join("");
  }
  function renderAll() {
    if (!data) return;
    const c = data.counts, loc = data.location;
    const okN = data.sources.filter(s => s.status === "ok").length;
    document.getElementById("dis-sub").textContent =
      `${okN}/${data.sources.length} FEEDS LIVE · ${c.total} EVENTS · ${c.near} WITHIN ${data.near_km} KM · UPDATED ${clock(data.generated)}`;
    document.getElementById("dis-nearkm").textContent = data.near_km;
    document.getElementById("dis-loc").textContent = loc ? `· YOU: ${loc.label} (${loc.lat.toFixed(2)}, ${loc.lon.toFixed(2)})` : "· location unknown";
    renderCounts(); renderList(); renderServices(); renderPrep(); renderSources(); renderMap();
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
      renderAll();
      // first boot: feeds are still being fetched → look again soon instead of in a minute
      const pending = Object.values(data.sources || {}).some(s => s && (s.status === "pending" || s.status === undefined));
      if (pending && ++warmups < 24) setTimeout(load, 5000);
    } catch (e) {
      const s = document.getElementById("dis-sub");
      if (s) s.textContent = "DISASTER FEEDS UNAVAILABLE · " + (e && e.message || "error") + (data ? " · showing last data" : "");
    }
  }
  async function refresh() {
    if (busy) return; busy = true;
    const b = document.getElementById("dis-refresh"); b.disabled = true; b.textContent = "FETCHING…";
    try {
      const r = await fetch("/api/disaster/refresh" + locQuery(), { method: "POST" });
      const j = await r.json();
      if (r.status === 429) {
        try { logEvent(`<span class="tag">[disaster]</span> refresh limited — try again in ${esc(j.retry_after)} s`, "warn"); } catch (e) {}
      } else if (r.ok) { data = j; renderAll(); }
    } catch (e) {
      try { logEvent(`<span class="tag">[disaster]</span> refresh failed`, "warn"); } catch (e2) {}
    } finally { busy = false; b.disabled = false; b.textContent = "REFRESH"; }
  }

  document.getElementById("dis-refresh").addEventListener("click", refresh);
  document.getElementById("dis-filters").addEventListener("click", (e) => {
    const f = e.target.closest("button[data-f]"); if (!f) return;
    filter = f.dataset.f;
    document.querySelectorAll("#dis-filters button").forEach(b => b.classList.toggle("on", b === f));
    if (data) renderList();
  });
  document.getElementById("dis-prep-tabs").addEventListener("click", (e) => {
    const t = e.target.closest("button[data-k]"); if (!t) return;
    prepSel = t.dataset.k; if (data) renderPrep();
  });

  load();
  setInterval(load, 60000);                          // server caches; feeds refresh every ~5 min
  // coastlines: drawn once; retry a few times if d3/topojson/the atlas aren't ready yet
  let tries = 0;
  (async function landLoop() { if (!(await drawLand()) && ++tries < 10) setTimeout(landLoop, 3000); })();
})();
