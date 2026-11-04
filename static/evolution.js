/* ═══ AUTONOMOUS EVOLUTION (evolution.py) ═══
   Renders into #evolution-mount. Observe → Analyze → Identify → Propose → Sandbox test →
   Approval → Deploy → Evaluate. Every number comes from /api/evolution (real measurements);
   code changes only ever happen after the commander presses APPROVE. */
(() => {
  const mount = document.getElementById("evolution-mount");
  if (!mount) return;
  const esc = (s) => (typeof escapeHTML === "function" ? escapeHTML(String(s ?? ""))
    : String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c])));
  const log = (html, kind = "info") => { try { logEvent(`<span class="tag">[evolution]</span> ${html}`, kind); } catch (e) {} };
  const $e = (id) => document.getElementById(id);
  const ago = (ts) => {
    if (!ts) return "never";
    const s = Date.now() / 1000 - ts, a = Math.abs(s), fut = s < 0;
    const t = a < 90 ? `${Math.round(a)} s` : a < 5400 ? `${Math.round(a / 60)} min` : a < 172800 ? `${Math.round(a / 3600)} h` : `${Math.round(a / 86400)} d`;
    return fut ? `in ${t}` : `${t} ago`;
  };
  const STAGE_LABEL = {
    identified: "IDENTIFIED", proposing: "PROPOSING", proposed: "PROPOSED", diagnosed: "NO CODE FIX", sandboxing: "SANDBOX…",
    sandbox_failed: "SANDBOX FAILED", awaiting_approval: "AWAITING APPROVAL", deploying: "DEPLOYING", evaluating: "EVALUATING",
    regressed: "REGRESSED", reverting: "REVERTING", kept: "KEPT", reverted: "REVERTED", rejected: "REJECTED",
    dismissed: "DISMISSED", resolved: "RESOLVED", failed: "FAILED",
  };
  const STAGE_TONE = { awaiting_approval: "warn", regressed: "bad", sandbox_failed: "bad", failed: "bad", kept: "good",
    resolved: "good", reverted: "dim", rejected: "dim", dismissed: "dim", evaluating: "live", sandboxing: "live",
    proposing: "live", deploying: "live", reverting: "live" };
  let data = null, caps = null, timer = null, capsTimer = null, capsIssuesOnly = true;
  const open = new Set();

  mount.innerHTML = `
  <div class="bs-card ev-card" id="ev-card">
    <div class="bs-head ev-head">
      <div>
        <div class="bs-title">AUTONOMOUS EVOLUTION · CONTROLLED SELF-IMPROVEMENT</div>
        <div class="bs-sub"><span id="ev-mode">—</span> · HEARTBEAT <span id="ev-hb">—</span> · AUTONOMY <span id="ev-auto">—</span></div>
      </div>
      <div class="ev-head-acts">
        <button class="cmd-btn" id="ev-beat" type="button" title="Run an observe + analyze pass now">HEARTBEAT NOW</button>
        <button class="cmd-btn" id="ev-analyst" type="button" title="LLM analyst — sees only the measured evidence">AI ANALYST</button>
        <button class="cmd-btn" id="ev-tests" type="button" title="Run the unit-test suite (owner machine)">RUN TESTS</button>
        <button class="cmd-btn" id="ev-research" type="button" title="Research mission: dependency releases + model news">RESEARCH</button>
        <button class="cmd-btn" id="ev-autonomy" type="button">—</button>
      </div>
    </div>
    <div class="ev-grid">
      <div class="ev-left">
        <div class="ev-ring-wrap"><svg class="ev-ring" id="ev-ring" viewBox="0 0 320 320" aria-label="Evolution loop"></svg></div>
        <div class="intel-divider">HEARTBEAT · OBSERVATION SOURCES</div>
        <div class="ev-sources" id="ev-sources"><div class="tf-empty">first heartbeat ~45 s after start…</div></div>
        <div class="intel-divider">RECENT ERRORS · SCRUBBED</div>
        <div class="ev-errs" id="ev-errs"><div class="tf-empty">no server errors recorded</div></div>
      </div>
      <div class="ev-mid">
        <div class="intel-divider">APPROVAL QUEUE · <span id="ev-appr-n">0</span></div>
        <div class="ev-approvals" id="ev-approvals"><div class="tf-empty">nothing waiting for you</div></div>
        <div class="intel-divider">IMPROVEMENT BACKLOG · <span id="ev-bl-n">0</span> OPEN</div>
        <div class="ev-backlog" id="ev-backlog"><div class="tf-empty">no improvements identified yet</div></div>
        <details class="ev-closed"><summary>CLOSED · <span id="ev-closed-n">0</span></summary><div id="ev-closed"></div></details>
      </div>
      <div class="ev-right">
        <div class="intel-divider">EVALUATION OUTCOMES</div>
        <div class="ev-evals" id="ev-evals"><div class="tf-empty">nothing deployed yet</div></div>
        <div class="intel-divider">SANDBOX TEST RUNS</div>
        <div class="ev-sbx" id="ev-sbx"><div class="tf-empty">no sandbox runs yet</div></div>
        <div class="intel-divider">AI ANALYST · <span id="ev-an-src">—</span></div>
        <div class="ev-analyst" id="ev-analyst-text">Press AI ANALYST for a read of the backlog grounded only in measured evidence.</div>
        <div class="intel-divider">FEEDBACK</div>
        <div class="ev-fb">
          <select id="ev-fb-area" aria-label="Feedback area"></select>
          <input id="ev-fb-note" maxlength="300" placeholder="what worked / what didn't (optional)" />
          <button class="ev-vote up" type="button" data-vote="up" title="Works well">👍</button>
          <button class="ev-vote down" type="button" data-vote="down" title="Needs work">👎</button>
        </div>
        <div class="ev-fb-sum" id="ev-fb-sum">—</div>
        <div class="intel-divider">RESEARCH · <span id="ev-rs-when">—</span></div>
        <div class="ev-research" id="ev-research-list"><div class="tf-empty">weekly mission: PyPI releases + AI model news</div></div>
      </div>
    </div>
    <details class="ev-caps" id="ev-caps-wrap">
      <summary>CAPABILITY MATRIX · <span id="ev-caps-sum">probing…</span></summary>
      <div class="ev-caps-bar"><label><input type="checkbox" id="ev-caps-issues" checked /> issues only</label>
        <span class="ev-legend"><i class="d ok"></i>LOADED <i class="d cf"></i>CONFIGURED <i class="d ts"></i>TESTS</span></div>
      <div class="ev-caps-grid" id="ev-caps"></div>
    </details>
  </div>`;

  const post = (u, b) => fetch(u, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b || {}) })
    .then((r) => r.json()).catch((e) => ({ ok: false, error: e.message }));

  // ── loop ring ────────────────────────────────────────────────
  function renderRing(d) {
    const loop = d.loop || [], cx = 160, cy = 160, R = 118;
    const cur = d.current;
    const pos = (i) => { const a = (i / loop.length) * Math.PI * 2 - Math.PI / 2; return [cx + R * Math.cos(a), cy + R * Math.sin(a)]; };
    let s = `<circle class="ev-r-track" cx="${cx}" cy="${cy}" r="${R}" />`;
    loop.forEach((st, i) => {
      const [x1, y1] = pos(i), [x2, y2] = pos((i + 1) % loop.length);
      const mx = (x1 + x2) / 2, my = (y1 + y2) / 2, ang = Math.atan2(y2 - y1, x2 - x1) * 180 / Math.PI;
      s += `<path class="ev-r-arrow" d="M -4 -3 L 2 0 L -4 3" transform="translate(${mx.toFixed(1)} ${my.toFixed(1)}) rotate(${ang.toFixed(1)})" />`;
    });
    loop.forEach((st, i) => {
      const [x, y] = pos(i), here = cur && cur.loop === st.stage;
      const cls = `ev-node${st.busy ? " busy" : ""}${here ? " here" : ""}${st.stage === "approval" && st.count ? " attn" : ""}`;
      s += `<g class="${cls}" transform="translate(${x.toFixed(1)} ${y.toFixed(1)})"><title>${esc(st.label)} · ${esc(st.count)} ${esc(st.note)}</title>
        <circle r="25" class="ev-n-bg" /><circle r="25" class="ev-n-ring" />
        <text class="ev-n-c" y="3">${esc(st.count > 9999 ? Math.round(st.count / 1000) + "k" : st.count)}</text>
        <text class="ev-n-l" y="${y < cy - 10 ? -31 : y > cy + 10 ? 39 : 37}">${esc(st.label)}</text></g>`;
    });
    if (cur) {
      const i = loop.findIndex((st) => st.stage === cur.loop);
      if (i >= 0) { const [x, y] = pos(i); s += `<circle class="ev-pip" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="5" />`; }
    }
    const t = cur ? cur.title : (d.heartbeat && d.heartbeat.running ? "observing…" : "idle — watching");
    const words = String(t).split(/\s+/); const lines = []; let ln = "";
    for (const w of words) { if ((ln + " " + w).trim().length > 22) { lines.push(ln.trim()); ln = w; } else ln += " " + w; }
    if (ln.trim()) lines.push(ln.trim());
    s += `<text class="ev-c-k" x="${cx}" y="${cy - 26}">${cur ? "CURRENT ITEM" : "LOOP"}</text>`;
    lines.slice(0, 3).forEach((l, k) => { s += `<text class="ev-c-t" x="${cx}" y="${cy - 8 + k * 13}">${esc(l)}${k === 2 && lines.length > 3 ? "…" : ""}</text>`; });
    if (cur) s += `<text class="ev-c-s ${esc(STAGE_TONE[cur.stage] || "")}" x="${cx}" y="${cy + 38}">${esc(STAGE_LABEL[cur.stage] || cur.stage)}</text>`;
    $e("ev-ring").innerHTML = s;
  }

  // ── pieces ───────────────────────────────────────────────────
  const meter = (lab, n) => `<span class="ev-m" title="${lab} ${n}/5"><b>${lab}</b>${"▮".repeat(n)}<i>${"▮".repeat(5 - n)}</i></span>`;
  const stageChip = (st) => `<span class="ev-st ${esc(STAGE_TONE[st] || "")}">${esc(STAGE_LABEL[st] || st)}</span>`;
  function spark(trend) {
    if (!trend || trend.length < 2) return "";
    const vs = trend.map((p) => +p[1]), mn = Math.min(...vs), mx = Math.max(...vs), rg = mx - mn || 1;
    const pts = vs.map((v, i) => `${(i / (vs.length - 1) * 118 + 1).toFixed(1)},${(22 - (v - mn) / rg * 20).toFixed(1)}`).join(" ");
    return `<svg class="ev-spark" viewBox="0 0 120 24" preserveAspectRatio="none"><polyline points="${pts}" /></svg>`;
  }
  function testsLine(t) {
    if (!t) return "—";
    if (t.ok === null || t.skipped) return `skipped · ${esc(t.skipped || "")}`;
    return `${t.ok ? "PASS" : "FAIL"} · ${esc((t.modules || []).join(", ") || "suite")} · ${esc(t.ran ?? "?")} tests${t.failing && t.failing.length ? " · failing " + esc(t.failing.slice(0, 3).join(", ")) : ""}`;
  }
  function bootLine(b) {
    if (!b) return "—";
    const pr = (b.probes || []).map((p) => `${p.path} ${p.ok ? p.ms + "ms" : "✗"}`).join(" · ");
    return `${b.ok ? "BOOTED" : "FAILED"}${b.boot_s != null ? " in " + b.boot_s + " s" : ""}${pr ? " · " + esc(pr) : ""}${b.tracebacks ? " · " + b.tracebacks + " traceback(s)" : ""}`;
  }
  function diffHTML(diff) {
    return String(diff || "").split("\n").map((l) => {
      const c = l.startsWith("+++") || l.startsWith("---") ? "f" : l.startsWith("@@") ? "h" : l[0] === "+" ? "a" : l[0] === "-" ? "r" : "";
      return `<span class="${c}">${esc(l)}</span>`;
    }).join("\n");
  }

  function actionsFor(it, owner) {
    const b = [];
    const btn = (act, label, title = "") => `<button class="cmd-btn" type="button" data-act="${act}" data-id="${esc(it.id)}" title="${esc(title)}">${label}</button>`;
    if (owner && it.code_level && ["identified", "diagnosed", "failed", "rejected", "reverted", "sandbox_failed"].includes(it.stage))
      b.push(btn("propose", it.stage === "identified" ? "PROPOSE FIX" : "PROPOSE AGAIN", "Engineering diagnoses (read-only), then tests the change in an isolated copy. Nothing reaches the real code until you approve."));
    if (owner && it.stage === "sandbox_failed") b.push(btn("sandbox", "RETRY SANDBOX"));
    if (owner && ["evaluating", "regressed", "kept"].includes(it.stage)) b.push(btn("revert", "REVERT", "Restore the files this change wrote (approval)"));
    if (["evaluating", "regressed"].includes(it.stage)) b.push(btn("keep", "KEEP"));
    if (owner && it.deploy && !it.deploy.shipped && ["evaluating", "kept"].includes(it.stage)) b.push(btn("ship", "SHIP", "Commit + push via engineering mode (tests must pass)"));
    if (["identified", "diagnosed", "failed", "rejected", "reverted", "sandbox_failed", "proposed"].includes(it.stage)) b.push(btn("dismiss", "DISMISS 7 D"));
    return b.join("");
  }

  function itemDrawer(it, owner) {
    const ev = it.eval;
    return `<div class="ev-drawer">
      <div class="ev-ev">${(it.evidence || []).map((e) => `<div>▸ ${esc(e)}</div>`).join("")}</div>
      ${it.trend && it.trend.length > 1 ? `<div class="ev-trend"><b>TREND</b>${spark(it.trend)}<span>${esc(it.trend.length)} observations · seen ${esc(it.seen)}×</span></div>` : ""}
      <div class="ev-meters">${meter("IMPACT", it.impact)}${meter("RISK", it.risk)}${meter("EFFORT", it.effort)}<span class="ev-m"><b>CONF</b>${Math.round((it.confidence || 0) * 100)}%</span><span class="ev-m"><b>PRIO</b>${esc(it.priority)}</span></div>
      ${it.recommendation ? `<div class="ev-rec"><b>RECOMMENDATION</b> ${esc(it.recommendation)}</div>` : ""}
      ${it.code_level ? `<div class="ev-rec"><b>CODE-LEVEL</b> handed to the engineering agent as a precise task (diagnose → sandbox → your approval)</div>` : ""}
      ${it.summary ? `<div class="ev-rec"><b>ENGINEERING</b> ${esc(it.summary)}</div>` : ""}
      ${it.analyst ? `<div class="ev-rec"><b>ANALYST</b> ${esc(it.analyst)}</div>` : ""}
      ${it.error ? `<div class="ev-rec bad"><b>ERROR</b> ${esc(it.error)}</div>` : ""}
      ${it.sandbox ? `<div class="ev-rec ${it.sandbox.ok ? "" : "bad"}"><b>SANDBOX</b> ${it.sandbox.ok ? "passed" : "failed: " + esc(it.sandbox.reason || "")} · tests ${testsLine(it.sandbox.tests)} · ${bootLine(it.sandbox.boot)}</div>` : ""}
      ${it.approval ? `<div class="ev-rec"><b>DECISION</b> ${esc(it.approval.decision)} by ${esc(it.approval.by)} ${esc(ago(it.approval.ts))}${it.approval.reason ? " — " + esc(it.approval.reason) : ""}</div>` : ""}
      ${ev ? `<div class="ev-rec ${ev.verdict === "regressed" || ev.verdict === "catastrophic" ? "bad" : ""}"><b>EVALUATION</b> ${esc(ev.status || "")}${ev.baseline != null ? ` · baseline ${(+ev.baseline).toFixed(1)}` : ""}${ev.after != null ? ` → after ${(+ev.after).toFixed(1)}` : ""}${ev.metric ? " · " + esc(ev.metric.key) : ""}</div>` : ""}
      <div class="sa-f-acts">${actionsFor(it, owner)}</div>
    </div>`;
  }

  function renderItem(it, owner) {
    const isOpen = open.has(it.id);
    return `<div class="ev-item ${esc(STAGE_TONE[it.stage] || "")}">
      <button class="ev-i-head" type="button" data-toggle="${esc(it.id)}" aria-expanded="${isOpen}">
        <span class="ev-kind">${esc(String(it.kind || "").toUpperCase())}</span>
        <span class="ev-i-title">${esc(it.title)}</span>
        ${it.code_level ? `<span class="ev-code" title="code-level: engineering can propose a change">CODE</span>` : ""}
        <span class="ev-prio" title="priority = (impact×2 + (6−risk) + (6−effort)) × confidence">${esc(it.priority)}</span>
        ${stageChip(it.stage)}
      </button>
      ${isOpen ? itemDrawer(it, owner) : ""}
    </div>`;
  }

  function renderApproval(it) {
    const sb = it.sandbox || {};
    return `<div class="ev-appr">
      <div class="ev-appr-h"><span class="ev-kind">${esc(String(it.kind).toUpperCase())}</span><span class="ev-i-title">${esc(it.title)}</span>${meter("RISK", it.risk)}</div>
      ${it.summary ? `<div class="ev-appr-s">${esc(it.summary)}</div>` : ""}
      <div class="ev-appr-grid">
        <span>FILES</span><b>${esc((sb.changed || []).join(", ") || "—")} <i class="ev-add">+${esc(sb.added || 0)}</i> <i class="ev-rem">−${esc(sb.removed || 0)}</i></b>
        <span>UNIT TESTS</span><b class="${sb.tests && sb.tests.ok === false ? "bad" : "good"}">${testsLine(sb.tests)}</b>
        <span>BOOT CHECK</span><b class="${sb.boot && sb.boot.ok ? "good" : "bad"}">${bootLine(sb.boot)}</b>
        <span>SANDBOX</span><b>${esc(sb.seconds ?? "?")} s · ${esc(ago(sb.ts))} · isolated copy, nothing deployed</b>
      </div>
      <details class="ev-diff-w"><summary>VIEW DIFF</summary><pre class="ev-diff">${diffHTML(sb.diff)}</pre></details>
      ${it.plan ? `<details class="ev-diff-w"><summary>ENGINEERING PLAN</summary><pre class="ev-plan">${esc(it.plan)}</pre></details>` : ""}
      <div class="sa-f-acts">
        <button class="cmd-btn ev-approve" type="button" data-act="approve" data-id="${esc(it.id)}" title="Deploy into the working tree via engineering mode (tests run again), then evaluate">APPROVE &amp; DEPLOY</button>
        <button class="cmd-btn" type="button" data-act="reject" data-id="${esc(it.id)}">REJECT…</button>
      </div>
    </div>`;
  }

  function renderCaps() {
    if (!caps) return;
    const s = caps.summary || {};
    $e("ev-caps-sum").textContent = `${s.done}/${s.total} DONE · ${s.loaded} LOADED · ${s.failing_tests} FAILING TESTS${caps.tests_last_run ? " · tests " + ago(caps.tests_last_run) : " · tests not run yet"}`;
    const rows = (caps.capabilities || []).filter((c) => !capsIssuesOnly || !c.loaded || c.tests === "fail" || c.configured === false);
    const dot = (v, k) => `<i class="d ${v === true || v === "pass" ? "on" : v === false || v === "fail" ? "off" : "na"}" title="${k}: ${v === null || v === undefined ? "n/a" : v}"></i>`;
    $e("ev-caps").innerHTML = rows.length ? rows.map((c) => `<div class="ev-cap${c.done ? "" : " nd"}">
      <span class="ev-cap-p">P${esc(c.phase)}</span><span class="ev-cap-n">${esc(c.name)}</span>
      <span class="ev-cap-d">${dot(c.loaded, "loaded")}${dot(c.configured, "configured")}${dot(c.tests, "tests")}</span>
      <span class="ev-cap-t">${c.last_success ? esc(ago(c.last_success)) : ""}</span></div>`).join("")
      : `<div class="tf-empty">no issues — every capability is loaded and its tests pass</div>`;
  }

  function render(d) {
    if (!d) return;
    data = d;
    const hb = d.heartbeat || {}, owner = !!d.owner;
    $e("ev-mode").textContent = (d.mode || "").toUpperCase();
    $e("ev-hb").textContent = hb.running ? "RUNNING…" : `#${hb.beats || 0} · ${ago(hb.last)} · NEXT ${hb.next ? ago(hb.next) : "—"}${hb.last_ms != null ? " · " + hb.last_ms + " ms" : ""}${hb.last_error ? " · ERROR" : ""}`;
    const auto = (d.settings || {}).autonomy;
    $e("ev-auto").textContent = auto === "propose" ? "PROPOSE (APPROVAL-GATED)" : "OBSERVE ONLY";
    const ab = $e("ev-autonomy");
    ab.textContent = auto === "propose" ? "SET: OBSERVE ONLY" : "SET: AUTO-PROPOSE";
    ab.title = auto === "propose" ? "Stop auto-proposing; keep observing + analysing" : "Let the loop hand its top item to engineering (max 1 per 6 h). Deploy still needs your approval.";
    ab.hidden = !owner; $e("ev-tests").hidden = !owner;
    renderRing(d);
    // sources
    const src = (d.observe && d.observe.sources) || {};
    const ks = Object.keys(src);
    if (ks.length) $e("ev-sources").innerHTML = ks.map((k) => `<div class="ev-src ${String(src[k]).startsWith("error") ? "bad" : ""}"><span>${esc(k.toUpperCase())}</span><em>${esc(src[k])}</em></div>`).join("") +
      `<div class="ev-src-f">${esc(d.observe.samples || 0)} samples last pass · analysis: ${esc((d.analysis || {}).candidates ?? 0)} candidates, +${esc((d.analysis || {}).new ?? 0)} new, ${esc((d.analysis || {}).resolved ?? 0)} resolved</div>`;
    const er = d.errors || [];
    $e("ev-errs").innerHTML = er.length ? er.slice(0, 6).map((e) => `<div class="ev-err" title="${esc(e.msg)}"><b>${esc(e.count)}×</b><span>${esc(e.type)} @ ${esc(e.where)}</span><i>${esc(ago(e.last))}</i></div>`).join("") : `<div class="tf-empty">no server errors recorded</div>`;
    // approvals
    const ap = d.approvals || [];
    $e("ev-appr-n").textContent = ap.length;
    $e("ev-approvals").innerHTML = ap.length ? ap.map(renderApproval).join("") : `<div class="tf-empty">nothing waiting for you</div>`;
    // backlog
    const bl = (d.backlog || []).filter((i) => i.stage !== "awaiting_approval");
    $e("ev-bl-n").textContent = (d.backlog || []).length;
    $e("ev-backlog").innerHTML = bl.length ? bl.map((i) => renderItem(i, owner)).join("") : `<div class="tf-empty">no improvements identified yet — the heartbeat keeps watching</div>`;
    const cl = d.closed || [];
    $e("ev-closed-n").textContent = cl.length;
    $e("ev-closed").innerHTML = cl.length ? cl.map((i) => renderItem(i, owner)).join("") : `<div class="tf-empty">none</div>`;
    // evaluations
    const evs = d.evaluations || [];
    $e("ev-evals").innerHTML = evs.length ? evs.map((i) => { const e = i.eval || {}; return `<div class="ev-eval ${esc(e.verdict || "")}">
      <span class="ev-i-title">${esc(i.title)}</span>${stageChip(i.stage)}
      <div class="ev-eval-d">${esc(e.status || "")}${e.baseline != null ? ` · ${(+e.baseline).toFixed(1)}` : ""}${e.after != null ? ` → ${(+e.after).toFixed(1)}` : ""}</div>
      ${["evaluating", "regressed"].includes(i.stage) && owner ? `<div class="sa-f-acts">${i.stage === "regressed" ? `<button class="cmd-btn ev-danger" type="button" data-act="revert" data-id="${esc(i.id)}">REVERT (ONE CLICK)</button>` : ""}<button class="cmd-btn" type="button" data-act="keep" data-id="${esc(i.id)}">KEEP</button></div>` : ""}</div>`; }).join("") : `<div class="tf-empty">nothing deployed yet</div>`;
    const sr = d.sandbox_runs || [];
    $e("ev-sbx").innerHTML = sr.length ? sr.map((s) => `<div class="ev-sb ${s.ok ? "good" : "bad"}"><b>${s.ok ? "PASS" : "FAIL"}</b><span class="ev-i-title">${esc(s.title)}</span><i>${esc(ago(s.ts))}</i>
      <div class="ev-eval-d">${esc((s.changed || []).join(", ") || s.reason || "")} · tests ${testsLine(s.tests)} · ${bootLine(s.boot)}</div></div>`).join("") : `<div class="tf-empty">no sandbox runs yet</div>`;
    if (d.analyst && d.analyst.text) {
      $e("ev-analyst-text").textContent = d.analyst.text;
      $e("ev-an-src").textContent = `${String(d.analyst.engine || "AI").toUpperCase()} · ${ago(d.analyst.ts)}`;
    }
    const fb = d.feedback || {};
    const sel = $e("ev-fb-area");
    if (!sel.options.length) sel.innerHTML = (fb.areas || ["general"]).map((a) => `<option value="${esc(a)}">${esc(a.toUpperCase())}</option>`).join("");
    $e("ev-fb-sum").textContent = `7 days: ${fb.up_7d || 0} 👍 · ${fb.down_7d || 0} 👎` + ((fb.recent || [])[0] ? ` · last: ${fb.recent[0].vote === "up" ? "👍" : "👎"} ${fb.recent[0].area}${fb.recent[0].note ? " — " + fb.recent[0].note : ""}` : "");
    const rs = d.research || {};
    $e("ev-rs-when").textContent = rs.running ? "RUNNING…" : rs.ts ? ago(rs.ts).toUpperCase() : "NOT RUN YET";
    const sg = rs.suggestions || [];
    $e("ev-research-list").innerHTML = sg.length ? sg.slice(0, 10).map((s) => `<div class="ev-rs"><span class="ev-kind">${esc(s.kind === "dependency" ? "DEP" : "WATCH")}</span><span class="ev-rs-t">${esc(s.title)}</span>${s.url && /^https:\/\//.test(s.url) ? `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">notes ↗</a>` : ""}</div>`).join("")
      + ((rs.errors || []).length ? `<div class="ev-src-f">${esc(rs.errors.length)} source error(s): ${esc(rs.errors.slice(0, 2).join("; "))}</div>` : "")
      : `<div class="tf-empty">${rs.ts ? "everything up to date" : "weekly mission: PyPI releases + AI model news"}</div>`;
    const busy = (d.loop || []).some((s) => s.busy) || hb.running || (d.tests || {}).running || rs.running;
    $e("ev-tests").textContent = (d.tests || {}).running ? "TESTS RUNNING…" : "RUN TESTS";
    clearTimeout(timer);
    timer = setTimeout(refresh, busy ? 4000 : 30000);
  }

  async function refresh() {
    try { render(await (await fetch("/api/evolution", { cache: "no-store" })).json()); }
    catch (e) { clearTimeout(timer); timer = setTimeout(refresh, 60000); }
  }
  async function refreshCaps() {
    try { caps = await (await fetch("/api/evolution/capabilities", { cache: "no-store" })).json(); renderCaps(); } catch (e) {}
    clearTimeout(capsTimer); capsTimer = setTimeout(refreshCaps, 120000);
  }

  async function simple(btn, url, body, okMsg) {
    const lab = btn.textContent; btn.disabled = true; btn.textContent = "…";
    const r = await post(url, body);
    if (r.ok === false) log(esc(r.error || "refused"), "warn"); else if (okMsg) log(okMsg);
    btn.textContent = lab; btn.disabled = false; refresh();
    return r;
  }
  $e("ev-beat").addEventListener("click", (e) => simple(e.currentTarget, "/api/evolution/heartbeat", {}, "heartbeat: observe + analyze pass complete").then(refreshCaps));
  $e("ev-analyst").addEventListener("click", (e) => { $e("ev-analyst-text").textContent = "Asking the connected AI — it sees only the measured evidence…"; simple(e.currentTarget, "/api/evolution/analyst", {}); });
  $e("ev-tests").addEventListener("click", (e) => simple(e.currentTarget, "/api/evolution/tests", {}, "unit-test run started (owner machine)"));
  $e("ev-research").addEventListener("click", (e) => simple(e.currentTarget, "/api/evolution/research", {}, "research mission started"));
  $e("ev-autonomy").addEventListener("click", (e) => simple(e.currentTarget, "/api/evolution/settings",
    { autonomy: (data && data.settings && data.settings.autonomy) === "propose" ? "observe" : "propose" }));
  $e("ev-caps-issues").addEventListener("change", (e) => { capsIssuesOnly = e.currentTarget.checked; renderCaps(); });
  $e("ev-caps-wrap").addEventListener("toggle", (e) => { if (e.currentTarget.open) refreshCaps(); });

  mount.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-toggle],[data-act],[data-vote]");
    if (!el || !mount.contains(el)) return;
    if (el.dataset.toggle) { const id = el.dataset.toggle; open.has(id) ? open.delete(id) : open.add(id); render(data); return; }
    if (el.dataset.vote) {
      const r = await post("/api/evolution/feedback", { vote: el.dataset.vote, area: $e("ev-fb-area").value, note: $e("ev-fb-note").value });
      if (r.ok === false) log(esc(r.error || "refused"), "warn"); else { $e("ev-fb-note").value = ""; log("feedback recorded — the analyzers use it"); }
      refresh(); return;
    }
    const id = el.dataset.id, act = el.dataset.act;
    let body = {};
    if (act === "reject") {
      const reason = prompt("Reject this change. Reason (kept with the decision):", "");
      if (!reason) return; body = { reason };
    } else if (act === "dismiss") {
      const reason = prompt("Dismiss for 7 days. Optional reason:", "");
      if (reason === null) return; body = { reason, days: 7 };
    } else if (act === "approve") {
      if (!confirm("Deploy this sandbox-tested change into the BUCS working tree? Engineering mode writes it, runs the tests again, and the evaluation starts. You can revert it.")) return;
    } else if (act === "revert") {
      if (!confirm("Revert this change? Only files still exactly as deployed are restored.")) return;
    } else if (act === "ship") {
      if (!confirm("Commit and push this change to GitHub via engineering mode?")) return;
    }
    el.disabled = true;
    const r = await post(`/api/evolution/items/${encodeURIComponent(id)}/${act}`, body);
    if (r.ok === false) log(esc(r.error || "refused"), "warn");
    else log(`${esc(act)}: ${esc(r.stage || "done")}`);
    el.disabled = false; refresh();
  });

  refresh();
  setTimeout(refreshCaps, 1500);
})();
