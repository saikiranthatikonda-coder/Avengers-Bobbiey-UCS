/* ═══ AI CORE · AGENTS, MEMORY, MODELS (agentos.py) ═══
   Renders into #agentos-mount. Every figure comes from /api/agentos* (real discovery,
   real health checks, real calls). Nothing here holds or shows an API key. */
(() => {
  const mount = document.getElementById("agentos-mount");
  if (!mount) return;
  const esc = (s) => (typeof escapeHTML === "function" ? escapeHTML(String(s ?? ""))
    : String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c])));
  const log = (html, kind = "info") => { try { logEvent(`<span class="tag">[ai core]</span> ${html}`, kind); } catch (e) {} };
  const $a = (id) => document.getElementById(id);
  const SRC = window.BUCS_SRC || null;     // app.js source-link helpers (http(s) only, new tab, noopener)
  const linkify = (escaped) => (SRC ? SRC.linkify(escaped) : escaped);
  // web tools (web_lookup / news_search) fetch live sources: list them as links back to the original pages
  async function showCites(steps) {
    const qs = [...new Set(steps.filter((s) => s.ok && ["web_lookup", "news_search"].includes(s.tool) && s.arguments && s.arguments.query)
      .map((s) => String(s.arguments.query)))].slice(0, 3);
    const box = $a("ao-cites"); if (!box || !qs.length || !SRC) return;
    const all = [];
    for (const q of qs) {
      try { const d = await post("/api/livefacts/sources", { query: q }); all.push(...((d && d.sources) || [])); } catch (e) {}
    }
    if (all.length && box.isConnected)
      box.innerHTML = `<span class="ao-dim">SOURCES</span> ` + all.slice(0, 8).map((c) => SRC.link(c.url, (c.kind === "Google News" ? c.source : c.kind || c.source) + (c.title ? ": " + String(c.title).slice(0, 60) : ""))).join(" · ");
  }
  const post = (u, b) => fetch(u, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b || {}) })
    .then(async (r) => { try { return await r.json(); } catch (e) { return { ok: false, error: "HTTP " + r.status }; } })
    .catch((e) => ({ ok: false, error: e.message }));
  const get = (u) => fetch(u, { cache: "no-store" }).then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); });
  const ago = (ts) => {
    if (!ts) return "never";
    const s = Math.max(0, Date.now() / 1000 - ts);
    return s < 90 ? `${Math.round(s)} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
  };
  const ms = (v) => (v == null ? "—" : v < 1000 ? `${v} ms` : `${(v / 1000).toFixed(1)} s`);
  const TIER = { read: "READ", action: "ACTION · COMMANDER", destructive: "APPROVAL" };
  const TABS = [["models", "MODELS & ROUTING"], ["tools", "TOOLS"], ["memory", "MEMORY"], ["studio", "AGENTS & WORKFLOWS"],
    ["market", "MARKETPLACE"], ["integrations", "INTEGRATIONS"]];
  let data = null, tab = "models", timer = null, editingAgent = null, editingWf = null, staged = null;
  // unsaved operator edits: polling (and other tabs' changes) must not overwrite them
  let routingDirty = false, foldersDirty = false;
  try { tab = localStorage.getItem("ao-tab") || "models"; } catch (e) {}

  mount.innerHTML = `
  <div class="bs-card ao-card" id="ao-card">
    <div class="bs-head">
      <div class="bs-title">AI CORE · AGENTS, MEMORY, MODELS</div>
      <div class="bs-sub" id="ao-sub">discovering providers…</div>
      <div class="bs-sub"><span class="src-local">SOURCE · this install (provider discovery + health checks); web answers list their sources below</span></div>
    </div>
    <div class="ao-approvals" id="ao-approvals" hidden></div>
    <div class="ao-tabs" role="tablist">${TABS.map(([k, l]) => `<button type="button" role="tab" class="ao-tab" data-tab="${k}">${l}</button>`).join("")}</div>

    <div class="ao-pane" data-pane="models">
      <div class="ao-grid2">
        <div>
          <div class="intel-divider">PROVIDERS · LIVE HEALTH <button class="ao-mini" type="button" data-act="refresh-providers" title="Re-discover models and re-check health">↻ REDISCOVER</button></div>
          <div id="ao-providers"><div class="tf-empty">loading…</div></div>
          <div class="intel-divider">SUGGESTED LOCAL MODELS (OLLAMA)</div>
          <div id="ao-pulls"></div>
        </div>
        <div>
          <div class="intel-divider">ROUTING POLICY</div>
          <div class="ao-row"><select id="ao-policy" class="ao-in"></select>
            <button class="cmd-btn" type="button" data-act="save-routing">SAVE</button><span class="ao-dim" id="ao-routing-state"></span></div>
          <div class="ao-note" id="ao-smart"></div>
          <details class="ao-det"><summary>PER-TASK PINS (operator override)</summary><div id="ao-pins" class="ao-pins"></div></details>
          <div class="intel-divider">TEST CONSOLE · ROUTED, REAL CALL</div>
          <textarea id="ao-prompt" class="ao-in ao-ta" rows="3" placeholder="e.g. What's the weather in Hyderabad and the latest ISRO news?"></textarea>
          <div class="ao-row ao-opts">
            <select id="ao-task" class="ao-in"><option value="">task: auto-detect</option></select>
            <select id="ao-tpolicy" class="ao-in"><option value="">policy: default</option></select>
            <label><input type="checkbox" id="ao-usetools" /> tools</label>
            <label><input type="checkbox" id="ao-usemem" checked /> memory</label>
            <label title="Save this exchange as an episode in long-term memory"><input type="checkbox" id="ao-remember" /> remember</label>
          </div>
          <div class="ao-row"><button class="cmd-btn" type="button" data-act="preview">PREVIEW ROUTE</button>
            <button class="cmd-btn" type="button" data-act="run">RUN</button></div>
          <div id="ao-result" class="ao-result"><div class="tf-empty">route decisions are explained here</div></div>
          <div class="intel-divider">RECENT DECISIONS</div>
          <div id="ao-history" class="ao-hist"></div>
        </div>
      </div>
    </div>

    <div class="ao-pane" data-pane="tools" hidden>
      <div class="ao-note">READ tools are free. ACTION tools need the COMMANDER role. APPROVAL tools (outward / hard to undo) also need you to approve each call here — models are told when a call is waiting.</div>
      <div id="ao-tools" class="ao-list"></div>
      <div class="ao-grid2">
        <div><div class="intel-divider">APPROVED FOLDERS (files_* tools)</div>
          <textarea id="ao-folders" class="ao-in ao-ta" rows="3" placeholder="one folder per line (host machine only)"></textarea>
          <div class="ao-row"><button class="cmd-btn" type="button" data-act="save-folders">SAVE FOLDERS</button></div>
          <div class="ao-note">The first folder (BUCS's own sandbox) is always approved. The BUCS install folder can never be added.</div></div>
        <div><div class="intel-divider">TOOL ACTIVITY</div><div id="ao-toollog" class="ao-hist"></div></div>
      </div>
    </div>

    <div class="ao-pane" data-pane="memory" hidden>
      <div class="ao-row"><input id="ao-mq" class="ao-in" placeholder="search memory (hybrid BM25 + embeddings)" />
        <select id="ao-mkind" class="ao-in"><option value="">all kinds</option><option>fact</option><option>episode</option><option>note</option><option>document</option></select>
        <button class="cmd-btn" type="button" data-act="msearch">SEARCH</button>
        <button class="cmd-btn" type="button" data-act="mimport" title="Copy operator memory facts, team memory and notes into the store (idempotent, originals untouched)">IMPORT LEGACY</button></div>
      <div class="ao-note" id="ao-mstats"></div>
      <div id="ao-mlist" class="ao-list"><div class="tf-empty">loading…</div></div>
      <details class="ao-det"><summary>ADD MEMORY</summary>
        <div class="ao-row"><select id="ao-akind" class="ao-in"><option>fact</option><option>note</option><option>document</option></select>
          <input id="ao-atitle" class="ao-in" placeholder="title (optional; documents)" /></div>
        <textarea id="ao-atext" class="ao-in ao-ta" rows="4" placeholder="text — documents are chunked automatically"></textarea>
        <div class="ao-row"><label><input type="checkbox" id="ao-apin" /> pin (always in context)</label>
          <button class="cmd-btn" type="button" data-act="madd">ADD</button></div>
      </details>
    </div>

    <div class="ao-pane" data-pane="studio" hidden>
      <div class="ao-grid2">
        <div>
          <div class="intel-divider">CUSTOM AGENTS <button class="ao-mini" type="button" data-act="new-agent">+ NEW</button></div>
          <div id="ao-agents" class="ao-list"></div>
          <div id="ao-agent-form" class="ao-form" hidden></div>
          <div class="intel-divider">WORKFLOWS <button class="ao-mini" type="button" data-act="new-wf">+ NEW</button></div>
          <div id="ao-wfs" class="ao-list"></div>
          <div id="ao-wf-form" class="ao-form" hidden></div>
        </div>
        <div>
          <div class="intel-divider">RUNS</div>
          <div id="ao-runs" class="ao-list"></div>
          <div id="ao-runlog" class="ao-result"></div>
        </div>
      </div>
    </div>

    <div class="ao-pane" data-pane="market" hidden>
      <div class="ao-warn" id="ao-pwarn"></div>
      <div class="ao-grid2">
        <div><div class="intel-divider">INSTALLED</div><div id="ao-installed" class="ao-list"></div></div>
        <div><div class="intel-divider">CATALOGUE</div><div id="ao-catalogue" class="ao-list"></div>
          <div class="intel-divider">INSTALL FROM FOLDER OR URL</div>
          <div class="ao-row"><select id="ao-psrc" class="ao-in"><option value="folder">local folder (host only)</option><option value="url">https .zip URL</option></select>
            <input id="ao-pval" class="ao-in" placeholder="C:\\path\\to\\plugin  or  https://…/plugin.zip" />
            <button class="cmd-btn" type="button" data-act="ppreview">INSPECT</button></div>
          <div id="ao-pstage" class="ao-result"></div></div>
      </div>
    </div>

    <div class="ao-pane" data-pane="integrations" hidden>
      <div class="ao-row"><span class="ao-note" id="ao-isum">checking…</span>
        <button class="ao-mini" type="button" data-act="irefresh">↻ RE-CHECK</button></div>
      <div id="ao-integrations" class="ao-matrix"><div class="tf-empty">detecting…</div></div>
    </div>
  </div>`;

  function showTab(t) {
    tab = t;
    try { localStorage.setItem("ao-tab", t); } catch (e) {}
    mount.querySelectorAll(".ao-tab").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
    mount.querySelectorAll(".ao-pane").forEach((p) => { p.hidden = p.dataset.pane !== t; });
    if (t === "memory") loadMemory();
    if (t === "integrations") loadIntegrations(false);
  }

  // ── renderers ────────────────────────────────────────────
  const caps = (cs) => (cs || []).map((c) => `<span class="ao-cap">${esc(c)}</span>`).join("");
  function renderProviders(d) {
    const disabled = new Set((d.routing && d.routing.disabled) || []);
    const ps = (d.providers && d.providers.providers) || [];
    const shown = ps.filter((p) => p.configured && (p.models.length || (p.health && p.health.ok)) || p.id === "ollama" || p.id === "claude-cli");
    const hidden = ps.filter((p) => !shown.includes(p));
    $a("ao-providers").innerHTML = shown.map((p) => {
      const h = p.health || {};
      const st = h.ok ? "ok" : h.ok === false ? "error" : "warn";
      return `<div class="ao-prov ${st}">
        <div class="ao-prov-h"><span class="hc-dot"></span><b>${esc(p.name)}</b><span class="ao-tagl">${p.local ? "LOCAL" : "CLOUD"}</span>
          ${p.version ? `<span class="ao-dim">v${esc(p.version)}</span>` : ""}<span class="ao-dim ao-r">${esc(h.detail || "")}${h.ms != null ? " · " + ms(h.ms) : ""}</span></div>
        ${p.setup && !h.ok ? `<div class="ao-note">${esc(p.setup)}</div>` : ""}
        ${p.models.map((m) => {
          const mh = m.health || {};
          const cool = mh.cooldown_until && mh.cooldown_until * 1000 > Date.now();
          return `<div class="ao-model${disabled.has(m.id) ? " off" : ""}${cool ? " cool" : ""}">
            <div class="ao-model-n"><span>${esc(m.model)}</span>
              <span class="ao-dim">Q${m.quality} · ${m.context >= 1000 ? Math.round(m.context / 1000) + "k" : m.context} ctx${m.meta && m.meta.params ? " · " + esc(m.meta.params) : ""}</span></div>
            <div class="ao-caps">${caps(m.caps)}</div>
            <div class="ao-model-s"><span title="EWMA latency from real calls">${mh.ewma_ms != null ? "⏱ " + ms(mh.ewma_ms) : "not measured"}</span>
              <span>${mh.ok || 0}✓ ${mh.fail || 0}✗</span>${cool ? `<span class="ao-bad" title="${esc(mh.last_error || "")}">COOLDOWN</span>` : ""}
              ${m.caps.includes("chat") ? `<button class="ao-mini" type="button" data-act="test-model" data-id="${esc(m.id)}">TEST</button>
              <label class="ao-dim" title="Exclude from routing"><input type="checkbox" data-act="toggle-model" data-id="${esc(m.id)}" ${disabled.has(m.id) ? "" : "checked"} /> route</label>` : ""}</div>
          </div>`;
        }).join("") || `<div class="tf-empty">${p.configured ? "no models found" : "not configured"}</div>`}
      </div>`;
    }).join("") + (hidden.length ? `<details class="ao-det"><summary>NOT CONFIGURED · ${hidden.length}</summary>${hidden.map((p) => `
        <div class="ao-prov-mini"><b>${esc(p.name)}</b> <span class="ao-dim">${esc(p.setup || (p.health && p.health.detail) || "unreachable")}</span></div>`).join("")}</details>` : "");
    const pulls = d.pulls || {};
    const have = new Set(ps.flatMap((p) => p.models.map((m) => m.model.split(":")[0])));
    $a("ao-pulls").innerHTML = (d.pull_suggestions || []).map((s) => {
      const pl = pulls[s.model];
      const installed = have.has(s.model.split(":")[0]) && (s.model.includes(":") ? ps.some((p) => p.models.some((m) => m.model === s.model)) : true);
      return `<div class="ao-pull"><b>${esc(s.model)}</b><span class="ao-dim">${esc(s.why)}</span>
        ${installed ? `<span class="ao-good">INSTALLED</span>` : pl && !pl.done ? `<span class="ao-dim">${esc(pl.status)} ${pl.pct}%</span>`
          : `<button class="ao-mini" type="button" data-act="pull" data-model="${esc(s.model)}" title="Downloads the model from the Ollama registry (host only, commander)">PULL</button>${pl && pl.error ? `<span class="ao-bad">${esc(pl.error)}</span>` : ""}`}</div>`;
    }).join("");
  }

  function renderRouting(d) {
    const pol = $a("ao-policy");
    if (!pol.options.length) {
      pol.innerHTML = Object.entries(d.policies).map(([k, l]) => `<option value="${esc(k)}">${esc(l)}</option>`).join("");
      $a("ao-tpolicy").innerHTML += Object.entries(d.policies).map(([k, l]) => `<option value="${esc(k)}">${esc(l)}</option>`).join("");
      $a("ao-task").innerHTML += Object.entries(d.tasks).map(([k, l]) => `<option value="${esc(k)}">${esc(l)}</option>`).join("");
    }
    const models = ((d.providers && d.providers.providers) || []).flatMap((p) => p.models.filter((m) => m.caps.includes("chat")));
    const pinsEl = $a("ao-pins");
    const sig = models.map((m) => m.id).join("|");
    if (!pinsEl.dataset.built || pinsEl.dataset.sig !== sig) {
      const keep = {};                                   // unsaved pin choices survive a rebuild
      if (routingDirty) pinsEl.querySelectorAll("[data-pin]").forEach((s) => { keep[s.dataset.pin] = s.value; });
      const cur = routingDirty ? keep : (d.routing.pins || {});
      pinsEl.innerHTML = Object.entries(d.tasks).map(([k, l]) => `<label>${esc(l)}<select class="ao-in" data-pin="${esc(k)}"><option value="">auto</option>
        ${models.map((m) => `<option value="${esc(m.id)}" ${cur[k] === m.id ? "selected" : ""}>${esc(m.id)}</option>`).join("")}
        ${cur[k] && !models.some((m) => m.id === cur[k]) ? `<option value="${esc(cur[k])}" selected>${esc(cur[k])} (not available now)</option>` : ""}</select></label>`).join("");
      pinsEl.dataset.built = "1"; pinsEl.dataset.sig = sig;
    }
    // mirror the SAVED routing (changed here, in another tab or by voice) unless mid-edit
    if (!routingDirty) {
      if (document.activeElement !== pol) pol.value = d.routing.policy;
      pinsEl.querySelectorAll("[data-pin]").forEach((s) => {
        if (document.activeElement === s) return;
        const want = (d.routing.pins || {})[s.dataset.pin] || "";
        if (want && ![...s.options].some((o) => o.value === want))
          s.insertAdjacentHTML("beforeend", `<option value="${esc(want)}">${esc(want)} (not available now)</option>`);
        s.value = want;
      });
    }
    $a("ao-routing-state").textContent = routingDirty ? "unsaved — press SAVE" : "";
    $a("ao-smart").innerHTML = d.smart_active
      ? `<span class="ao-good">●</span> All BUCS agent replies route through this router (AI BRAIN ROUTER → Smart router).`
      : `Agent replies use the AI BRAIN ROUTER card's choice. <button class="ao-mini" type="button" data-act="smart-on">ROUTE ALL REPLIES HERE</button>`
        + (d.cloud_allowed ? "" : ` <span class="ao-bad">cloud models locked (licence)</span>`);
    $a("ao-history").innerHTML = (d.history || []).length ? d.history.map((h) => `<div class="ao-hrow">
        <span class="ao-dim">${new Date(h.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
        <span>${esc(d.tasks[h.task] || h.task)}</span><span class="ao-dim">${esc(h.policy)}</span>
        <b class="${h.used ? "" : "ao-bad"}">${esc(h.used || "failed")}</b>${h.used && h.used !== h.chosen ? `<span class="ao-warnc">fallback</span>` : ""}</div>`).join("")
      : `<div class="tf-empty">no routed calls yet</div>`;
  }

  function decisionHTML(dec) {
    if (!dec) return "";
    return `<div class="ao-dec"><div class="ao-explain">${esc(dec.explain)}</div>
      <table class="ao-tbl"><tr><th>#</th><th>MODEL</th><th>SCORE</th><th>WHY</th></tr>
      ${(dec.ranked || []).slice(0, 6).map((r, i) => `<tr class="${r.id === (dec.used || dec.chosen) ? "pick" : ""}"><td>${i + 1}</td><td>${esc(r.id)}</td>
        <td>${r.score}</td><td>${esc(r.reasons.join(" · "))}</td></tr>`).join("")}</table>
      ${(dec.excluded || []).length ? `<details class="ao-det"><summary>EXCLUDED · ${dec.excluded.length}</summary>${dec.excluded.map((e) => `<div class="ao-dim">${esc(e.id)} — ${esc(e.why)}</div>`).join("")}</details>` : ""}
      ${(dec.attempts || []).length ? `<div class="ao-dim">attempts: ${dec.attempts.map((a) => `${esc(a.model)} ${a.ok ? "✓" : "✗"} ${ms(a.ms)}${a.native_tools ? " (native tools)" : ""}${a.error ? " — " + esc(a.error) : ""}`).join(" → ")}</div>` : ""}</div>`;
  }

  function renderTools(d) {
    $a("ao-tools").innerHTML = (d.tools || []).map((t) => `<div class="ao-tool ${esc(t.tier)}${t.available ? "" : " off"}">
      <div class="ao-tool-h"><span class="ao-tier ${esc(t.tier)}">${TIER[t.tier]}</span><b>${esc(t.name)}</b>
        <span class="ao-dim">${esc(t.source)}</span>${t.available ? "" : `<span class="ao-bad">${esc(t.unavailable_reason)}</span>`}
        <button class="ao-mini ao-r" type="button" data-act="tool-open" data-name="${esc(t.name)}">RUN…</button></div>
      <div class="ao-dim">${esc(t.description)}</div>
      <div class="ao-toolrun" data-tool-run="${esc(t.name)}" hidden></div></div>`).join("") || `<div class="tf-empty">no tools</div>`;
    const fEl = $a("ao-folders");
    if (document.activeElement !== fEl && !foldersDirty) fEl.value = (d.folders || []).slice(1).join("\n");
    fEl.placeholder = `always: ${(d.folders || [""])[0]}\n+ one folder per line (host machine only)`;
    $a("ao-toollog").innerHTML = (d.tool_log || []).length ? d.tool_log.map((l) => `<div class="ao-hrow"><span class="ao-dim">${ago(l.ts)}</span>
      <b>${esc(l.tool)}</b><span class="ao-tier ${esc(l.tier)}">${esc(l.tier)}</span><span class="${l.ok ? "ao-good" : "ao-bad"}">${l.ok ? "ok" : "failed"}</span><span class="ao-dim">${esc(l.by)} · ${ms(l.ms)}</span></div>`).join("")
      : `<div class="tf-empty">no tool calls yet</div>`;
  }

  function renderApprovals(d) {
    const aps = d.approvals || [];
    const el = $a("ao-approvals");
    el.hidden = !aps.length;
    el.innerHTML = aps.map((a) => `<div class="ao-ap"><b>APPROVAL NEEDED</b>
      <span>${a.kind === "tool" ? `tool <code>${esc(a.tool)}</code> ${esc(JSON.stringify(a.args)).slice(0, 300)}` : `workflow step — ${esc(a.message)}`}</span>
      <span class="ao-dim">by ${esc(a.requester)} · ${ago(a.created)}</span>
      <button class="cmd-btn" type="button" data-act="approve" data-id="${esc(a.id)}">APPROVE</button>
      <button class="cmd-btn" type="button" data-act="reject" data-id="${esc(a.id)}">REJECT</button></div>`).join("");
  }

  function renderStudio(d) {
    const s = d.studio || {};
    $a("ao-agents").innerHTML = (s.agents || []).length ? s.agents.map((a) => `<div class="ao-item">
      <div class="ao-item-h"><b>${esc(a.name)}</b><span class="ao-dim">${esc(a.role)}</span>
        <span class="ao-dim ao-r">${esc(d.policies[a.policy] || a.policy)}${a.schedule ? " · every " + a.schedule.every_min + " min" : ""}</span></div>
      <div class="ao-dim">tools: ${esc((a.tools || []).join(", ") || "none")} · memory: ${esc(a.memory_scope)}</div>
      <div class="ao-row"><input class="ao-in" data-agent-prompt="${esc(a.id)}" placeholder="prompt (empty = heartbeat)" />
        <button class="ao-mini" type="button" data-act="agent-run" data-id="${esc(a.id)}">RUN</button>
        <button class="ao-mini" type="button" data-act="agent-edit" data-id="${esc(a.id)}">EDIT</button>
        <button class="ao-mini" type="button" data-act="agent-del" data-id="${esc(a.id)}">DELETE</button></div></div>`).join("")
      : `<div class="tf-empty">no custom agents yet — + NEW</div>`;
    $a("ao-wfs").innerHTML = (s.workflows || []).length ? s.workflows.map((w) => `<div class="ao-item">
      <div class="ao-item-h"><b>${esc(w.name)}</b><span class="ao-dim">${w.steps.length} steps: ${esc(w.steps.map((x) => x.type).join(" → "))}</span>
        <span class="ao-dim ao-r">${w.schedule ? "every " + w.schedule.every_min + " min" : "manual"}</span></div>
      <div class="ao-row"><input class="ao-in" data-wf-input="${esc(w.id)}" placeholder="input ({{input}})" />
        <button class="ao-mini" type="button" data-act="wf-run" data-id="${esc(w.id)}">RUN</button>
        <button class="ao-mini" type="button" data-act="wf-edit" data-id="${esc(w.id)}">EDIT</button>
        <button class="ao-mini" type="button" data-act="wf-del" data-id="${esc(w.id)}">DELETE</button></div></div>`).join("")
      : `<div class="tf-empty">no workflows yet — + NEW</div>`;
    $a("ao-runs").innerHTML = (s.runs || []).length ? s.runs.map((r) => `<div class="ao-run ${esc(r.status)}" data-act="run-open" data-id="${esc(r.id)}" role="button" tabindex="0">
      <span class="ao-st ${esc(r.status)}">${esc(r.status.replace("_", " ").toUpperCase())}</span><b>${esc(r.name)}</b>
      <span class="ao-dim">${esc(r.kind)} · ${esc(r.trigger)} · ${ago(r.started)}</span>
      <div class="ao-dim ao-clip">${esc(r.output || "")}</div></div>`).join("")
      : `<div class="tf-empty">no runs yet</div>`;
  }

  function renderMarket(d) {
    const p = d.plugins || {};
    $a("ao-pwarn").textContent = "⚠ " + (p.warning || "");
    $a("ao-installed").innerHTML = (p.installed || []).length ? p.installed.map((x) => `<div class="ao-item${x.tampered ? " bad" : ""}">
      <div class="ao-item-h"><b>${esc(x.name)}</b><span class="ao-dim">v${esc(x.version)} · ${esc(x.author)}</span>
        ${x.first_party ? `<span class="ao-tagl">FIRST-PARTY</span>` : `<span class="ao-tagl warn">THIRD-PARTY</span>`}
        <span class="ao-r ${x.loaded ? "ao-good" : "ao-dim"}">${x.loaded ? "LOADED" : x.enabled ? "ENABLED (not loaded)" : "DISABLED"}</span></div>
      <div class="ao-dim">${esc(x.description || "")}</div>
      <div class="ao-dim">tools: ${esc((x.tools || []).join(", "))} · permissions: ${esc((x.permissions || []).join(", ") || "none")} · sha256 ${esc(x.sha256)}…</div>
      ${x.tampered ? `<div class="ao-bad">INTEGRITY: ${esc(x.tampered)}</div>` : ""}
      <div class="ao-row"><button class="ao-mini" type="button" data-act="p-enable" data-id="${esc(x.id)}" data-on="${x.enabled ? 0 : 1}">${x.enabled ? "DISABLE" : "ENABLE"}</button>
        <button class="ao-mini" type="button" data-act="p-uninstall" data-id="${esc(x.id)}">UNINSTALL</button></div></div>`).join("")
      : `<div class="tf-empty">nothing installed</div>`;
    $a("ao-catalogue").innerHTML = (p.catalogue || []).map((c) => `<div class="ao-item"><div class="ao-item-h"><b>${esc(c.name)}</b>
      <span class="ao-dim">v${esc(c.version)}</span><span class="ao-r">${c.installed ? `<span class="ao-good">INSTALLED</span>` : `<button class="ao-mini" type="button" data-act="p-cat" data-id="${esc(c.id)}">INSTALL…</button>`}</span></div>
      <div class="ao-dim">${esc(c.description)}</div><div class="ao-dim">permissions: ${esc((c.permissions || []).join(", ") || "none")}</div></div>`).join("")
      || `<div class="tf-empty">catalogue empty</div>`;
  }

  function render(d) {
    data = d;
    const n = (d.providers && d.providers.count) || 0;
    const healthy = ((d.providers && d.providers.providers) || []).filter((p) => p.health && p.health.ok && p.models.length).length;
    $a("ao-sub").textContent = `${n} MODEL${n === 1 ? "" : "S"} · ${healthy} PROVIDER${healthy === 1 ? "" : "S"} UP · POLICY ${String(d.policies[d.routing.policy] || "").toUpperCase()}`
      + ` · ${(d.tools || []).length} TOOLS · MEMORY ${d.memory ? d.memory.total : 0}` + (d.booted ? "" : " · BOOTING…") + (d.error ? " · ⚠ " + d.error : "");
    renderApprovals(d);
    renderProviders(d);
    renderRouting(d);
    renderTools(d);
    renderStudio(d);
    renderMarket(d);
  }

  async function refresh() {
    clearTimeout(timer);
    try {
      render(await get("/api/agentos"));
      $a("ao-card").classList.remove("is-error");
    } catch (e) {
      $a("ao-card").classList.add("is-error");
      $a("ao-sub").textContent = "AI CORE unavailable (" + (e.message || "error") + ") · retrying";
    }
    const busy = data && ((data.studio && (data.studio.runs || []).some((r) => r.status === "running")) || Object.values(data.pulls || {}).some((p) => !p.done) || !data.booted);
    timer = setTimeout(refresh, document.hidden ? 60000 : busy ? 3000 : 15000);
  }

  // ── memory ───────────────────────────────────────────────
  async function loadMemory() {
    const q = $a("ao-mq").value.trim(), k = $a("ao-mkind").value;
    try {
      const r = await get(`/api/agentos/memory?limit=60&q=${encodeURIComponent(q)}&kind=${encodeURIComponent(k)}`);
      const s = r.stats || {};
      $a("ao-mstats").textContent = `${s.total || 0} items (${Object.entries(s.counts || {}).map(([a, b]) => `${b} ${a}`).join(", ")}) · ${s.pinned || 0} pinned · retrieval: ${s.retrieval || "?"}`
        + (s.embed_model ? ` · ${s.vectors} vectors (${s.embed_model})` : "") + (s.commander_linked ? " · commander profile linked (private, not copied)" : "");
      $a("ao-mlist").innerHTML = (r.items || []).length ? r.items.map((m) => `<div class="ao-mem${m.pinned ? " pinned" : ""}" data-mid="${m.id}">
        <div class="ao-item-h"><span class="ao-tagl">${esc(m.kind.toUpperCase())}</span><b>M${m.id}${m.title ? " · " + esc(m.title) : ""}</b>
          <span class="ao-dim">${esc(m.source || "")} · ${ago(m.updated)}${m.score != null ? " · score " + m.score : ""}</span>
          <span class="ao-r"><button class="ao-mini" type="button" data-act="m-pin" data-id="${m.id}" data-on="${m.pinned ? 0 : 1}">${m.pinned ? "UNPIN" : "PIN"}</button>
          <button class="ao-mini" type="button" data-act="m-edit" data-id="${m.id}">EDIT</button>
          <button class="ao-mini" type="button" data-act="m-forget" data-id="${m.id}">FORGET</button></span></div>
        <div class="ao-mtext">${esc(m.text)}</div></div>`).join("")
        : `<div class="tf-empty">${q ? "no matches" : "memory is empty — add facts/notes/documents or IMPORT LEGACY"}</div>`;
    } catch (e) {
      $a("ao-mlist").innerHTML = `<div class="tf-empty">memory unavailable (${esc(e.message)})</div>`;
    }
  }

  // ── integrations ─────────────────────────────────────────
  async function loadIntegrations(force) {
    $a("ao-isum").textContent = "checking…";
    try {
      const r = await get("/api/agentos/integrations" + (force ? "?refresh=1" : ""));
      const s = r.summary || {};
      $a("ao-isum").textContent = `${s.ok || 0} ready · ${s.partial || 0} partial · ${s.missing || 0} not set up · checked ${ago(r.at)} · key values are never shown`;
      const groups = {};
      (r.items || []).forEach((i) => { (groups[i.group] = groups[i.group] || []).push(i); });
      $a("ao-integrations").innerHTML = Object.entries(groups).map(([g, items]) => `<div class="ao-igrp"><div class="intel-divider">${esc(g.toUpperCase())}</div>
        ${items.map((i) => `<div class="ao-int ${esc(i.status)}"><span class="ao-st ${esc(i.status)}">${esc(i.status.toUpperCase())}</span><b>${esc(i.name)}</b>
          <span class="ao-dim">${esc(i.detail)}</span>
          ${(i.setup || []).length ? `<details class="ao-det"><summary>SET UP</summary><ol>${i.setup.map((x) => `<li>${esc(x)}</li>`).join("")}</ol></details>` : ""}</div>`).join("")}</div>`).join("");
    } catch (e) {
      $a("ao-integrations").innerHTML = `<div class="tf-empty">detection failed (${esc(e.message)})</div>`;
    }
  }

  // ── forms ────────────────────────────────────────────────
  function agentForm(a) {
    const toolNames = (data.tools || []).map((t) => t);
    a = a || { name: "", role: "", system: "", policy: data.routing.policy, tools: [], memory_scope: "global", schedule: null, heartbeat: "" };
    editingAgent = a.id || null;
    const el = $a("ao-agent-form");
    el.hidden = false;
    el.innerHTML = `<div class="ao-row"><input class="ao-in" id="ao-fa-name" placeholder="name" value="${esc(a.name)}" />
        <input class="ao-in" id="ao-fa-role" placeholder="role (e.g. research analyst)" value="${esc(a.role)}" /></div>
      <textarea class="ao-in ao-ta" id="ao-fa-sys" rows="3" placeholder="system prompt / instructions">${esc(a.system)}</textarea>
      <div class="ao-row"><label>policy <select class="ao-in" id="ao-fa-pol">${Object.entries(data.policies).map(([k, l]) => `<option value="${esc(k)}" ${a.policy === k ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
        <label>memory <select class="ao-in" id="ao-fa-mem">${["global", "own", "none"].map((m) => `<option ${a.memory_scope === m ? "selected" : ""}>${m}</option>`).join("")}</select></label>
        <label>every <input class="ao-in ao-num" id="ao-fa-every" type="number" min="5" placeholder="—" value="${a.schedule ? a.schedule.every_min : ""}" /> min</label></div>
      <input class="ao-in" id="ao-fa-hb" placeholder="heartbeat prompt (used by schedule / empty RUN)" value="${esc(a.heartbeat || "")}" />
      <div class="ao-tchk">${toolNames.map((t) => `<label class="ao-tier-l ${esc(t.tier)}"><input type="checkbox" value="${esc(t.name)}" ${(a.tools || []).includes(t.name) ? "checked" : ""} /> ${esc(t.name)}</label>`).join("")}</div>
      <div class="ao-note">Allowed ACTION tools run without asking (you created the agent as commander); APPROVAL tools still wait for you per call.</div>
      <div class="ao-row"><button class="cmd-btn" type="button" data-act="agent-save">SAVE AGENT</button>
        <button class="cmd-btn" type="button" data-act="agent-cancel">CANCEL</button><span class="ao-bad" id="ao-fa-err"></span></div>`;
  }

  const WF_EXAMPLE = {
    name: "Morning brief", description: "weather + headlines, ask before notifying",
    schedule: null,
    steps: [
      { id: "wx", type: "tool", tool: "weather", args: { city: "Hyderabad" }, save_as: "weather" },
      { id: "news", type: "tool", tool: "news_search", args: { query: "{{input}}" }, save_as: "news" },
      { id: "brief", type: "agent", prompt: "Write a 3-line morning brief from this weather: {{weather}} and these headlines: {{news}}", save_as: "brief" },
      { id: "check", type: "condition", if: { var: "brief", op: "empty" }, then: "end", else: "ok" },
      { id: "ok", type: "approval", message: "Post the brief to the dashboard?" },
      { id: "post", type: "notify", message: "{{brief}}" },
    ],
  };
  function wfForm(w) {
    editingWf = w ? w.id : null;
    const el = $a("ao-wf-form");
    el.hidden = false;
    const body = w ? { name: w.name, description: w.description, schedule: w.schedule, enabled: w.enabled, steps: w.steps } : WF_EXAMPLE;
    el.innerHTML = `<div class="ao-note">Steps: <b>agent</b> {agent | prompt, save_as} · <b>tool</b> {tool, args, save_as} · <b>condition</b> {if:{var, op, value}, then, else}
      (ops: contains, not_contains, equals, gt, lt, exists, empty; targets: step id | "end") · <b>approval</b> {message} · <b>notify</b> {message}.
      Use {{input}}, {{last}} and {{save_as}} variables. schedule: {"every_min": 60} or null.</div>
      <textarea class="ao-in ao-ta ao-code" id="ao-fw-json" rows="14" spellcheck="false">${esc(JSON.stringify(body, null, 2))}</textarea>
      <div class="ao-row"><button class="cmd-btn" type="button" data-act="wf-save">SAVE WORKFLOW</button>
        <button class="cmd-btn" type="button" data-act="wf-cancel">CANCEL</button><span class="ao-bad" id="ao-fw-err"></span></div>`;
  }

  function stageHTML(r) {
    if (!r.ok && !r.manifest) return `<div class="ao-bad">${esc(r.error || "refused")}</div>`;
    const m = r.manifest || {};
    return `<div class="ao-stage"><b>${esc(m.name)}</b> <span class="ao-dim">v${esc(m.version)} · ${esc(m.author)} · id ${esc(m.id)}</span>
      ${r.upgrade_from ? `<div class="ao-warnc">replaces installed v${esc(r.upgrade_from)}</div>` : ""}
      <div class="ao-dim">${esc(m.description || "")}</div>
      <div><b>REQUESTED PERMISSIONS</b> ${(r.permissions || []).length ? r.permissions.map((p) => `<div>• <code>${esc(p.id)}</code> — ${esc(p.means)}</div>`).join("") : "<div>none</div>"}</div>
      <div><b>TOOLS</b> ${(r.tools || []).map((t) => `<div>• <code>${esc(t.name)}</code> <span class="ao-tier ${esc(t.tier)}">${esc(t.tier)}</span> ${esc(t.description)}</div>`).join("")}</div>
      <div class="ao-dim">integrity: ${esc(r.integrity)} · sha256 ${esc((m.sha256 || "").slice(0, 16))}…</div>
      ${(r.errors || []).length ? `<div class="ao-bad">${r.errors.map(esc).join("<br>")}</div>` : ""}
      ${r.warning ? `<div class="ao-warn">⚠ ${esc(r.warning)}</div>` : ""}
      ${r.ok ? `<label><input type="checkbox" id="ao-pconfirm" /> I reviewed the permissions and trust this code</label>
        <div class="ao-row"><button class="cmd-btn" type="button" data-act="pinstall">INSTALL</button></div>` : ""}</div>`;
  }

  // ── actions ──────────────────────────────────────────────
  async function act(el) {
    const a = el.dataset.act, id = el.dataset.id;
    const done = (r, okMsg) => { if (r && r.ok === false) log(esc(r.error || "refused"), "warn"); else if (okMsg) log(okMsg); refresh(); return r; };
    switch (a) {
      case "refresh-providers": el.disabled = true; done(await post("/api/agentos/providers/refresh"), "providers re-discovered"); el.disabled = false; break;
      case "test-model": {
        el.disabled = true; el.textContent = "…";
        const r = await post("/api/agentos/providers/test", { model: id });
        el.textContent = r.ok ? `OK ${ms(r.ms)}` : "FAIL"; el.title = r.error || r.reply || "";
        if (!r.ok) log(`${esc(id)} test failed: ${esc(r.error || "")}`, "warn");
        setTimeout(refresh, 1500); break;
      }
      case "toggle-model": {
        const dis = new Set(data.routing.disabled || []);
        if (el.checked) dis.delete(id); else dis.add(id);
        done(await post("/api/agentos/routing", { disabled: [...dis] })); break;
      }
      case "save-routing": {
        const pins = {};
        mount.querySelectorAll("[data-pin]").forEach((s) => { if (s.value) pins[s.dataset.pin] = s.value; });
        el.disabled = true; el.textContent = "SAVING…";
        const r = await post("/api/agentos/routing", { policy: $a("ao-policy").value, pins });
        el.disabled = false; el.textContent = "SAVE";
        if (r && r.ok) routingDirty = false;              // a refused save keeps the edits on screen
        done(r, "routing saved — the smart router uses it from the next reply"); break;
      }
      case "smart-on": el.disabled = true; done(await post("/api/llm/active", { id: "smart" }), "all agent replies now use the smart router"); break;
      case "pull": if (confirm(`Download ${el.dataset.model} from the Ollama registry? This can be several GB.`)) done(await post("/api/agentos/ollama/pull", { model: el.dataset.model }), `pulling ${esc(el.dataset.model)}…`); break;
      case "preview": case "run": {
        const prompt = $a("ao-prompt").value.trim();
        if (!prompt) { $a("ao-prompt").focus(); break; }
        const body = { prompt, task: $a("ao-task").value, policy: $a("ao-tpolicy").value, tools: $a("ao-usetools").checked,
          memory: $a("ao-usemem").checked, remember: $a("ao-remember").checked };
        const out = $a("ao-result");
        if (a === "preview") { out.innerHTML = decisionHTML(await post("/api/agentos/route", body)); break; }
        el.disabled = true; out.innerHTML = `<div class="tf-empty">routing + calling… (local CPU models can take a minute)</div>`;
        const t0 = performance.now();
        const r = await post("/api/agentos/chat", body);
        el.disabled = false;
        out.innerHTML = `${r.ok === false && !r.decision ? `<div class="ao-bad">${esc(r.error || "failed")}</div>` : ""}
          <div class="ao-answer">${linkify(esc(r.answer || r.error || "(no answer)"))}</div>
          <div class="ao-dim">${ms(Math.round(performance.now() - t0))} total${(r.citations || []).length ? " · memory used: " + r.citations.map((c) => esc(c.id)).join(", ") : ""}</div>
          ${(r.steps || []).length ? `<div class="ao-steps">${r.steps.map((s) => `<div class="${s.ok ? "" : "ao-bad"}">⚙ <b>${esc(s.tool)}</b> ${esc(JSON.stringify(s.arguments))} → ${s.ok ? esc((s.result_preview || "").slice(0, 220)) : esc(s.error)} <span class="ao-dim">${ms(s.ms)}</span></div>`).join("")}</div>` : ""}
          <div class="ao-cites" id="ao-cites"></div>
          ${decisionHTML(r.decision)}`;
        showCites(r.steps || []);
        refresh(); break;
      }
      case "approve": case "reject": done(await post(`/api/agentos/approvals/${encodeURIComponent(id)}`, { approve: a === "approve" }), a === "approve" ? "approved" : "rejected"); break;
      case "tool-open": {
        const box = mount.querySelector(`[data-tool-run="${CSS.escape(el.dataset.name)}"]`);
        const t = data.tools.find((x) => x.name === el.dataset.name);
        const skel = {}; Object.keys((t.parameters && t.parameters.properties) || {}).forEach((k) => { skel[k] = ""; });
        box.hidden = !box.hidden;
        box.innerHTML = `<textarea class="ao-in ao-ta ao-code" rows="3" spellcheck="false">${esc(JSON.stringify(skel))}</textarea>
          <div class="ao-row"><button class="ao-mini" type="button" data-act="tool-run" data-name="${esc(t.name)}">CALL</button></div><pre class="ao-pre"></pre>`;
        break;
      }
      case "tool-run": {
        const box = el.closest(".ao-toolrun");
        let args = {};
        try { args = JSON.parse(box.querySelector("textarea").value || "{}"); Object.keys(args).forEach((k) => { if (args[k] === "") delete args[k]; }); }
        catch (e) { box.querySelector("pre").textContent = "arguments must be JSON"; break; }
        box.querySelector("pre").textContent = "calling…";
        const r = await post(`/api/agentos/tools/${encodeURIComponent(el.dataset.name)}/run`, { args });
        box.querySelector("pre").textContent = JSON.stringify(r.ok ? r.result : r, null, 2).slice(0, 6000);
        refresh(); break;
      }
      case "save-folders": {
        el.disabled = true;
        const r = await post("/api/agentos/files", { folders: $a("ao-folders").value.split(/\n/).map((s) => s.trim()).filter(Boolean) });
        el.disabled = false;
        if (r && r.ok) foldersDirty = false;
        done(r, "approved folders saved"); break;
      }
      case "msearch": loadMemory(); break;
      case "mimport": { const r = await post("/api/agentos/memory/import"); done(r, r.ok ? "imported " + esc(JSON.stringify(r.imported)) : null); loadMemory(); break; }
      case "madd": {
        const r = await post("/api/agentos/memory", { kind: $a("ao-akind").value, title: $a("ao-atitle").value, text: $a("ao-atext").value, pinned: $a("ao-apin").checked });
        if (r.ok) { $a("ao-atext").value = ""; $a("ao-atitle").value = ""; }
        done(r, r.ok ? (r.chunks ? `document stored in ${r.chunks} chunk(s)` : r.duplicate ? "already remembered" : "remembered") : null); loadMemory(); break;
      }
      case "m-pin": done(await post(`/api/agentos/memory/${id}`, { pinned: el.dataset.on === "1" })); loadMemory(); break;
      case "m-forget": if (confirm(`Forget M${id}? (documents are forgotten as a whole)`)) { done(await post(`/api/agentos/memory/${id}/forget`), "forgotten"); loadMemory(); } break;
      case "m-edit": {
        const card = el.closest(".ao-mem"), txt = card.querySelector(".ao-mtext");
        if (card.querySelector("textarea")) break;
        txt.innerHTML = `<textarea class="ao-in ao-ta" rows="3">${esc(txt.textContent)}</textarea><button class="ao-mini" type="button" data-act="m-save" data-id="${id}">SAVE</button>`;
        break;
      }
      case "m-save": done(await post(`/api/agentos/memory/${id}`, { text: el.previousElementSibling.value }), "memory updated"); loadMemory(); break;
      case "new-agent": agentForm(null); break;
      case "agent-edit": agentForm(data.studio.agents.find((x) => x.id === id)); break;
      case "agent-cancel": $a("ao-agent-form").hidden = true; break;
      case "agent-save": {
        const every = parseInt($a("ao-fa-every").value, 10);
        const body = { id: editingAgent || undefined, name: $a("ao-fa-name").value, role: $a("ao-fa-role").value, system: $a("ao-fa-sys").value,
          policy: $a("ao-fa-pol").value, memory_scope: $a("ao-fa-mem").value, heartbeat: $a("ao-fa-hb").value,
          schedule: every ? { every_min: every } : null, tools: [...$a("ao-agent-form").querySelectorAll(".ao-tchk input:checked")].map((c) => c.value) };
        const r = await post("/api/agentos/agents", body);
        if (r.ok) $a("ao-agent-form").hidden = true; else $a("ao-fa-err").textContent = r.error || "refused";
        done(r, r.ok ? "agent saved" : null); break;
      }
      case "agent-del": if (confirm("Delete this agent?")) done(await post(`/api/agentos/agents/${encodeURIComponent(id)}/delete`), "agent deleted"); break;
      case "agent-run": done(await post(`/api/agentos/agents/${encodeURIComponent(id)}/run`, { prompt: (mount.querySelector(`[data-agent-prompt="${CSS.escape(id)}"]`) || {}).value || "" }), "agent run started"); break;
      case "new-wf": wfForm(null); break;
      case "wf-edit": wfForm(data.studio.workflows.find((x) => x.id === id)); break;
      case "wf-cancel": $a("ao-wf-form").hidden = true; break;
      case "wf-save": {
        let body;
        try { body = JSON.parse($a("ao-fw-json").value); } catch (e) { $a("ao-fw-err").textContent = "invalid JSON: " + e.message; break; }
        if (editingWf) body.id = editingWf;
        const r = await post("/api/agentos/workflows", body);
        if (r.ok) $a("ao-wf-form").hidden = true; else $a("ao-fw-err").textContent = r.error || "refused";
        done(r, r.ok ? "workflow saved" : null); break;
      }
      case "wf-del": if (confirm("Delete this workflow?")) done(await post(`/api/agentos/workflows/${encodeURIComponent(id)}/delete`), "workflow deleted"); break;
      case "wf-run": done(await post(`/api/agentos/workflows/${encodeURIComponent(id)}/run`, { input: (mount.querySelector(`[data-wf-input="${CSS.escape(id)}"]`) || {}).value || "" }), "workflow started"); break;
      case "run-open": {
        try {
          const r = await get(`/api/agentos/runs/${encodeURIComponent(id)}`);
          $a("ao-runlog").innerHTML = `<div class="ao-item-h"><b>${esc(r.name)}</b><span class="ao-st ${esc(r.status)}">${esc(r.status.toUpperCase())}</span></div>
            ${(r.log || []).map((l) => `<div class="ao-hrow"><span class="ao-dim">${new Date(l.ts * 1000).toLocaleTimeString()}</span>${l.step ? `<code>${esc(l.step)}</code>` : ""}<span>${esc(l.msg)}</span>${l.model ? `<span class="ao-dim">${esc(l.model)}</span>` : ""}</div>`).join("")}
            ${r.output ? `<div class="ao-answer">${esc(r.output)}</div>` : ""}`;
        } catch (e) { $a("ao-runlog").textContent = "run unavailable"; }
        break;
      }
      case "p-enable": done(await post(`/api/agentos/plugins/${encodeURIComponent(id)}/enable`, { on: el.dataset.on === "1" }), "plugin updated"); break;
      case "p-uninstall": if (confirm("Uninstall this plugin? Third-party plugin files are deleted.")) done(await post(`/api/agentos/plugins/${encodeURIComponent(id)}/uninstall`), "plugin uninstalled"); break;
      case "p-cat": staged = await post("/api/agentos/plugins/preview", { source: "catalogue", value: id }); $a("ao-pstage").innerHTML = stageHTML(staged); showTab("market"); break;
      case "ppreview": staged = await post("/api/agentos/plugins/preview", { source: $a("ao-psrc").value, value: $a("ao-pval").value.trim() }); $a("ao-pstage").innerHTML = stageHTML(staged); break;
      case "pinstall": {
        if (!staged || !staged.token) break;
        if (!$a("ao-pconfirm").checked) { log("tick the confirmation to install", "warn"); break; }
        const r = await post("/api/agentos/plugins/install", { token: staged.token, confirm: true });
        $a("ao-pstage").innerHTML = r.ok ? `<div class="ao-good">installed ${esc(r.id)} — disabled until you ENABLE it</div>` : `<div class="ao-bad">${esc(r.error)}</div>`;
        staged = null; done(r); break;
      }
      case "irefresh": loadIntegrations(true); break;
      default: break;
    }
  }

  mount.addEventListener("click", (e) => {
    const t = e.target.closest("[data-tab]");
    if (t) { showTab(t.dataset.tab); return; }
    const el = e.target.closest("[data-act]");
    if (el && el.dataset.act !== "toggle-model") act(el);
  });
  mount.addEventListener("change", (e) => {
    const el = e.target.closest("[data-act='toggle-model']");
    if (el) { act(el); return; }
    if (e.target.id === "ao-policy" || e.target.matches("[data-pin]")) {
      routingDirty = true; $a("ao-routing-state").textContent = "unsaved — press SAVE";
    }
  });
  mount.addEventListener("input", (e) => { if (e.target.id === "ao-folders") foldersDirty = true; });
  // the server announces changes from other tabs, voice or the AI BRAIN ROUTER card
  // ({"type":"sync","topic":"agentos"} / "models-updated"); app.js forwards them as a
  // "bucs:sync" window event. Until then the 15 s poll below catches them.
  window.addEventListener("bucs:sync", (e) => {
    const t = e && e.detail && e.detail.topic;
    if (t === "agentos" || t === "models") refresh();
  });
  mount.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.id === "ao-mq") loadMemory();
    if (e.key === "Enter" && e.target.classList.contains("ao-run")) act(e.target);
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && e.target.id === "ao-prompt") act(mount.querySelector("[data-act='run']"));
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  showTab(tab);
  refresh();
})();
