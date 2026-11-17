/* ═══ CUSTOM AGENTS · build your own team members (customagents.py) ═══
   Renders into #customagents-mount. Everything shown comes from /api/customagents (the real
   store, real provider / tool / section / voice registries). Custom agents are real team
   members: this file also refreshes the AVENGERS roster and marks them CUSTOM there. */
(() => {
  const mount = document.getElementById("customagents-mount");
  if (!mount) return;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c]));
  const $c = (id) => document.getElementById(id);
  const say = (html, kind = "info") => { try { logEvent(`<span class="tag">[agents]</span> ${html}`, kind); } catch (e) {} };
  const post = (u, b) => fetch(u, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b || {}) })
    .then(async (r) => { try { return await r.json(); } catch (e) { return { ok: false, error: "HTTP " + r.status }; } })
    .catch((e) => ({ ok: false, error: e.message }));
  const get = (u) => fetch(u, { cache: "no-store" }).then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); });
  const color = (c) => (/^#[0-9a-fA-F]{6}$/.test(c || "") ? c : "#00d9ff");
  const GLYPH = { bolt: "⚡", eye: "◉", shield: "⛨", pen: "✎", chart: "▟", radar: "◎", book: "▤", gear: "⚙", globe: "◍", star: "★", flask: "⚗", compass: "✦" };
  const TIER = { read: "READ", action: "ACTION · COMMANDER", destructive: "APPROVAL EACH CALL" };
  const ms = (v) => (v == null ? "—" : v < 1000 ? `${v} ms` : `${(v / 1000).toFixed(1)} s`);

  let data = null, editing = null, testing = null, busy = false, timer = null;
  const customIds = new Map();           // id → {tag, color} for the roster marker

  mount.innerHTML = `
  <div class="bs-card ca-card" id="ca-card">
    <div class="bs-head">
      <div class="bs-title">CUSTOM AGENTS · BUILD YOUR TEAM</div>
      <div class="bs-sub" id="ca-sub">loading…</div>
      <div class="bs-sub"><span class="src-local">SOURCE · this install (same agent store as AI CORE); instructions are stored as text and never executed</span></div>
    </div>
    <div class="ca-bar">
      <button class="cmd-btn ca-new" type="button" data-act="new">+ NEW AGENT</button>
      <button class="cmd-btn" type="button" data-act="import-open">IMPORT JSON</button>
      <span class="ca-dim" id="ca-role"></span>
    </div>
    <div id="ca-import" class="ca-panel" hidden></div>
    <div id="ca-editor" class="ca-panel" hidden></div>
    <div id="ca-grid" class="ca-grid" aria-live="polite"><div class="tf-empty">loading agents…</div></div>
    <div id="ca-test" class="ca-panel" hidden></div>
    <details class="ca-det" id="ca-builtins-det"><summary>BUILT-IN AVENGERS · PERSONA OVERRIDE</summary><div id="ca-builtins" class="ca-list"></div></details>
    <details class="ca-det" id="ca-aicore-det" hidden><summary>AI CORE TASK AGENTS · NOT ON THE TEAM YET</summary><div id="ca-aicore" class="ca-list"></div></details>
  </div>`;

  /* ── roster integration (no app.js edit: renderAgents is a global of the classic script) ── */
  function decorateRoster() {
    document.querySelectorAll("#avengers-list .agent-card").forEach((card) => {
      const info = customIds.get(card.dataset.agent);
      if (!info || card.querySelector(".ca-mark")) return;
      const icon = card.querySelector(".agent-icon");
      if (icon) icon.textContent = info.tag;
      const name = card.querySelector(".agent-name");
      if (name) { const m = document.createElement("span"); m.className = "ca-mark"; m.textContent = "CUSTOM"; name.appendChild(m); }
      card.classList.add("ca-roster");
    });
  }
  async function refreshRoster() {
    try {
      const st = await get("/api/status");
      if (typeof renderAgents === "function" && Array.isArray(st.agents)) {
        const active = new Map([...document.querySelectorAll("#avengers-list .agent-card")].map((c) => [c.dataset.agent, c.querySelector(".agent-status")?.textContent]));
        renderAgents(st.agents);
        st.agents.forEach((a) => { if (typeof updateAgentStatus === "function" && (a.status !== "idle" || active.get(a.name))) updateAgentStatus(a.name, a.status, a.current_task); });
      }
    } catch (e) {}
    decorateRoster();
  }
  const rosterEl = document.getElementById("avengers-list");
  if (rosterEl && window.MutationObserver) new MutationObserver(() => decorateRoster()).observe(rosterEl, { childList: true });

  /* ── data ── */
  async function load(quiet) {
    try {
      const d = await get("/api/customagents");
      data = d;
      customIds.clear();
      d.agents.filter((a) => a.enabled).forEach((a) => customIds.set(a.id, { tag: a.tag, color: a.color }));
      $c("ca-card").classList.remove("is-error");
      render();
      decorateRoster();
    } catch (e) {
      if (quiet && data) return;
      $c("ca-card").classList.add("is-error");
      $c("ca-sub").textContent = "could not load custom agents";
      $c("ca-grid").innerHTML = `<div class="ca-err" role="alert">Could not load custom agents (${esc(e.message)}). <button class="ca-mini" type="button" data-act="retry">RETRY</button></div>`;
    }
  }

  function card(a, preview) {
    const live = a.enabled ? (a.status || "idle") : "disabled";
    const secs = a.sections || [], tools = a.tools || [];
    const prov = ((data && data.meta.providers) || []).find((p) => p.id === a.provider);
    return `<div class="ca-agent ${a.enabled ? "" : "off"} ${live !== "idle" && a.enabled ? "busy" : ""}" style="--ca-color:${color(a.color)}" ${preview ? "" : `data-id="${esc(a.id)}"`}>
      <div class="ca-agent-h">
        <div class="ca-ico" aria-hidden="true">${GLYPH[a.icon] || GLYPH.bolt}</div>
        <div class="ca-agent-t">
          <div class="ca-name">${esc(a.name || "Unnamed agent")} <span class="ca-tag">${esc(a.tag || "···")}</span> <span class="ca-mark">CUSTOM</span></div>
          <div class="ca-rolel">${esc(a.role || "Custom agent")}</div>
        </div>
        <div class="ca-status ${esc(live)}">${esc(live.toUpperCase())}</div>
      </div>
      ${a.description ? `<div class="ca-desc">${esc(a.description)}</div>` : ""}
      ${a.enabled && a.current_task && a.current_task !== "—" ? `<div class="ca-task">▸ ${esc(a.current_task)}</div>` : ""}
      <div class="ca-facts">
        <span title="How to address this agent">SAY “${esc((a.phrases && a.phrases[0]) || (a.name || "").toLowerCase() || "name")}, …”</span>
        <span>MODEL ${esc(prov ? prov.name.split(" (")[0] : a.provider || "active")}</span>
        <span>${secs.length} SECTION${secs.length === 1 ? "" : "S"}</span>
        <span>${tools.length} TOOL${tools.length === 1 ? "" : "S"}</span>
        ${a.voice ? `<span>VOICE ${esc(a.voice.replace(/^Microsoft /, ""))}</span>` : ""}
      </div>
      ${preview ? "" : `<div class="ca-acts">
        <button class="ca-mini" type="button" data-act="test" data-id="${esc(a.id)}">TEST</button>
        <button class="ca-mini" type="button" data-act="edit" data-id="${esc(a.id)}">EDIT</button>
        <button class="ca-mini" type="button" data-act="dup" data-id="${esc(a.id)}">DUPLICATE</button>
        <button class="ca-mini" type="button" data-act="toggle" data-id="${esc(a.id)}">${a.enabled ? "DISABLE" : "ENABLE"}</button>
        <button class="ca-mini" type="button" data-act="export" data-id="${esc(a.id)}">EXPORT</button>
        <button class="ca-mini ca-danger" type="button" data-act="delete" data-id="${esc(a.id)}">DELETE</button>
      </div>`}
    </div>`;
  }

  function render() {
    const d = data; if (!d) return;
    const on = d.agents.filter((a) => a.enabled).length;
    $c("ca-sub").textContent = d.agents.length
      ? `${d.agents.length} custom agent${d.agents.length === 1 ? "" : "s"} · ${on} on the team · ${d.builtins.length} built-in`
      : `no custom agents yet · ${d.builtins.length} built-in Avengers on the team`;
    $c("ca-role").textContent = d.commander ? "" : "OBSERVER — view only";
    mount.classList.toggle("ca-readonly", !d.commander);
    // polling must not rebuild cards that did not change (keeps focus and an armed DELETE)
    const key = JSON.stringify([d.commander, d.agents.map((a) => [a.id, a.updated, a.enabled, a.status, a.current_task])]);
    if (key !== render.key) $c("ca-grid").innerHTML = d.agents.length ? d.agents.map((a) => card(a)).join("")
      : `<div class="ca-empty"><b>No custom agents yet.</b><span>Create one from a template or from scratch. It joins the Avengers roster, answers when you type “Name, …” in the command bar, and listens for its wake phrase when voice is on.</span>
          ${d.commander ? `<button class="cmd-btn ca-new" type="button" data-act="new">+ NEW AGENT</button>` : ""}</div>`;
    render.key = key;
    if (!$c("ca-builtins").querySelector("textarea:focus"))
      $c("ca-builtins").innerHTML = d.builtins.map((b) => `<div class="ca-item" style="--ca-color:${color(b.color)}">
        <div class="ca-item-h"><b>${esc(b.codename)}</b><span class="ca-dim">${esc(b.role)}</span>
          <span class="ca-st ${b.overridden ? "warn" : ""}">${b.overridden ? "PERSONA OVERRIDDEN" : "DEFAULT PERSONA"}</span><span class="ca-dim">built-in · cannot be deleted</span></div>
        <textarea class="ca-in ca-ta" rows="2" data-builtin="${esc(b.key)}" aria-label="${esc(b.codename)} persona" maxlength="${d.meta.limits.system}">${esc(b.persona)}</textarea>
        <div class="ca-row"><button class="ca-mini" type="button" data-act="bsave" data-id="${esc(b.key)}">SAVE PERSONA</button>
          <button class="ca-mini" type="button" data-act="breset" data-id="${esc(b.key)}" ${b.overridden ? "" : "disabled"}>RESET TO DEFAULT</button></div></div>`).join("");
    $c("ca-aicore-det").hidden = !d.aicore.length;
    $c("ca-aicore").innerHTML = d.aicore.map((a) => `<div class="ca-item"><div class="ca-item-h"><b>${esc(a.name)}</b><span class="ca-dim">${esc(a.role || "AI CORE agent")}</span>
      <span class="ca-dim">${a.tools.length} tool(s)</span><button class="ca-mini ca-r" type="button" data-act="promote" data-id="${esc(a.id)}">ADD TO TEAM</button></div></div>`).join("");
  }

  /* ── editor ── */
  const blank = () => ({ id: null, name: "", tag: "", role: "", description: "", system: "", wake: [], color: "#00d9ff", icon: "bolt",
    voice: "", provider: "active", sections: [], tools: [], notes: "", profile: false, enabled: true });

  function readForm() {
    const f = $c("ca-form"); if (!f) return null;
    const v = (n) => (f.elements[n] ? f.elements[n].value : "");
    return { id: editing.id || undefined, name: v("name").trim(), tag: v("tag").trim().toUpperCase(), role: v("role").trim(),
      description: v("description").trim(), system: v("system"), wake: v("wake").split(/[,\n]/).map((s) => s.trim()).filter(Boolean),
      color: v("color"), icon: v("icon"), voice: v("voice"), provider: v("provider"),
      sections: [...f.querySelectorAll("[data-sec]:checked")].map((x) => x.dataset.sec),
      tools: [...f.querySelectorAll("[data-tool]:checked")].map((x) => x.dataset.tool),
      notes: v("notes"), profile: !!(f.elements.profile && f.elements.profile.checked), enabled: editing.enabled !== false };
  }
  function preview() {
    const a = readForm(); if (!a) return;
    const nm = a.name.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    $c("ca-preview").innerHTML = card({ ...a, tag: a.tag || a.name.replace(/[^A-Za-z0-9]/g, "").slice(0, 3).toUpperCase(), phrases: [nm].filter(Boolean), status: "idle" }, true)
      + `<div class="ca-note">Preview. In the roster it shows as <b>${esc((a.name || "NAME").toUpperCase())}</b> with a CUSTOM marker.<br>
         Address it with <code>${esc(a.name || "Name")}, your request</code> or <code>@id request</code> in the command bar${data.meta.voice_input ? ", or say its name / wake phrase" : " (voice input is off on this install)"}.</div>`;
    const cnt = $c("ca-syscount"); if (cnt) cnt.textContent = `${a.system.length} / ${data.meta.limits.system}`;
  }

  function openEditor(a, title) {
    if (!data) return;
    editing = { ...blank(), ...a };
    const m = data.meta, e = editing, L = m.limits;
    const opt = (val, label, cur, dis) => `<option value="${esc(val)}" ${val === cur ? "selected" : ""} ${dis ? "disabled" : ""}>${esc(label)}</option>`;
    const ed = $c("ca-editor");
    ed.hidden = false;
    ed.innerHTML = `
      <div class="ca-panel-h"><b>${esc(title || (e.id ? "EDIT AGENT" : "NEW AGENT"))}</b>
        <button class="ca-mini ca-r" type="button" data-act="cancel" aria-label="Close editor">✕ CLOSE</button></div>
      ${e.id ? "" : `<div class="ca-tpl"><span class="ca-dim">START FROM A TEMPLATE (pre-filled form, change anything):</span>
        ${m.templates.map((t) => `<button class="ca-chip" type="button" data-act="tpl" data-id="${esc(t.template)}"><span class="ca-tplmark">TEMPLATE</span> ${esc(t.label)}</button>`).join("")}
        <button class="ca-chip" type="button" data-act="tpl" data-id="">BLANK</button></div>`}
      <div class="ca-edit">
        <form id="ca-form" class="ca-form" autocomplete="off" novalidate>
          <fieldset><legend>1 · IDENTITY</legend>
            <div class="ca-row2">
              <label>NAME<input class="ca-in" name="name" maxlength="${L.name}" required value="${esc(e.name)}" placeholder="e.g. Nova" /></label>
              <label class="ca-narrow">CODENAME TAG<input class="ca-in" name="tag" maxlength="3" value="${esc(e.tag)}" placeholder="NOV" /></label>
            </div>
            <label>ROLE<input class="ca-in" name="role" maxlength="${L.role}" value="${esc(e.role)}" placeholder="what it is for, in a few words" /></label>
            <label>DESCRIPTION<input class="ca-in" name="description" maxlength="${L.description}" value="${esc(e.description)}" placeholder="optional, shown on the card" /></label>
            <div class="ca-row2">
              <label class="ca-narrow">COLOUR<input class="ca-in ca-color" type="color" name="color" value="${esc(color(e.color))}" /></label>
              <label>ICON<select class="ca-in" name="icon">${m.icons.map((i) => opt(i, `${GLYPH[i] || ""} ${i}`, e.icon)).join("")}</select></label>
            </div>
          </fieldset>
          <fieldset><legend>2 · PERSONA / INSTRUCTIONS</legend>
            <label>HOW IT SHOULD THINK AND ANSWER <span class="ca-dim" id="ca-syscount"></span>
              <textarea class="ca-in ca-ta" name="system" rows="6" maxlength="${L.system}" placeholder="You are … Lead with … Never …">${esc(e.system)}</textarea></label>
            <label>KNOWLEDGE NOTES (optional reference text it always sees — no secrets)
              <textarea class="ca-in ca-ta" name="notes" rows="3" maxlength="${L.notes}">${esc(e.notes)}</textarea></label>
            <label class="ca-check"><input type="checkbox" name="profile" ${e.profile ? "checked" : ""} /> may read the commander profile (your private long-form profile)</label>
          </fieldset>
          <fieldset><legend>3 · ADDRESSING, VOICE, MODEL</legend>
            <label>EXTRA WAKE PHRASES (comma separated; its name always works)
              <input class="ca-in" name="wake" value="${esc((e.wake || []).join(", "))}" placeholder="e.g. analyst, research desk" /></label>
            <div class="ca-row2">
              <label>SPEAKING VOICE<select class="ca-in" name="voice">${opt("", m.tts ? "Default system voice" : "Default (speech engine is off)", e.voice)}${m.voices.map((v) => opt(v, v, e.voice)).join("")}
                ${e.voice && !m.voices.includes(e.voice) ? opt(e.voice, e.voice + " (not installed)", e.voice) : ""}</select></label>
              <label>MODEL / PROVIDER<select class="ca-in" name="provider">${m.providers.map((p) => opt(p.id, p.name + (p.model ? ` · ${p.model}` : "") + (p.locked ? " (locked: needs Pro)" : ""), e.provider, p.locked)).join("")}</select></label>
            </div>
            <div class="ca-note">If the chosen provider fails, the agent falls back to the active brain and the test box says so.</div>
          </fieldset>
          <fieldset><legend>4 · DASHBOARD SECTIONS IT MAY READ</legend>
            <div class="ca-row"><button class="ca-mini" type="button" data-act="sec-all">ALL</button><button class="ca-mini" type="button" data-act="sec-none">NONE</button>
              <span class="ca-dim">read-only live facts; nothing outside this list reaches the agent</span></div>
            <div class="ca-checks">${m.sections.map((s) => `<label class="ca-check"><input type="checkbox" data-sec="${esc(s.section)}" ${e.sections.includes(s.section) ? "checked" : ""} /> ${esc(s.title)}</label>`).join("") || `<span class="ca-dim">no sections available</span>`}</div>
          </fieldset>
          <fieldset><legend>5 · TOOLS (AI CORE)</legend>
            ${m.tools.length ? `<div class="ca-note">READ tools are free. ACTION tools run only while the session is COMMANDER. APPROVAL tools are queued in AI CORE for you to approve each call — an agent can never pre-approve them. With tools enabled, answers go through the AI CORE tool loop and its router.</div>
            <div class="ca-checks ca-tools">${m.tools.map((t) => `<label class="ca-check" title="${esc(t.description)}"><input type="checkbox" data-tool="${esc(t.name)}" ${e.tools.includes(t.name) ? "checked" : ""} /> <code>${esc(t.name)}</code> <span class="ca-tier ${esc(t.tier)}">${TIER[t.tier] || esc(t.tier)}</span>${t.available ? "" : ` <span class="ca-dim">unavailable now</span>`}</label>`).join("")}</div>`
              : `<div class="ca-note">AI CORE is not loaded on this install, so no tools are available. The agent can still answer and read its dashboard sections.</div>`}
          </fieldset>
          <div class="ca-err" id="ca-formerr" role="alert" hidden></div>
          <div class="ca-row"><button class="cmd-btn" type="submit" id="ca-save">${e.id ? "SAVE CHANGES" : "CREATE AGENT"}</button>
            <button class="cmd-btn" type="button" data-act="cancel">CANCEL</button><span class="ca-dim" id="ca-savestate"></span></div>
        </form>
        <div class="ca-side"><div class="intel-divider">LIVE PREVIEW</div><div id="ca-preview"></div></div>
      </div>`;
    const f = $c("ca-form");
    f.addEventListener("input", preview);
    f.addEventListener("submit", (ev) => { ev.preventDefault(); saveForm(); });
    preview();
    f.elements.name.focus();
    ed.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  function closeEditor() { editing = null; const ed = $c("ca-editor"); ed.hidden = true; ed.innerHTML = ""; }

  async function saveForm() {
    if (busy) return;
    const body = readForm(), err = $c("ca-formerr"), btn = $c("ca-save");
    err.hidden = true;
    if (!body.name) { err.textContent = "Give the agent a name."; err.hidden = false; $c("ca-form").elements.name.focus(); return; }
    busy = true; btn.disabled = true; $c("ca-savestate").textContent = "saving…";
    const res = await post("/api/customagents", body);
    busy = false; btn.disabled = false; $c("ca-savestate").textContent = "";
    if (!res.ok) { err.textContent = res.error || "could not save"; err.hidden = false; return; }
    say(`${esc(res.agent.name)} ${res.created ? "created and added to the team" : "updated"}`);
    closeEditor();
    await load(); refreshRoster();
    const el = mount.querySelector(`.ca-agent[data-id="${CSS.escape(res.agent.id)}"] [data-act="test"]`); if (el) el.focus();
  }

  /* ── test box ── */
  function openTest(id) {
    const a = data.agents.find((x) => x.id === id); if (!a) return;
    testing = id;
    const t = $c("ca-test");
    t.hidden = false;
    t.innerHTML = `<div class="ca-panel-h"><b>TEST · ${esc(a.name.toUpperCase())}</b><span class="ca-dim">a real prompt through the real reply path (engine → agent → model)</span>
        <button class="ca-mini ca-r" type="button" data-act="test-close" aria-label="Close test box">✕ CLOSE</button></div>
      <form id="ca-testform" class="ca-row"><input class="ca-in" id="ca-testq" maxlength="${data.meta.limits.prompt}" placeholder="ask ${esc(a.name)} something…" aria-label="Test prompt" />
        <button class="cmd-btn" type="submit" id="ca-testgo" ${a.enabled ? "" : "disabled"}>SEND</button></form>
      <div id="ca-testout" class="ca-out">${a.enabled ? `<div class="tf-empty">the answer, route and latency appear here</div>` : `<div class="ca-err">This agent is disabled. Enable it to test it.</div>`}</div>`;
    $c("ca-testform").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const q = $c("ca-testq").value.trim(); if (!q || busy) return;
      busy = true; $c("ca-testgo").disabled = true;
      $c("ca-testout").innerHTML = `<div class="ca-wait" role="status">thinking…</div>`;
      const r = await post(`/api/customagents/${encodeURIComponent(id)}/test`, { prompt: q });
      busy = false; if ($c("ca-testgo")) $c("ca-testgo").disabled = false;
      if (!$c("ca-testout")) return;
      if (!r.ok && !r.reply) { $c("ca-testout").innerHTML = `<div class="ca-err" role="alert">${esc(r.error || "test failed")}</div>`; return; }
      $c("ca-testout").innerHTML = `<div class="ca-answer">${esc(r.reply || "(empty reply)")}</div>
        <div class="ca-facts"><span>ROUTE ${esc(r.path || "—")}</span><span>ANSWERED BY ${esc(r.agent || "—")}</span><span>TOTAL ${ms(r.ms)}</span>
          <span>FIRST TOKEN ${ms(r.first_token_ms)}</span><span>DASHBOARD FACTS ${r.sections && r.sections.length ? esc(r.sections.join(", ")) : "none used"}</span></div>
        ${r.fallback ? `<div class="ca-warn">The agent's own provider / tool loop did not answer, so the active brain replied instead.</div>` : ""}
        ${(r.steps || []).length ? `<div class="ca-steps">${r.steps.map((s) => `<div><b>${esc(s.tool)}</b> <span class="ca-tier ${esc(s.tier || "")}">${esc(TIER[s.tier] || s.tier || "")}</span> ${s.ok ? "ok" : esc(s.error || "refused")}</div>`).join("")}</div>` : ""}
        ${(r.approvals || []).length ? `<div class="ca-warn">${r.approvals.length} tool call(s) are waiting for your approval in AI CORE.</div>` : ""}
        ${r.error ? `<div class="ca-err">${esc(r.error)}</div>` : ""}`;
      load(true);
    });
    $c("ca-testq").focus();
    t.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  /* ── import ── */
  function openImport() {
    const p = $c("ca-import");
    p.hidden = false;
    p.innerHTML = `<div class="ca-panel-h"><b>IMPORT AGENT</b><span class="ca-dim">a .json exported from BUCS — validated strictly, imported DISABLED for review</span>
        <button class="ca-mini ca-r" type="button" data-act="import-close" aria-label="Close import">✕ CLOSE</button></div>
      <div class="ca-row"><input type="file" id="ca-impfile" accept="application/json,.json" aria-label="Agent JSON file" /></div>
      <textarea class="ca-in ca-ta" id="ca-imptext" rows="4" placeholder="…or paste the agent JSON here" aria-label="Agent JSON"></textarea>
      <div class="ca-err" id="ca-imperr" role="alert" hidden></div>
      <div class="ca-row"><button class="cmd-btn" type="button" data-act="import-go">IMPORT</button></div>`;
    $c("ca-impfile").addEventListener("change", async (ev) => {
      const f = ev.target.files[0]; if (!f) return;
      if (f.size > data.meta.limits.import_bytes) { $c("ca-imperr").textContent = "file is too large"; $c("ca-imperr").hidden = false; return; }
      $c("ca-imptext").value = await f.text();
    });
    $c("ca-imptext").focus();
  }
  async function doImport() {
    const err = $c("ca-imperr"); err.hidden = true;
    let obj;
    try { obj = JSON.parse($c("ca-imptext").value); } catch (e) { err.textContent = "that is not valid JSON"; err.hidden = false; return; }
    const res = await post("/api/customagents/import", { agent: obj });
    if (!res.ok) { err.textContent = res.error || "import refused"; err.hidden = false; return; }
    say(`${esc(res.agent.name)} imported (disabled) — ${esc((res.notes || []).join("; "))}`);
    $c("ca-import").hidden = true; $c("ca-import").innerHTML = "";
    await load();
  }

  /* ── actions ── */
  mount.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-act]"); if (!b || b.disabled) return;
    const act = b.dataset.act, id = b.dataset.id;
    if (act === "retry") return load();
    if (!data) return;
    const need = () => { if (data.commander) return true; say("OBSERVER role — switch to COMMANDER to change custom agents.", "warn"); return false; };
    const done = async (res, msg) => {
      if (!res.ok) { say(esc(res.error || "request refused"), "error"); return false; }
      if (msg) say(msg);
      await load(); refreshRoster(); return true;
    };
    if (act === "new") { if (need()) openEditor(blank()); }
    else if (act === "cancel") closeEditor();
    else if (act === "tpl") {
      const t = data.meta.templates.find((x) => x.template === id);
      openEditor(t ? { ...blank(), ...t, id: null } : blank(), t ? `NEW AGENT · FROM TEMPLATE “${t.label.toUpperCase()}”` : "NEW AGENT");
    }
    else if (act === "edit") { if (need()) { const a = data.agents.find((x) => x.id === id); if (a) openEditor(a); } }
    else if (act === "promote") {
      if (!need()) return;
      const a = data.aicore.find((x) => x.id === id);
      if (a) openEditor({ ...blank(), id: a.id, name: a.name, role: (a.role || "").slice(0, data.meta.limits.role), system: a.system, tools: a.tools }, "ADD AI CORE AGENT TO THE TEAM");
    }
    else if (act === "sec-all" || act === "sec-none") { $c("ca-form").querySelectorAll("[data-sec]").forEach((x) => { x.checked = act === "sec-all"; }); preview(); }
    else if (act === "test") openTest(id);
    else if (act === "test-close") { testing = null; $c("ca-test").hidden = true; $c("ca-test").innerHTML = ""; }
    else if (act === "dup") { if (need()) await done(await post(`/api/customagents/${encodeURIComponent(id)}/duplicate`), "agent duplicated (the copy starts disabled)"); }
    else if (act === "toggle") {
      if (!need()) return;
      const a = data.agents.find((x) => x.id === id);
      await done(await post(`/api/customagents/${encodeURIComponent(id)}/enable`, { on: !a.enabled }), `${esc(a.name)} ${a.enabled ? "disabled (off the team)" : "enabled (on the team)"}`);
    }
    else if (act === "delete") {
      if (!need()) return;
      const a = data.agents.find((x) => x.id === id);
      if (b.dataset.armed !== "1") { b.dataset.armed = "1"; b.textContent = "CONFIRM DELETE"; setTimeout(() => { if (b.isConnected) { b.dataset.armed = ""; b.textContent = "DELETE"; } }, 4000); return; }
      if (await done(await post(`/api/customagents/${encodeURIComponent(id)}/delete`), `${esc(a.name)} deleted`)) {
        if (testing === id) { testing = null; $c("ca-test").hidden = true; }
        if (editing && editing.id === id) closeEditor();
      }
    }
    else if (act === "export") {
      try {
        const r = await get(`/api/customagents/${encodeURIComponent(id)}/export`);
        if (!r.ok) throw new Error(r.error || "export failed");
        const url = URL.createObjectURL(new Blob([JSON.stringify(r.agent, null, 2)], { type: "application/json" }));
        const a = document.createElement("a"); a.href = url; a.download = `${id}.bucs-agent.json`; document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
      } catch (e) { say(esc(e.message), "error"); }
    }
    else if (act === "import-open") { if (need()) openImport(); }
    else if (act === "import-close") { $c("ca-import").hidden = true; $c("ca-import").innerHTML = ""; }
    else if (act === "import-go") doImport();
    else if (act === "bsave" || act === "breset") {
      if (!need()) return;
      const ta = mount.querySelector(`textarea[data-builtin="${CSS.escape(id)}"]`);
      ta.blur();
      await done(await post(`/api/customagents/builtin/${encodeURIComponent(id)}`, act === "breset" ? { reset: true } : { persona: ta.value }),
        act === "breset" ? `${esc(id)} persona reset to default` : `${esc(id)} persona saved`);
    }
  });
  mount.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    if (editing) { closeEditor(); mount.querySelector('[data-act="new"]')?.focus(); }
    else if (testing) { testing = null; $c("ca-test").hidden = true; }
  });

  /* another tab (or AI CORE) changed the team → re-read the store and the roster */
  window.addEventListener("bucs:sync", (ev) => {
    const t = ev.detail && ev.detail.topic;
    if (t === "customagents" || t === "agentos") { load(true); if (t === "customagents") refreshRoster(); }
    if (t === "enterprise") load(true);                       // role switched
  });
  const tick = () => { if (!document.hidden && !busy) load(true); };
  load().then(refreshRoster);
  timer = setInterval(tick, 15000);
})();
