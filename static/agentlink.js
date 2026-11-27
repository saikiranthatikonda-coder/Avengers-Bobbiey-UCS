/* AGENT LINK · v8.6 — the agents see and drive the dashboard like the operator does.
   1. SCREEN REPORT: every few seconds (only when something changed) this tab tells the server what
      it shows: each panel's title + visible text, the Global Activity Map view / mode / selection,
      theme, camera full screen, open dialog. dashboard_context.py serves it to the agents as the
      "On screen now" section, so "what does the world clocks panel say?" is answered from the screen.
   2. UI COMMANDS: the server's {"type":"ui"} messages (voice / chat: "switch the map to live
      satellite", "show Tokyo on the map", "doom theme", "camera full screen", "scroll down") run here
      through the same functions the buttons use.
   3. ASK: a small ◇ ASK chip on every panel puts "About the <panel> panel: " in the command bar.
   4. VOICE BAR under the orb: CONVERSATION · HANDS-FREE · AGENT VOICES, each a real switch.
   Nothing here sends data anywhere but this BUCS server. */
(function () {
  "use strict";
  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const log = (t, lvl) => { try { logEvent(`<span class="tag">[agents]</span> ${t}`, lvl); } catch (e) { /* app.js not ready */ } };

  // ── 1. screen report ──
  function panels() {
    const out = [], seen = new Set();
    const vh = innerHeight;
    document.querySelectorAll("section.panel, [id$='-mount']").forEach(sec => {
      if (seen.has(sec) || sec.closest("[hidden]")) return;
      seen.add(sec);
      const heads = sec.querySelectorAll(".panel-title, h2, h3");
      const r = sec.getBoundingClientRect();
      const visible = r.bottom > 0 && r.top < vh && r.width > 0;
      if (!heads.length) return;
      // a tall column holds several titled blocks: report each block under its own title
      heads.forEach((h, i) => {
        const title = (h.textContent || "").replace("◇ ASK", "").replace(/[ⓘ⚙↻⟳✕×↗⤢★◎+]+/g, " ").replace(/\s+/g, " ").trim();
        if (!title || title.length > 70) return;
        let txt = "", n = h.nextElementSibling, guard = 0;
        while (n && guard++ < 12 && !(heads[i + 1] && (n === heads[i + 1] || n.contains(heads[i + 1])))) {
          if (!n.hidden && n.offsetParent !== null) txt += " " + (n.innerText || "");
          if (txt.length > 900) break;
          n = n.nextElementSibling;
        }
        if (!txt.trim() && heads.length === 1) txt = sec.innerText || "";
        const hr = h.getBoundingClientRect();
        out.push({ title, visible: visible && hr.bottom > 0 && hr.top < vh, text: txt.replace(/\s+/g, " ").trim().slice(0, 900) });
      });
    });
    return out.slice(0, 40);
  }
  let lastSent = "", lastAt = 0;
  async function report(force) {
    if (document.hidden) return;
    let map = null;
    try { map = window.GlobalMap && window.GlobalMap.screen ? window.GlobalMap.screen() : null; } catch (e) { map = null; }
    const theme = ([...document.body.classList].find(c => c.startsWith("theme-")) || "").slice(6);
    const modal = $(".modal:not([hidden]) .modal-title, #modal:not([hidden]) #modal-title, [role=dialog]:not([hidden]) h2");
    const body = { panels: panels(), map, theme, camfs: !!(window.CamFS && window.CamFS.active),
      modal: modal ? modal.textContent.trim() : null, scroll: scrollY < 200 ? "top" : (innerHeight + scrollY >= document.body.scrollHeight - 200 ? "bottom" : "middle") };
    const key = JSON.stringify(body);
    if (!force && key === lastSent && Date.now() - lastAt < 60000) return;      // unchanged: re-send once a minute
    lastSent = key; lastAt = Date.now();
    try { await fetch("/api/dashboard/screen", { method: "POST", headers: { "content-type": "application/json" }, body: key }); } catch (e) { /* offline */ }
  }
  setInterval(report, 5000);
  setTimeout(() => report(true), 2500);
  let scrollT = 0;
  addEventListener("scroll", () => { clearTimeout(scrollT); scrollT = setTimeout(report, 900); }, { passive: true });

  // ── 2. UI commands from the agents (voice / chat) ──
  const mapEl = () => $("#globalmap-mount");
  const showMap = () => { const m = mapEl(); if (m) m.scrollIntoView({ behavior: "smooth", block: "center" }); };
  async function run(m) {
    const GM = window.GlobalMap;
    try {
      if (m.action === "map-mode" && GM && GM.setView) {
        const [view, sat] = String(m.mode || "").split(":");
        showMap(); await GM.setView(view, sat); log(`map → ${esc(m.mode)}`);
      } else if (m.action === "map-locate" && GM && GM.myLocation) {
        showMap(); const r = await GM.myLocation(); log(r ? `map → your location (${esc(r.how)})` : "map → location unavailable", r ? "" : "warn");
      } else if (m.action === "map-fly" && GM && GM.flyToPlace) {
        showMap(); const r = await GM.flyToPlace(m.q); log(r ? `map → ${esc(r.name)}, ${esc(r.country || "")}` : `map → couldn't find “${esc(m.q)}”`, r ? "" : "warn");
      } else if (/^region-(mode|metric|tour|air)$/.test(m.action || "")) {
        // v8.8 · INDIA / TELANGANA / DISASTER immersive maps (regionmap.js, disaster.js)
        const reg = m.region === "india" || m.region === "telangana" ? m.region : "disaster";
        const panel = reg === "disaster" ? $("#disaster-mount") : $(reg === "india" ? ".india-panel" : ".telangana-panel");
        if (panel) panel.scrollIntoView({ behavior: "smooth", block: "center" });
        const RM = window.RegionMaps, DM = window.DisasterMap;
        let ok = false;
        if (reg === "disaster") ok = !!(DM && (m.action === "region-mode" ? DM.mode(m.mode) : m.action === "region-tour" ? DM.tour() : false));
        else if (RM) ok = m.action === "region-mode" ? RM.mode(reg, m.mode) : m.action === "region-metric" ? RM.metric(reg, m.metric)
          : m.action === "region-tour" ? RM.tour(reg, true) : RM.air(reg, true);
        log(`${reg} map → ${esc(m.mode || m.metric || m.action.slice(7))}${ok ? "" : " (map still loading — try again in a moment)"}`, ok ? "" : "warn");
      } else if (m.action === "theme") {
        const b = document.querySelector(`.theme-btn[data-theme="${CSS.escape(m.theme || "")}"]`); if (b) b.click();
      } else if (m.action === "camfs" && window.CamFS) {
        const cam = $("#operator-cam"); if (cam) cam.scrollIntoView({ behavior: "smooth", block: "center" });
        if (m.on === false) window.CamFS.exit(); else window.CamFS.enter();
      } else if (m.action === "scroll") {
        const to = m.to;
        if (to === "top") scrollTo({ top: 0, behavior: "smooth" });
        else if (to === "bottom") scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
        else scrollBy({ top: (to === "up" ? -1 : 1) * innerHeight * 0.8, behavior: "smooth" });
      }
    } catch (e) { log(`dashboard command failed: ${esc(e.message)}`, "warn"); }
    setTimeout(() => report(true), 1800);
  }

  // ── 3. ◇ ASK on every panel ──
  function addAsk() {
    document.querySelectorAll(".panel-title").forEach(h => {
      if (h.querySelector(".ask-chip")) return;
      const b = document.createElement("button");
      b.type = "button"; b.className = "ask-chip"; b.textContent = "◇ ASK";
      b.title = "Ask an agent about this panel (it reads what the panel shows right now)";
      b.addEventListener("click", e => {
        e.stopPropagation();
        const title = h.textContent.replace("◇ ASK", "").replace(/\s+/g, " ").trim();
        const inp = $("#ask-input"); if (!inp) return;
        inp.value = `About the ${title} panel: `;
        inp.dispatchEvent(new Event("input", { bubbles: true }));
        $("#ask-form")?.scrollIntoView({ behavior: "smooth", block: "center" });
        inp.focus(); try { inp.setSelectionRange(inp.value.length, inp.value.length); } catch (x) { /* textarea */ }
        report(true);
      });
      h.appendChild(b);
    });
  }
  setTimeout(addAsk, 1500); setInterval(addAsk, 15000);

  // ── 4. voice bar: CONVERSATION · HANDS-FREE · AGENT VOICES ──
  const bar = document.createElement("div");
  bar.className = "vmode"; bar.id = "vmode"; bar.setAttribute("role", "group"); bar.setAttribute("aria-label", "Voice modes");
  bar.innerHTML = `<button type="button" data-vm="conv" aria-pressed="true" title="Conversation ON: agents listen to the room. OFF: only “Hey Jarvis…” at the start of a sentence reaches them (talk with colleagues freely)">CONVERSATION <b>—</b></button>
    <button type="button" data-vm="hf" aria-pressed="true" title="Hands-free: just talk, no wake word (needs Conversation ON). OFF: say an agent's name">HANDS-FREE <b>—</b></button>
    <button type="button" data-vm="mute" aria-pressed="false" title="Mute every agent's voice: replies stay on screen">AGENT VOICES <b>—</b></button>
    <span class="vmode-note" id="vmode-note" aria-live="polite"></span>`;
  const wrap = $(".orb-wrap");
  if (wrap && wrap.parentNode) wrap.parentNode.insertBefore(bar, wrap.nextSibling);
  function paintVoice() {
    const C = window.ConversationMode || {}, hf = C.hf || {};
    let A = null; try { A = AudioCtl; } catch (e) { A = null; }
    const on = C.on !== false, hfOn = hf.enabled !== false, muted = !!(A && A.muted);
    const set = (k, val, txt) => { const b = bar.querySelector(`[data-vm="${k}"]`); if (!b) return; b.setAttribute("aria-pressed", String(val)); b.classList.toggle("on", val); b.querySelector("b").textContent = txt; };
    set("conv", on, on ? "ON" : "OFF");
    set("hf", on && hfOn, hfOn ? (on ? (hf.asleep ? "PAUSED" : "ON") : "ON · idle") : "OFF");
    set("mute", !muted, muted ? "MUTED" : "ON");
    const who = String(hf.agent || "jarvis").toUpperCase();
    const note = $("#vmode-note");
    if (note) note.innerHTML = !on ? "agents ignore room talk · start with “Hey Jarvis…”"
      : hfOn ? (hf.asleep ? `paused · say a name · <button type="button" class="hf-link" data-hf-wake>RESUME</button>` : `talking to <b>${esc(who)}</b> · name another agent to switch`)
      : "say an agent's name to ask";
  }
  bar.addEventListener("click", async e => {
    const b = e.target.closest("[data-vm]"); if (!b) return;
    const C = window.ConversationMode;
    if (b.dataset.vm === "conv" && C) await C.toggle();
    else if (b.dataset.vm === "hf" && C) {
      const next = !(C.hf && C.hf.enabled !== false);
      const d = await C.patch({ handsfree: next });
      if (d && d.ok !== false) log(`hands-free ${next ? "ON — just talk" : "OFF — say an agent's name"}`);
    } else if (b.dataset.vm === "mute") { try { await AudioCtl.toggleMute(); } catch (x) { /* no audio control */ } }
    paintVoice();
  });
  setInterval(paintVoice, 1500); setTimeout(paintVoice, 800);

  window.AgentLink = { run, report, paintVoice };
})();
