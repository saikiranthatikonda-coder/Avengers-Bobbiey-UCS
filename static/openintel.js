/* OPEN INTELLIGENCE · live open-data deck — renders into #openintel-mount.
   Data: GET /api/openintel (openintel.py). Keyless public APIs only; every card names its
   source, as-of time and status, and every row links to the original item. A source that
   can't be reached says so ("unavailable" / "stale") — nothing here is ever a placeholder.
   "NEW" / "since you last looked" marks compare against what this browser saw on its previous
   look (localStorage, per browser). Panel order, visibility and query settings are shared
   server state (commander-gated); other tabs follow through the "bucs:sync" window event. */
(function () {
  "use strict";
  const mount = document.getElementById("openintel-mount");
  if (!mount) return;

  let data = null, opts = null, canEdit = false, tab = null, showSettings = false, draft = null, loadErr = null;
  let timer = null, fastPolls = 0, notes = {}, fxAll = false, busy = false;
  const LISTS = ["who", "launches", "neo", "arxiv", "hn", "holidays"];       // item feeds (NEW marks)
  const STATUS = { ok: "LIVE", stale: "STALE", error: "UNAVAILABLE", pending: "LOADING", off: "HIDDEN" };

  function store(k, v) {
    try {
      if (v === undefined) return JSON.parse(localStorage.getItem("oi." + k));
      localStorage.setItem("oi." + k, JSON.stringify(v));
    } catch (e) { return null; }
  }
  let seen = store("seen") || {};
  { const t = store("tab"); if (typeof t === "string") tab = t; }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function safeUrl(u) { return typeof u === "string" && /^https?:\/\/[^\s"'<>\\`]+$/.test(u) ? u : null; }
  function link(u, inner, cls, title) {
    const s = safeUrl(u);
    const t = title ? ` title="${esc(title)}"` : "";
    return s ? `<a class="${cls || ""}" href="${esc(s)}" target="_blank" rel="noopener noreferrer"${t}>${inner}</a>`
             : `<span class="${cls || ""}"${t}>${inner}</span>`;
  }
  function num(v, max) {
    if (typeof v !== "number" || !isFinite(v)) return "—";
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(1) + "B";
    if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
    return v.toLocaleString(undefined, { maximumFractionDigits: max != null ? max : (a >= 100 ? 1 : a >= 1 ? 2 : 4) });
  }
  function signed(v, d) { return (v > 0 ? "+" : "") + num(v, d); }
  function rel(ts) {
    if (!ts) return "—";
    const d = ts - Date.now() / 1000, a = Math.abs(d);
    const txt = a < 90 ? Math.round(a) + "s" : a < 5400 ? Math.round(a / 60) + "m" : a < 172800 ? Math.round(a / 3600) + "h"
      : Math.round(a / 86400) + "d";
    return d > 0 ? "in " + txt : txt + " ago";
  }
  function abs(ts) { return ts ? new Date(ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—"; }
  function clock(ts) { return ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—"; }

  // ── sparkline: real points only; min / max / last are in the accessible label ──
  function spark(vals, o) {
    o = o || {};
    const v = (vals || []).filter(x => typeof x === "number" && isFinite(x));
    if (v.length < 2) return "";
    const W = o.w || 120, H = o.h || 28, lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1;
    const X = i => (i / (v.length - 1)) * W, Y = x => H - 2 - ((x - lo) / span) * (H - 4);
    const pts = v.map((x, i) => X(i).toFixed(1) + "," + Y(x).toFixed(1)).join(" ");
    const dir = v[v.length - 1] > v[0] ? "up" : v[v.length - 1] < v[0] ? "down" : "flat";
    let mark = "";
    if (o.nowIdx != null && o.nowIdx >= 0) {
      const x = X(o.nowIdx).toFixed(1);
      mark = `<line class="oi-now" x1="${x}" x2="${x}" y1="0" y2="${H}"/>`;
    }
    const label = `${o.label || "trend"}: min ${num(lo)}, max ${num(hi)}, latest ${num(v[v.length - 1])}`;
    return `<svg class="oi-spark ${o.cls || ""} ${dir}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(label)}"><title>${esc(label)}</title>` +
      `<polygon class="oi-area" points="0,${H} ${pts} ${W},${H}"/><polyline points="${pts}"/>${mark}</svg>`;
  }
  function seriesChart(s) {
    if (!s || !s.points || s.points.length < 2) return "";
    const vals = s.points.map(p => p[1]);
    let nowIdx = null;
    if (s.now) { nowIdx = s.points.findIndex(p => p[0] >= s.now); }
    const lo = Math.min(...vals), hi = Math.max(...vals);
    return `<div class="oi-chart">${spark(vals, { w: 320, h: 56, cls: "big", label: s.label, nowIdx })}` +
      `<div class="oi-chart-cap"><span>${esc(s.label || "")}</span><span>min ${num(lo)} · max ${num(hi)}` +
      `${nowIdx != null && nowIdx >= 0 ? " · line = now" : ""}</span></div></div>`;
  }

  // ── "since you last looked" (per browser) ──
  function snapOf(src) {
    const items = (src.data && src.data.items) || [];
    const vals = {};
    items.forEach(i => { if (typeof i.value === "number") vals[i.id] = i.value; });
    return { ids: items.map(i => String(i.id)), vals, at: Date.now() / 1000 };
  }
  function isNew(key, id) { const s = seen[key]; return !!(s && LISTS.includes(key) && !s.ids.includes(String(id))); }
  function delta(key, item) {
    const s = seen[key];
    if (!s || LISTS.includes(key) || typeof item.value !== "number" || typeof s.vals[item.id] !== "number") return "";
    const d = item.value - s.vals[item.id];
    if (!d || Math.abs(d) < Math.abs(item.value) * 1e-6) return "";
    return `<span class="oi-delta ${d > 0 ? "up" : "down"}" title="Change since you last looked (${esc(abs(s.at))})">${d > 0 ? "▲" : "▼"} ${esc(signed(d))}</span>`;
  }
  function newCount(panel) {
    if (!data) return 0;
    let n = 0;
    panel.sources.forEach(k => {
      const src = data.sources[k];
      if (src && src.data && seen[k] && LISTS.includes(k)) n += src.data.items.filter(i => isNew(k, i.id)).length;
    });
    return n;
  }
  function markSeen(panelKey) {
    if (!data) return;
    data.panels.forEach(p => {
      if (panelKey && p.key !== panelKey) return;
      p.sources.forEach(k => { const src = data.sources[k]; if (src && src.data) seen[k] = snapOf(src); });
    });
    store("seen", seen);
  }
  function baseline() {      // first look at a source: remember it, flag nothing
    let dirty = false;
    Object.keys(data.sources).forEach(k => {
      const src = data.sources[k];
      if (src.data && !seen[k]) { seen[k] = snapOf(src); dirty = true; }
    });
    if (dirty) store("seen", seen);
  }

  // ── rows ──
  function right(key, i) {
    if (i.value_text) return esc(i.value_text);
    if (typeof i.value === "number") return `${esc(i.signed ? signed(i.value) : num(i.value))}${i.unit ? ` <small>${esc(i.unit)}</small>` : ""}`;
    return i.time ? esc(rel(i.time)) : "";
  }
  function row(key, i) {
    const fresh = isNew(key, i.id);
    const chg = typeof i.change_pct === "number"
      ? `<span class="oi-chg ${i.change_pct > 0 ? "up" : i.change_pct < 0 ? "down" : ""}" title="Change vs the previous value from the source">${esc(signed(i.change_pct, 2))}%</span>` : "";
    const when = i.time && (typeof i.value === "number" || i.value_text) ? ` · ${esc(rel(i.time))}` : "";
    const sub = [i.detail ? esc(i.detail) : "", when ? when.slice(3) : ""].filter(Boolean).join(" · ");
    const inner =
      `${i.badge ? `<span class="oi-badge">${esc(i.badge)}</span>` : ""}` +
      `<span class="oi-row-main"><span class="oi-row-t">${fresh ? '<em class="oi-new">NEW</em>' : ""}${esc(i.title)}</span>` +
      `${sub ? `<span class="oi-row-d">${sub}</span>` : ""}</span>` +
      `${i.spark && i.spark.length > 1 ? spark(i.spark, { label: i.title }) : ""}` +
      `<span class="oi-row-v">${right(key, i)}${chg}${delta(key, i)}</span>`;
    const title = i.time ? abs(i.time) : "";
    const extra = i.discuss_url && i.discuss_url !== i.url ? link(i.discuss_url, "💬", "oi-side", "Discussion on Hacker News") : "";
    return `<li class="oi-li${fresh ? " fresh" : ""}">${link(i.url, inner, "oi-row" + (safeUrl(i.url) ? "" : " nolink"), title)}${extra}</li>`;
  }

  const AQI_CLS = ["good", "mod", "usg", "bad", "vbad", "haz"];
  function aqiCls(v) { return AQI_CLS[[50, 100, 150, 200, 300, 1e9].findIndex(t => v <= t)] || ""; }

  function body(key, src) {
    const d = src.data, s = d.summary || {};
    let items = d.items || [], top = "", foot = "";
    if (key === "air" && typeof s.us_aqi === "number") {
      top = `<div class="oi-hero aqi-${aqiCls(s.us_aqi)}"><b>${esc(num(s.us_aqi, 0))}</b><span>US AQI · ${esc(s.band || "")}</span>` +
        `${s.peak_24h ? `<small>next 24 h peak ${esc(num(s.peak_24h.us_aqi, 0))} (${esc(s.peak_24h.band)}) at ${esc(clock(s.peak_24h.time))}</small>` : ""}</div>`;
      foot = `Model grid cell ${esc(num(s.grid && s.grid.lat, 2))}, ${esc(num(s.grid && s.grid.lon, 2))} (CAMS) — not a street-level sensor.`;
    }
    if (key === "co2" && typeof s.ppm === "number") {
      top = `<div class="oi-hero"><b>${esc(num(s.ppm, 2))}</b><span>ppm CO₂ · ${esc(abs(d.as_of).split(",")[0])}</span>` +
        `${typeof s.yoy_ppm === "number" ? `<small>${esc(signed(s.yoy_ppm, 2))} ppm vs the same day last year</small>` : ""}</div>`;
      foot = esc(s.note || "");
    }
    if (key === "fx") {
      const watch = items.filter(i => i.watch), rest = items.filter(i => !i.watch);
      items = fxAll ? watch.concat(rest) : watch;
      foot = `${esc(s.note || "")} Rates for ${esc(s.date || "")}; sparkline ${esc(s.from || "")} → ${esc(s.date || "")}. ` +
        `<button type="button" class="ft-link" data-act="fxall">${fxAll ? "WATCH-LIST ONLY" : `SHOW ALL ${rest.length + watch.length}`}</button>`;
      if (!items.length && !fxAll) top = `<div class="tf-empty">No currencies on the watch-list — add some in CUSTOMISE.</div>`;
    }
    if (key === "neo") foot = esc(s.window || "");
    if (key === "holidays" && s.not_covered && s.not_covered.length)
      foot = `Nager.Date has no data for: ${esc(s.not_covered.join(", "))}.`;
    if (key === "arxiv" && s.categories) foot = `Categories: ${esc(s.categories.join(", "))}`;
    const empty = !items.length && !top
      ? `<div class="tf-empty">${esc(s.note || "The source answered but returned no items.")}</div>` : "";
    return top + seriesChart(d.series) + (items.length ? `<ul class="oi-list">${items.map(i => row(key, i)).join("")}</ul>` : "") +
      empty + (foot ? `<div class="oi-foot">${foot}</div>` : "");
  }

  function card(key) {
    const src = data.sources[key];
    const st = src.status, lab = STATUS[st] || st.toUpperCase();
    let inner;
    if (src.data) inner = body(key, src);
    else if (st === "pending") inner = `<div class="oi-wait" role="status"><div class="ft-skel"></div><div class="ft-skel"></div><span>Fetching from ${esc(src.label)}…</span></div>`;
    else inner = `<div class="oi-err" role="alert"><b>Unavailable.</b> ${esc(src.error || "The source could not be reached.")}` +
      `<span>No data is shown rather than a guess. Next automatic try ${esc(rel(src.next_at))}.</span></div>`;
    const asof = src.data && src.data.as_of ? `data ${esc(rel(src.data.as_of))}` : "";
    const meta = [asof, src.last_ok ? `fetched ${esc(clock(src.last_ok))}` : "", st === "stale" && src.error ? `last error: ${esc(src.error)}` : ""]
      .filter(Boolean).join(" · ");
    return `<article class="oi-src st-${esc(st)}" aria-label="${esc(src.label)}">
      <header class="oi-src-h">
        <div class="oi-src-t">${link(src.home, esc(src.label) + " ↗", "oi-src-name", "Open the source: " + (src.terms || ""))}
          <div class="oi-src-m"><span class="oi-dot" aria-hidden="true"></span><b>${esc(lab)}</b>${meta ? " · " + meta : ""}</div></div>
        <button type="button" class="oi-ref" data-act="refresh" data-src="${esc(key)}" title="Re-fetch this source (rate-limited to protect the public API)"
          aria-label="Refresh ${esc(src.label)}"${src.inflight ? " disabled" : ""}>↻</button>
      </header>
      ${notes[key] ? `<div class="oi-note" role="status">${esc(notes[key])}</div>` : ""}
      ${inner}
      <div class="oi-terms">${esc(src.terms || "")}</div>
    </article>`;
  }

  function skyCard() {
    const c = data.computed && data.computed.sky;
    if (!c) return "";
    const m = c.moon, s = c.sun;
    const rows = [];
    if (s && s.sunrise) rows.push(["Sunrise", clock(s.sunrise)], ["Sunset", clock(s.sunset)], ["Day length", num(s.day_length_h, 2) + " h"]);
    else if (s && s.note) rows.push(["Sun", s.note]);
    else rows.push(["Sun", "location not known — cannot compute"]);
    rows.push(["Moon", `${m.phase} · ${m.illumination_pct}% lit`], ["Next full moon", `in about ${m.next_full_days} d`]);
    return `<article class="oi-src st-computed" aria-label="Sun and moon, computed locally">
      <header class="oi-src-h"><div class="oi-src-t"><span class="oi-src-name">SUN &amp; MOON</span>
        <div class="oi-src-m"><span class="oi-dot" aria-hidden="true"></span><b>COMPUTED LOCALLY</b> · not fetched from any service</div></div></header>
      <ul class="oi-list">${rows.map(r => `<li class="oi-li"><span class="oi-row nolink"><span class="oi-row-main"><span class="oi-row-t">${esc(r[0])}</span></span><span class="oi-row-v">${esc(r[1])}</span></span></li>`).join("")}</ul>
      <div class="oi-terms">${esc((s && s.method) || "")}${s && s.method ? " · " : ""}${esc(m.method)}</div>
    </article>`;
  }

  // ── settings ──
  function settingsPane() {
    const s = draft, o = opts, dis = canEdit ? "" : " disabled";
    const order = s.order.map((p, i) => `<li><label><input type="checkbox" data-set="show" value="${esc(p)}"${s.hidden.includes(p) ? "" : " checked"}${dis}> ${esc(o.panels[p] || p)}</label>
      <span><button type="button" class="oi-mv" data-act="up" data-p="${esc(p)}" aria-label="Move ${esc(o.panels[p])} earlier"${i === 0 || !canEdit ? " disabled" : ""}>▲</button>
      <button type="button" class="oi-mv" data-act="down" data-p="${esc(p)}" aria-label="Move ${esc(o.panels[p])} later"${i === s.order.length - 1 || !canEdit ? " disabled" : ""}>▼</button></span></li>`).join("");
    const cats = Object.keys(o.arxiv_cats).map(c => `<label class="oi-chk" title="${esc(o.arxiv_cats[c])}"><input type="checkbox" data-set="cat" value="${esc(c)}"${s.arxiv_cats.includes(c) ? " checked" : ""}${dis}> ${esc(c)}</label>`).join("");
    const base = o.fx_codes.map(c => `<option${c === s.fx_base ? " selected" : ""}>${esc(c)}</option>`).join("");
    const watch = o.fx_codes.filter(c => c !== s.fx_base).map(c => `<label class="oi-chk"><input type="checkbox" data-set="watch" value="${esc(c)}"${s.fx_watch.includes(c) ? " checked" : ""}${dis}> ${esc(c)}</label>`).join("");
    return `<form class="oi-settings" id="oi-settings" aria-label="Open Intelligence settings">
      ${canEdit ? "" : `<div class="oi-note" role="status">OBSERVER role — settings are read-only. Switch to COMMANDER to change them.</div>`}
      <fieldset><legend>PANELS · show / order</legend><ul class="oi-order">${order}</ul></fieldset>
      <fieldset><legend>arXiv CATEGORIES · up to ${o.limits.arxiv_cats}</legend><div class="oi-chks">${cats}</div></fieldset>
      <fieldset><legend>CURRENCIES</legend>
        <label class="oi-fld">Base <select data-set="base"${dis}>${base}</select></label>
        <div class="oi-sub">Watch-list · up to ${o.limits.fx_watch}</div><div class="oi-chks">${watch}</div></fieldset>
      <fieldset><legend>HOLIDAYS &amp; REFRESH</legend>
        <label class="oi-fld">Countries (ISO codes, up to ${o.limits.holiday_countries})
          <input type="text" data-set="countries" value="${esc(s.holiday_countries.join(", "))}" maxlength="30" autocomplete="off" spellcheck="false"${dis}></label>
        <label class="oi-fld">Dashboard refresh (seconds, ${o.refresh_s[0]}–${o.refresh_s[1]})
          <input type="number" data-set="refresh" min="${o.refresh_s[0]}" max="${o.refresh_s[1]}" step="30" value="${esc(s.refresh_s)}"${dis}></label>
        <div class="oi-sub">This is how often the dashboard re-reads the deck. Each public API keeps its own, slower schedule.</div></fieldset>
      <div class="oi-set-actions">
        <button type="submit" class="cmd-btn"${dis}>SAVE</button>
        <button type="button" class="cmd-btn" data-act="reset"${dis}>RESET TO DEFAULTS</button>
        <button type="button" class="cmd-btn" data-act="settings">CLOSE</button>
        <span class="oi-set-msg" id="oi-set-msg" role="status"></span></div>
    </form>`;
  }
  function readDraft() {
    const f = mount.querySelector("#oi-settings");
    if (!f || !draft) return;
    const vals = n => Array.from(f.querySelectorAll(`[data-set="${n}"]:checked`)).map(e => e.value);
    const shown = vals("show");
    draft.hidden = draft.order.filter(p => !shown.includes(p));
    draft.arxiv_cats = vals("cat");
    draft.fx_base = f.querySelector('[data-set="base"]').value;
    draft.fx_watch = vals("watch").filter(c => c !== draft.fx_base);
    draft.holiday_countries = f.querySelector('[data-set="countries"]').value.toUpperCase().split(/[\s,;]+/).filter(Boolean);
    draft.refresh_s = Number(f.querySelector('[data-set="refresh"]').value);
  }

  // ── render ──
  function render() {
    if (!data) {
      mount.innerHTML = `<div class="bs-card ft-card oi-card"><div class="ft-head"><div class="ft-head-txt">
        <div class="ft-kicker">LIVE · KEYLESS OPEN DATA</div><div class="bs-title">OPEN INTELLIGENCE</div>
        <div class="bs-sub">${loadErr ? esc(loadErr) : "loading…"}</div></div></div>
        ${loadErr ? `<div class="oi-err" role="alert"><b>The deck could not be loaded.</b> ${esc(loadErr)} <button type="button" class="ft-link" data-act="reload">RETRY</button></div>`
                  : `<div class="ft-stats"><div class="ft-skel"></div><div class="ft-skel"></div><div class="ft-skel"></div></div>`}</div>`;
      return;
    }
    const vis = data.panels.filter(p => !p.hidden);
    if (!vis.some(p => p.key === tab)) tab = vis.length ? vis[0].key : null;
    const all = Object.values(data.sources).filter(s => s.status !== "off");
    const live = all.filter(s => s.status === "ok").length, bad = all.filter(s => s.status === "error" || s.status === "stale").length;
    const loc = data.location;
    const totalNew = vis.reduce((n, p) => n + newCount(p), 0);
    const tabs = vis.map(p => {
      const n = newCount(p), on = p.key === tab;
      return `<button type="button" role="tab" id="oi-tab-${esc(p.key)}" data-tab="${esc(p.key)}" aria-selected="${on}" aria-controls="oi-pane" tabindex="${on ? 0 : -1}" class="${on ? "on" : ""}">${esc(p.label)}${n ? `<em class="oi-cnt" aria-label="${n} new since you last looked">${n}</em>` : ""}</button>`;
    }).join("");
    const panel = vis.find(p => p.key === tab);
    const cards = panel ? panel.sources.map(card).join("") + (tab === "space" ? skyCard() : "") : "";
    const focusId = document.activeElement && mount.contains(document.activeElement) ? document.activeElement.id : null;
    mount.innerHTML = `<div class="bs-card ft-card oi-card${bad ? " is-degraded" : ""}">
      <div class="ft-head"><div class="ft-head-txt">
        <div class="ft-kicker">LIVE · KEYLESS OPEN DATA</div><div class="bs-title">OPEN INTELLIGENCE</div>
        <div class="bs-sub">${live}/${all.length} sources live${bad ? ` · ${bad} unavailable or stale` : ""} · ${loc ? `location ${esc(loc.label)} (${esc(loc.note)})` : "location not known"} · read ${esc(clock(data.generated))}</div></div>
        <div class="ft-actions">
          <button type="button" class="cmd-btn" data-act="seen" title="Clear the NEW marks and deltas: remember everything shown now"${totalNew ? "" : " disabled"}>✓ MARK SEEN${totalNew ? ` · ${totalNew}` : ""}</button>
          <button type="button" class="cmd-btn${showSettings ? " on" : ""}" data-act="settings" aria-expanded="${showSettings}">⚙ CUSTOMISE</button>
        </div></div>
      ${showSettings && draft && opts ? settingsPane() : ""}
      ${vis.length ? `<div class="ft-tabs oi-tabs" role="tablist" aria-label="Open Intelligence panels">${tabs}</div>
      <div class="oi-pane" id="oi-pane" role="tabpanel" aria-labelledby="oi-tab-${esc(tab)}">${cards}</div>`
        : `<div class="tf-empty">Every panel is hidden. Use CUSTOMISE to show one.</div>`}
    </div>`;
    if (focusId) { const el = document.getElementById(focusId); if (el) el.focus(); }
  }

  // ── data ──
  function schedule() {
    clearTimeout(timer);
    if (document.hidden) return;
    const pending = data && Object.values(data.sources).some(s => s.status === "pending" || s.inflight);
    let wait = ((data && data.poll_s) || 120) * 1000;
    if (!data) wait = 15000;
    else if (pending && fastPolls < 12) { fastPolls++; wait = 2500; }
    timer = setTimeout(load, wait);
  }
  async function load() {
    if (busy) return;
    busy = true;
    try {
      const r = await fetch("/api/openintel");
      if (!r.ok) throw new Error("HTTP " + r.status);
      data = await r.json();
      loadErr = null;
      baseline();
    } catch (e) {
      if (!data) loadErr = "The local server did not answer (" + (e && e.message || e) + ").";
    } finally { busy = false; }
    if (!(showSettings && mount.querySelector("#oi-settings") && mount.contains(document.activeElement) && document.activeElement.closest("#oi-settings"))) render();
    schedule();
  }
  async function loadSettings() {
    try {
      const r = await (await fetch("/api/openintel/settings")).json();
      opts = r.options; canEdit = !!r.can_edit; draft = JSON.parse(JSON.stringify(r.settings));
    } catch (e) { opts = null; }
  }
  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    let j = null;
    try { j = await r.json(); } catch (e) {}
    return j || { ok: false, error: "HTTP " + r.status };
  }
  function setMsg(t, bad) {
    const m = mount.querySelector("#oi-set-msg");
    if (m) { m.textContent = t; m.classList.toggle("bad", !!bad); }
  }
  async function save(reset) {
    if (!canEdit) return;
    if (!reset) readDraft();
    setMsg("Saving…");
    const r = await post(reset ? "/api/openintel/settings/reset" : "/api/openintel/settings", reset ? {} : draft);
    if (!r.ok) { setMsg(r.error || "Not saved.", true); return; }
    draft = JSON.parse(JSON.stringify(r.settings));
    fastPolls = 0;
    await load();
    render();
    setMsg(r.changed && r.changed.length ? "Saved for every dashboard." : "Nothing changed.");
  }
  function selectTab(k, focus) {
    if (k === tab) return;
    markSeen(tab);                         // leaving a panel = you have looked at it
    tab = k; store("tab", k);
    render();
    if (focus) { const b = document.getElementById("oi-tab-" + k); if (b) b.focus(); }
  }

  mount.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-tab],[data-act]");
    if (!t || !mount.contains(t)) return;
    if (t.dataset.tab) return selectTab(t.dataset.tab);
    const act = t.dataset.act;
    if (act === "reload") { loadErr = null; render(); return load(); }
    if (act === "seen") { markSeen(null); return render(); }
    if (act === "fxall") { fxAll = !fxAll; return render(); }
    if (act === "settings") {
      showSettings = !showSettings;
      if (showSettings) await loadSettings();
      render();
      if (showSettings && !opts) { showSettings = false; render(); }
      return;
    }
    if (act === "reset") return save(true);
    if (act === "up" || act === "down") {
      readDraft();
      const i = draft.order.indexOf(t.dataset.p), j = i + (act === "up" ? -1 : 1);
      if (i < 0 || j < 0 || j >= draft.order.length) return;
      [draft.order[i], draft.order[j]] = [draft.order[j], draft.order[i]];
      render();
      const again = mount.querySelector(`[data-act="${act}"][data-p="${t.dataset.p}"]`);
      if (again && !again.disabled) again.focus();
      return;
    }
    if (act === "refresh") {
      const k = t.dataset.src;
      t.disabled = true;
      const r = await post("/api/openintel/refresh", { source: k });
      const res = r && r.results && r.results[k];
      notes[k] = res ? (res.queued ? "Refreshing from the source…" : "Not refreshed: " + res.reason + ".") : (r.error || "Refresh failed.");
      render();
      setTimeout(() => { delete notes[k]; fastPolls = 0; load(); }, res && res.queued ? 2500 : 5000);
    }
  });
  mount.addEventListener("submit", (e) => { if (e.target.id === "oi-settings") { e.preventDefault(); save(false); } });
  mount.addEventListener("keydown", (e) => {
    const t = e.target.closest('[role="tab"]');
    if (!t || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const keys = Array.from(mount.querySelectorAll('[role="tab"]')).map(b => b.dataset.tab);
    let i = keys.indexOf(t.dataset.tab);
    i = e.key === "Home" ? 0 : e.key === "End" ? keys.length - 1 : (i + (e.key === "ArrowRight" ? 1 : -1) + keys.length) % keys.length;
    e.preventDefault();
    selectTab(keys[i], true);
  });
  window.addEventListener("bucs:sync", async (e) => {
    if (!(e && e.detail && e.detail.topic === "openintel")) return;
    fastPolls = 0;
    if (showSettings) await loadSettings();
    load();
  });
  window.addEventListener("pagehide", () => markSeen(tab));
  document.addEventListener("visibilitychange", () => { if (document.hidden) clearTimeout(timer); else load(); });

  render();
  load();
})();
