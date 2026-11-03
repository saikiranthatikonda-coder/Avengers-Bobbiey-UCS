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
  <div class="bs-card dv-card" id="dv-card">
    <div class="bs-head">
      <div class="bs-title">DEVICES &amp; CONNECTIVITY</div>
      <div class="bs-sub">LOCAL PROTOCOLS ONLY · LAN-RESTRICTED · EVERY ACTION AUDITED · <span id="dv-stat">—</span></div>
    </div>
    <div class="dv-cols">
      <div class="dv-col">
        <div class="intel-divider">SMART DEVICES · <span id="dv-count">0</span> ADDED</div>
        <div class="dv-list" id="dv-list"><div class="tf-empty">loading…</div></div>
        <details class="fleet-add dv-add" id="dv-add">
          <summary>◆ ADD A DEVICE / INTEGRATION</summary>
          <form class="dv-form" id="dv-form" autocomplete="off">
            <select id="dv-kind" class="dv-in"></select>
            <input class="dv-in" name="name" placeholder="Display name" maxlength="60"/>
            <div id="dv-fields" class="dv-fields"></div>
            <div class="dv-note" id="dv-note"></div>
            <div class="dv-acts">
              <button type="submit" class="cmd-btn" id="dv-submit">ADD DEVICE</button>
            </div>
          </form>
        </details>
        <div class="intel-divider">LAN DISCOVERY · mDNS + SSDP · <span id="dv-disc-when">—</span>
          <button class="cmd-btn dv-mini" id="dv-scan" type="button">SCAN</button></div>
        <div class="dv-disc" id="dv-disc"><div class="tf-empty">no scan yet</div></div>
      </div>
      <div class="dv-col">
        <div class="intel-divider">NODE CONNECTIVITY · DETECTED ON THIS HOST · <span id="dv-conn-when">—</span>
          <button class="cmd-btn dv-mini" id="dv-conn-refresh" type="button">RE-CHECK</button></div>
        <div class="dv-conn" id="dv-conn"><div class="tf-empty">probing interfaces…</div></div>
      </div>
    </div>
  </div>`;

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
    const online = data.devices.filter((d) => d.status && d.status.online).length;
    document.getElementById("dv-stat").textContent = data.devices.length
      ? `${online}/${data.devices.length} ONLINE` : "NO DEVICES";
    if (!data.devices.length) {
      list.innerHTML = `<div class="dv-empty">No devices added. BUCS only shows real hardware it can reach.
        Open <b>ADD A DEVICE</b> below (Home Assistant alone covers Matter, Zigbee, Z-Wave and most brands),
        or press <b>SCAN</b> to find devices advertising on this network.</div>`;
      return;
    }
    list.innerHTML = data.devices.map((d) => {
      const st = d.status || {};
      const cls = st.online === true ? (st.auth === false ? "warn" : "ok") : st.online === false ? "fail" : "off";
      const where = d.host || d.url || "";
      return `<div class="dv-dev heal-chk ${cls}" data-id="${esc(d.id)}">
        <span class="hc-dot"></span>
        <div class="dv-dev-head"><span class="hc-name">${esc(d.name)}</span>
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
        document.getElementById("dv-add").open = false;
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
    document.getElementById("dv-disc-when").textContent = d.running ? "scanning…" : (d.at ? ago(d.at) : "—");
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
    const det = document.getElementById("dv-add");
    det.open = true;
    det.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  // ── connectivity ────────────────────────────────────────
  function renderConn() {
    const box = document.getElementById("dv-conn");
    if (!conn) return;
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
    const b = ev.target; b.disabled = true;
    document.getElementById("dv-disc-when").textContent = "scanning… (~6 s)";
    try {
      const r = await api("/api/devices/discover", {});
      if (r.ok === false) log(`Discovery: ${r.error}`, "warn");
      else { data.discovery = r; log(`LAN discovery: ${(r.items || []).length} device(s) found`); }
    } finally { b.disabled = false; renderDiscovery(); }
  });
  document.getElementById("dv-conn-refresh").addEventListener("click", async (ev) => {
    ev.target.disabled = true;
    try { conn = await api("/api/devices/connectivity?refresh=1"); renderConn(); }
    finally { ev.target.disabled = false; }
  });

  async function load() {
    try {
      const j = await api("/api/devices");
      if (!j.devices) return;
      const first = !Object.keys(data.adapters).length;
      data = j;
      if (first) renderKinds();
      // don't wipe the device list while the operator is typing into it
      const active = document.activeElement;
      if (!(active && active.closest && active.closest("#dv-list"))) renderDevices();
      renderDiscovery();
    } catch (e) {}
  }
  async function loadConn() {
    try { const j = await api("/api/devices/connectivity"); if (j.methods) { conn = j; renderConn(); } } catch (e) {}
  }

  load(); loadConn();
  setInterval(load, 30000);
  setInterval(loadConn, 120000);
})();
