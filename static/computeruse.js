/* ═══ COMPUTER USE · SUPERVISED DESKTOP AGENT (computeruse.py) ═══
   Renders into #computeruse-mount. Polls /api/computeruse (fast while a task runs).
   Every string from the server / model / screen is escaped before it touches the DOM. */
(() => {
  const mount = document.getElementById("computeruse-mount");
  if (!mount) return;
  const esc = (s) => (typeof escapeHTML === "function" ? escapeHTML(String(s ?? ""))
    : String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c])));
  const log = (html, kind = "info") => { try { logEvent(`<span class="tag">[computer]</span> ${html}`, kind); } catch (e) {} };
  const ACTIVE = ["starting", "thinking", "acting", "awaiting_approval", "awaiting_input", "paused", "taken_over"];
  const LABEL = {
    idle: "IDLE", starting: "STARTING", thinking: "THINKING", acting: "ACTING", awaiting_approval: "WAITING FOR APPROVAL",
    awaiting_input: "WAITING FOR YOUR ANSWER", paused: "PAUSED", taken_over: "YOU HAVE CONTROL", done: "DONE",
    failed: "FAILED", stopped: "STOPPED",
  };
  let data = null, timer = null, frameKey = "", settingsDirty = false, lastPendingKey = "";

  mount.innerHTML = `
  <div class="bs-card cu-card" id="cu-card">
    <div class="bs-head">
      <div class="bs-title">COMPUTER USE · SUPERVISED DESKTOP AGENT</div>
      <div class="bs-sub"><span class="cu-dot" id="cu-dot"></span><span id="cu-state">IDLE</span> · MODEL <span id="cu-model">—</span> · <span id="cu-safe">FAIL-SAFE: MOUSE TO ANY CORNER</span></div>
    </div>
    <div class="cu-grid">
      <div class="cu-left">
        <div class="intel-divider">TASK</div>
        <textarea id="cu-task" class="cu-input" rows="3" maxlength="1000" placeholder="e.g. Open Notepad and type: hello from BUCS"></textarea>
        <div class="cu-row">
          <select id="cu-adapter" class="cu-select" title="Vision model"></select>
          <button class="cmd-btn cu-go" id="cu-start" type="button">RUN TASK</button>
        </div>
        <div class="cu-nomodel" id="cu-nomodel" hidden></div>
        <div class="cu-perm" id="cu-perm" hidden></div>
        <div class="intel-divider">CONTROLS</div>
        <div class="cu-ctrls">
          <button class="cmd-btn" id="cu-pause" type="button" data-ctl="pause">PAUSE</button>
          <button class="cmd-btn" id="cu-resume" type="button" data-ctl="resume">RESUME</button>
          <button class="cmd-btn" id="cu-take" type="button" data-ctl="takeover" title="Agent pauses and releases mouse & keyboard to you">TAKE OVER</button>
          <button class="cmd-btn" id="cu-hand" type="button" data-ctl="handback" title="Give control back to the agent">HAND BACK</button>
          <button class="cmd-btn cu-stop" id="cu-stop" type="button" data-ctl="stop">STOP</button>
        </div>
        <div class="cu-note" id="cu-hk">Emergency stop: slam the mouse into any screen corner.</div>
        <details class="cu-set" id="cu-set">
          <summary>PERMISSIONS &amp; LIMITS</summary>
          <div class="cu-form">
            <label>Monitor <select id="cu-mon" class="cu-select"></select></label>
            <label>Max steps <input id="cu-steps" class="cu-num" type="number" min="1" max="100"></label>
            <label>Max minutes <input id="cu-mins" class="cu-num" type="number" min="1" max="60"></label>
            <label>Min gap (s) <input id="cu-gap" class="cu-num" type="number" min="0.2" max="5" step="0.1"></label>
            <label class="cu-chk"><input id="cu-all" type="checkbox"> Approve every action (step mode)</label>
            <label class="cu-chk"><input id="cu-mouse" type="checkbox"> Pause when I move the mouse</label>
            <label class="cu-chk"><input id="cu-apps" type="checkbox"> Allow launching apps</label>
            <label class="cu-chk"><input id="cu-urls" type="checkbox"> Allow opening URLs</label>
            <label class="cu-wide">Readable folders (one per line; empty = no file access)
              <textarea id="cu-dirs" class="cu-input" rows="2" placeholder="C:\\Users\\you\\Documents\\shared"></textarea></label>
            <button class="cmd-btn" id="cu-save" type="button">SAVE SETTINGS</button>
            <div class="cu-note" id="cu-set-msg"></div>
          </div>
        </details>
      </div>
      <div class="cu-mid">
        <div class="intel-divider">LIVE SCREEN · <span id="cu-frame-lab">no frame</span></div>
        <div class="cu-screen" id="cu-screen">
          <img id="cu-img" alt="Latest screenshot the agent saw" hidden>
          <svg id="cu-ov" class="cu-ov" preserveAspectRatio="none" aria-hidden="true"></svg>
          <div class="cu-empty" id="cu-empty">The agent's view appears here while a task runs.<br>Screenshots stay on this PC and are deleted 10 min after the task ends.</div>
          <div class="cu-banner" id="cu-banner" hidden></div>
        </div>
        <div class="cu-think" id="cu-think"></div>
        <div class="cu-ask" id="cu-approve" hidden></div>
        <div class="cu-ask" id="cu-question" hidden>
          <div class="cu-ask-h">THE AGENT ASKS</div>
          <div class="cu-ask-q" id="cu-q-text"></div>
          <div class="cu-row"><input id="cu-q-in" class="cu-input" maxlength="1000" placeholder="Your answer">
            <button class="cmd-btn" id="cu-q-send" type="button">ANSWER</button></div>
        </div>
      </div>
      <div class="cu-right">
        <div class="intel-divider">STEPS · <span id="cu-step-n">0</span></div>
        <div class="cu-steps" id="cu-steps-list"><div class="tf-empty">no task yet</div></div>
        <div class="intel-divider">TASK HISTORY</div>
        <div class="cu-hist" id="cu-hist"><div class="tf-empty">none</div></div>
      </div>
    </div>
  </div>`;

  const $c = (id) => document.getElementById(id);
  const post = (u, b) => fetch(u, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b || {}) })
    .then((r) => r.json()).catch((e) => ({ ok: false, error: e.message }));
  const t2 = (ts) => ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";

  function marker(fw, fh, s, pending) {
    if (!s || s.x == null) return "";
    const r = Math.max(fw, fh) / 70, cls = pending ? "cu-mk pend" : "cu-mk";
    let h = "";
    if (s.x2 != null) h += `<line class="${cls}" x1="${s.x}" y1="${s.y}" x2="${s.x2}" y2="${s.y2}" />`;
    h += `<circle class="${cls}" cx="${s.x}" cy="${s.y}" r="${r}" /><circle class="${cls} core" cx="${s.x}" cy="${s.y}" r="${r / 4}" />`;
    h += `<line class="${cls} hair" x1="${s.x - r * 2}" y1="${s.y}" x2="${s.x + r * 2}" y2="${s.y}" /><line class="${cls} hair" x1="${s.x}" y1="${s.y - r * 2}" x2="${s.x}" y2="${s.y + r * 2}" />`;
    return h;
  }

  function renderScreen(d) {
    const t = d.task, img = $c("cu-img"), ov = $c("cu-ov");
    const fr = t && t.frame;
    if (!t || !fr || !t.frames_kept) {
      img.hidden = true; ov.innerHTML = ""; $c("cu-empty").hidden = false;
      $c("cu-frame-lab").textContent = t && t.frame && !t.frames_kept ? "screenshots deleted" : "no frame";
      frameKey = ""; return;
    }
    const key = `${t.id}/${fr.step}`;
    if (key !== frameKey) { frameKey = key; img.src = `/api/computeruse/frame/${encodeURIComponent(t.id)}/${fr.step}?t=${Date.now()}`; }
    img.hidden = false; $c("cu-empty").hidden = true;
    ov.setAttribute("viewBox", `0 0 ${fr.w} ${fr.h}`);
    const pend = d.pending && d.pending.kind === "approval" && d.pending.x != null ? d.pending : null;
    const last = [...(t.steps || [])].reverse().find((s) => s.frame === fr.step && s.x != null);
    ov.innerHTML = marker(fr.w, fr.h, pend || last, !!pend);
    $c("cu-frame-lab").textContent = `${fr.w}×${fr.h} · MONITOR ${fr.monitor[2]}×${fr.monitor[3]} · ${t2(fr.ts)}`;
  }

  function renderApproval(d) {
    const p = d.pending, box = $c("cu-approve"), q = $c("cu-question");
    if (p && p.kind === "approval") {
      const left = Math.max(0, Math.round(p.timeout - (Date.now() / 1000 - p.since)));
      box.hidden = false;
      const key = `${p.since}`;
      if (key !== lastPendingKey) { lastPendingKey = key; log(`approval needed: <b>${esc(p.category)}</b> · ${esc(p.action)}`, "warn"); }
      box.innerHTML = `<div class="cu-ask-h">APPROVAL REQUIRED · ${esc(String(p.category || "").toUpperCase())} <span class="cu-tmo">${left}s</span></div>
        <div class="cu-ask-q"><b>${esc(p.action)}</b></div>
        <div class="cu-ask-r">${esc(p.reason)}</div>
        ${p.reasoning ? `<div class="cu-ask-m">Agent: ${esc(p.reasoning)}</div>` : ""}
        <div class="cu-row cu-ask-btns">
          <button class="cmd-btn cu-ok" type="button" data-decide="approve">APPROVE</button>
          <button class="cmd-btn" type="button" data-decide="deny">DENY</button>
          <button class="cmd-btn" type="button" data-decide="takeover">TAKE OVER</button>
          <button class="cmd-btn cu-stop" type="button" data-decide="stop">STOP</button></div>`;
    } else { box.hidden = true; box.innerHTML = ""; }
    if (p && p.kind === "question") {
      q.hidden = false; $c("cu-q-text").textContent = p.question || "";
    } else q.hidden = true;
  }

  function renderSteps(d) {
    const t = d.task;
    $c("cu-step-n").textContent = t ? `${t.step}/${t.max_steps} · ${Math.round(t.elapsed)} s` : "0";
    if (!t || !t.steps.length) { $c("cu-steps-list").innerHTML = `<div class="tf-empty">${t ? "waiting for the first action…" : "no task yet"}</div>`; return; }
    const list = $c("cu-steps-list"), atBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 8;
    list.innerHTML = t.steps.map((s) => `
      <details class="cu-st ${s.ok ? "ok" : "bad"}${s.gate ? " gated" : ""}">
        <summary><span class="cu-st-n">${s.n}</span><span class="cu-st-d">${esc(s.desc)}</span>
          ${s.gate ? `<span class="cu-tag">${esc(String(s.gate.category || "").toUpperCase())}</span>` : ""}</summary>
        <div class="cu-st-b">${s.reasoning ? `<div><b>WHY</b> ${esc(s.reasoning)}</div>` : ""}
          <div><b>RESULT</b> ${esc(s.result)}</div><div class="cu-st-t">${t2(s.ts)}</div></div>
      </details>`).join("");
    if (atBottom) list.scrollTop = list.scrollHeight;
  }

  function renderHistory(d) {
    const h = d.history || [];
    $c("cu-hist").innerHTML = h.length ? h.slice(0, 10).map((x) => `
      <div class="cu-h ${esc(x.status)}" title="${esc(x.summary || x.error)}"><span class="cu-h-s">${esc(String(x.status).toUpperCase())}</span>
        <span class="cu-h-t">${esc(x.task)}</span><span class="cu-h-m">${x.steps} steps · ${esc(new Date(x.started * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }))}</span>
        <span class="cu-h-r">${esc(x.summary || x.error)}</span></div>`).join("") : `<div class="tf-empty">none</div>`;
  }

  function renderSettings(d) {
    const sel = $c("cu-adapter"), cur = sel.value || d.settings.adapter;
    const opts = [`<option value="auto">AUTO · best available</option>`].concat((d.candidates || []).filter((c) => c.id !== "error").map((c) =>
      `<option value="${esc(c.id)}" ${c.available ? "" : "disabled"}>${esc(c.name)}${c.available ? "" : " — " + esc(c.reason)}</option>`));
    const html = opts.join("");
    if (sel.dataset.h !== html) { sel.innerHTML = html; sel.dataset.h = html; sel.value = [...sel.options].some((o) => o.value === cur && !o.disabled) ? cur : "auto"; }
    if (settingsDirty) return;
    const s = d.settings;
    $c("cu-steps").value = s.max_steps; $c("cu-mins").value = s.max_minutes; $c("cu-gap").value = s.min_interval;
    $c("cu-all").checked = s.approve_all; $c("cu-mouse").checked = s.pause_on_mouse;
    $c("cu-apps").checked = s.allow_apps; $c("cu-urls").checked = s.allow_urls;
    $c("cu-dirs").value = (s.allowed_dirs || []).join("\n");
    const mon = $c("cu-mon");
    if (!mon.options.length) mon.innerHTML = `<option value="${s.monitor}">Display ${s.monitor}</option>`;
    mon.value = String(s.monitor);
  }

  function render(d) {
    if (!d || !d.settings) return;
    data = d;
    const st = d.status || "idle", active = ACTIVE.includes(st);
    $c("cu-state").textContent = LABEL[st] || st.toUpperCase();
    $c("cu-dot").className = "cu-dot " + st;
    const chosen = d.task && active ? d.task.adapter_name : (d.chosen ? d.chosen.name : "none available");
    $c("cu-model").textContent = chosen;
    $c("cu-safe").textContent = d.hotkey ? `FAIL-SAFE: MOUSE TO A CORNER · ${d.hotkey.toUpperCase()}` : "FAIL-SAFE: MOUSE TO A CORNER";
    $c("cu-hk").textContent = `Emergency stop: slam the mouse into any screen corner${d.hotkey ? " or press " + d.hotkey : ""}. Moving the mouse during a task pauses the agent.`;
    const nm = $c("cu-nomodel");
    nm.hidden = !d.no_model; if (d.no_model) nm.innerHTML = `<b>NO VISION MODEL</b> ${esc(d.howto)}`;
    const pm = d.permissions || {}, pb = $c("cu-perm");
    pb.hidden = pm.ok !== false;
    if (pm.ok === false) pb.innerHTML = `<b>SCREEN CONTROL UNAVAILABLE</b> ${esc((pm.problems || []).join("; "))}${(pm.howto || []).length ? `<br>${(pm.howto || []).map(esc).join("<br>")}` : ""}`;
    $c("cu-start").disabled = active || !!d.no_model || pm.ok === false;
    $c("cu-pause").disabled = !active || ["paused", "taken_over"].includes(st);
    $c("cu-resume").disabled = st !== "paused";
    $c("cu-take").disabled = !active || st === "taken_over";
    $c("cu-hand").disabled = st !== "taken_over";
    $c("cu-stop").disabled = !active;
    const bn = $c("cu-banner"), t = d.task;
    if (st === "taken_over" || st === "paused") { bn.hidden = false; bn.className = "cu-banner hold"; bn.textContent = (st === "taken_over" ? "YOU HAVE CONTROL — " : "PAUSED — ") + (d.paused_reason || ""); }
    else if (t && ["done", "failed", "stopped"].includes(t.status)) { bn.hidden = false; bn.className = "cu-banner " + t.status; bn.textContent = `${t.status.toUpperCase()} · ${t.summary || t.error || ""}`; }
    else bn.hidden = true;
    $c("cu-think").textContent = t && active && t.reasoning ? t.reasoning : "";
    renderScreen(d); renderApproval(d); renderSteps(d); renderHistory(d); renderSettings(d);
    clearTimeout(timer);
    timer = setTimeout(refresh, active ? 900 : 8000);
  }

  async function refresh() {
    try { render(await (await fetch("/api/computeruse", { cache: "no-store" })).json()); }
    catch (e) { clearTimeout(timer); timer = setTimeout(refresh, 10000); }
  }

  $c("cu-start").addEventListener("click", async (e) => {
    const b = e.currentTarget, task = $c("cu-task").value.trim();
    if (!task) { $c("cu-task").focus(); return; }
    b.disabled = true; b.textContent = "STARTING…";
    const r = await post("/api/computeruse/task", { task, adapter: $c("cu-adapter").value });
    b.textContent = "RUN TASK";
    if (r.ok === false) log(esc(r.error || "refused") + (r.howto ? ` — ${esc(Array.isArray(r.howto) ? r.howto.join("; ") : r.howto)}` : ""), "warn");
    else log(`task started with ${esc(r.adapter && r.adapter.name)}`);
    refresh();
  });
  $c("cu-task").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $c("cu-start").click(); });

  mount.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-ctl],[data-decide]");
    if (!el || !mount.contains(el)) return;
    el.disabled = true;
    const r = el.dataset.ctl ? await post("/api/computeruse/control", { action: el.dataset.ctl })
      : await post("/api/computeruse/approve", { decision: el.dataset.decide });
    if (r.ok === false) log(esc(r.error || "refused"), "warn");
    el.disabled = false; refresh();
  });

  $c("cu-q-send").addEventListener("click", async () => {
    const text = $c("cu-q-in").value.trim();
    if (!text) return;
    const r = await post("/api/computeruse/reply", { text });
    if (r.ok === false) log(esc(r.error || "refused"), "warn"); else $c("cu-q-in").value = "";
    refresh();
  });

  $c("cu-set").addEventListener("input", () => { settingsDirty = true; });
  $c("cu-set").addEventListener("toggle", async () => {
    if (!$c("cu-set").open) return;
    try {
      const r = await (await fetch("/api/computeruse/monitors", { cache: "no-store" })).json();
      if (r.monitors && r.monitors.length) {
        const mon = $c("cu-mon"), cur = data ? String(data.settings.monitor) : "1";
        mon.innerHTML = r.monitors.map((m) => `<option value="${m.index}">${m.index}${m.primary ? " · primary" : ""} · ${m.width}×${m.height}</option>`).join("");
        mon.value = cur;
      }
    } catch (e) {}
  });
  $c("cu-save").addEventListener("click", async () => {
    const body = {
      monitor: +$c("cu-mon").value || 1, max_steps: +$c("cu-steps").value, max_minutes: +$c("cu-mins").value,
      min_interval: +$c("cu-gap").value, approve_all: $c("cu-all").checked, pause_on_mouse: $c("cu-mouse").checked,
      allow_apps: $c("cu-apps").checked, allow_urls: $c("cu-urls").checked, adapter: $c("cu-adapter").value,
      allowed_dirs: $c("cu-dirs").value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean),
    };
    const r = await post("/api/computeruse/settings", body);
    $c("cu-set-msg").textContent = r.ok ? "saved" : (r.errors || [r.error]).filter(Boolean).join(" · ");
    settingsDirty = false; refresh();
  });

  refresh();
})();
