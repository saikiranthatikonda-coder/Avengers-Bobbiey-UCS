/* DOOM SENTINEL · v8.9 — DEFCON, intrusion watch, code integrity and the injection shield.
   Data: GET /api/sentinel (doomsentinel.py) every 10 s while visible. Every number is measured on
   this machine: perimeter refusals, git + SHA-256 integrity, neutralised injection phrases. */
(function () {
  "use strict";
  const mount = document.getElementById("sentinel-mount");
  if (!mount) return;
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ago = t => { if (!t) return "—"; const s = Math.max(0, Date.now() / 1000 - t); return s < 90 ? `${Math.round(s)} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : `${(s / 3600).toFixed(1)} h ago`; };
  const DEF_COL = { 1: "#ff1f4b", 2: "#ff6b3d", 3: "#ffb000", 4: "#ffd84d", 5: "#2bd46f" };
  mount.innerHTML = `
  <div class="bs-card ft-card pv-card sn-card" id="sn-card">
    <div class="ft-head">
      <div class="ft-head-txt">
        <div class="ft-kicker">DOOM · LIVE DEFENCE · MEASURED, NEVER SIMULATED</div>
        <div class="bs-title">DOOM SENTINEL</div>
        <div class="bs-sub">Intrusion watch · code integrity · prompt-injection shield · <span id="sn-when">arming…</span></div>
      </div>
      <div class="ft-actions">
        <button class="cmd-btn" id="sn-scan" type="button" title="Run the code-integrity check now (git + SHA-256 of the perimeter files)">INTEGRITY CHECK</button>
        <button class="cmd-btn" id="sn-base" type="button" title="After you reviewed an approved change: make the current perimeter code the new reference">ACCEPT CURRENT CODE</button>
        <button class="cmd-btn" id="sn-ask" type="button" title="Ask DOOM for a security verdict">ASK DOOM</button>
      </div>
    </div>
    <div class="pv-grid sn-grid">
      <div class="sn-def">
        <div class="sn-def-n" id="sn-def-n">—</div>
        <div class="sn-def-l">DEFCON <b id="sn-def-name">—</b></div>
        <div class="sn-def-bar" id="sn-def-bar">${[5, 4, 3, 2, 1].map(n => `<i data-l="${n}" style="--c:${DEF_COL[n]}">${n}</i>`).join("")}</div>
        <ul class="pv-factors" id="sn-reasons"></ul>
      </div>
      <div class="pv-col">
        <div class="pv-h">INTRUSION WATCH · 24 H <span class="pv-badge" id="sn-int-n">—</span></div>
        <div class="sn-spark" id="sn-spark" aria-label="Perimeter refusals per hour, last 24 hours"></div>
        <div class="sn-kinds" id="sn-kinds"></div>
        <div class="pv-table" id="sn-src"><div class="tf-empty">no refused requests: nobody has knocked</div></div>
        <div class="pv-foot">Memory only: IP, path without query, reason and time. Never headers, cookies, tokens or bodies.</div>
      </div>
      <div class="pv-col">
        <div class="pv-h">CODE INTEGRITY</div>
        <div class="sn-integ" id="sn-integ">first check runs ~20 s after start…</div>
        <div class="pv-h">INJECTION SHIELD <span class="pv-badge" id="sn-inj-n">—</span></div>
        <div class="sn-inj" id="sn-inj">—</div>
        <div class="pv-h">CHAIN OF DEFENCE</div>
        <div class="pv-guard">Perimeter (loopback-only · password · CSRF / DNS-rebinding guard) → <b>Sentinel</b> watches every refusal → <b>DOOM GATE</b> scans self-written code → <b>BOB signs</b> → <b>you approve</b> → Sentinel fingerprints the result.</div>
      </div>
    </div>
  </div>`;
  const $ = id => document.getElementById(id);
  function render(d) {
    if (!d || !d.defcon) { $("sn-when").textContent = (d && d.error) || "unavailable"; return; }
    const D = d.defcon, I = d.intrusion, G = d.integrity || {}, J = d.injection;
    $("sn-when").textContent = `updated ${ago(d.generated)}`;
    const col = DEF_COL[D.level];
    $("sn-def-n").textContent = D.level; $("sn-def-n").style.color = col; $("sn-def-n").style.textShadow = `0 0 24px ${col}`;
    $("sn-def-name").textContent = D.name; $("sn-def-name").style.color = col;
    mount.querySelectorAll("#sn-def-bar i").forEach(i => i.classList.toggle("on", +i.dataset.l === D.level));
    $("sn-reasons").innerHTML = D.reasons.length ? D.reasons.map(r => `<li class="neg">${esc(r)}</li>`).join("")
      : `<li class="ok">✓ no threat measured: perimeter quiet, code intact, no injections</li>`;
    $("sn-int-n").textContent = `${I.total_24h} refused · ${I.last_hour} last hour`;
    const mx = Math.max(1, ...I.hourly);
    $("sn-spark").innerHTML = I.hourly.map((v, k) => `<i style="height:${Math.max(4, v / mx * 100)}%" class="${v ? "on" : ""}" title="${v} refusal(s) ${23 - k} h ago"></i>`).join("");
    $("sn-kinds").innerHTML = Object.entries(I.by_kind).map(([k, v]) => `<span title="${esc(v.label)}">${esc(v.label)} <b>${v.n}</b></span>`).join("");
    $("sn-src").innerHTML = I.top_sources.length ? I.top_sources.map(s => `<div class="pv-row ${s.n >= 12 ? "unl" : ""}"><b>${esc(s.ip)}</b>
        <span>${s.n} refused · ${esc(s.kinds.join(", "))}</span><small>${ago(s.last)}</small></div>`).join("")
      + (I.bursts.length ? `<div class="sn-burst">⚠ ${I.bursts.length} intrusion burst(s) in 24 h · latest ${esc(I.bursts[0].ip)} (${I.bursts[0].n} in 5 min)</div>` : "")
      : `<div class="tf-empty">no refused requests in 24 h: nobody has knocked</div>`;
    const v = G.verdict || "pending";
    const vcol = { intact: "ok", review: "warn", tampered: "bad", partial: "warn", pending: "" }[v];
    let ih = `<div class="sn-verdict ${vcol}"><span class="pv-dot ${vcol}"></span><b>${esc(v.toUpperCase())}</b> ${G.checked_at ? "· checked " + ago(G.checked_at) : ""}</div>`;
    if (G.perimeter_files) ih += `<div class="sn-sub">${G.perimeter_files.length} perimeter files fingerprinted at start${G.perimeter_changed && G.perimeter_changed.length ? ` · <b class="bad">CHANGED ON DISK: ${esc(G.perimeter_changed.join(", "))}</b>` : " · unchanged"}</div>`;
    if (G.git === false) ih += `<div class="sn-sub">git not available: perimeter fingerprints only</div>`;
    const list = (lab, arr, cls) => arr && arr.length ? `<div class="sn-sub ${cls || ""}">${lab} (${arr.length}): ${arr.slice(0, 6).map(esc).join(", ")}${arr.length > 6 ? " …" : ""}</div>` : "";
    ih += list("changed outside a commit", G.modified) + list("deleted", G.deleted) + list("NEW untracked executables/scripts", G.untracked_exec, "bad");
    if (v === "intact") ih += `<div class="sn-sub">every tracked code file matches the last commit</div>`;
    $("sn-integ").innerHTML = ih;
    $("sn-inj-n").textContent = `${J.last_24h} in 24 h · ${J.total} total`;
    $("sn-inj").innerHTML = J.total ? `${Object.entries(J.by_source).map(([s, n]) => `<span class="sn-chip">${esc(s)} <b>${n}</b></span>`).join("")}
        <div class="sn-sub">${J.recent.slice(0, 4).map(r => `${esc(r.pattern)} in ${esc(r.source)} · ${ago(r.t)}`).join("<br>")}</div>`
      : `<div class="sn-sub">Every fact handed to an agent is screened for ${J.patterns.length} injection patterns (${esc(J.patterns.join(", "))}). Nothing has fired yet.</div>`;
  }
  async function load() {
    try { render(await (await fetch("/api/sentinel", { cache: "no-store" })).json()); } catch (e) { $("sn-when").textContent = "unavailable"; }
  }
  async function post(path, btn) {
    btn.disabled = true;
    try {
      const r = await fetch(path, { method: "POST" }); const j = await r.json();
      if (!r.ok || j.ok === false) $("sn-when").textContent = j.error || "refused"; else await load();
    } catch (e) { $("sn-when").textContent = "failed"; }
    btn.disabled = false;
  }
  $("sn-scan").addEventListener("click", () => post("/api/sentinel/scan", $("sn-scan")));
  $("sn-base").addEventListener("click", () => {
    if (!confirm("Accept the CURRENT perimeter code as the new trusted reference?\n\nOnly do this after you reviewed the change (git diff) and it was an approved update.")) return;
    post("/api/sentinel/rebaseline", $("sn-base"));
  });
  $("sn-ask").addEventListener("click", () => {
    const inp = document.getElementById("ask-input"); if (!inp) return;
    inp.value = "@doom what's our DEFCON, any intrusions, and is the code intact?";
    inp.dispatchEvent(new Event("input", { bubbles: true }));
    document.getElementById("ask-form")?.scrollIntoView({ behavior: "smooth", block: "center" }); inp.focus();
  });
  let vis = true;
  if ("IntersectionObserver" in window) new IntersectionObserver(es => { vis = es[0].isIntersecting; if (vis) load(); }).observe(mount);
  setInterval(() => { if (vis && !document.hidden) load(); }, 10000);
  load();
})();
