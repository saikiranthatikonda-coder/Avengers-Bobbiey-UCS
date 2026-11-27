/* VOICE DICTATION · v8.5 — a microphone inside the command bar, like ChatGPT's dictation.
   Tap the mic (or Alt+M), speak, tap ✓ (or Enter): the words land in the box as editable text,
   then ▶ / Enter sends them to the agent picked on the left (AUTO lets the team route it).
   Engines:
     · LOCAL (default when installed) — Whisper on this PC via POST /api/dictate. Private:
       audio goes only to this BUCS server, is transcribed in memory and never stored.
       Auto-detects ~99 languages; can translate to English.
     · BROWSER — the browser's speech service (live words while you talk). Audio is sent to
       the browser vendor's cloud (Google for Chrome, Microsoft for Edge); labelled as such.
   The microphone is open only between start and ✓/✕, and while it is, the hands-free voice
   loop is told to ignore what it hears (POST /api/dictate/hold) so nothing gets answered twice. */
(function () {
  "use strict";
  const $ = s => document.querySelector(s);
  const form = $("#ask-form"), input = $("#ask-input");
  if (!form || !input) return;
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pref = (k, v) => { try { if (v === undefined) return JSON.parse(localStorage.getItem("dict." + k)); localStorage.setItem("dict." + k, JSON.stringify(v)); } catch (e) { return null; } };
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const MAX_S = 240;
  const FALLBACK_LANGS = [["auto", "Auto-detect"], ["en", "English"], ["hi", "Hindi · हिन्दी"], ["te", "Telugu · తెలుగు"], ["ta", "Tamil · தமிழ்"],
    ["es", "Spanish · Español"], ["fr", "French · Français"], ["de", "German · Deutsch"], ["ar", "Arabic · العربية"], ["zh", "Chinese · 中文"], ["ja", "Japanese · 日本語"]];
  const BCP = { en: "en-US", hi: "hi-IN", te: "te-IN", ta: "ta-IN", kn: "kn-IN", ml: "ml-IN", mr: "mr-IN", bn: "bn-IN", gu: "gu-IN", pa: "pa-IN",
    ur: "ur-PK", es: "es-ES", fr: "fr-FR", de: "de-DE", it: "it-IT", pt: "pt-BR", nl: "nl-NL", ru: "ru-RU", uk: "uk-UA", pl: "pl-PL", tr: "tr-TR",
    ar: "ar-SA", fa: "fa-IR", he: "he-IL", zh: "zh-CN", ja: "ja-JP", ko: "ko-KR", vi: "vi-VN", th: "th-TH", id: "id-ID", ms: "ms-MY", sw: "sw-KE" };
  const MIC_SVG = `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2Z"/></svg>`;

  // ── build the UI around the existing input ──
  const field = document.createElement("div"); field.className = "ask-field";
  input.parentNode.insertBefore(field, input); field.appendChild(input);
  field.insertAdjacentHTML("beforeend", `
    <button type="button" class="dict-mic" id="dict-mic" aria-label="Dictate: speak instead of typing (Alt+M)" title="Dictate: speak instead of typing (Alt+M)" aria-pressed="false">${MIC_SVG}</button>
    <button type="button" class="dict-opt" id="dict-opt" aria-label="Dictation settings" aria-haspopup="dialog" aria-expanded="false" title="Dictation language and engine">▾</button>
    <div class="dict-rec" id="dict-rec" hidden role="status" aria-live="polite">
      <span class="dict-dot" aria-hidden="true"></span>
      <canvas class="dict-wave" id="dict-wave" aria-hidden="true"></canvas>
      <span class="dict-live" id="dict-live"></span>
      <span class="dict-time" id="dict-time">0:00</span>
      <button type="button" class="dict-x" id="dict-cancel" aria-label="Cancel dictation (Esc)" title="Cancel (Esc)">✕</button>
      <button type="button" class="dict-ok" id="dict-done" aria-label="Finish and insert text (Enter)" title="Done (Enter)">✓</button>
    </div>`);
  const sel = document.createElement("select");
  sel.id = "ask-agent"; sel.className = "ask-agent"; sel.setAttribute("aria-label", "Send to agent");
  sel.title = "Which agent receives this. AUTO lets the team route it; @name in the text also works.";
  sel.innerHTML = `<option value="">AUTO</option>`;
  form.insertBefore(sel, field);
  const pop = document.createElement("div");
  pop.className = "dict-pop"; pop.id = "dict-pop"; pop.hidden = true; pop.setAttribute("role", "dialog"); pop.setAttribute("aria-label", "Dictation settings");
  form.parentNode.insertBefore(pop, form.nextSibling);
  const note = document.createElement("div");
  note.className = "dict-note"; note.id = "dict-note"; note.hidden = true; note.setAttribute("role", "status");
  form.parentNode.insertBefore(note, pop);

  const mic = $("#dict-mic"), opt = $("#dict-opt"), rec = $("#dict-rec"), wave = $("#dict-wave"),
    live = $("#dict-live"), tEl = $("#dict-time");

  // ── agent picker mirrors the roster cards (rebuilt on every snapshot) ──
  function syncAgents() {
    const cards = [...document.querySelectorAll("#avengers-list .agent-card[data-agent]")];
    if (!cards.length) return;
    const want = cards.map(c => [c.dataset.agent, (c.querySelector(".agent-name") || {}).textContent || c.dataset.agent]);
    const have = [...sel.options].slice(1).map(o => o.value).join();
    if (have === want.map(w => w[0]).join()) return;
    const cur = sel.value;
    sel.innerHTML = `<option value="">AUTO</option>` + want.map(([k, n]) => `<option value="${esc(k)}">${esc(String(n).toUpperCase())}</option>`).join("");
    sel.value = want.some(w => w[0] === cur) ? cur : "";
  }
  const list = $("#avengers-list");
  if (list) new MutationObserver(syncAgents).observe(list, { childList: true });
  setTimeout(syncAgents, 1200);
  sel.value = "";
  sel.addEventListener("change", () => { input.placeholder = sel.value ? `message ${sel.options[sel.selectedIndex].text}…  (type or 🎙 speak)` : "address the team…   (type, or 🎙 speak · try @stark or @widow)"; });
  input.placeholder = "address the team…   (type, or 🎙 speak · try @stark or @widow)";

  // ── engine status ──
  let status = null;
  async function loadStatus() {
    try { const r = await fetch("/api/dictate/status", { cache: "no-store" }); status = r.ok ? await r.json() : null; } catch (e) { status = null; }
    return status;
  }
  loadStatus();
  const engine = () => {
    const p = pref("engine");
    if (p === "browser" && SR) return "browser";
    if (p === "local" && status && status.available) return "local";
    if (status && status.available) return "local";
    return SR ? "browser" : (status ? "none" : "local");
  };
  const langs = () => (status && status.available && status.languages ? status.languages.map(l => [l.code, l.name]) : FALLBACK_LANGS);

  function showNote(html, kind, ms) {
    note.innerHTML = html; note.className = "dict-note" + (kind ? " " + kind : ""); note.hidden = false;
    clearTimeout(showNote.t); if (ms !== 0) showNote.t = setTimeout(() => { note.hidden = true; }, ms || 5000);
  }

  // ── settings popover ──
  function renderPop() {
    const e = engine(), L = pref("lang") || "auto";
    const localOk = status && status.available;
    pop.innerHTML = `<div class="dp-h">🎙 DICTATION</div>
      <label class="dp-row"><span>Language</span><select id="dp-lang">${langs().map(([c, n]) => `<option value="${esc(c)}"${c === L ? " selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
      <fieldset class="dp-eng"><legend>Engine</legend>
        <label><input type="radio" name="dp-eng" value="local"${e === "local" ? " checked" : ""}${localOk ? "" : " disabled"}/>
          <span><b>Local · private</b><small>${localOk ? `Whisper (${esc(status.model)}) on this PC · audio never leaves it` : esc((status && status.reason) || "local engine unavailable")}</small></span></label>
        <label><input type="radio" name="dp-eng" value="browser"${e === "browser" ? " checked" : ""}${SR ? "" : " disabled"}/>
          <span><b>Browser · live words</b><small>${SR ? "shows text as you speak · audio goes to your browser's speech service (Google / Microsoft cloud)" : "not supported in this browser"}</small></span></label>
      </fieldset>
      <label class="dp-chk"><input type="checkbox" id="dp-tr"${pref("translate") ? " checked" : ""}${localOk && e === "local" && status.translate ? "" : " disabled"}/> Translate to English (local engine)</label>
      <label class="dp-chk"><input type="checkbox" id="dp-send"${pref("autosend") ? " checked" : ""}/> Send immediately after dictation</label>
      <p class="dp-foot">Alt+M start / stop · Enter finish · Esc cancel · the text stays editable before you send</p>`;
  }
  function togglePop(on) {
    on = on == null ? pop.hidden : on;
    if (on) { renderPop(); loadStatus().then(() => { if (!pop.hidden) renderPop(); }); }
    pop.hidden = !on; opt.setAttribute("aria-expanded", String(on));
  }
  opt.addEventListener("click", e => { e.preventDefault(); togglePop(); });
  pop.addEventListener("change", e => {
    const t = e.target;
    if (t.id === "dp-lang") pref("lang", t.value);
    else if (t.name === "dp-eng") { pref("engine", t.value); renderPop(); }
    else if (t.id === "dp-tr") pref("translate", t.checked);
    else if (t.id === "dp-send") pref("autosend", t.checked);
  });
  document.addEventListener("click", e => { if (!pop.hidden && !pop.contains(e.target) && e.target !== opt) togglePop(false); });

  // ── recording ──
  let S = null;            // active session
  const hold = on => fetch("/api/dictate/hold", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ on }) }).catch(() => {});
  const WORKLET = "class R extends AudioWorkletProcessor{process(i){const c=i[0]&&i[0][0];if(c)this.port.postMessage(c.slice(0));return true}}registerProcessor('bucs-dict',R);";

  function setUI(state) {           // idle | rec | busy
    form.classList.toggle("dict-on", state !== "idle");
    rec.hidden = state === "idle";
    rec.classList.toggle("busy", state === "busy");
    mic.classList.toggle("on", state === "rec");
    mic.setAttribute("aria-pressed", String(state === "rec"));
    mic.disabled = state === "busy";
    $("#dict-done").disabled = state === "busy";
    if (state === "busy") { live.textContent = "transcribing…"; tEl.textContent = ""; }
    if (state === "idle") { live.textContent = ""; tEl.textContent = "0:00"; }
  }

  async function start() {
    if (S) return;
    togglePop(false);
    note.hidden = true;
    if (!status) await loadStatus();
    const eng = engine();
    if (eng === "none") { showNote("Dictation isn't available: the local speech engine isn't installed and this browser has no speech recognition.", "bad", 8000); return; }
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      showNote("The microphone needs a secure page: open BUCS at <b>http://localhost</b> (or over https) to dictate.", "bad", 8000); return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch (e) {
      const n = e && e.name;
      showNote(n === "NotAllowedError" || n === "SecurityError"
        ? "Microphone access is blocked. Allow it with the 🔒 / site-settings icon next to the address bar, then tap 🎙 again."
        : n === "NotFoundError" ? "No microphone found. Plug one in or check your sound settings." : `Microphone error: ${esc(e && e.message || e)}`, "bad", 9000);
      return;
    }
    hold(true);
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const src = ctx.createMediaStreamSource(stream);
    const an = ctx.createAnalyser(); an.fftSize = 1024; src.connect(an);
    S = { eng, stream, ctx, an, chunks: [], rate: ctx.sampleRate, t0: Date.now(), caret: [input.selectionStart ?? input.value.length, input.selectionEnd ?? input.value.length],
      base: input.value, finalText: "", interim: "", rec: null, node: null, raf: 0, timer: 0 };
    if (eng === "local") {
      try {
        const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
        await ctx.audioWorklet.addModule(url); URL.revokeObjectURL(url);
        const node = new AudioWorkletNode(ctx, "bucs-dict");
        node.port.onmessage = ev => { if (S) S.chunks.push(ev.data); };
        src.connect(node); S.node = node;
      } catch (e) {                                             // older engines: ScriptProcessor
        const node = ctx.createScriptProcessor(4096, 1, 1);
        node.onaudioprocess = ev => { if (S) S.chunks.push(new Float32Array(ev.inputBuffer.getChannelData(0))); };
        const mute = ctx.createGain(); mute.gain.value = 0;
        src.connect(node); node.connect(mute); mute.connect(ctx.destination); S.node = node;
      }
    } else {
      const r = new SR();
      const L = pref("lang") || "auto";
      r.lang = L === "auto" ? (navigator.language || "en-US") : (BCP[L] || L);
      r.continuous = true; r.interimResults = true;
      r.onresult = ev => {
        let fin = "", tmp = "";
        for (let i = 0; i < ev.results.length; i++) (ev.results[i].isFinal ? (fin += ev.results[i][0].transcript) : (tmp += ev.results[i][0].transcript));
        if (S) { S.finalText = fin; S.interim = tmp; live.classList.toggle("hint", !(fin + tmp).trim()); live.textContent = (fin + " " + tmp).trim() || "listening…"; }
      };
      r.onerror = ev => { if (S && ev.error !== "no-speech" && ev.error !== "aborted") { showNote(`Browser speech service: ${esc(ev.error)}${ev.error === "network" ? " (it needs an internet connection; the local engine doesn't)" : ""}`, "bad", 8000); stop(false); } };
      r.onend = () => { if (S && S.rec === r && !S.stopping) { try { r.start(); } catch (e) { /* already restarting */ } } };
      try { r.start(); } catch (e) { /* surfaced by onerror */ }
      S.rec = r;
    }
    setUI("rec");
    live.classList.add("hint");
    live.textContent = eng === "local" ? "listening… speak naturally" : "listening…";
    draw();
    S.timer = setInterval(() => {
      if (!S) return;
      const s = Math.floor((Date.now() - S.t0) / 1000);
      tEl.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
      if (s >= MAX_S) stop(true);
    }, 250);
  }

  function draw() {
    if (!S || !S.an) return;
    const c = wave, g = c.getContext("2d"), dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    if (c.width !== Math.round(w * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
    const buf = new Uint8Array(S.an.fftSize); S.an.getByteTimeDomainData(buf);
    g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
    const bars = Math.max(8, Math.floor(w / 4)), step = Math.floor(buf.length / bars);
    g.fillStyle = getComputedStyle(form).getPropertyValue("--accent") || "#00d9ff";
    for (let i = 0; i < bars; i++) {
      let peak = 0; for (let j = 0; j < step; j++) peak = Math.max(peak, Math.abs(buf[i * step + j] - 128));
      const bh = Math.max(2, (peak / 128) * h * 1.6);
      g.fillRect(i * 4, (h - Math.min(h, bh)) / 2, 2, Math.min(h, bh));
    }
    S.raf = requestAnimationFrame(draw);
  }

  function release() {
    if (!S) return;
    cancelAnimationFrame(S.raf); clearInterval(S.timer);
    try { S.stream.getTracks().forEach(t => t.stop()); } catch (e) { /* gone */ }
    try { if (S.node) S.node.disconnect(); } catch (e) { /* gone */ }
    try { S.ctx.close(); } catch (e) { /* gone */ }
    if (S.rec) { S.stopping = true; try { S.rec.stop(); } catch (e) { /* stopped */ } }
  }

  function encodeWav(chunks, rate) {
    let n = 0; for (const c of chunks) n += c.length;
    const ratio = rate / 16000, outN = Math.floor(n / ratio);
    const all = new Float32Array(n); let o = 0; for (const c of chunks) { all.set(c, o); o += c.length; }
    const pcm = new Int16Array(outN);
    for (let i = 0; i < outN; i++) {                           // box-filter downsample to 16 kHz
      const a = Math.floor(i * ratio), b = Math.min(n, Math.floor((i + 1) * ratio)) || a + 1;
      let s = 0; for (let j = a; j < b; j++) s += all[j];
      const v = Math.max(-1, Math.min(1, s / Math.max(1, b - a)));
      pcm[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
    }
    const buf = new ArrayBuffer(44 + pcm.length * 2), d = new DataView(buf);
    const str = (off, t) => { for (let i = 0; i < t.length; i++) d.setUint8(off + i, t.charCodeAt(i)); };
    str(0, "RIFF"); d.setUint32(4, 36 + pcm.length * 2, true); str(8, "WAVE"); str(12, "fmt ");
    d.setUint32(16, 16, true); d.setUint16(20, 1, true); d.setUint16(22, 1, true); d.setUint32(24, 16000, true);
    d.setUint32(28, 32000, true); d.setUint16(32, 2, true); d.setUint16(34, 16, true); str(36, "data"); d.setUint32(40, pcm.length * 2, true);
    new Int16Array(buf, 44).set(pcm);
    return new Blob([buf], { type: "audio/wav" });
  }

  function insert(text, res) {
    text = (text || "").trim();
    if (!text) return false;
    let base = S ? S.base : input.value, [a, b] = S ? S.caret : [input.value.length, input.value.length];
    if (res && res.agent && !base.trim()) {                    // "Doom, scan my network" → DOOM + text
      if ([...sel.options].some(o => o.value === res.agent)) { sel.value = res.agent; sel.dispatchEvent(new Event("change")); text = (res.agent_text || "").trim() || text; }
    }
    const before = base.slice(0, a), after = base.slice(b);
    const pad1 = before && !/\s$/.test(before) ? " " : "", pad2 = after && !/^\s/.test(after) ? " " : "";
    input.value = before + pad1 + text + pad2 + after;
    const pos = (before + pad1 + text).length;
    input.focus(); try { input.setSelectionRange(pos, pos); } catch (e) { /* not focusable */ }
    input.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  }

  async function stop(commit) {
    if (!S) return;
    const s = S;
    release();
    if (!commit) { S = null; setUI("idle"); hold(false); input.focus(); return; }
    const secs = (Date.now() - s.t0) / 1000;
    if (s.eng === "browser") {
      const text = (s.finalText + " " + s.interim).trim();
      const ok = insert(text, null);
      S = null; setUI("idle"); hold(false);
      if (!ok) showNote("Didn't catch anything. Tap 🎙 and try again.", "", 4000);
      else { showNote(`🎙 ${secs.toFixed(1)} s · browser speech service · edit if needed, then press Enter`, "ok", 4500); if (pref("autosend")) form.requestSubmit(); }
      return;
    }
    if (secs < 0.4 || !s.chunks.length) { S = null; setUI("idle"); hold(false); showNote("Too short. Hold on a moment longer next time.", "", 3500); return; }
    setUI("busy");
    try {
      const wav = encodeWav(s.chunks, s.rate);
      const L = pref("lang") || "auto", task = pref("translate") ? "translate" : "transcribe";
      const r = await fetch(`/api/dictate?lang=${encodeURIComponent(L)}&task=${task}`, {
        method: "POST", headers: { "content-type": "audio/wav", "x-dictate-context": encodeURIComponent(s.base.slice(-300)) }, body: wav });
      let d = null; try { d = await r.json(); } catch (e) { d = { ok: false, error: `HTTP ${r.status}` }; }
      if (!d.ok) throw Object.assign(new Error(d.error || `HTTP ${r.status}`), { fallback: d.fallback });
      const ok = insert(d.text, d);
      if (!ok) showNote(esc(d.note || "No speech recognised. Try again a little closer to the microphone."), "", 5000);
      else {
        const lang = d.language_name ? `${esc(d.language_name)}${d.language_probability != null && (pref("lang") || "auto") === "auto" ? ` (${Math.round(d.language_probability * 100)}%)` : ""}` : "";
        showNote(`🎙 ${[lang, d.translated ? "translated to English" : "", `${d.duration} s`, `${(d.took_ms / 1000).toFixed(1)} s to transcribe`, "local Whisper"].filter(Boolean).join(" · ")} · edit if needed, then press Enter`, "ok", 6000);
        if (pref("autosend")) form.requestSubmit();
      }
    } catch (e) {
      showNote(`Dictation failed: ${esc(e.message)}${e.fallback === "browser" && SR ? " · you can switch to the Browser engine in ▾" : ""}`, "bad", 9000);
    } finally { S = null; setUI("idle"); hold(false); }
  }

  mic.addEventListener("click", e => { e.preventDefault(); S ? stop(true) : start(); });
  $("#dict-done").addEventListener("click", e => { e.preventDefault(); stop(true); });
  $("#dict-cancel").addEventListener("click", e => { e.preventDefault(); stop(false); });
  document.addEventListener("keydown", e => {
    if (e.altKey && !e.ctrlKey && !e.metaKey && (e.key === "m" || e.key === "M" || e.code === "KeyM")) { e.preventDefault(); S ? stop(true) : start(); return; }
    if (!S) { if (e.key === "Escape" && !pop.hidden) togglePop(false); return; }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); stop(false); }
    else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); stop(true); }
  }, true);
  window.addEventListener("pagehide", () => { if (S) { release(); S = null; hold(false); } });

  window.BucsDictation = { start, stop, status: () => status, engine };
})();
