/* CAMERA FULLSCREEN · adds a "⛶" button to the OPERATOR VIEW camera panel (#operator-cam).
   Puts #cam-frame (the <video> plus its HUD overlay) into fullscreen via the Fullscreen API,
   falling back to a fixed full-viewport overlay (✕ / Esc to close). Read-only towards app.js:
   it never starts or stops the camera — it only watches #cam-video's stream.
   Keys: F toggles while the camera panel is hovered/focused (ignored while typing); double-click
   the video toggles too. The button is disabled while the camera is off. */
(function () {
  "use strict";
  const root = document.getElementById("operator-cam");
  const frame = document.getElementById("cam-frame");
  const video = document.getElementById("cam-video");
  if (!root || !frame || !video || frame.dataset.camfs) return;
  frame.dataset.camfs = "1";

  // stylesheet (self-loaded so only the <script> tag is required)
  if (!document.querySelector('link[href$="/static/camfs.css"],link[href$="static/camfs.css"]')) {
    const l = document.createElement("link"); l.rel = "stylesheet"; l.href = "/static/camfs.css"; document.head.appendChild(l);
  }

  const btn = document.createElement("button");
  btn.type = "button"; btn.className = "camfs-btn"; btn.id = "camfs-btn"; btn.textContent = "⛶";
  btn.setAttribute("aria-label", "Camera full screen");
  const host = root.querySelector(".cam-chips") || root.querySelector(".cam-head") || root;
  host.appendChild(btn);

  const hud = document.createElement("div");
  hud.className = "camfs-hud"; hud.setAttribute("aria-hidden", "true");
  hud.innerHTML = `<span class="camfs-label"></span><span class="camfs-clock"></span><span class="camfs-esc">ESC to exit</span>`;
  const close = document.createElement("button");
  close.type = "button"; close.className = "camfs-close"; close.textContent = "✕"; close.setAttribute("aria-label", "Exit camera full screen");
  frame.append(hud, close);

  let clockT = 0, hovered = false;
  const isLive = () => {
    const s = video.srcObject;
    if (!s) return false;
    if (typeof s.getVideoTracks === "function") return s.active !== false && s.getVideoTracks().some(t => t.readyState === "live");
    return true;
  };
  const isFull = () => document.fullscreenElement === frame || document.webkitFullscreenElement === frame || frame.classList.contains("camfs-on");

  function label() {
    const sel = document.getElementById("cam-select");
    const opt = sel && sel.selectedOptions && sel.selectedOptions[0];
    const name = (opt && opt.value && opt.textContent.trim()) || "OPERATOR VIEW · DEFAULT CAMERA";
    const res = video.videoWidth ? ` · ${video.videoWidth}×${video.videoHeight}` : "";
    return name.slice(0, 48) + res;
  }
  function tick() {
    hud.querySelector(".camfs-label").textContent = label();
    hud.querySelector(".camfs-clock").textContent = new Date().toLocaleTimeString([], { hour12: false });
  }
  function onEnter() {
    frame.classList.add("camfs-active");
    tick(); clearInterval(clockT); clockT = setInterval(tick, 1000);
    btn.classList.add("on"); btn.setAttribute("aria-pressed", "true");
  }
  function onExit() {
    frame.classList.remove("camfs-active", "camfs-on");
    document.querySelectorAll(".camfs-host").forEach(e => e.classList.remove("camfs-host"));
    document.documentElement.classList.remove("camfs-lock");
    clearInterval(clockT); clockT = 0;
    btn.classList.remove("on"); btn.setAttribute("aria-pressed", "false");
  }

  function enter() {
    if (!isLive() || isFull()) return;
    const req = frame.requestFullscreen || frame.webkitRequestFullscreen;
    const apiOn = () => document.fullscreenElement === frame || document.webkitFullscreenElement === frame;
    const fallback = () => {
      if (apiOn() || frame.classList.contains("camfs-on")) return;
      // an ancestor with backdrop-filter/transform/contain would become the containing block of
      // position:fixed (and trap z-index): neutralise those on the ancestor chain while open
      for (let e = frame.parentElement; e && e !== document.body; e = e.parentElement) e.classList.add("camfs-host");
      frame.classList.add("camfs-on"); document.documentElement.classList.add("camfs-lock"); onEnter();
    };
    if (!req) return fallback();
    try {
      const p = req.call(frame);
      if (p && typeof p.then === "function") p.then(onEnter, fallback);
    } catch (e) { return fallback(); }
    setTimeout(fallback, 700);          // some embedded webviews ignore the request without rejecting
  }
  function exit() {
    if (document.fullscreenElement === frame && document.exitFullscreen) document.exitFullscreen().catch(() => {});
    else if (document.webkitFullscreenElement === frame && document.webkitExitFullscreen) document.webkitExitFullscreen();
    onExit();
  }
  function toggle() { isFull() ? exit() : enter(); }

  function sync() {
    const live = isLive();
    btn.disabled = !live;
    btn.title = live ? "Camera full screen (F · double-click video · Esc to exit)" : "Camera full screen — enable the camera first";
    if (!live && isFull()) exit();
  }

  btn.addEventListener("click", e => { e.stopPropagation(); toggle(); });
  close.addEventListener("click", e => { e.stopPropagation(); exit(); });
  frame.addEventListener("dblclick", e => { if (isLive() || isFull()) { e.preventDefault(); toggle(); } });
  const fsChange = () => { if (document.fullscreenElement === frame || document.webkitFullscreenElement === frame) onEnter(); else if (!frame.classList.contains("camfs-on")) onExit(); };
  document.addEventListener("fullscreenchange", fsChange);
  document.addEventListener("webkitfullscreenchange", fsChange);
  root.addEventListener("pointerenter", () => { hovered = true; });
  root.addEventListener("pointerleave", () => { hovered = false; });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && frame.classList.contains("camfs-on")) { e.preventDefault(); exit(); return; }
    if ((e.key !== "f" && e.key !== "F") || e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
    const t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    if (!(isFull() || hovered || root.contains(document.activeElement))) return;
    e.preventDefault(); toggle();
  });

  // camera on/off: app.js sets video.srcObject and toggles .live on the panel
  for (const ev of ["loadedmetadata", "play", "playing", "pause", "emptied", "ended"]) video.addEventListener(ev, sync);
  if ("MutationObserver" in window) new MutationObserver(sync).observe(root, { attributes: true, attributeFilter: ["class"] });
  setInterval(sync, 1500);              // srcObject changes fire no event of their own
  sync();

  window.CamFS = { toggle, enter, exit, get active() { return isFull(); } };
})();
