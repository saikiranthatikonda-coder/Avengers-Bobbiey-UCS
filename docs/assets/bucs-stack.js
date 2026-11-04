/* BUCS product showcase: the console as a holographic stack of layers that separates as you
   scroll (sticky on wide screens), with gentle pointer parallax. Panel artwork is drawn
   procedurally on canvases (no external images). Shares the lazy three.js r128 loader with
   the hero, pauses off-screen and in background tabs, adapts pixel ratio, renders one
   exploded still frame under prefers-reduced-motion, and keeps a CSS stack without WebGL. */
(() => {
  "use strict";
  const stage = document.getElementById("sc-stage"), track = document.getElementById("sc-track");
  const items = Array.from(document.querySelectorAll("#sc-layers li"));
  if (!stage || !track) return;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const mobile = matchMedia("(max-width: 760px)").matches;
  const THREE_URL = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js";

  const LAYERS = [
    { k: "APPROVALS", c: "#ffb35c" },
    { k: "AI CORE", c: "#8b7cf8" },
    { k: "GLOBAL ACTIVITY MAP", c: "#00d9ff" },
    { k: "AGENT ROSTER", c: "#50fa7b" },
    { k: "COMMAND HUD", c: "#00d9ff" },
  ]; // bottom → top in 3D; the list in the page reads top → bottom

  /* scroll progress, shared by the 3D scene and the layer list (works without WebGL too) */
  let progress = reduced ? 1 : 0;
  function measure() {
    if (reduced) return 1;
    const sticky = getComputedStyle(track).getPropertyValue("--sticky").trim() === "1";
    if (sticky) {
      const r = track.getBoundingClientRect(), span = Math.max(1, r.height - innerHeight);
      return Math.max(0, Math.min(1, -r.top / span));
    }
    const r = stage.getBoundingClientRect();
    return Math.max(0, Math.min(1, (innerHeight * 0.85 - r.top) / (innerHeight * 0.85)));
  }
  function activeIndex(p) { return Math.min(LAYERS.length - 1, Math.floor(p * LAYERS.length * 0.999)); }
  function syncList(p) {
    const a = LAYERS.length - 1 - activeIndex(p);           // list order is top → bottom
    items.forEach((li, i) => li.classList.toggle("on", i === a));
  }
  const onScroll = () => { progress = measure(); syncList(progress); };
  addEventListener("scroll", onScroll, { passive: true });
  addEventListener("resize", onScroll);
  onScroll();

  function webglOK() {
    try { const c = document.createElement("canvas"); return !!(window.WebGLRenderingContext && (c.getContext("webgl") || c.getContext("experimental-webgl"))); }
    catch (e) { return false; }
  }
  if (!webglOK()) { stage.classList.add("no-gl"); return; }
  const loadThree = window.__bucsThree || (() => new Promise((ok, bad) => {
    if (window.THREE) return ok(window.THREE);
    const s = document.createElement("script"); s.src = THREE_URL; s.async = true; s.crossOrigin = "anonymous";
    s.onload = () => ok(window.THREE); s.onerror = () => bad(new Error("three failed")); document.head.appendChild(s);
  }));

  const near = new IntersectionObserver((es) => {
    if (!es.some((e) => e.isIntersecting)) return;
    near.disconnect();
    const fonts = document.fonts && document.fonts.load ? Promise.all([
      document.fonts.load("700 40px Orbitron"), document.fonts.load("500 30px Rajdhani"), document.fonts.load("24px 'Share Tech Mono'"),
    ]).catch(() => null) : Promise.resolve();
    Promise.all([loadThree(), fonts]).then(([THREE]) => init(THREE)).catch(() => stage.classList.add("no-gl"));
  }, { rootMargin: "400px" });
  near.observe(stage);

  /* ─── panel artwork ─── */
  const PW = 1024, PH = 640;
  function rr(g, x, y, w, h, r) { g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r); g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath(); }
  function base(g, title, col) {
    const grad = g.createLinearGradient(0, 0, PW, PH);
    grad.addColorStop(0, "rgba(10,30,48,0.92)"); grad.addColorStop(1, "rgba(3,8,16,0.94)");
    rr(g, 6, 6, PW - 12, PH - 12, 34); g.fillStyle = grad; g.fill();
    g.lineWidth = 3; g.strokeStyle = col; g.globalAlpha = 0.55; g.stroke(); g.globalAlpha = 1;
    g.fillStyle = col; g.font = "700 30px Orbitron, sans-serif"; g.fillText(title, 52, 78);
    g.fillStyle = "rgba(126,150,179,0.9)"; g.font = "22px 'Share Tech Mono', monospace"; g.fillText("BUCS", PW - 120, 78);
    g.fillStyle = "rgba(0,217,255,0.14)"; g.fillRect(52, 104, PW - 104, 2);
  }
  function card(g, x, y, w, h) { rr(g, x, y, w, h, 18); g.fillStyle = "rgba(0,0,0,0.38)"; g.fill(); g.lineWidth = 2; g.strokeStyle = "rgba(0,217,255,0.18)"; g.stroke(); }
  function label(g, t, x, y, col) { g.fillStyle = col || "rgba(126,150,179,0.95)"; g.font = "700 17px Orbitron, sans-serif"; g.fillText(t, x, y); }
  const DRAW = {
    "COMMAND HUD"(g, col) {
      base(g, "COMMAND HUD", col);
      card(g, 52, 136, 440, 220); label(g, "TELEMETRY", 76, 174);
      [["CPU", 0.42], ["MEMORY", 0.61], ["NETWORK", 0.28]].forEach(([n, v], i) => {
        const y = 214 + i * 46; g.fillStyle = "#dbeeff"; g.font = "500 24px Rajdhani, sans-serif"; g.fillText(n, 76, y);
        rr(g, 210, y - 16, 250, 14, 7); g.fillStyle = "rgba(0,217,255,0.12)"; g.fill();
        rr(g, 210, y - 16, 250 * v, 14, 7); g.fillStyle = col; g.fill();
      });
      card(g, 520, 136, 452, 220); label(g, "15-MIN FORECAST", 544, 174);
      g.beginPath(); for (let i = 0; i <= 40; i++) { const x = 544 + i * 10.2, y = 300 - Math.sin(i / 5) * 40 - i * 1.1; i ? g.lineTo(x, y) : g.moveTo(x, y); }
      g.lineWidth = 4; g.strokeStyle = col; g.stroke();
      g.setLineDash([8, 8]); g.beginPath(); g.moveTo(544, 330); g.lineTo(952, 330); g.strokeStyle = "rgba(255,179,92,0.6)"; g.lineWidth = 2; g.stroke(); g.setLineDash([]);
      card(g, 52, 380, 920, 200); label(g, "AGENDA & ALERTS", 76, 418);
      ["Briefing ready · 3 items need you", "Network stable · no anomalies", "Next meeting in 25 min"].forEach((t, i) => {
        g.fillStyle = i ? "#a9c4dd" : "#dbeeff"; g.font = "500 26px Rajdhani, sans-serif"; g.fillText(t, 100, 466 + i * 40);
        g.beginPath(); g.arc(82, 457 + i * 40, 6, 0, 7); g.fillStyle = i ? "rgba(80,250,123,0.9)" : col; g.fill();
      });
    },
    "AGENT ROSTER"(g, col) {
      base(g, "AGENT ROSTER", col);
      const A = [["JARVIS", "#00d9ff"], ["CAPTAIN", "#3b82f6"], ["STARK", "#ff8a3d"], ["BLACK WIDOW", "#ff6b6b"], ["HAWKEYE", "#a78bfa"], ["HULK", "#22c55e"], ["THOR", "#facc15"], ["VISION", "#e879f9"]];
      A.forEach(([n, c], i) => {
        const x = 52 + (i % 2) * 468, y = 136 + Math.floor(i / 2) * 112;
        card(g, x, y, 452, 96);
        g.beginPath(); g.arc(x + 40, y + 48, 11, 0, 7); g.fillStyle = c; g.shadowColor = c; g.shadowBlur = 18; g.fill(); g.shadowBlur = 0;
        g.fillStyle = "#dbeeff"; g.font = "700 22px Orbitron, sans-serif"; g.fillText(n, x + 70, y + 56);
      });
    },
    "GLOBAL ACTIVITY MAP"(g, col) {
      base(g, "GLOBAL ACTIVITY MAP", col);
      card(g, 52, 136, 920, 444);
      const land = window.__bucsLandCanvas;
      const px = land ? land.getContext("2d").getImageData(0, 0, land.width, land.height).data : null;
      for (let yy = 0; yy < 40; yy++) for (let xx = 0; xx < 88; xx++) {
        const lon = xx / 88 * 360 - 180, lat = 80 - yy / 40 * 140;
        let on;
        if (px) on = px[(Math.floor((90 - lat) / 180 * land.height) * land.width + Math.floor((lon + 180) / 360 * land.width)) * 4] > 128;
        else on = Math.sin(xx * 0.21 + 1) * Math.cos(yy * 0.3) + Math.sin(xx * 0.09 + yy * 0.12) > 0.55;
        g.beginPath(); g.arc(80 + xx * 10.2, 168 + yy * 10, on ? 3 : 1.4, 0, 7);
        g.fillStyle = on ? "rgba(127,233,255,0.75)" : "rgba(0,217,255,0.10)"; g.fill();
      }
      [[0.86, 0.33, "#ff6b6b"], [0.72, 0.45, "#00d9ff"], [0.16, 0.36, "#e879f9"], [0.5, 0.2, "#00d9ff"], [0.3, 0.78, "#ffb35c"]].forEach(([u, v, c]) => {
        const x = 80 + u * 890, y = 168 + v * 400; g.beginPath(); g.arc(x, y, 9, 0, 7); g.fillStyle = c; g.shadowColor = c; g.shadowBlur = 22; g.fill(); g.shadowBlur = 0;
        g.beginPath(); g.arc(x, y, 22, 0, 7); g.strokeStyle = c; g.globalAlpha = 0.5; g.lineWidth = 2; g.stroke(); g.globalAlpha = 1;
      });
      g.beginPath(); g.moveTo(80 + 0.72 * 890, 168 + 0.45 * 400); g.quadraticCurveTo(560, 120, 80 + 0.5 * 890, 168 + 0.2 * 400);
      g.strokeStyle = col; g.lineWidth = 3; g.stroke();
    },
    "AI CORE"(g, col) {
      base(g, "AI CORE", col);
      const box = (x, y, w, t, c) => { card(g, x, y, w, 80); g.strokeStyle = c; g.lineWidth = 2; rr(g, x, y, w, 80, 18); g.stroke(); g.fillStyle = c; g.font = "700 20px Orbitron, sans-serif"; g.fillText(t, x + 24, y + 50); };
      box(52, 290, 230, "REQUEST", "#a9c4dd"); box(390, 290, 240, "ROUTER", "#00d9ff");
      box(742, 170, 230, "LOCAL AI", "#50fa7b"); box(742, 290, 230, "CLOUD AI", col); box(742, 410, 230, "TOOLS", "#ffb35c");
      g.lineWidth = 3; g.setLineDash([10, 10]); g.strokeStyle = "rgba(0,217,255,0.7)";
      [[282, 330, 390, 330], [630, 320, 742, 210], [630, 330, 742, 330], [630, 340, 742, 450]].forEach(([a, b, c, d]) => { g.beginPath(); g.moveTo(a, b); g.lineTo(c, d); g.stroke(); });
      g.setLineDash([]);
      ["Retrieval memory", "Agent & workflow builder", "Plugin marketplace"].forEach((t, i) => {
        const x = 52 + i * 310; rr(g, x, 530, 290, 48, 24); g.strokeStyle = "rgba(0,217,255,0.35)"; g.lineWidth = 2; g.stroke();
        g.fillStyle = "#dbeeff"; g.font = "22px 'Share Tech Mono', monospace"; g.fillText(t, x + 22, 562);
      });
    },
    APPROVALS(g, col) {
      base(g, "APPROVALS", col);
      const ask = (y, k, t, c) => {
        card(g, 52, y, 920, 128); g.strokeStyle = c; g.globalAlpha = 0.6; rr(g, 52, y, 920, 128, 18); g.lineWidth = 2; g.stroke(); g.globalAlpha = 1;
        label(g, k, 80, y + 42, c); g.fillStyle = "#dbeeff"; g.font = "500 28px Rajdhani, sans-serif"; g.fillText(t, 80, y + 90);
        rr(g, 700, y + 60, 120, 44, 10); g.strokeStyle = "rgba(80,250,123,0.8)"; g.stroke(); g.fillStyle = "#50fa7b"; g.font = "700 16px Orbitron, sans-serif"; g.fillText("ALLOW", 724, y + 89);
        rr(g, 834, y + 60, 112, 44, 10); g.strokeStyle = "rgba(255,255,255,0.25)"; g.stroke(); g.fillStyle = "#a9c4dd"; g.fillText("DENY", 860, y + 89);
      };
      ask(136, "COMPUTER USE", "Send the drafted email to finance?", col);
      ask(286, "PHONE AGENT", "Call the clinic to reschedule?", "#00d9ff");
      ask(436, "EVOLUTION", "Deploy the tested improvement?", "#8b7cf8");
    },
  };

  function init(THREE) {
    let renderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" }); }
    catch (e) { stage.classList.add("no-gl"); return; }
    let dprCap = mobile ? 1.5 : 2;
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, dprCap));
    renderer.setClearColor(0x000000, 0);
    const cv = renderer.domElement; cv.className = "sc-canvas"; cv.setAttribute("aria-hidden", "true");
    stage.appendChild(cv);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);
    const ADD = THREE.AdditiveBlending;
    const rig = new THREE.Group(); scene.add(rig);
    const stack = new THREE.Group(); rig.add(stack);

    const aniso = Math.min(8, renderer.capabilities.getMaxAnisotropy());
    const PWU = 4, PHU = PWU * PH / PW;
    const panels = LAYERS.map((L, i) => {
      const c = document.createElement("canvas"); c.width = PW; c.height = PH;
      DRAW[L.k](c.getContext("2d"), L.c);
      const tex = new THREE.CanvasTexture(c); tex.anisotropy = aniso;
      const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0.9, depthWrite: false, side: THREE.DoubleSide });
      const mesh = new THREE.Mesh(new THREE.PlaneGeometry(PWU, PHU), mat);
      const holder = new THREE.Group(); holder.add(mesh);
      const edge = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.PlaneGeometry(PWU * 1.006, PHU * 1.01)),
        new THREE.LineBasicMaterial({ color: L.c, transparent: true, opacity: 0.5, blending: ADD }));
      holder.add(edge);
      holder.rotation.x = -Math.PI / 2 + 0.52;
      holder.renderOrder = i;
      stack.add(holder);
      return { holder, mat, edge, lift: 0 };
    });

    /* data beam through the stack, with packets rising */
    const beamMat = new THREE.LineBasicMaterial({ color: 0x00d9ff, transparent: true, opacity: 0.35, blending: ADD });
    const beamGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 1, 0)]);
    const beam = new THREE.Line(beamGeo, beamMat); stack.add(beam);
    const glowC = document.createElement("canvas"); glowC.width = glowC.height = 64;
    const gg = glowC.getContext("2d"), rg = gg.createRadialGradient(32, 32, 0, 32, 32, 32);
    rg.addColorStop(0, "rgba(255,255,255,1)"); rg.addColorStop(.4, "rgba(255,255,255,.35)"); rg.addColorStop(1, "rgba(255,255,255,0)");
    gg.fillStyle = rg; gg.fillRect(0, 0, 64, 64);
    const glowTex = new THREE.CanvasTexture(glowC);
    const packets = [0, 0.33, 0.66].map((ph) => {
      const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, color: 0x9ff3ff, transparent: true, blending: ADD, depthWrite: false }));
      s.scale.set(0.16, 0.16, 1); stack.add(s); return { s, ph };
    });
    const floor = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, color: 0x0a6cff, transparent: true, opacity: 0.35, blending: ADD, depthWrite: false }));
    floor.scale.set(7, 3.2, 1); rig.add(floor);

    let W = 1, H = 1;
    function resize() {
      const r = stage.getBoundingClientRect(); W = Math.max(1, r.width); H = Math.max(1, r.height);
      renderer.setSize(W, H, false); camera.aspect = W / H;
      camera.fov = W / H < 1 ? 40 : 32; camera.updateProjectionMatrix();
      if (!running) frame(performance.now());
    }
    const mouse = { x: 0, y: 0, tx: 0, ty: 0 };
    if (!reduced) stage.addEventListener("pointermove", (e) => {
      const r = stage.getBoundingClientRect(); mouse.tx = (e.clientX - r.left) / r.width * 2 - 1; mouse.ty = (e.clientY - r.top) / r.height * 2 - 1;
    });
    stage.addEventListener("pointerleave", () => { mouse.tx = 0; mouse.ty = 0; });

    let last = performance.now(), t = 0, running = false, onScreen = true, raf = 0, slow = 0, p = progress, fpsAcc = 0, fpsN = 0;
    function frame(now) {
      const dt = Math.min(0.05, (now - last) / 1000); last = now;
      if (running) t += dt;
      p += (progress - p) * (reduced ? 1 : 0.08);
      mouse.x += (mouse.tx - mouse.x) * 0.06; mouse.y += (mouse.ty - mouse.y) * 0.06;
      const ex = 0.14 + Math.min(1, p * 1.25) * 0.72;                // layer spacing: closed → exploded
      const act = activeIndex(p), n = panels.length;
      panels.forEach((P, i) => {
        P.lift += ((i === act ? 1 : 0) - P.lift) * 0.12;
        P.holder.position.set(0, (i - (n - 1) / 2) * ex + P.lift * 0.12, P.lift * 0.55);
        const on = i === act;
        P.mat.opacity += ((on ? 1 : 0.88) - P.mat.opacity) * 0.12;
        P.edge.material.opacity += ((on ? 0.95 : 0.3) - P.edge.material.opacity) * 0.12;
      });
      const top = ((n - 1) / 2) * ex + 0.3, bot = -top;
      beam.position.y = bot; beam.scale.y = top - bot;
      packets.forEach((k) => { const u = (t * 0.25 + k.ph) % 1; k.s.position.set(0, bot + u * (top - bot), 0); k.s.material.opacity = Math.sin(u * Math.PI); });
      floor.position.y = bot - 0.4;
      // scroll-linked camera: orbit and descend slightly as the stack opens
      rig.rotation.y = -0.5 + p * 0.32 + mouse.x * 0.1;
      rig.rotation.x = 0.0 + mouse.y * 0.05;
      const dist = W / H < 1 ? 11.6 : 9.4;
      camera.position.set(0, 2.2 - p * 0.7, dist - p * 0.5);
      camera.lookAt(0, -0.05, 0);
      renderer.render(scene, camera);
      if (running) {
        fpsAcc += dt; fpsN++;
        if (fpsAcc >= 1) { (window.__bucsFps = window.__bucsFps || {}).showcase = Math.round(fpsN / fpsAcc); fpsAcc = 0; fpsN = 0; }
        if (dt > 0.028) { if (++slow > 60 && dprCap > 1) { dprCap = Math.max(1, dprCap - 0.5); slow = 0; renderer.setPixelRatio(Math.min(devicePixelRatio || 1, dprCap)); resize(); } }
        else slow = Math.max(0, slow - 1);
        raf = requestAnimationFrame(frame);
      }
    }
    function start() { if (running || reduced || !onScreen || document.hidden) return; running = true; last = performance.now(); raf = requestAnimationFrame(frame); }
    function stop() { running = false; cancelAnimationFrame(raf); }
    if ("ResizeObserver" in window) new ResizeObserver(resize).observe(stage); else addEventListener("resize", resize);
    new IntersectionObserver((es) => { onScreen = es[0].isIntersecting; onScreen ? start() : stop(); }).observe(stage);
    document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));
    cv.addEventListener("webglcontextlost", (e) => { e.preventDefault(); stop(); stage.classList.remove("gl-ready"); stage.classList.add("no-gl"); });
    resize();
    p = progress; frame(performance.now());     // reduced motion: one exploded still frame
    stage.classList.add("gl-ready");
    start();
  }
})();
