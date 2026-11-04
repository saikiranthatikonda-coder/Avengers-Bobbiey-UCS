/* ═══ PHONE AGENT · AI VOICE CALLS (phone.py) ═══
   Renders into #phone-mount. Every call, turn and number here comes from /api/phone
   (real provider webhooks). Nothing is simulated: with no provider configured the card
   says so and shows the next setup step. */
(() => {
  const mount = document.getElementById("phone-mount");
  if (!mount) return;
  const esc = (s) => (typeof escapeHTML === "function" ? escapeHTML(String(s ?? ""))
    : String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c])));
  const log = (html, kind = "info") => { try { logEvent(`<span class="tag">[phone]</span> ${html}`, kind); } catch (e) {} };
  const $p = (id) => document.getElementById(id);
  const post = (u, b) => fetch(u, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b || {}) })
    .then((r) => r.json()).catch((e) => ({ ok: false, error: e.message }));
  const getj = (u) => fetch(u, { cache: "no-store" }).then((r) => r.json());
  const t2 = (ts) => ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
  const dt = (ts) => ts ? new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
  const dur = (s) => (s == null ? "—" : s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`);
  let data = null, contacts = [], openCall = null, editing = null, timer = null, liveTimer = null, pending = null, pendTimer = null;

  mount.innerHTML = `
  <div class="bs-card ph-card" id="ph-card">
    <div class="bs-head">
      <div class="bs-title">PHONE AGENT · AI VOICE CALLS</div>
      <div class="bs-sub">STATUS: <span id="ph-state">—</span> · PROVIDER <span id="ph-prov">—</span> · AI <span id="ph-brain">—</span></div>
    </div>
    <div class="ph-grid">
      <div class="ph-left">
        <div class="intel-divider">SETUP</div>
        <div class="ph-steps" id="ph-steps"><div class="tf-empty">loading…</div></div>
        <div class="ph-next" id="ph-next"></div>
        <div class="intel-divider">PUBLIC URL · TUNNEL</div>
        <div class="ph-tunnel" id="ph-tunnel">—</div>
        <div class="ph-acts">
          <button class="cmd-btn" id="ph-tun-start" type="button" title="Start a Cloudflare quick tunnel (cloudflared) to this BUCS. Only the signed /phone/twilio webhooks are usable through it.">START TUNNEL</button>
          <button class="cmd-btn" id="ph-tun-stop" type="button">STOP TUNNEL</button>
        </div>
        <div class="intel-divider">NUMBER</div>
        <div class="ph-row"><select id="ph-number" class="ph-in" aria-label="Twilio number"></select></div>
        <div class="ph-acts">
          <button class="cmd-btn" id="ph-verify" type="button" title="Check the credentials with Twilio and list your numbers">VERIFY</button>
          <button class="cmd-btn" id="ph-point" type="button" title="Set the selected number's Voice URL and status callback to this BUCS's public URL">POINT NUMBER HERE</button>
        </div>
        <details class="ph-cfg" id="ph-cfg"><summary>PROVIDER &amp; AGENT SETTINGS</summary>
          <form id="ph-form" autocomplete="off">
            <label>Provider<select name="provider" class="ph-in" id="ph-provsel"></select></label>
            <label>Account SID<input name="account_sid" class="ph-in" placeholder="AC…" spellcheck="false" /></label>
            <label>Auth Token<input name="auth_token" type="password" class="ph-in" id="ph-token" placeholder="not set" autocomplete="new-password" spellcheck="false" /></label>
            <label>Public base URL<input name="public_base_url" class="ph-in" placeholder="https://… (or START TUNNEL)" spellcheck="false" /></label>
            <div class="ph-two"><label>Assistant name<input name="assistant_name" class="ph-in" /></label>
              <label>Owner name<input name="owner_name" class="ph-in" /></label></div>
            <div class="ph-two"><label>Voice<input name="voice" class="ph-in" placeholder="Polly.Raveena" /></label>
              <label>Language<input name="language" class="ph-in" placeholder="en-IN" /></label></div>
            <label class="ph-chk"><input type="checkbox" name="disclosure_enabled" /> Announce that the caller is speaking to an AI assistant</label>
            <label>Disclosure (inbound)<input name="disclosure_inbound" class="ph-in" /></label>
            <label>Disclosure (outbound)<input name="disclosure_outbound" class="ph-in" /></label>
            <label class="ph-chk"><input type="checkbox" name="recording" /> Record calls (announced at call start)</label>
            <label>Recording notice<input name="recording_text" class="ph-in" /></label>
            <label class="ph-chk"><input type="checkbox" name="inbound_enabled" /> Answer inbound calls with the AI agent</label>
            <label>Inbound greeting<input name="inbound_greeting" class="ph-in" /></label>
            <label>Outbound opening<input name="outbound_opening" class="ph-in" /></label>
            <label>Persona / instructions<textarea name="persona" class="ph-in" rows="3"></textarea></label>
            <label>Goodbye<input name="goodbye_text" class="ph-in" /></label>
            <label class="ph-chk"><input type="checkbox" name="handoff_enabled" /> Offer handoff: dial the owner when asked for a human</label>
            <label>Owner number (handoff)<input name="owner_number" class="ph-in" placeholder="+91…" /></label>
            <div class="ph-two"><label>Max turns<input name="max_turns" type="number" class="ph-in" /></label>
              <label>Max duration (s)<input name="max_duration_s" type="number" class="ph-in" /></label></div>
            <div class="ph-three"><label>Owner timezone<input name="owner_tz" class="ph-in" /></label>
              <label>Calls from<input name="window_start" class="ph-in" placeholder="10:00" /></label>
              <label>until<input name="window_end" class="ph-in" placeholder="19:00" /></label></div>
            <div class="ph-three"><label>Outbound / hour<input name="outbound_per_hour" type="number" class="ph-in" /></label>
              <label>/ day<input name="outbound_per_day" type="number" class="ph-in" /></label>
              <label>Gap per contact (h)<input name="per_contact_gap_h" type="number" class="ph-in" /></label></div>
            <div class="ph-note">India: commercial / telemarketing calls are regulated by TRAI (TCCCPR, DND registry). Call only people who gave consent, for what they consented to, inside the allowed hours.</div>
            <div class="ph-acts"><button class="cmd-btn" type="submit">SAVE</button>
              <button class="cmd-btn" type="button" id="ph-clear">FORGET CREDENTIALS</button></div>
            <div class="ph-msg" id="ph-form-msg"></div>
          </form>
        </details>
      </div>
      <div class="ph-mid">
        <div class="intel-divider">ACTIVE CALL</div>
        <div id="ph-live"><div class="tf-empty">no active call</div></div>
        <div class="intel-divider">CALL HISTORY</div>
        <div class="ph-list" id="ph-calls"><div class="tf-empty">no calls yet</div></div>
      </div>
      <div class="ph-right">
        <div class="intel-divider">ANALYTICS</div>
        <div class="ph-stats" id="ph-stats"></div>
        <div class="intel-divider">FOLLOW-UPS</div>
        <div class="ph-list ph-fu" id="ph-fu"><div class="tf-empty">none</div></div>
        <div class="intel-divider">CONTACTS · <span id="ph-cn">0</span></div>
        <div class="ph-list ph-contacts" id="ph-contacts"><div class="tf-empty">no contacts</div></div>
        <div id="ph-dial"></div>
        <details class="ph-cfg" id="ph-cform-wrap"><summary id="ph-cform-title">ADD CONTACT</summary>
          <form id="ph-cform" autocomplete="off">
            <div class="ph-two"><label>Name<input name="name" class="ph-in" required /></label>
              <label>Number (E.164)<input name="number" class="ph-in" placeholder="+919876543210" required /></label></div>
            <label>Notes (private; never read out)<textarea name="notes" class="ph-in" rows="2"></textarea></label>
            <label>Timezone (optional)<input name="tz" class="ph-in" placeholder="Asia/Kolkata" /></label>
            <label class="ph-chk"><input type="checkbox" name="consent" /> This person agreed to receive calls from this AI assistant</label>
            <label>Consent source<input name="consent_source" class="ph-in" placeholder="e.g. verbal at meeting, 3 Nov 2026" /></label>
            <label class="ph-chk"><input type="checkbox" name="dnc" /> Do not call</label>
            <label class="ph-chk"><input type="checkbox" name="blocked" /> Block inbound calls from this number</label>
            <div class="ph-acts"><button class="cmd-btn" type="submit">SAVE CONTACT</button>
              <button class="cmd-btn" type="button" id="ph-cnew">NEW</button></div>
            <div class="ph-msg" id="ph-cmsg"></div>
          </form>
        </details>
        <div class="intel-divider">AGENT ACTIVITY</div>
        <div class="ph-log" id="ph-log"><div class="tf-empty">idle</div></div>
      </div>
    </div>
  </div>`;

  function renderSetup(d) {
    const s = d.setup || {};
    $p("ph-state").textContent = s.ready ? "READY" : "NOT CONFIGURED";
    $p("ph-state").className = s.ready ? "ph-ok" : "ph-warn";
    const prov = (d.providers || []).find((p) => p.name === d.config.provider);
    $p("ph-prov").textContent = prov ? prov.label.toUpperCase() : "—";
    $p("ph-brain").textContent = String(d.brain || "—").toUpperCase();
    $p("ph-steps").innerHTML = (s.steps || []).map((st) => `
      <div class="sa-chk ${st.ok ? "ok" : "warn"}" title="${esc(st.detail)}"><span class="hc-dot"></span>
        <span class="sa-chk-n">${esc(st.label.toUpperCase())}</span><span class="sa-chk-d">${esc(st.detail)}</span></div>`).join("");
    $p("ph-next").textContent = s.ready ? "Ready: inbound calls to the number reach the AI agent." : `Next: ${s.next || "—"}`;
    const t = d.tunnel || {}, w = d.webhooks || {};
    $p("ph-tunnel").innerHTML = `
      <div><span>CLOUDFLARED</span><b>${t.installed ? (t.running ? "running" : "stopped") : "not installed"}</b></div>
      <div><span>URL</span><b class="ph-url">${esc(d.config.public_base_url || "—")}</b></div>
      ${t.error ? `<div><span>NOTE</span><b>${esc(t.error)}</b></div>` : ""}
      <div><span>WEBHOOKS</span><b>${w.accepted || 0} verified · ${w.rejected || 0} rejected${w.last_rejected ? ` (last: ${esc(w.last_reason)})` : ""}</b></div>`;
    const nums = d.config.numbers || [];
    $p("ph-number").innerHTML = nums.length
      ? `<option value="">— select a number —</option>` + nums.map((n) => `<option value="${esc(n.id)}"${n.id === d.config.selected_number_id ? " selected" : ""}>${esc(n.number)}${n.name ? " · " + esc(n.name) : ""}${n.voice ? "" : " (no voice)"}</option>`).join("")
      : `<option value="">${d.config.account_sid_set ? "press VERIFY to list numbers" : "not configured"}</option>`;
  }

  function fillForm(d) {
    const f = $p("ph-form");
    if (f.contains(document.activeElement)) return;          // don't clobber typing
    const c = d.config;
    $p("ph-provsel").innerHTML = (d.providers || []).map((p) => `<option value="${esc(p.name)}"${p.name === c.provider ? " selected" : ""}${p.implemented ? "" : " disabled"}>${esc(p.label)}</option>`).join("");
    for (const el of f.elements) {
      if (!el.name || el.name === "provider") continue;
      if (el.name === "auth_token") { el.value = ""; el.placeholder = c.auth_token_set ? `saved ${c.auth_token} (leave blank to keep)` : "not set"; continue; }
      if (el.type === "checkbox") el.checked = !!c[el.name];
      else el.value = c[el.name] ?? "";
    }
  }

  function renderLive(active) {
    const box = $p("ph-live");
    if (!active || !active.length) { box.innerHTML = `<div class="tf-empty">no active call</div>`; return; }
    box.innerHTML = active.map((L) => {
      const startTs = L.answered || L.created;
      return `<div class="ph-livecard ${esc(L.status)}">
        <div class="ph-lc-head">
          <span class="ph-dot ${esc(L.status)}"></span>
          <span class="ph-lc-who">${esc(L.direction === "inbound" ? "FROM" : "TO")} ${esc(L.name || (L.direction === "inbound" ? L.from : L.to))}</span>
          <span class="ph-badge">${esc(String(L.status).toUpperCase())}</span>
          <span class="ph-lc-t" data-since="${startTs}">${dur(Math.round(Date.now() / 1000 - startTs))}</span>
          <button class="cmd-btn ph-hang" type="button" data-hangup="${L.id}">HANG UP</button>
        </div>
        ${L.purpose ? `<div class="ph-purpose">${esc(L.purpose)}</div>` : ""}
        <div class="ph-transcript">${(L.turns || []).map((t) => `
          <div class="ph-turn ${t.who === "caller" ? "them" : "us"}"><span>${t.who === "caller" ? "CALLER" : "AGENT"} · ${t2(t.ts)}${t.confidence != null ? ` · ${Math.round(t.confidence * 100)}%` : ""}</span>${esc(t.text)}</div>`).join("") || `<div class="tf-empty">waiting for the call to connect…</div>`}
          ${L.thinking ? `<div class="ph-turn us thinking"><span>AGENT</span>thinking…</div>` : ""}</div>
      </div>`;
    }).join("");
    const tr = box.querySelector(".ph-transcript");
    if (tr) tr.scrollTop = tr.scrollHeight;
  }

  function renderCalls(calls) {
    $p("ph-calls").innerHTML = (calls || []).length ? calls.map((c) => `
      <div class="ph-call ${esc(c.status)}">
        <button class="ph-c-head" type="button" data-call="${c.id}" aria-expanded="${openCall === c.id}">
          <span class="ph-dir">${c.direction === "inbound" ? "IN" : "OUT"}</span>
          <span class="ph-c-who">${esc(c.contact_name || (c.direction === "inbound" ? c.from_number : c.to_number))}</span>
          <span class="ph-badge">${esc(String(c.status || "?").toUpperCase())}</span>
          <span class="ph-c-meta">${dt(c.created)} · ${dur(c.duration)} · ${c.turns || 0} turns</span>
        </button>
        ${openCall === c.id ? `<div class="ph-drawer" id="ph-drawer">loading…</div>` : ""}
      </div>`).join("") : `<div class="tf-empty">no calls yet — calls appear here only when the provider reports them</div>`;
    if (openCall != null && $p("ph-drawer")) loadDrawer(openCall);
  }

  async function loadDrawer(id) {
    const r = await getj(`/api/phone/calls/${id}`).catch(() => null);
    const el = $p("ph-drawer");
    if (!el || !r || !r.ok) { if (el) el.textContent = "could not load call"; return; }
    const c = r.call;
    const sum = c.summary ? esc(c.summary)
      : c.summary_engine === "unavailable" ? "AI summary unavailable (AI brain offline or no valid reply)."
      : c.status && ["completed", "busy", "no-answer", "failed", "canceled"].includes(c.status) ? "summary pending…" : "call in progress";
    el.innerHTML = `
      <div><b>SUMMARY</b> ${sum} ${c.summary_engine ? `<i>(${esc(c.summary_engine)})</i>` : ""}
        <button class="cmd-btn ph-mini" type="button" data-resum="${c.id}">RE-SUMMARISE</button></div>
      ${c.purpose ? `<div><b>PURPOSE</b> ${esc(c.purpose)}</div>` : ""}
      <div><b>DETAILS</b> ${esc(c.from_number)} → ${esc(c.to_number)} · ${esc(c.end_reason || "")}${c.price ? ` · cost ${esc(c.price)} ${esc(c.price_unit || "")}` : ""}${c.recorded ? " · recorded" : ""}</div>
      ${(r.followups || []).length ? `<div><b>FOLLOW-UPS</b><ul>${r.followups.map((f) => `<li>${esc(f.text)}${f.due ? ` <i>(${esc(f.due)})</i>` : ""} — ${esc(f.status)}</li>`).join("")}</ul></div>` : ""}
      <div><b>TIMELINE</b> ${(r.events || []).map((e) => `<span class="ph-ev">${t2(e.ts)} ${esc(e.status)}</span>`).join(" ")}</div>
      <div class="ph-transcript">${(r.turns || []).map((t) => `<div class="ph-turn ${t.who === "caller" ? "them" : "us"}"><span>${t.who === "caller" ? "CALLER" : "AGENT"} · ${t2(t.ts)}</span>${esc(t.text)}</div>`).join("") || `<div class="tf-empty">no transcript</div>`}</div>`;
  }

  function renderStats(a) {
    const pct = (x) => (x == null ? "—" : Math.round(x * 100) + "%");
    const bs = a.by_status || {}, bd = a.by_direction || {};
    $p("ph-stats").innerHTML = `
      <div class="ph-stat"><b>${a.total || 0}</b><span>CALLS</span></div>
      <div class="ph-stat"><b>${bd.inbound || 0}/${bd.outbound || 0}</b><span>IN / OUT</span></div>
      <div class="ph-stat"><b>${a.avg_duration_s == null ? "—" : dur(Math.round(a.avg_duration_s))}</b><span>AVG LENGTH</span></div>
      <div class="ph-stat"><b>${pct(a.answer_rate)}</b><span>ANSWER RATE</span></div>
      <div class="ph-stat"><b>${a.turns_per_call == null ? "—" : a.turns_per_call}</b><span>TURNS / CALL</span></div>
      <div class="ph-stat"><b>${a.open_followups || 0}</b><span>OPEN FOLLOW-UPS</span></div>
      <div class="ph-statline">${Object.entries(bs).map(([k, v]) => `${esc(k)} ${v}`).join(" · ") || "no calls recorded"}</div>`;
  }

  function renderFollowups(fu) {
    $p("ph-fu").innerHTML = (fu || []).length ? fu.map((f) => `
      <div class="ph-fu-i"><span>${esc(f.text)}${f.due ? ` <i>(${esc(f.due)})</i>` : ""}</span>
        <button class="cmd-btn ph-mini" type="button" data-fu="${f.id}">DONE</button></div>`).join("")
      : `<div class="tf-empty">none — follow-ups are extracted from real call summaries</div>`;
  }

  function renderContacts() {
    $p("ph-cn").textContent = contacts.length;
    $p("ph-contacts").innerHTML = contacts.length ? contacts.map((c) => `
      <div class="ph-ct">
        <div class="ph-ct-main"><b>${esc(c.name)}</b> <span>${esc(c.number)}</span>
          ${c.consent ? `<span class="ph-tag ok" title="${esc(c.consent_source)} · ${esc(dt(c.consent_ts))}">CONSENT</span>` : `<span class="ph-tag">NO CONSENT</span>`}
          ${c.dnc ? `<span class="ph-tag bad">DNC</span>` : ""}${c.blocked ? `<span class="ph-tag bad">BLOCKED</span>` : ""}</div>
        <div class="ph-ct-acts">
          <button class="cmd-btn ph-mini" type="button" data-callc="${c.id}"${c.consent && !c.dnc ? "" : " disabled"}>CALL</button>
          <button class="cmd-btn ph-mini" type="button" data-edit="${c.id}">EDIT</button>
          <button class="cmd-btn ph-mini" type="button" data-del="${c.id}">DELETE</button></div>
      </div>`).join("") : `<div class="tf-empty">no contacts — add one below (outbound calls need recorded consent)</div>`;
  }

  function renderLog(lg) {
    $p("ph-log").innerHTML = (lg || []).length ? lg.slice(0, 10).map((e) => `
      <div class="heal-ev ${esc(e.level)}"><span class="he-t">${new Date(e.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
        <span class="he-m">${esc(e.msg)}</span></div>`).join("") : `<div class="tf-empty">idle</div>`;
  }

  function render(d) {
    if (!d || !d.config) return;
    data = d;
    renderSetup(d); fillForm(d); renderLive(d.active); renderCalls(d.calls);
    renderStats(d.analytics || {}); renderFollowups(d.followups); renderLog(d.log);
    scheduleLive((d.active || []).length > 0);
  }

  async function refresh() {
    clearTimeout(timer);
    try {
      const [d, c] = await Promise.all([getj("/api/phone"), getj("/api/phone/contacts")]);
      if (c && c.ok) { contacts = c.contacts || []; renderContacts(); }
      render(d);
    } catch (e) {}
    timer = setTimeout(refresh, 15000);
  }

  function scheduleLive(on) {
    clearTimeout(liveTimer);
    if (!on) return;
    liveTimer = setTimeout(async () => {
      try {
        const r = await getj("/api/phone/live");
        renderLive(r.active);
        if (!(r.active || []).length) { refresh(); return; }       // call ended → reload history
        scheduleLive(true);
      } catch (e) { scheduleLive(true); }
    }, 1500);
  }
  setInterval(() => {                                                  // call timers tick locally
    mount.querySelectorAll(".ph-lc-t[data-since]").forEach((el) => { el.textContent = dur(Math.round(Date.now() / 1000 - Number(el.dataset.since))); });
  }, 1000);

  async function act(btn, url, body, okMsg) {
    const lab = btn.textContent; btn.disabled = true; btn.textContent = "…";
    const r = await post(url, body);
    btn.disabled = false; btn.textContent = lab;
    if (r.ok === false) log(esc(r.error || "refused"), "warn"); else if (okMsg) log(okMsg);
    return r;
  }

  $p("ph-tun-start").addEventListener("click", async (e) => {
    const r = await act(e.currentTarget, "/api/phone/tunnel/start", {}, "quick tunnel started");
    if (r.tunnel && r.tunnel.url) log(`public URL: ${esc(r.tunnel.url)}. Now press POINT NUMBER HERE`);
    refresh();
  });
  $p("ph-tun-stop").addEventListener("click", async (e) => { await act(e.currentTarget, "/api/phone/tunnel/stop", {}, "tunnel stopped"); refresh(); });
  $p("ph-verify").addEventListener("click", async (e) => {
    const r = await act(e.currentTarget, "/api/phone/verify", {});
    if (r.ok) log(`Twilio verified: ${(r.numbers || []).length} number(s)`);
    refresh();
  });
  $p("ph-point").addEventListener("click", async (e) => { await act(e.currentTarget, "/api/phone/numbers/point", {}, "number now points at this BUCS"); refresh(); });
  $p("ph-number").addEventListener("change", async (e) => {
    const r = await post("/api/phone/config", { selected_number_id: e.target.value });
    if (r.ok === false) log(esc(r.error), "warn");
    refresh();
  });

  $p("ph-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.currentTarget, body = {};
    for (const el of f.elements) {
      if (!el.name) continue;
      if (el.type === "checkbox") body[el.name] = el.checked;
      else if (el.name === "auth_token") { if (el.value.trim()) body.auth_token = el.value.trim(); }
      else if (el.name === "account_sid") { if (!el.value.includes("…")) body.account_sid = el.value.trim(); }
      else if (el.type === "number") { if (el.value !== "") body[el.name] = Number(el.value); }
      else body[el.name] = el.value;
    }
    const r = await post("/api/phone/config", body);
    $p("ph-form-msg").textContent = r.ok ? (r.changed && r.changed.length ? "saved: " + r.changed.join(", ") : "no changes") : (r.error || "refused");
    $p("ph-token").value = "";
    document.activeElement && document.activeElement.blur && document.activeElement.blur();
    refresh();
  });
  $p("ph-clear").addEventListener("click", async () => {
    if (!confirm("Forget the saved Twilio Account SID and Auth Token on this machine?")) return;
    const r = await post("/api/phone/credentials/clear", {});
    $p("ph-form-msg").textContent = r.ok ? "credentials removed" : (r.error || "refused");
    refresh();
  });

  function editContact(c) {
    editing = c ? c.id : null;
    const f = $p("ph-cform");
    f.reset();
    if (c) for (const el of f.elements) { if (!el.name) continue; if (el.type === "checkbox") el.checked = !!c[el.name]; else el.value = c[el.name] ?? ""; }
    $p("ph-cform-title").textContent = c ? `EDIT CONTACT · ${c.name}` : "ADD CONTACT";
    $p("ph-cform-wrap").open = true;
    $p("ph-cmsg").textContent = "";
  }
  $p("ph-cnew").addEventListener("click", () => editContact(null));
  $p("ph-cform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.currentTarget, body = { id: editing };
    for (const el of f.elements) { if (!el.name) continue; body[el.name] = el.type === "checkbox" ? el.checked : el.value; }
    const r = await post("/api/phone/contacts", body);
    $p("ph-cmsg").textContent = r.ok ? "saved" : (r.error || "refused");
    if (r.ok) { editing = r.contact.id; refresh(); }
  });

  function showDial(c) {
    pending = null; clearTimeout(pendTimer);
    $p("ph-dial").innerHTML = `<div class="ph-dialbox">
      <div class="intel-divider">OUTBOUND CALL · ${esc(c.name)} ${esc(c.number)}</div>
      <label>What should the agent do on this call?<textarea id="ph-purpose" class="ph-in" rows="3" placeholder="e.g. Confirm Thursday's 4 pm meeting and ask if they need the address"></textarea></label>
      <label>Opening line (optional)<input id="ph-opening" class="ph-in" placeholder="${esc((data && data.config.outbound_opening) || "")}" /></label>
      <div class="ph-acts"><button class="cmd-btn" type="button" id="ph-prep" data-cid="${c.id}">CHECK &amp; PREPARE</button>
        <button class="cmd-btn" type="button" id="ph-dcancel">CANCEL</button></div>
      <div id="ph-confirm"></div></div>`;
  }

  $p("ph-dial").addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.id === "ph-dcancel") { pending = null; $p("ph-dial").innerHTML = ""; return; }
    if (b.id === "ph-prep") {
      const r = await act(b, "/api/phone/call/prepare", { contact_id: Number(b.dataset.cid), purpose: $p("ph-purpose").value, opening: $p("ph-opening").value });
      if (!r.ok) { $p("ph-confirm").innerHTML = `<div class="ph-msg bad">${(r.problems || [r.error]).map(esc).join("<br>")}</div>`; return; }
      pending = r.confirm_token;
      const s = r.summary;
      $p("ph-confirm").innerHTML = `<div class="ph-confirm">
        <div>Call <b>${esc(s.name)}</b> (${esc(s.to)}) from ${esc(s.from)}.</div>
        <div>Consent: ${esc(s.consent)} · ${esc(dt(s.consent_ts))}</div>
        <div>AI disclosure: ${s.disclosure ? "on" : "OFF"} · Recording: ${s.recording ? "on (announced)" : "off"} · Max ${dur(s.max_duration_s)}</div>
        <div>This places a REAL phone call and may cost money.</div>
        <div class="ph-acts"><button class="cmd-btn danger" type="button" id="ph-go">CONFIRM — PLACE CALL</button>
          <span class="ph-exp" id="ph-exp">expires in ${r.expires_in}s</span></div></div>`;
      let left = r.expires_in;
      const tick = () => { left -= 1; const x = $p("ph-exp"); if (!x) return; if (left <= 0) { pending = null; x.textContent = "expired — prepare again"; const g = $p("ph-go"); if (g) g.disabled = true; return; } x.textContent = `expires in ${left}s`; pendTimer = setTimeout(tick, 1000); };
      clearTimeout(pendTimer); pendTimer = setTimeout(tick, 1000);
      return;
    }
    if (b.id === "ph-go" && pending) {
      const tok = pending; pending = null;
      const r = await act(b, "/api/phone/call/confirm", { token: tok });
      if (r.ok) { log(`outbound call placed (#${r.call_id})`); $p("ph-dial").innerHTML = ""; }
      else $p("ph-confirm").innerHTML = `<div class="ph-msg bad">${(r.problems || [r.error]).map(esc).join("<br>")}</div>`;
      refresh();
    }
  });

  mount.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-call],[data-hangup],[data-fu],[data-callc],[data-edit],[data-del],[data-resum]");
    if (!el || !mount.contains(el)) return;
    if (el.dataset.call) { const id = Number(el.dataset.call); openCall = openCall === id ? null : id; renderCalls(data.calls); return; }
    if (el.dataset.hangup) {
      if (!confirm("Hang up this call now?")) return;
      await act(el, "/api/phone/hangup", { id: Number(el.dataset.hangup) }, "hang-up sent");
      refresh(); return;
    }
    if (el.dataset.fu) { await act(el, "/api/phone/followups", { id: Number(el.dataset.fu), status: "done" }); refresh(); return; }
    if (el.dataset.callc) { const c = contacts.find((x) => x.id === Number(el.dataset.callc)); if (c) showDial(c); return; }
    if (el.dataset.edit) { editContact(contacts.find((x) => x.id === Number(el.dataset.edit))); return; }
    if (el.dataset.del) {
      const c = contacts.find((x) => x.id === Number(el.dataset.del));
      if (!c || !confirm(`Delete contact ${c.name}? Call history is kept.`)) return;
      await act(el, "/api/phone/contacts/delete", { id: c.id }); refresh(); return;
    }
    if (el.dataset.resum) { await act(el, `/api/phone/calls/${el.dataset.resum}/summarize`, {}); loadDrawer(Number(el.dataset.resum)); }
  });

  refresh();
})();
