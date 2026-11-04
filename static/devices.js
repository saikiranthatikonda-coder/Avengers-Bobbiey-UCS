/* DEVICES & CONNECTIVITY: renders into #devices-mount.
   Data: GET /api/devices (devices, adapters, discovery), GET /api/devices/connectivity,
   GET /api/devices/{id}/entities; actions POST /api/devices, /hue/pair, /discover,
   /{id}/control, /{id}/remove. Every value shown comes from those endpoints. */
(function () {
  "use strict";
  const mount = document.getElementById("devices-mount");
  if (!mount) return;

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c]));
  const log = (msg, kind) => { try { logEvent(esc(msg), kind || "info"); } catch (e) {} };
  const ST_LABEL = { connected: "CONNECTED", available: "AVAILABLE", not_installed: "NOT INSTALLED",
                     down: "DOWN", unknown: "UNKNOWN" };
  const ST_CLASS = { connected: "ok", available: "warn", not_installed: "off", down: "fail", unknown: "off" };

  let data = { devices: [], adapters: {}, discovery: { items: [] }, commander: false };
  let conn = null;
  const haCache = {};           // device id -> entity list (loaded on demand)

  mount.innerHTML = `
  <div class="bs-card dv-card ft-card" id="dv-card">
    <div class="ft-head">
      <div class="ft-head-txt">
        <div class="ft-kicker">LOCAL PROTOCOLS ONLY · LAN-RESTRICTED · EVERY ACTION AUDITED</div>
        <div class="bs-title">DEVICES &amp; CONNECTIVITY</div>
        <div class="bs-sub" id="dv-stat">loading…</div>
      </div>
      <div class="ft-actions">
        <button class="cmd-btn" id="dv-scan" type="button" title="Find devices advertising on this network (mDNS + SSDP, ~6 s)">⌕ SCAN LAN</button>
        <button class="cmd-btn" type="button" data-goto="add">+ ADD DEVICE</button>
      </div>
    </div>
    <div class="ft-stats" id="dv-tiles"><div class="ft-skel"></div><div class="ft-skel"></div><div class="ft-skel"></div><div class="ft-skel"></div></div>
    <div class="ft-tabs" role="tablist" id="dv-tabs">
      <button type="button" role="tab" data-tab="devices" class="on">MY DEVICES · <span id="dv-count">0</span></button>
      <button type="button" role="tab" data-tab="disc">LAN DISCOVERY · <span id="dv-disc-n">0</span></button>
      <button type="button" role="tab" data-tab="conn">NODE CONNECTIVITY</button>
      <button type="button" role="tab" data-tab="add">ADD DEVICE</button>
    </div>
    <div class="ft-pane" data-pane="devices">
      <div class="dv-list" id="dv-list"><div class="tf-empty">loading…</div></div>
    </div>
    <div class="ft-pane" data-pane="disc" hidden>
      <div class="dv-pane-head">mDNS + SSDP · <span id="dv-disc-when">—</span></div>
      <div class="dv-disc" id="dv-disc"><div class="tf-empty">no scan yet · press SCAN LAN</div></div>
    </div>
    <div class="ft-pane" data-pane="conn" hidden>
      <div class="dv-pane-head">DETECTED ON THIS HOST · <span id="dv-conn-when">—</span>
        <button class="ft-link" id="dv-conn-refresh" type="button">RE-CHECK ↻</button></div>
      <div class="dv-conn" id="dv-conn"><div class="tf-empty">probing interfaces…</div></div>
    </div>
    <div class="ft-pane" data-pane="add" hidden>
      <form class="dv-form" id="dv-form" autocomplete="off">
        <select id="dv-kind" class="dv-in" aria-label="Integration type"></select>
        <input class="dv-in" name="name" placeholder="Display name" maxlength="60"/>
        <div id="dv-fields" class="dv-fields"></div>
        <div class="dv-note" id="dv-note"></div>
        <div class="dv-acts">
          <button type="submit" class="cmd-btn" id="dv-submit">ADD DEVICE</button>
        </div>
      </form>
    </div>
  </div>`;

  function showTab(t) {
    mount.querySelectorAll("#dv-tabs [data-tab]").forEach(b => { b.classList.toggle("on", b.dataset.tab === t); b.setAttribute("aria-selected", b.dataset.tab === t); });
    mount.querySelectorAll(".ft-pane").forEach(p => { p.hidden = p.dataset.pane !== t; });
  }
  function renderTiles() {
    const devs = data.devices || [], online = devs.filter(d => d.status && d.status.online).length;
    const disc = (data.discovery || {}).items || [];
    const linked = conn ? conn.methods.filter(m => m.status === "connected").length : null;
    const t = (tab, n, lab, cls, tip) => `<button type="button" class="ft-stat${cls ? " " + cls : ""}" data-goto="${tab}" title="${esc(tip)}"><b>${n}</b><span>${lab}</span></button>`;
    document.getElementById("dv-tiles").innerHTML =
      t("devices", devs.length, "DEVICES", "", "Devices you added") +
      t("devices", devs.length ? `${online}/${devs.length}` : "—", "ONLINE", online ? "ok" : "", "Reachable on the last status check") +
      t("disc", disc.length, "FOUND ON LAN", "", (data.discovery || {}).at ? "From the last LAN scan" : "No scan yet") +
      t("conn", linked == null ? "…" : `${linked}/${conn.methods.length}`, "LINK METHODS UP", linked ? "ok" : "", "Ways this node can reach your other machines") +
      (conn && conn.fleet ? t("conn", `${conn.fleet.online}/${conn.fleet.nodes}`, "FLEET ONLINE", conn.fleet.online ? "ok" : "", "Fleet nodes reporting") : "");
    const sub = [];
    sub.push(devs.length ? `${online} of ${devs.length} device${devs.length === 1 ? "" : "s"} online` : "No devices added yet");
    if ((data.discovery || {}).at) sub.push(`${disc.length} found on the last LAN scan (${ago(data.discovery.at)})`);
    if (conn) sub.push(`${linked} link method${linked === 1 ? "" : "s"} available`);
    document.getElementById("dv-stat").textContent = sub.join(" · ");
    document.getElementById("dv-disc-n").textContent = disc.length;
  }
  function openDevice(d) {
    const st = d.status || {};
    const html = `<div class="mb-grid">
        <div class="mb-stat"><span class="lbl">STATUS</span><span class="val">${st.online === true ? "ONLINE" : st.online === false ? "OFFLINE" : "UNKNOWN"}</span></div>
        <div class="mb-stat"><span class="lbl">INTEGRATION</span><span class="val">${esc(d.label)}</span></div>
        <div class="mb-stat"><span class="lbl">ADDRESS</span><span class="val">${esc(d.host || d.url || "—")}</span></div>
        <div class="mb-stat"><span class="lbl">CONTROL</span><span class="val">${d.control ? "YES · audited" : "READ-ONLY"}</span></div>
        <div class="mb-stat"><span class="lbl">LAST CHECK</span><span class="val">${st.checked ? esc(ago(st.checked)) : "—"}</span></div>
        ${d.token ? `<div class="mb-stat"><span class="lbl">CREDENTIAL</span><span class="val">${esc(d.token)}</span></div>` : ""}
      </div>
      <div class="mb-list">${st.detail ? `<div class="mb-row">${esc(st.detail)}</div>` : ""}
        <div class="mb-row">Controls for this device are on its row in MY DEVICES. Removing it only forgets it in BUCS; the device itself is not changed.</div></div>
      <div class="mb-actions"><button class="cmd-btn" type="button" data-dv-remove="${esc(d.id)}">REMOVE FROM BUCS</button></div>
      <div class="si-src"><span class="lbl">DATA SOURCE</span> devices.py adapter status check <code>/api/devices</code> · read ${new Date().toLocaleTimeString()}</div>`;
    if (typeof openCustomModal === "function") openCustomModal(d.name, "DEVICE · " + String(d.label || d.kind).toUpperCase(), null, html);
  }

  async function api(path, body) {
    const opt = body === undefined ? {} : { method: "POST", headers: { "content-type": "application/json" },
                                             body: JSON.stringify(body) };
    const r = await fetch(path, opt);
    let j = {};
    try { j = await r.json(); } catch (e) {}
    if (!r.ok && j.ok === undefined) j.ok = false;
    return j;
  }

  function ago(ts) {
    if (!ts) return "—";
    const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    return s < 60 ? s + "s ago" : Math.round(s / 60) + "m ago";
  }

  // ── devices ─────────────────────────────────────────────
  function btn(act, label, extra) {
    return `<button type="button" class="cmd-btn dv-mini" data-act="${esc(act)}"${extra || ""}>${esc(label)}</button>`;
  }
  function trio(params) {
    const p = esc(JSON.stringify(params));
    return ["on", "off", "toggle"].map((a) => btn(a, a.toUpperCase(), ` data-p="${p}"`)).join("");
  }

  function controlsFor(d) {
    const st = d.status || {};
    if (!d.control) return `<div class="dv-ro">read-only presence</div>`;
    switch (d.kind) {
      case "homeassistant": {
        const ents = haCache[d.id];
        let h = btn("entities", ents ? "RELOAD ENTITIES" : "LOAD ENTITIES");
        if (ents) {
          h += `<input class="dv-in dv-filter" data-filter="${esc(d.id)}" placeholder="filter ${ents.length} entities…"/>`;
          h += `<div class="dv-ents" data-ents="${esc(d.id)}">` + ents.slice(0, 300).map((e) =>
            `<div class="dv-ent" data-name="${esc((e.name + " " + e.entity_id).toLowerCase())}">
               <span class="dv-ent-n" title="${esc(e.entity_id)}">${esc(e.name)}</span>
               <span class="dv-ent-s">${esc(e.state)}</span>
               ${btn("ha", "TOGGLE", ` data-p="${esc(JSON.stringify({ entity_id: e.entity_id, service: haService(e.entity_id) }))}"`)}
             </div>`).join("") + `</div>`;
        }
        return h;
      }
      case "mqtt":
        return `<div class="dv-mqtt"><input class="dv-in" data-mq="topic" placeholder="topic e.g. home/lamp/set" maxlength="200"/>
                <input class="dv-in" data-mq="payload" placeholder="payload" maxlength="4096"/>
                ${btn("mq-publish", "PUBLISH")}${btn("mq-read", "READ ONE")}</div><div class="dv-out" data-out="${esc(d.id)}"></div>`;
      case "hue":
        if (!d.has_token) return btn("hue-pair", "PAIR (PRESS LINK BUTTON FIRST)");
        return (st.lights || []).map((l) => `<div class="dv-ent"><span class="dv-ent-n">${esc(l.name)}</span>
            <span class="dv-ent-s">${l.reachable === false ? "unreachable" : (l.on ? "ON" : "off")}</span>
            ${trio({ light: l.id })}</div>`).join("") || `<div class="dv-ro">no lights reported</div>`;
      case "shelly": case "tasmota":
        return (st.channels && st.channels.length ? st.channels : [{ ch: d.kind === "tasmota" ? 1 : 0 }]).map((c) =>
          `<div class="dv-ent"><span class="dv-ent-n">CH ${esc(c.ch)}</span><span class="dv-ent-s">${c.on === undefined ? "—" : (c.on ? "ON" : "off")}</span>
           ${trio({ channel: c.ch })}</div>`).join("");
      case "esphome":
        return (st.entities || []).map((e) => `<div class="dv-ent"><span class="dv-ent-n">${esc(e.entity)}</span>
            <span class="dv-ent-s">${esc(e.state)}</span>${trio({ entity: e.entity })}</div>`).join("") || `<div class="dv-ro">no entities</div>`;
      case "wled": return trio({});
      case "http": return btn("on", "ON", ` data-p="{}"`) + btn("off", "OFF", ` data-p="{}"`);
    }
    return "";
  }

  function haService(eid) {
    const dom = eid.split(".")[0];
    if (dom === "scene" || dom === "script") return "turn_on";
    if (dom === "button") return "press";
    if (dom === "lock") return "lock";
    return "toggle";
  }

  function renderDevices() {
    const list = document.getElementById("dv-list");
    document.getElementById("dv-count").textContent = data.devices.length;
    renderTiles();
    if (!data.devices.length) {
      list.innerHTML = `<div class="dv-empty">
        <div class="dv-empty-t">No devices yet</div>
        <div>BUCS only lists real hardware it can reach. Home Assistant alone covers Matter, Zigbee, Z-Wave and most brands.</div>
        <div class="dv-empty-a"><button type="button" class="cmd-btn" data-goto="add">+ ADD A DEVICE</button>
          <button type="button" class="cmd-btn" data-act-scan="1">⌕ SCAN THIS NETWORK</button></div></div>`;
      return;
    }
    list.innerHTML = data.devices.map((d) => {
      const st = d.status || {};
      const cls = st.online === true ? (st.auth === false ? "warn" : "ok") : st.online === false ? "fail" : "off";
      const where = d.host || d.url || "";
      return `<div class="dv-dev heal-chk ${cls}" data-id="${esc(d.id)}">
        <span class="hc-dot"></span>
        <div class="dv-dev-head"><button type="button" class="hc-name dv-name" data-dv-open="${esc(d.id)}" title="Device detail">${esc(d.name)}</button>
          <span class="dv-kind">${esc(d.label)} · ${esc(where)}${d.token ? " · token " + esc(d.token) : ""}</span>
          <button type="button" class="dv-x" data-act="remove" title="remove device">✕</button></div>
        <div class="hc-detail" title="${esc(st.detail)}">${esc(st.online === false ? "OFFLINE · " : "")}${esc(st.detail || "")}${st.checked ? " · " + ago(st.checked) : ""}</div>
        <div class="dv-ctl">${controlsFor(d)}</div>
      </div>`;
    }).join("");
  }

  // ── add form ────────────────────────────────────────────
  function renderKinds() {
    const sel = document.getElementById("dv-kind");
    const cur = sel.value;
    sel.innerHTML = Object.entries(data.adapters).map(([k, a]) =>
      `<option value="${esc(k)}">${esc(a.label)}${a.control ? "" : " (read-only)"}</option>`).join("");
    if (cur && data.adapters[cur]) sel.value = cur;
    renderFields();
  }
  function renderFields(prefill) {
    const k = document.getElementById("dv-kind").value;
    const a = data.adapters[k];
    if (!a) return;
    prefill = prefill || {};
    document.getElementById("dv-fields").innerHTML = a.fields.map((f) =>
      `<input class="dv-in" name="${esc(f.key)}" type="${f.type === "password" ? "password" : f.type === "number" ? "number" : "text"}"
         placeholder="${esc(f.label)}${f.required ? " *" : ""}" value="${esc(prefill[f.key] || "")}" maxlength="2000"/>`).join("");
    document.getElementById("dv-note").textContent = a.note || "";
    document.getElementById("dv-submit").textContent = a.pair ? "PAIR BRIDGE" : "ADD DEVICE";
  }

  async function submitAdd(ev) {
    ev.preventDefault();
    const form = ev.target;
    const kind = document.getElementById("dv-kind").value;
    const body = { kind };
    form.querySelectorAll("input[name]").forEach((i) => { if (i.value.trim()) body[i.name] = i.value.trim(); });
    const b = document.getElementById("dv-submit");
    b.disabled = true;
    try {
      const r = kind === "hue" ? await api("/api/devices/hue/pair", body) : await api("/api/devices", body);
      if (r.ok) {
        log(`Device added: ${r.device.name}`);
        form.querySelectorAll("input[name]").forEach((i) => { i.value = ""; });
        showTab("devices");
        await load();
      } else {
        log(`Add device: ${r.error || "failed"}`, "warn");
        document.getElementById("dv-note").textContent = r.error || "failed";
      }
    } finally { b.disabled = false; }
  }

  // ── discovery ───────────────────────────────────────────
  function renderDiscovery() {
    const d = data.discovery || {};
    document.getElementById("dv-disc-when").textContent = d.running ? "scanning…" : (d.at ? "last scan " + ago(d.at) : "no scan yet");
    renderTiles();
    const box = document.getElementById("dv-disc");
    const items = d.items || [];
    if (!items.length) {
      box.innerHTML = `<div class="tf-empty">${esc(d.at ? (d.note || "nothing found") : "no scan yet · press SCAN")}</div>`;
      return;
    }
    box.innerHTML = items.map((it, i) => `<div class="dv-found">
        <span class="dv-badge">${esc(it.via)}</span>
        <span class="dv-found-n" title="${esc(it.service)}">${esc(it.name)}</span>
        <span class="dv-kind">${esc(it.label)} · ${esc(it.host)}${it.port ? ":" + esc(it.port) : ""}${it.model ? " · " + esc(it.model) : ""}</span>
        ${it.added ? `<span class="dv-ro">added</span>` : it.addable ? btn("disc-add", "ADD", ` data-i="${i}"`) :
          `<span class="dv-ro">${it.kind === "homekit" ? "pair via Home Assistant" : "info only"}</span>`}
      </div>`).join("");
  }

  function prefillFromDiscovery(it) {
    const sel = document.getElementById("dv-kind");
    sel.value = it.kind;
    const pre = { host: it.host };
    if (it.kind === "homeassistant") pre.url = `http://${it.host}:${it.port || 8123}`;
    if (it.kind === "http") pre.url = `http://${it.host}${it.port && it.port !== 80 ? ":" + it.port : ""}`;
    if ((it.kind === "mqtt" || it.kind === "dlna") && it.port) pre.port = it.port;
    renderFields(pre);
    document.querySelector("#dv-form input[name=name]").value = it.name || "";
    showTab("add");
    document.getElementById("dv-form").scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  // ── connectivity ────────────────────────────────────────
  function renderConn() {
    const box = document.getElementById("dv-conn");
    if (!conn) return;
    renderTiles();
    document.getElementById("dv-conn-when").textContent = ago(conn.at) +
      (conn.fleet ? ` · FLEET ${conn.fleet.online}/${conn.fleet.nodes} ONLINE` : "");
    box.innerHTML = conn.methods.map((m) => `<details class="dv-link heal-chk ${ST_CLASS[m.status] || "off"}">
        <summary><span class="hc-dot"></span><span class="hc-name">${esc(m.name)}</span>
          <span class="dv-st">${esc(ST_LABEL[m.status] || m.status)}</span>
          <span class="hc-detail" title="${esc(m.detail)}">${esc(m.detail)}</span></summary>
        <div class="dv-steps">
          ${m.url ? `<div>Node URL: <code class="fleet-cmd">${esc(m.url)}</code></div>` : ""}
          ${(m.steps || []).map((s) => `<div class="dv-step">› ${esc(s)}</div>`).join("")}
        </div>
      </details>`).join("");
  }

  // ── actions (event delegation) ──────────────────────────
  mount.addEventListener("click", (ev) => {
    const g = ev.target.closest("[data-goto], [data-tab]");
    if (g) { showTab(g.dataset.goto || g.dataset.tab); return; }
    const o = ev.target.closest("[data-dv-open]");
    if (o) { const d = data.devices.find(x => x.id === o.dataset.dvOpen); if (d) openDevice(d); return; }
    if (ev.target.closest("[data-act-scan]")) { showTab("disc"); document.getElementById("dv-scan").click(); }
  });
  document.addEventListener("click", async (ev) => {
    const r = ev.target.closest("#tool-modal [data-dv-remove]");
    if (!r) return;
    const dev = data.devices.find(x => x.id === r.dataset.dvRemove); if (!dev) return;
    if (!confirm(`Remove ${dev.name} from BUCS? (the device itself is not changed)`)) return;
    r.disabled = true;
    const res = await api(`/api/devices/${encodeURIComponent(dev.id)}/remove`, {});
    log(res.ok ? `Device removed: ${dev.name}` : `Remove: ${res.error}`, res.ok ? "info" : "warn");
    try { closeToolModal(); } catch (e) {}
    load();
  });

  mount.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "disc-add") return prefillFromDiscovery((data.discovery.items || [])[+b.dataset.i]);
    const card = b.closest("[data-id]");
    const id = card && card.dataset.id;
    const dev = data.devices.find((d) => d.id === id);
    b.disabled = true;
    try {
      if (act === "remove") {
        if (!confirm(`Remove ${dev.name} from BUCS? (the device itself is not changed)`)) return;
        const r = await api(`/api/devices/${encodeURIComponent(id)}/remove`, {});
        log(r.ok ? `Device removed: ${dev.name}` : `Remove: ${r.error}`, r.ok ? "info" : "warn");
        return load();
      }
      if (act === "entities") {
        const r = await api(`/api/devices/${encodeURIComponent(id)}/entities`);
        if (r.error) log(`Home Assistant: ${r.error}`, "warn");
        haCache[id] = r.items || [];
        return renderDevices();
      }
      if (act === "hue-pair") {
        const r = await api("/api/devices/hue/pair", { host: dev.host, id: dev.id, name: dev.name });
        log(r.ok ? "Hue bridge paired" : `Hue: ${r.error}`, r.ok ? "info" : "warn");
        return load();
      }
      let action = act, params = {};
      if (b.dataset.p) params = JSON.parse(b.dataset.p);
      if (act === "ha") action = params.service;
      if (act === "mq-publish" || act === "mq-read") {
        action = act === "mq-publish" ? "publish" : "read";
        card.querySelectorAll("[data-mq]").forEach((i) => { params[i.dataset.mq] = i.value; });
      }
      const r = await api(`/api/devices/${encodeURIComponent(id)}/control`, { action, params });
      const what = params.entity_id || params.topic || params.entity || (params.light ? "light " + params.light : "") ||
                   (params.channel !== undefined ? "ch " + params.channel : "");
      log(`${dev.name}: ${action} ${what} → ${r.ok ? "OK" : (r.error || "failed")}`, r.ok ? "info" : "warn");
      if (r.device) {
        const i = data.devices.findIndex((d) => d.id === id);
        if (i >= 0) data.devices[i] = r.device;
        renderDevices();
      }
      if (act === "mq-read" || act === "mq-publish") {
        const out = mount.querySelector(`[data-out="${CSS.escape(id)}"]`);
        if (out) out.textContent = !r.ok ? (r.error || "failed") : act === "mq-publish" ? `published to ${r.published}` :
          r.message ? `${r.message.topic}: ${r.message.payload}` : "no message within 3 s";
      }
    } catch (e) {
      log(`Device action failed: ${e.message || e}`, "warn");
    } finally { b.disabled = false; }
  });

  mount.addEventListener("input", (ev) => {
    const f = ev.target.closest("[data-filter]");
    if (!f) return;
    const q = f.value.trim().toLowerCase();
    const box = mount.querySelector(`[data-ents="${CSS.escape(f.dataset.filter)}"]`);
    if (box) box.querySelectorAll(".dv-ent").forEach((r) => { r.hidden = q && !r.dataset.name.includes(q); });
  });

  document.getElementById("dv-kind").addEventListener("change", () => renderFields());
  document.getElementById("dv-form").addEventListener("submit", submitAdd);
  document.getElementById("dv-scan").addEventListener("click", async (ev) => {
    const b = document.getElementById("dv-scan"); b.disabled = true; b.textContent = "SCANNING…";
    showTab("disc");
    document.getElementById("dv-disc-when").textContent = "scanning… (~6 s)";
    try {
      const r = await api("/api/devices/discover", {});
      if (r.ok === false) log(`Discovery: ${r.error}`, "warn");
      else { data.discovery = r; log(`LAN discovery: ${(r.items || []).length} device(s) found`); }
    } catch (e) { log("Discovery failed: server unreachable", "warn"); }
    finally { b.disabled = false; b.textContent = "⌕ SCAN LAN"; renderDiscovery(); }
  });
  document.getElementById("dv-conn-refresh").addEventListener("click", async (ev) => {
    const b = ev.currentTarget; b.disabled = true; b.textContent = "CHECKING…";
    try { const j = await api("/api/devices/connectivity?refresh=1"); if (j.methods) { conn = j; renderConn(); } }
    finally { b.disabled = false; b.textContent = "RE-CHECK ↻"; }
  });

  async function load() {
    try {
      const j = await api("/api/devices");
      if (!j.devices) throw new Error(j.error || "no data");
      document.getElementById("dv-card").classList.remove("is-error");
      const first = !Object.keys(data.adapters).length;
      data = j;
      if (first) renderKinds();
      // don't wipe the device list while the operator is typing into it
      const active = document.activeElement;
      if (!(active && active.closest && active.closest("#dv-list"))) renderDevices();
      renderDiscovery();
    } catch (e) {
      document.getElementById("dv-card").classList.add("is-error");
      document.getElementById("dv-stat").textContent = "Devices service unavailable · retrying every 30 s";
      if (!data.devices.length) document.getElementById("dv-list").innerHTML = `<div class="tf-empty">could not reach /api/devices</div>`;
    }
  }
  async function loadConn() {
    try { const j = await api("/api/devices/connectivity"); if (j.methods) { conn = j; renderConn(); } } catch (e) {}
  }

  load(); loadConn();
  setInterval(load, 30000);
  setInterval(loadConn, 120000);
})();
