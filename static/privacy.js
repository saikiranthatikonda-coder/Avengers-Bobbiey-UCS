/* PRIVACY SHIELD · v8.7 — DOOM's live proof of what stays on this machine and what leaves it.
   Data: GET /api/privacy (privacyshield.py) every 15 s while visible. Everything shown is measured:
   this BUCS process's own sockets, git's own ignore check, the active AI route. Nothing simulated. */
(function () {
  "use strict";
  const mount = document.getElementById("privacy-mount");
  if (!mount) return;
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ago = t => { if (!t) return "—"; const s = Math.max(0, Date.now() / 1000 - t); return s < 90 ? `${Math.round(s)} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : `${(s / 3600).toFixed(1)} h ago`; };
  const kb = n => n == null ? "—" : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
  mount.innerHTML = `
  <div class="bs-card ft-card pv-card" id="pv-card">
    <div class="ft-head">
      <div class="ft-head-txt">
        <div class="ft-kicker">DOOM · MEASURED ON THIS PC · NOTHING SIMULATED</div>
        <div class="bs-title">PRIVACY SHIELD</div>
        <div class="bs-sub">What stays on this machine, and every host that hears from it · <span id="pv-when">sampling…</span></div>
      </div>
      <div class="ft-actions">
        <button class="cmd-btn" id="pv-scan" type="button" title="Sample this process's connections now">SCAN NOW</button>
        <button class="cmd-btn" id="pv-ask" type="button" title="Ask DOOM for a privacy verdict">ASK DOOM</button>
      </div>
    </div>
    <div class="pv-grid">
      <div class="pv-score">
        <svg viewBox="0 0 120 120" class="pv-ring" aria-hidden="true"><circle cx="60" cy="60" r="50" class="pv-ring-bg"/><circle cx="60" cy="60" r="50" class="pv-ring-fg" id="pv-ring" pathLength="100" stroke-dasharray="0 100"/></svg>
        <div class="pv-num" id="pv-num">—</div><div class="pv-lbl">PRIVACY</div>
        <ul class="pv-factors" id="pv-factors"></ul>
      </div>
      <div class="pv-col">
        <div class="pv-h">OUTBOUND · LIVE <span id="pv-live-n" class="pv-badge">—</span></div>
        <div class="pv-table" id="pv-out"><div class="tf-empty">sampling this process's sockets…</div></div>
        <div class="pv-foot" id="pv-out-foot"></div>
      </div>
      <div class="pv-col">
        <div class="pv-h">AI PROMPTS</div>
        <div class="pv-ai" id="pv-ai">—</div>
        <div class="pv-h">PERIMETER</div>
        <div class="pv-ai" id="pv-perim">—</div>
        <div class="pv-h">DATA AT REST · NEVER UPLOADED</div>
        <ul class="pv-files" id="pv-files"></ul>
        <div class="pv-h">SELF-DEVELOPMENT GUARD</div>
        <div class="pv-guard">Every change the agents write for themselves: <b>sandbox + tests</b> → <b>DOOM scans the exact bytes</b> (blocks secrets, shell, eval, TLS-off, private files) → <b>BOB signs</b> → <b>you approve</b>. Nothing deploys without you.</div>
      </div>
    </div>
  </div>`;
  const $ = id => document.getElementById(id);
  function render(d) {
    if (!d || d.ok === false) { $("pv-when").textContent = (d && d.error) || "unavailable"; return; }
    $("pv-when").textContent = d.sampled_at ? `sampled ${ago(d.sampled_at)}` : "first sample pending";
    $("pv-num").textContent = d.score;
    const ring = $("pv-ring"); ring.setAttribute("stroke-dasharray", `${d.score} ${100 - d.score}`);
    ring.classList.toggle("warn", d.score < 80); ring.classList.toggle("bad", d.score < 60);
    $("pv-factors").innerHTML = (d.factors || []).map(f => `<li class="${f.delta < 0 ? "neg" : "ok"}">${f.delta ? f.delta : "✓"} · ${esc(f.why)}</li>`).join("");
    const o = d.outbound || {};
    $("pv-live-n").textContent = `${o.live_count ?? 0} now · ${o.hosts_seen ?? 0} seen`;
    const rows = (o.live && o.live.length ? o.live : (o.ledger || []).slice(0, 12));
    $("pv-out").innerHTML = rows.length ? rows.map(h => `<div class="pv-row ${h.purpose ? "" : "unl"}">
        <b title="${esc(h.ip)}">${esc(h.name || h.ip)}</b>
        <span>${esc(h.purpose || (h.scope === "lan" ? "local network" : "UNLABELLED — judge it"))}</span>
        <small>:${esc((h.ports || []).join(", :"))} · ${ago(h.last_seen)}</small></div>`).join("")
      : `<div class="tf-empty">no outbound connections right now — nothing is leaving this machine</div>`;
    $("pv-out-foot").textContent = d.error ? `socket read: ${d.error}` : (o.live && o.live.length ? "" : "showing the most recent hosts (none connected this minute)");
    const ai = d.ai || {};
    $("pv-ai").innerHTML = `<span class="pv-dot ${ai.local ? "ok" : ai.local === false ? "warn" : ""}"></span>${esc(ai.where || "—")}`;
    $("pv-perim").innerHTML = `<span class="pv-dot ${/disabled|password/.test((d.perimeter || {}).remote_access || "") ? "ok" : ""}"></span>remote access ${esc((d.perimeter || {}).remote_access || "—")} · host console only trusted`;
    const r = d.at_rest || {};
    $("pv-files").innerHTML = (r.files || []).map(f => `<li><span class="pv-dot ${f.git_ignored ? "ok" : f.git_ignored === false ? "bad" : ""}"></span>${esc(f.name)} <small>${kb(f.bytes)} · ${f.git_ignored ? "git-ignored" : f.git_ignored === false ? "NOT ignored" : "no repo"}</small></li>`).join("")
      + (r.update_dir_files ? `<li><span class="pv-dot ${r.update_dir_ignored ? "ok" : ""}"></span>.update/ <small>${r.update_dir_files} private state files · ${r.update_dir_ignored ? "git-ignored" : "check"}</small></li>` : "");
  }
  async function load() {
    try { render(await (await fetch("/api/privacy", { cache: "no-store" })).json()); } catch (e) { $("pv-when").textContent = "unavailable"; }
  }
  $("pv-scan").addEventListener("click", async () => {
    $("pv-scan").disabled = true;
    try { const r = await fetch("/api/privacy/scan", { method: "POST" }); render(await r.json()); } catch (e) { /* shown by next load */ }
    $("pv-scan").disabled = false;
  });
  $("pv-ask").addEventListener("click", () => {
    const inp = document.getElementById("ask-input"); if (!inp) return;
    inp.value = "@doom privacy report: who is BUCS talking to, and is anything leaving this machine that shouldn't?";
    inp.dispatchEvent(new Event("input", { bubbles: true }));
    document.getElementById("ask-form")?.scrollIntoView({ behavior: "smooth", block: "center" }); inp.focus();
  });
  let vis = true;
  if ("IntersectionObserver" in window) new IntersectionObserver(es => { vis = es[0].isIntersecting; if (vis) load(); }).observe(mount);
  setInterval(() => { if (vis && !document.hidden) load(); }, 15000);
  load();
})();
