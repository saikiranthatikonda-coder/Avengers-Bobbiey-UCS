/* ═══ CYBER ANALYST · AUTONOMOUS DEFENCE (secagent.py) ═══
   Renders into #secagent-mount. Every number here comes from /api/secagent (real scans). */
(() => {
  const mount = document.getElementById("secagent-mount");
  if (!mount) return;
  const esc = (s) => (typeof escapeHTML === "function" ? escapeHTML(String(s ?? ""))
    : String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" }[c])));
  const log = (html, kind = "info") => { try { logEvent(`<span class="tag">[cyber]</span> ${html}`, kind); } catch (e) {} };
  const SEV = ["critical", "high", "medium", "low", "info"];
  const CAT = { deps: "DEPENDENCIES", secrets: "SECRETS", exposure: "EXPOSURE", config: "APP CONFIG", intel: "THREAT INTEL", kev_feed: "KEV FEED" };
  let data = null, filter = null, timer = null;
  const open = new Set();

  mount.innerHTML = `
  <div class="bs-card sa-card ft-card" id="sa-card">
    <div class="ft-head">
      <div class="ft-head-txt">
        <div class="ft-kicker">LOCAL SCANS · OSV.dev · CISA KEV · FIXES NEED YOUR APPROVAL</div>
        <div class="bs-title">CYBER ANALYST</div>
        <div class="bs-sub">Posture <span id="sa-posture">—</span> · <span id="sa-when">no scan yet</span> · ruleset <span id="sa-ruleset">—</span></div>
      </div>
      <div class="ft-actions">
        <button class="cmd-btn" id="sa-scan" type="button" title="Full scan: OSV dependencies, secrets, exposure, config, CISA KEV">SCAN NOW</button>
        <button class="cmd-btn" id="sa-light" type="button" title="Light check: exposure + app config">LIGHT CHECK</button>
      </div>
    </div>
    <div class="sa-top">
      <button type="button" class="sa-gauge-wrap" data-tab-go="checks" title="What the score is made of">
        <svg class="sa-gauge" viewBox="0 0 120 72" aria-label="Security posture score">
          <path class="sa-g-bg" d="M10 66 A50 50 0 0 1 110 66" />
          <path class="sa-g-fg" id="sa-g-fg" d="M10 66 A50 50 0 0 1 110 66" pathLength="100" stroke-dasharray="0 100" />
          <text x="60" y="58" class="sa-g-num" id="sa-score">—</text><text x="60" y="70" class="sa-g-lab">POSTURE</text>
        </svg></button>
      <div class="sa-top-r">
        <div class="ft-stats sa-sev" id="sa-sev"><div class="ft-skel"></div><div class="ft-skel"></div><div class="ft-skel"></div><div class="ft-skel"></div></div>
        <div class="sa-trend">
          <svg class="sa-spark" id="sa-spark" viewBox="0 0 200 40" preserveAspectRatio="none"></svg>
          <div class="sa-trend-note" id="sa-trend-note">—</div>
        </div>
      </div>
    </div>
    <div class="sa-findings-head"><span>FINDINGS · <b id="sa-filter-lab">ALL</b></span>
      <button type="button" class="ft-link" id="sa-filter-clear" hidden>SHOW ALL ✕</button></div>
    <div class="sa-list" id="sa-list"><div class="tf-empty">no findings yet</div></div>
    <div class="ft-tabs" role="tablist" id="sa-tabs">
      <button type="button" role="tab" data-tab="checks" class="on">CHECKS</button>
      <button type="button" role="tab" data-tab="brief">ANALYST BRIEFING</button>
      <button type="button" role="tab" data-tab="intel">THREAT INTEL</button>
      <button type="button" role="tab" data-tab="log">ACTIVITY</button>
      <button type="button" role="tab" data-tab="acc">ACCEPTED RISKS · <span id="sa-acc-n">0</span></button>
    </div>
    <div class="ft-pane" data-pane="checks"><div class="sa-checks" id="sa-checks"><div class="tf-empty">first scan runs ~90 s after start…</div></div></div>
    <div class="ft-pane" data-pane="brief" hidden>
      <div class="sa-brief" id="sa-brief">Press AI BRIEF for a briefing grounded only in the measured findings.</div>
      <div class="sa-acts"><button class="cmd-btn" id="sa-brief-btn" type="button">AI BRIEF</button>
        <span class="ft-note">engine: <span id="sa-brief-src">—</span></span></div>
    </div>
    <div class="ft-pane" data-pane="intel" hidden><div class="sa-intel" id="sa-intel">—</div></div>
    <div class="ft-pane" data-pane="log" hidden><div class="sa-log" id="sa-log"><div class="tf-empty">idle</div></div></div>
    <div class="ft-pane" data-pane="acc" hidden><div id="sa-acc"></div></div>
  </div>`;

  function showTab(t) {
    mount.querySelectorAll("#sa-tabs [data-tab]").forEach(b => { b.classList.toggle("on", b.dataset.tab === t); b.setAttribute("aria-selected", b.dataset.tab === t); });
    mount.querySelectorAll(".ft-pane").forEach(p => { p.hidden = p.dataset.pane !== t; });
  }

  const $s = (id) => document.getElementById(id);
  const ago = (ts) => {
    if (!ts) return "never";
    const s = Math.max(0, Date.now() / 1000 - ts);
    return s < 90 ? `${Math.round(s)} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
  };
  const scoreClass = (n) => (n >= 85 ? "good" : n >= 60 ? "fair" : "poor");

  function renderFinding(f, compact) {
    const isOpen = open.has(f.id);
    const url = typeof f.url === "string" && f.url.startsWith("https://osv.dev/") ? f.url : null;
    return `<div class="sa-f ${esc(f.severity)}${f.stale ? " stale" : ""}" data-id="${esc(f.id)}">
      <button class="sa-f-head" type="button" data-toggle="${esc(f.id)}" aria-expanded="${isOpen}">
        <span class="sa-badge ${esc(f.severity)}">${esc(f.severity.toUpperCase())}</span>
        ${compact ? "" : `<span class="sa-cat">${esc(CAT[f.category] || f.category)}</span>`}
        <span class="sa-f-title">${esc(f.title)}</span>${f.kev ? `<span class="sa-kev" title="CISA Known Exploited Vulnerability">KEV</span>` : ""}${f.stale ? `<span class="sa-stale">STALE</span>` : ""}
      </button>
      ${isOpen ? `<div class="sa-f-body">
        <div><b>EVIDENCE</b> ${esc(f.evidence)}</div>
        <div><b>FIX</b> ${esc(f.fix)}</div>
        ${url ? `<div><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(f.ref || "advisory")} ↗</a></div>` : ""}
        <div class="sa-f-acts">
          ${f.fixable ? `<button class="cmd-btn" type="button" data-propose="${esc(f.id)}" title="Hand this fix to engineering mode. Nothing is applied until you approve it there.">PROPOSE FIX</button>` : ""}
          <button class="cmd-btn" type="button" data-accept="${esc(f.id)}">ACCEPT RISK</button>
          <button class="cmd-btn" type="button" data-accept-rule="${esc(f.id)}" title="Accept every finding of rule ${esc(f.rule)}">ACCEPT RULE</button>
        </div></div>` : ""}
    </div>`;
  }

  function renderList() {
    const all = (data && data.findings) || [];
    const fs = filter ? all.filter((f) => f.severity === filter) : all;
    $s("sa-filter-lab").textContent = filter ? filter.toUpperCase() + " · " + fs.length : "ALL · " + fs.length;
    $s("sa-filter-clear").hidden = !filter;
    if (!fs.length) {
      $s("sa-list").innerHTML = `<div class="tf-empty">${data && data.last_scan && data.last_scan.full ? "no open findings at this level" : "no scan yet"}</div>`;
      return;
    }
    // dependency advisories grouped per package — one upgrade usually fixes them all
    const groups = new Map(), rest = [];
    for (const f of fs) {
      if (f.category === "deps" && f.package) { if (!groups.has(f.package)) groups.set(f.package, []); groups.get(f.package).push(f); }
      else rest.push(f);
    }
    const rank = (sv) => SEV.indexOf(sv);
    const blocks = rest.map((f) => ({ r: rank(f.severity), html: renderFinding(f, false) }));
    for (const [pkg, items] of groups) {
      const worst = SEV.find((s) => items.some((f) => f.severity === s)) || "info";
      const gid = "pkg:" + pkg, isOpen = open.has(gid);
      const lead = items.find((f) => f.fixable);
      blocks.push({ r: rank(worst), html: `<div class="sa-grp ${esc(worst)}">
        <button class="sa-f-head" type="button" data-toggle="${esc(gid)}" aria-expanded="${isOpen}">
          <span class="sa-badge ${esc(worst)}">${esc(worst.toUpperCase())}</span><span class="sa-cat">DEPENDENCY</span>
          <span class="sa-f-title">${esc(pkg)} · ${items.length} advisor${items.length === 1 ? "y" : "ies"}</span>
          ${items.some((f) => f.kev) ? `<span class="sa-kev">KEV</span>` : ""}
        </button>
        ${isOpen ? `<div class="sa-grp-body">
          ${lead ? `<div class="sa-f-acts"><span class="sa-grp-fix">${esc(lead.fix)}</span>
            <button class="cmd-btn" type="button" data-propose="${esc(lead.id)}">PROPOSE UPGRADE</button></div>` : ""}
          ${items.map((f) => renderFinding(f, true)).join("")}</div>` : ""}
      </div>` });
    }
    $s("sa-list").innerHTML = blocks.sort((a, b) => a.r - b.r).map((b) => b.html).join("");
  }

  function render(d) {
    if (!d) return;
    data = d;
    const sc = d.last_scan && (d.last_scan.full || d.last_scan.light) ? d.score : null;
    $s("sa-score").textContent = sc == null ? "—" : sc;
    const fg = $s("sa-g-fg");
    fg.setAttribute("stroke-dasharray", `${sc || 0} 100`);
    fg.setAttribute("class", "sa-g-fg " + (sc == null ? "" : scoreClass(sc)));
    $s("sa-posture").textContent = sc == null ? "—" : `${sc}/100`;
    $s("sa-when").textContent = d.running ? `${d.running.toUpperCase()} SCAN RUNNING…`
      : `FULL ${ago(d.last_scan && d.last_scan.full)} · LIGHT ${ago(d.last_scan && d.last_scan.light)}`;
    $s("sa-ruleset").textContent = (d.intel && d.intel.ruleset) || "—";
    const c = d.counts || {};
    $s("sa-sev").innerHTML = SEV.filter((s) => s !== "info").map((s) =>
      `<button type="button" class="ft-stat sa-st ${s}${filter === s ? " on" : ""}" data-filter="${s}" title="Show ${s} findings only">
        <b>${c[s] || 0}</b><span>${s.toUpperCase()}</span></button>`).join("");
    $s("sa-card").classList.toggle("is-running", !!d.running);
    // sparkline
    const t = d.trend || [];
    if (t.length > 1) {
      const xs = (i) => (i / (t.length - 1)) * 196 + 2, ys = (v) => 38 - (v / 100) * 36;
      const pts = t.map((p, i) => `${xs(i).toFixed(1)},${ys(p.score).toFixed(1)}`).join(" ");
      $s("sa-spark").innerHTML = `<polyline class="sa-sp-line" points="${pts}" />` +
        t.map((p, i) => (p.new || p.resolved) ? `<circle class="sa-sp-dot ${p.new ? "new" : "res"}" cx="${xs(i).toFixed(1)}" cy="${ys(p.score).toFixed(1)}" r="1.8"><title>${esc(new Date(p.ts * 1000).toLocaleString())} · score ${p.score} · +${p.new} new / −${p.resolved} resolved</title></circle>` : "").join("");
    } else $s("sa-spark").innerHTML = "";
    $s("sa-trend-note").textContent = (t.length > 1 ? "" : "trend builds after 2 scans · ") +
      `last scan: ${(d.new || []).length} new · ${(d.resolved || []).length} resolved`;
    const i = d.intel || {};
    $s("sa-intel").innerHTML = `
      <div><span>OSV.dev</span><b>${esc(ago(i.osv_updated))}</b><em>${esc(i.osv_cached || 0)} advisories cached</em></div>
      <div><span>CISA KEV</span><b>${esc(ago(i.kev_updated))}</b><em>${i.kev_version ? "catalog " + esc(i.kev_version) + " · " + esc(i.kev_count) + " entries" : "not loaded"}</em></div>
      <div><span>NEXT FULL</span><b>${d.next_full ? esc(new Date(d.next_full * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })) : "—"}</b><em>light checks every 30 min</em></div>`;
    const ch = Object.entries(d.checks || {});
    if (ch.length) $s("sa-checks").innerHTML = ch.map(([k, v]) => `
      <div class="sa-chk ${esc(v.status)}" data-chk="${esc(k)}" role="button" tabindex="0" title="${esc(v.detail)} · click for detail"><span class="hc-dot"></span>
        <span class="sa-chk-n">${esc(CAT[k] || k.toUpperCase())}</span><span class="sa-chk-d">${esc(v.detail)}</span></div>`).join("");
    renderList();
    const acc = d.accepted || [];
    $s("sa-acc-n").textContent = acc.length + (d.accepted_rules && d.accepted_rules.length ? ` · ${d.accepted_rules.length} rule(s)` : "");
    $s("sa-acc").innerHTML = acc.length ? acc.map((f) => {
      const a = f.accepted || {}, ruleWide = (d.accepted_rules || []).includes(f.rule);
      return `<div class="sa-acc-i"><span class="sa-badge ${esc(f.severity)}">${esc(f.severity.toUpperCase())}</span>
        <span class="sa-f-title">${esc(f.title)}${a.note ? ` <i>— ${esc(a.note)}</i>` : ""}</span>
        <button class="cmd-btn" type="button" data-undo="${esc(f.id)}" data-scope="${ruleWide ? "rule" : "finding"}">UNDO</button></div>`;
    }).join("") : `<div class="tf-empty">nothing accepted</div>`;
    if (d.brief && d.brief.text) {
      $s("sa-brief").textContent = d.brief.text;
      $s("sa-brief-src").textContent = `${String(d.brief.engine || "AI").toUpperCase()} · ${new Date(d.brief.ts * 1000).toLocaleTimeString()}`;
    }
    const lg = d.log || [];
    $s("sa-log").innerHTML = lg.length ? lg.slice(0, 12).map((e) => `
      <div class="heal-ev ${esc(e.level)}"><span class="he-t">${new Date(e.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
        <span class="he-m">${esc(e.msg)}</span></div>`).join("") : `<div class="tf-empty">idle</div>`;
    clearTimeout(timer);
    timer = setTimeout(refresh, d.running ? 4000 : 60000);
  }

  async function refresh() {
    try {
      const r = await fetch("/api/secagent", { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      $s("sa-card").classList.remove("is-error");
      render(await r.json());
    } catch (e) {
      $s("sa-card").classList.add("is-error");
      $s("sa-when").textContent = "analyst unavailable (" + (e.message || "error") + ") · retrying";
      clearTimeout(timer); timer = setTimeout(refresh, 60000);
    }
  }
  const post = (u, b) => fetch(u, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b || {}) })
    .then((r) => r.json()).catch((e) => ({ ok: false, error: e.message }));

  async function scan(full, btn) {
    const lab = btn.textContent; btn.disabled = true; btn.textContent = "STARTING…";
    const r = await post("/api/secagent/scan", { full });
    if (r.ok === false) log(esc(r.error || "scan refused"), "warn");
    else log(`${full ? "full scan" : "light check"} started`);
    btn.textContent = lab; btn.disabled = false;
    setTimeout(refresh, 800);
  }
  $s("sa-scan").addEventListener("click", (e) => scan(true, e.currentTarget));
  $s("sa-light").addEventListener("click", (e) => scan(false, e.currentTarget));
  $s("sa-brief-btn").addEventListener("click", async (e) => {
    const b = e.currentTarget; b.disabled = true; b.textContent = "THINKING…";
    $s("sa-brief").textContent = "Asking the connected AI to brief on the measured findings…";
    const r = await post("/api/secagent/brief");
    if (r.ok === false) $s("sa-brief").textContent = r.error || "briefing unavailable";
    b.disabled = false; b.textContent = "AI BRIEF"; refresh();
  });

  function openCheck(k) {
    const v = (data && data.checks || {})[k]; if (!v) return;
    const rel = ((data && data.findings) || []).filter((f) => f.category === k);
    const html = `<div class="mb-grid">
        <div class="mb-stat"><span class="lbl">STATUS</span><span class="val">${esc(String(v.status).toUpperCase())}</span></div>
        <div class="mb-stat"><span class="lbl">OPEN FINDINGS</span><span class="val">${rel.length}</span></div>
        <div class="mb-stat"><span class="lbl">LAST FULL SCAN</span><span class="val">${esc(ago(data.last_scan && data.last_scan.full))}</span></div>
      </div><div class="mb-list"><div class="mb-row">${esc(v.detail || "—")}</div>
        ${rel.slice(0, 8).map((f) => `<div class="mb-row">${esc(f.severity.toUpperCase())} · ${esc(f.title)}</div>`).join("")}</div>
      <div class="si-src"><span class="lbl">DATA SOURCE</span> secagent.py ${esc(CAT[k] || k)} check <code>/api/secagent</code> · read ${new Date().toLocaleTimeString()}</div>`;
    if (typeof openCustomModal === "function") openCustomModal(CAT[k] || k.toUpperCase(), "CYBER ANALYST · CHECK", null, html);
  }
  $s("sa-filter-clear").addEventListener("click", () => { filter = null; render(data); });
  mount.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.matches("[data-chk]")) { e.preventDefault(); openCheck(e.target.dataset.chk); }
  });

  mount.addEventListener("click", async (e) => {
    const tb = e.target.closest("[data-tab], [data-tab-go]");
    if (tb) { showTab(tb.dataset.tab || tb.dataset.tabGo); return; }
    const ck = e.target.closest("[data-chk]");
    if (ck) { openCheck(ck.dataset.chk); return; }
    const el = e.target.closest("[data-toggle],[data-filter],[data-propose],[data-accept],[data-accept-rule],[data-undo]");
    if (!el || !mount.contains(el)) return;
    if (el.dataset.toggle) { const id = el.dataset.toggle; open.has(id) ? open.delete(id) : open.add(id); renderList(); return; }
    if (el.dataset.filter) { filter = filter === el.dataset.filter ? null : el.dataset.filter; render(data); return; }
    if (el.dataset.propose) {
      const lab = el.textContent; el.disabled = true; el.textContent = "SENDING…";
      const r = await post("/api/secagent/propose", { id: el.dataset.propose });
      if (r.ok === false) {
        log(esc(r.error || "proposal refused"), "warn");
      } else if (r.queued) {
        log("fix handed to engineering mode: review the proposal in the ENGINEERING panel (nothing is applied until you approve)");
      } else if (r.task) {
        // engineering agent not running: pre-fill its input so the commander can send it with one click
        const inp = document.getElementById("dev-input"), panel = document.getElementById("dev-panel");
        if (inp) inp.value = r.task;
        if (panel) panel.hidden = false;
        log("engineering agent offline: fix task pre-filled in the ENGINEERING panel. Press send to request a proposal", "warn");
      }
      el.textContent = lab; el.disabled = false; refresh(); return;
    }
    if (el.dataset.accept || el.dataset.acceptRule) {
      const id = el.dataset.accept || el.dataset.acceptRule, rule = !!el.dataset.acceptRule;
      const note = prompt(rule ? "Accept this whole rule as a known risk? Optional reason:" : "Accept this finding as a known risk? Optional reason:", "");
      if (note === null) return;
      const r = await post("/api/secagent/accept", { id, note, scope: rule ? "rule" : "finding" });
      if (r.ok === false) log(esc(r.error || "refused"), "warn"); else render(r);
      return;
    }
    if (el.dataset.undo) {
      const r = await post("/api/secagent/accept", { id: el.dataset.undo, scope: el.dataset.scope, undo: true });
      if (r.ok === false) log(esc(r.error || "refused"), "warn"); else render(r);
    }
  });

  refresh();
})();
