/* ═══ CORE EDITING · create / edit / delete across the core dashboard ═══
   Server side: coreedit.py + corestore.py (dashboard_store.json). Every write is
   commander-gated, audited and broadcast ({"type":"sync"}), so other tabs follow.
   Loaded after app.js; uses its globals ($, escapeHTML, openCustomModal, logEvent, …). */
(() => {
  "use strict";
  const esc = (s) => escapeHTML(String(s ?? ""));
  const J = async (method, url, body) => {
    try {
      const r = await fetch(url, { method, cache: "no-store",
        headers: body !== undefined ? { "content-type": "application/json" } : undefined,
        body: body !== undefined ? JSON.stringify(body) : undefined });
      if (!r.ok) return { ok: false, error: "HTTP " + r.status };
      return await r.json();
    } catch (e) { return { ok: false, error: "server unreachable: " + e.message }; }
  };
  const fail = (tag, r) => logEvent(`<span class="tag">[${tag}]</span> ${esc(r.error || "not saved")}`, "warn");

  /* one modal at a time: handlers belong to the modal body they were opened with */
  let cur = { seq: -1, click: null, submit: null };
  function modal(title, sub, html, click, submit) {
    openCustomModal(title, sub, null, `<div class="ce">${html}<div class="ce-err" id="ce-err" role="alert"></div></div>`);
    cur = { seq: window._mSeq, click, submit };
  }
  const live = () => cur.seq === window._mSeq && !!document.querySelector("#modal-body .ce");
  const err = (msg) => { const e = $("#ce-err"); if (e) e.textContent = msg || ""; };
  const mb = $("#modal-body");
  mb?.addEventListener("click", (e) => { if (live() && cur.click) cur.click(e); });
  mb?.addEventListener("submit", (e) => { if (live() && cur.submit) { e.preventDefault(); cur.submit(e); } });
  /* result of a write made from the open modal: show the refusal inline or run `then` */
  const done = (r, then) => { if (r && r.ok === false) { err(r.error || "not saved"); return false; } err(""); if (then) then(r); return true; };
  const btn = (text, title, cls = "ghost-btn ce-gbtn") => { const b = document.createElement("button"); b.type = "button"; b.className = cls; b.textContent = text; b.title = title; b.setAttribute("aria-label", title); return b; };

  let prefs = null;
  async function loadPrefs() {
    const p = await J("GET", "/api/prefs");
    if (p && p.world_clocks) { prefs = p; applyClocks(); renderQuick(); }
    return prefs;
  }
  const savePrefs = async (patch) => { const r = await J("POST", "/api/prefs", patch); if (r.ok && r.prefs) { prefs = r.prefs; applyClocks(); renderQuick(); } return r; };

  /* ── WORLD CLOCKS ─────────────────────────────────────────── */
  function applyClocks() {
    if (!prefs || typeof WORLD_CLOCKS === "undefined") return;
    WORLD_CLOCKS.splice(0, WORLD_CLOCKS.length, ...prefs.world_clocks.map(c => ({ city: c.label, tz: c.tz })));
    renderWorldClocks();
    const host = $("#world-clocks");
    if (host && !prefs.world_clocks.length) host.innerHTML = '<div class="tf-empty">no clocks: click to add one</div>';
  }
  function clocksHTML(list) {
    let zones = []; try { zones = Intl.supportedValuesOf ? Intl.supportedValuesOf("timeZone") : []; } catch (e) {}
    return `<div class="mb-list">${list.map((c, i) => `<div class="ce-row">
        <input class="ce-in ce-label" data-i="${i}" value="${esc(c.label)}" maxlength="14" aria-label="label for ${esc(c.tz)}"/>
        <span class="ce-dim ce-grow">${esc(c.tz)}</span>
        <button type="button" class="ghost-btn" data-mv="${i}:-1" ${i ? "" : "disabled"} title="move up">↑</button>
        <button type="button" class="ghost-btn" data-mv="${i}:1" ${i < list.length - 1 ? "" : "disabled"} title="move down">↓</button>
        <button type="button" class="ghost-btn" data-rm="${i}" title="remove">×</button></div>`).join("") || '<div class="tf-empty">no clocks yet</div>'}</div>
      <form class="ce-form"><input class="ce-in ce-grow" id="ce-tz" list="ce-tzlist" placeholder="add a zone, e.g. Asia/Dubai" maxlength="60" autocomplete="off"/>
        <datalist id="ce-tzlist">${zones.map(z => `<option value="${z}">`).join("")}</datalist>
        <input class="ce-in" id="ce-tzlabel" placeholder="label" maxlength="14" style="width:5.5rem"/>
        <button class="cmd-btn" type="submit">ADD</button></form>
      <div class="mb-actions"><button class="cmd-btn" type="button" data-save>SAVE LABELS</button></div>
      <div class="ce-dim">Up to ${prefs.limits.world_clocks} clocks · saved on this machine, shared by every open dashboard.</div>`;
  }
  function openClocks() {
    if (!prefs) return;
    const withLabels = () => prefs.world_clocks.map((c, i) => ({ tz: c.tz, label: (document.querySelector(`.ce-label[data-i="${i}"]`)?.value || c.label).trim() }));
    const redraw = (r) => done(r, openClocks);
    modal("WORLD CLOCKS", "EDIT · ADD · REORDER", clocksHTML(prefs.world_clocks), async (e) => {
      const rm = e.target.closest("[data-rm]"), mv = e.target.closest("[data-mv]");
      const list = withLabels();
      if (rm) { list.splice(+rm.dataset.rm, 1); redraw(await savePrefs({ world_clocks: list })); }
      else if (mv) { const [i, d] = mv.dataset.mv.split(":").map(Number); const [x] = list.splice(i, 1); list.splice(i + d, 0, x); redraw(await savePrefs({ world_clocks: list })); }
      else if (e.target.closest("[data-save]")) redraw(await savePrefs({ world_clocks: list }));
    }, async () => {
      const tz = ($("#ce-tz")?.value || "").trim(); if (!tz) return;
      redraw(await savePrefs({ world_clocks: [...withLabels(), { tz, label: ($("#ce-tzlabel")?.value || "").trim() }] }));
    });
  }
  makeInteractive($("#world-clocks"), openClocks, "World clocks: click to add, rename, reorder or remove");

  /* ── QUICK ACCESS ─────────────────────────────────────────── */
  const grid = document.querySelector(".bs-quick .quick-grid");
  const builtin = {};                                  // original buttons keep their app.js handlers
  grid?.querySelectorAll(".quick-btn[data-action]").forEach(b => { builtin[b.dataset.action] = b; });
  const EXTRA = {
    "speedtest": async () => { const r = await J("POST", "/api/network/speedtest"); logEvent(`<span class="tag">[quick]</span> ${r.ok === false ? esc(r.error || "speed test refused") : "internet speed test started"}`); },
    "health-check": async () => { const r = await J("POST", "/api/health/run"); logEvent(`<span class="tag">[quick]</span> ${r.ok === false ? esc(r.error || "self-check refused") : "self-check run"}`); try { refreshHeal(); } catch (e) {} },
    "focus-toggle": async () => { await prodAction({ action: prodSnap && prodSnap.focus_active ? "focus_stop" : "focus_start" }); },
    "refresh-weather": async () => { await refreshWeather(true); },
  };
  async function runQuick(b, fn) {
    if (b.classList.contains("busy")) return;
    b.classList.add("busy");
    try { await fn(); } catch (e) { logEvent("quick-action failed: " + esc(e.message), "error"); }
    finally { setTimeout(() => b.classList.remove("busy"), 800); }
  }
  function renderQuick() {
    if (!grid || !prefs) return;
    grid.textContent = "";
    for (const q of prefs.quick_access) {
      let b;
      if (q.action && builtin[q.action]) b = builtin[q.action];
      else {
        b = document.createElement("button"); b.type = "button"; b.className = "quick-btn ce-quick"; b.textContent = q.label;
        if (q.action) b.addEventListener("click", () => runQuick(b, EXTRA[q.action] || (async () => {})));
        else {
          b.title = "sends: " + q.command;
          b.addEventListener("click", () => runQuick(b, async () => {
            logEvent(`<span class="tag">[quick]</span> ${esc(q.command)}`);
            const r = await J("POST", "/api/command", { text: q.command, agent: null, source: "text" });
            if (!r.ok) fail("quick", { error: r.error || "command rejected" });
          }));
        }
      }
      grid.appendChild(b);
    }
    if (!prefs.quick_access.length) grid.innerHTML = '<div class="tf-empty">no buttons: use EDIT to choose commands</div>';
  }
  function openQuick() {
    if (!prefs) return;
    const q = prefs.quick_access, used = new Set(q.filter(x => x.action).map(x => x.action));
    const html = `<div class="mb-list">${q.map((x, i) => `<div class="ce-row"><span class="ce-grow">${esc(x.label)}${x.command ? `<div class="mb-row-meta">sends “${esc(x.command)}”</div>` : ""}</span>
        <button type="button" class="ghost-btn" data-mv="${i}:-1" ${i ? "" : "disabled"} title="move up">↑</button>
        <button type="button" class="ghost-btn" data-mv="${i}:1" ${i < q.length - 1 ? "" : "disabled"} title="move down">↓</button>
        <button type="button" class="ghost-btn" data-rm="${i}" title="remove">×</button></div>`).join("") || '<div class="tf-empty">no buttons yet</div>'}</div>
      <div class="ce-h">ADD A BUILT-IN ACTION</div>
      <div class="ce-chips">${prefs.quick_catalog.filter(c => !used.has(c.action)).map(c => `<button type="button" class="cmd-btn" data-add="${esc(c.action)}">+ ${esc(c.label)}</button>`).join("") || '<span class="ce-dim">all built-in actions are on the panel</span>'}</div>
      <div class="ce-h">ADD YOUR OWN COMMAND</div>
      <form class="ce-form"><input class="ce-in" id="ce-ql" placeholder="button label" maxlength="22" style="width:8rem" required/>
        <input class="ce-in ce-grow" id="ce-qc" placeholder="what to send, e.g. @stark disk usage report" maxlength="200" required/>
        <button class="cmd-btn" type="submit">ADD</button></form>
      <div class="ce-dim">A custom button sends its text through the command bar pipeline. Up to ${prefs.limits.quick_access} buttons.</div>`;
    const plain = () => prefs.quick_access.map(x => x.action ? { action: x.action } : { label: x.label, command: x.command });
    const redraw = (r) => done(r, openQuick);
    modal("QUICK ACCESS", "CHOOSE YOUR COMMANDS", html, async (e) => {
      const rm = e.target.closest("[data-rm]"), mv = e.target.closest("[data-mv]"), add = e.target.closest("[data-add]");
      const list = plain();
      if (rm) list.splice(+rm.dataset.rm, 1);
      else if (mv) { const [i, d] = mv.dataset.mv.split(":").map(Number); const [x] = list.splice(i, 1); list.splice(i + d, 0, x); }
      else if (add) list.push({ action: add.dataset.add });
      else return;
      redraw(await savePrefs({ quick_access: list }));
    }, async () => redraw(await savePrefs({ quick_access: [...plain(), { label: $("#ce-ql").value, command: $("#ce-qc").value }] })));
  }
  const qh = document.querySelector(".bs-quick .bs-head");
  if (qh) { const b = btn("✎", "Edit quick access buttons"); b.addEventListener("click", openQuick); qh.appendChild(b); }

  /* ── WEATHER LOCATION ─────────────────────────────────────── */
  function openWeatherLoc() {
    if (!prefs) return;
    const loc = prefs.weather_location;
    modal("WEATHER LOCATION", "OPEN-METEO", `<div class="mb-list"><div class="mb-row">${loc ? `Fixed to <b>${esc(loc.name)}</b> (${loc.lat}, ${loc.lon})` : "Automatic: IP geolocation, refined by browser GPS when allowed"}<div class="mb-row-meta">CURRENT</div></div></div>
      <form class="ce-form"><input class="ce-in ce-grow" id="ce-wq" placeholder="search a city or place" maxlength="60" autocomplete="off"/><button class="cmd-btn" type="submit">SEARCH</button></form>
      <div class="mb-list" id="ce-wres"></div>
      <div class="mb-actions">${loc ? '<button class="cmd-btn" type="button" data-auto>USE AUTOMATIC LOCATION</button>' : ""}</div>`,
    async (e) => {
      const pick = e.target.closest("[data-lat]");
      const after = () => { try { refreshWeather(true); } catch (x) {} openWeatherLoc(); };
      if (pick) done(await savePrefs({ weather_location: { name: pick.dataset.name, lat: +pick.dataset.lat, lon: +pick.dataset.lon } }), after);
      else if (e.target.closest("[data-auto]")) done(await savePrefs({ weather_location: null }), after);
    }, async () => {
      const q = ($("#ce-wq")?.value || "").trim(), host = $("#ce-wres"); if (!q || !host) return;
      host.innerHTML = '<div class="tf-empty">searching…</div>';
      const r = await J("GET", "/api/weather/search?q=" + encodeURIComponent(q));
      if (!live() || !$("#ce-wres")) return;
      host.innerHTML = r.ok === false ? `<div class="mb-row" style="color:var(--error)">${esc(r.error)}</div>`
        : (r.results || []).length ? r.results.map(p => `<div class="mb-row mb-click" role="button" tabindex="0" data-name="${esc(p.name)}" data-lat="${p.lat}" data-lon="${p.lon}">${esc(p.name)}<div class="mb-row-meta">${(+p.lat).toFixed(2)}, ${(+p.lon).toFixed(2)} · click to use</div></div>`).join("")
        : '<div class="tf-empty">no place found with that name</div>';
    });
  }
  const wr = $("#weather-refresh");
  if (wr) { const b = btn("⌖", "Choose the weather location"); b.addEventListener("click", (e) => { e.stopPropagation(); openWeatherLoc(); }); wr.before(b); }

  /* ── WORLD FEED SOURCES ───────────────────────────────────── */
  function openNews() {
    if (!prefs) return;
    const n = prefs.news, off = new Set(n.disabled);
    const row = (name, custom) => `<label class="ce-row"><input type="checkbox" data-src="${esc(name)}" ${off.has(name) ? "" : "checked"}/>
        <span class="ce-grow">${esc(name)}${custom ? `<div class="mb-row-meta">${esc(custom.url)}</div>` : ""}</span>
        ${custom ? `<button type="button" class="ghost-btn" data-rmfeed="${esc(name)}" title="remove feed">×</button>` : ""}</label>`;
    modal("WORLD FEED SOURCES", n.selection_applies ? "OPEN FEEDS" : "NEWSAPI ACTIVE", `
      ${n.selection_applies ? "" : '<div class="mb-row" style="color:var(--warn)">NEWSAPI_KEY is set, so headlines come from NewsAPI. This selection applies when the open feeds are in use.</div>'}
      <div class="mb-list">${n.builtin.map(s => row(s)).join("")}${n.custom.map(c => row(c.name, c)).join("")}</div>
      <div class="ce-h">ADD AN RSS / ATOM FEED</div>
      <form class="ce-form"><input class="ce-in" id="ce-fn" placeholder="name" maxlength="30" style="width:8rem" required/>
        <input class="ce-in ce-grow" id="ce-fu" placeholder="https://… feed URL" maxlength="300" required/>
        <button class="cmd-btn" type="submit">ADD</button></form>
      <div class="ce-dim">Public https feeds only, up to ${prefs.limits.feeds}. The feed refreshes right after a change.</div>`,
    async (e) => {
      const rm = e.target.closest("[data-rmfeed]");
      if (rm) { e.preventDefault(); return done(await savePrefs({ news: { custom: n.custom.filter(c => c.name !== rm.dataset.rmfeed) } }), openNews); }
      const cb = e.target.closest("input[data-src]");
      if (cb) {
        const disabled = [...document.querySelectorAll("#modal-body input[data-src]")].filter(x => !x.checked).map(x => x.dataset.src);
        if (!done(await savePrefs({ news: { disabled } }))) cb.checked = !cb.checked;
      }
    }, async () => done(await savePrefs({ news: { custom: [...n.custom, { name: $("#ce-fn").value, url: $("#ce-fu").value }] } }), openNews));
  }
  const nr = $("#news-refresh");
  if (nr) { const b = btn("⚙", "Choose world feed sources"); b.addEventListener("click", openNews); nr.before(b); }

  /* ── NOTES · edit in place, pin ───────────────────────────── */
  $("#notes-list")?.addEventListener("click", async (e) => {
    const note = e.target.closest(".note"); if (!note) return;
    const id = note.dataset.id;
    if (e.target.closest(".note-pin")) {
      const r = await J("POST", `/api/notes/${encodeURIComponent(id)}`, { pinned: !note.classList.contains("pinned") });
      if (r.ok === false) fail("notes", r);
      return refreshNotes();
    }
    if (!e.target.closest(".note-edit, .note-body") || note.querySelector(".note-in")) return;
    const body = note.querySelector(".note-body"); if (!body) return;
    const old = body.textContent;
    const inp = document.createElement("input");
    inp.className = "ce-in note-in"; inp.maxLength = 200; inp.value = old; inp.setAttribute("aria-label", "edit note");
    body.replaceWith(inp); inp.focus(); inp.select();
    let closed = false;
    const finish = async (save) => {
      if (closed) return; closed = true;
      const text = inp.value.trim();
      if (save && text && text !== old) { const r = await J("POST", `/api/notes/${encodeURIComponent(id)}`, { text }); if (r.ok === false) fail("notes", r); }
      refreshNotes();
    };
    inp.addEventListener("keydown", (k) => { if (k.key === "Enter") { k.preventDefault(); finish(true); } else if (k.key === "Escape") finish(false); });
    inp.addEventListener("blur", () => finish(true));
  });

  /* ── AGENDA · local events and tasks ──────────────────────── */
  const pad = (n) => String(n).padStart(2, "0");
  const localISO = (ts) => { const d = new Date(ts * 1000); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`; };
  const opt = (v, cur2, label) => `<option value="${v}" ${v === cur2 ? "selected" : ""}>${label || v.toUpperCase()}</option>`;
  const eventForm = (ev) => `<form class="ce-form ce-col" data-form="event">
      <label>TITLE<input class="ce-in" name="title" maxlength="120" required value="${esc(ev?.title || "")}"/></label>
      <label>STARTS<input class="ce-in" name="start" type="datetime-local" required value="${localISO(ev ? ev.start_ts : Math.ceil(Date.now() / 36e5) * 3600)}"/></label>
      <label>MINUTES<input class="ce-in" name="duration_min" type="number" min="5" max="1440" value="${ev?.duration_min || 30}"/></label>
      <label>WHERE<input class="ce-in" name="location" maxlength="80" value="${esc(ev?.location || "")}"/></label>
      <label>PRIORITY<select class="ce-in" name="priority">${opt("normal", ev?.priority || "normal")}${opt("high", ev?.priority)}</select></label>
      <div class="mb-actions"><button class="cmd-btn" type="submit">${ev ? "SAVE EVENT" : "ADD EVENT"}</button>
        ${ev ? '<button class="cmd-btn" type="button" data-del="event">DELETE</button>' : ""}</div></form>`;
  const taskForm = (t) => `<form class="ce-form ce-col" data-form="task">
      <label>TASK<input class="ce-in" name="title" maxlength="120" required value="${esc(t?.title || "")}"/></label>
      <label>PRIORITY<select class="ce-in" name="priority">${["low", "normal", "high"].map(p => opt(p, t?.priority || "normal")).join("")}</select></label>
      <label>STATUS<select class="ce-in" name="status">${[["open", "OPEN"], ["doing", "IN PROGRESS"], ["done", "DONE"]].map(([v, l]) => opt(v, t?.status || "open", l)).join("")}</select></label>
      <label>DUE<input class="ce-in" name="due" type="date" value="${esc(t?.due || "")}"/></label>
      <div class="mb-actions"><button class="cmd-btn" type="submit">${t ? "SAVE TASK" : "ADD TASK"}</button>
        ${t ? '<button class="cmd-btn" type="button" data-del="task">DELETE</button>' : ""}</div></form>`;
  const formData = (f) => Object.fromEntries([...new FormData(f).entries()].map(([k, v]) => [k, k === "duration_min" ? +v : v]));
  const agendaDone = (r) => done(r, () => { closeToolModal(); refreshAgenda(); });
  function openAgendaAdd() {
    modal("ADD TO AGENDA", "LOCAL · SAVED ON THIS MACHINE", `<div class="ce-h">NEW EVENT</div>${eventForm(null)}<div class="ce-h">NEW TASK</div>${taskForm(null)}
      <div class="ce-dim">Local items live beside Google ones. Google Calendar and Tasks stay read-only here: BUCS only holds read-only Google access.</div>`,
      null, async (e) => {
        const f = e.target, d = formData(f);
        if (f.dataset.form === "event") agendaDone(await J("POST", "/api/events", d));
        else agendaDone(await J("POST", "/api/tasks", { ...d, due: d.due || null }));
      });
  }
  function openEvent(ev) {
    modal(ev.title, "LOCAL EVENT · EDIT", eventForm(ev), async (e) => {
      if (e.target.closest("[data-del]")) agendaDone(await J("DELETE", `/api/events/${encodeURIComponent(ev.id)}`));
    }, async (e) => agendaDone(await J("POST", `/api/events/${encodeURIComponent(ev.id)}`, formData(e.target))));
  }
  function openTask(t) {
    modal(t.title, "LOCAL TASK · EDIT", taskForm(t), async (e) => {
      if (e.target.closest("[data-del]")) agendaDone(await J("DELETE", `/api/tasks/${encodeURIComponent(t.id)}`));
    }, async (e) => { const d = formData(e.target); agendaDone(await J("POST", `/api/tasks/${encodeURIComponent(t.id)}`, { ...d, due: d.due || null })); });
  }
  // capture phase: local items open their editor instead of the read-only detail
  $("#agenda-list")?.addEventListener("click", async (e) => {
    if (e.target.closest("a")) return;
    const a = (typeof lastAgenda !== "undefined" && lastAgenda) || {};
    const box = e.target.closest(".agenda-task[data-tid] .agenda-task-box");
    const tk = e.target.closest(".agenda-task[data-tid]"), evEl = e.target.closest(".agenda-item[data-eid]");
    if (!tk && !evEl) return;
    e.stopPropagation();
    if (box) { const r = await J("POST", `/api/tasks/${encodeURIComponent(tk.dataset.tid)}`, { status: "done" }); if (r.ok === false) fail("tasks", r); return refreshAgenda(); }
    if (tk) { const t = (a.tasks || []).find(x => x.id === tk.dataset.tid); if (t) openTask(t); return; }
    const ev = (a.events || []).find(x => x.id === evEl.dataset.eid); if (ev) openEvent(ev);
  }, true);
  const gb = $("#gcal-btn");
  if (gb) { const b = btn("+", "Add a local event or task"); b.id = "agenda-add"; b.addEventListener("click", openAgendaAdd); gb.before(b); }

  /* ── THREAT INCIDENT · resolve / re-open / note (buttons rendered by openIncident) ── */
  mb?.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-inc-act]"); if (!b) return;
    const id = b.dataset.incId, note = ($("#inc-note")?.value || "").trim();
    if (b.dataset.incAct === "note" && !note) return;
    b.disabled = true;
    const r = await J("POST", `/api/threats/incident/${encodeURIComponent(id)}`, { action: b.dataset.incAct, note });
    if (r.ok === false) { b.disabled = false; b.textContent = "REFUSED"; return fail("threat", r); }
    try { seedThreats(); } catch (x) {}
    openIncident(id);
  });

  /* ── DECISIONS · autonomy whitelist + dismissed proposals ─── */
  async function openDecisionSettings() {
    modal("DECISION SETTINGS", "SUPERVISED AUTONOMY", '<div class="tf-empty">reading…</div>', null, null);
    const seq = window._mSeq, d = await J("GET", "/api/decisions");
    if (seq !== window._mSeq) return;
    if (d.ok === false) { $("#modal-body").innerHTML = `<div class="mb-row" style="color:var(--error)">${esc(d.error)}</div>`; return; }
    const on = new Set(d.whitelist || []);
    modal("DECISION SETTINGS", `AUTONOMY ${d.autonomy ? "ON" : "OFF"}`, `<div class="ce-h">MAY AUTO-EXECUTE WHEN AUTONOMY IS ON</div>
      <div class="mb-list">${(d.whitelist_all || []).map(k => `<label class="ce-row"><input type="checkbox" data-wl="${esc(k)}" ${on.has(k) ? "checked" : ""}/><span class="ce-grow">${esc(k.replace(/_/g, " ").toUpperCase())}</span></label>`).join("")}</div>
      <div class="ce-dim">Only these low-risk kinds can ever run unattended. You can switch one off; nothing else can be added.</div>
      <div class="ce-h">DISMISSED IN THE LAST 2 HOURS</div>
      <div class="mb-list">${(d.dismissed || []).map(x => `<div class="ce-row"><span class="ce-grow">${esc(x.title)}<div class="mb-row-meta">dismissed ${_ago(x.ts)}</div></span>
        <button type="button" class="cmd-btn" data-restore="${esc(x.key)}">RESTORE</button></div>`).join("") || '<div class="tf-empty">nothing dismissed</div>'}</div>
      <div class="ce-dim">A restored proposal returns only if its condition still holds.</div>`,
    async (e) => {
      const cb = e.target.closest("input[data-wl]"), rs = e.target.closest("[data-restore]");
      if (cb) { if (!done(await J("POST", "/api/decisions/whitelist", { key: cb.dataset.wl, on: cb.checked }))) cb.checked = !cb.checked; refreshDecisions(); }
      else if (rs) done(await J("POST", "/api/decisions/restore", { key: rs.dataset.restore }), () => { refreshDecisions(); openDecisionSettings(); });
    }, null);
  }
  makeInteractive($("#dec-auto-note"), openDecisionSettings, "Decision settings: what may auto-execute, restore dismissed proposals");

  /* ── OPERATORS · rename / role / remove ───────────────────── */
  async function openOperators() {
    const d = await J("GET", "/api/enterprise");
    if (d.ok === false) return fail("rbac", d);
    modal("OPERATORS", "RENAME · ROLE · REMOVE", `<div class="mb-list">${(d.operators || []).map((o, i) => `<form class="ce-row ce-form" data-op="${esc(o.name)}">
        <input class="ce-in ce-grow" name="name" value="${esc(o.name)}" maxlength="40" aria-label="operator name" required/>
        <select class="ce-in" name="role" aria-label="role">${opt("commander", o.role)}${opt("observer", o.role)}</select>
        <button class="cmd-btn" type="submit">SAVE</button>
        <button class="ghost-btn" type="button" data-rmop="${esc(o.name)}" title="remove operator" ${o.name === d.operator ? "disabled" : ""}>×</button></form>`).join("")}</div>
      <div class="ce-dim">Active operator: ${esc(d.operator || "—")}. The active operator cannot be removed and one commander must remain. Every change is audited.</div>`,
    async (e) => {
      const rm = e.target.closest("[data-rmop]");
      if (rm) done(await J("DELETE", `/api/operators/${encodeURIComponent(rm.dataset.rmop)}`), () => { refreshEnterprise(); openOperators(); });
    }, async (e) => {
      const f = e.target, v = formData(f);
      done(await J("POST", `/api/operators/${encodeURIComponent(f.dataset.op)}`, v), () => { refreshEnterprise(); openOperators(); });
    });
  }
  const of = $("#ent-op-form");
  if (of) { const b = btn("✎", "Manage operators: rename, change role, remove"); b.addEventListener("click", openOperators); of.appendChild(b); }

  /* ── PRODUCTIVITY · daily deep-work goal ──────────────────── */
  function openGoal() {
    const cur2 = (typeof prodSnap !== "undefined" && prodSnap) || {};
    modal("DEEP-WORK GOAL", "OPERATOR PRODUCTIVITY", `<div class="mb-grid">${_stat("FOCUS TODAY", `${cur2.focus_min_today ?? "—"} min`)}${_stat("GOAL", `${cur2.focus_target_min ?? "—"} min`)}</div>
      <form class="ce-form"><input class="ce-in" id="ce-goal" type="number" min="15" max="720" step="5" value="${cur2.focus_target_min || 120}" aria-label="goal in minutes" required/>
        <span class="ce-dim">minutes per day</span><button class="cmd-btn" type="submit">SAVE GOAL</button></form>
      <div class="ce-dim">The focus part of the score (up to 45 points) is today's focus minutes measured against this goal.</div>`,
      null, async () => {
        const r = await J("POST", "/api/productivity", { action: "goal", minutes: +$("#ce-goal").value });
        if (r.error) return err(r.error);
        r._rx = Date.now(); renderProd(r); closeToolModal();
      });
  }
  makeInteractive($("#pc-focus")?.closest(".pc"), openGoal, "Focus minutes today: click to set your daily goal");

  /* ── follow edits made in other tabs ──────────────────────── */
  window.addEventListener("bucs:sync", (e) => {
    const t = e.detail && e.detail.topic;
    try {
      if (t === "prefs") loadPrefs().then(() => { try { refreshWeather(true); } catch (x) {} });
      else if (t === "agenda") refreshAgenda();
      else if (t === "threats") seedThreats();
    } catch (x) {}
  });
  loadPrefs();
})();
