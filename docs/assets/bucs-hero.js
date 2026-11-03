/* BUCS hero: real-time 3D arc-reactor globe with the eight Avengers agents in orbit.
   three.js r128 is loaded lazily from cdnjs only when the hero is near the viewport.
   Pauses off-screen / in background tabs, renders one still frame under
   prefers-reduced-motion, and leaves the CSS reactor fallback in place without WebGL. */
(() => {
  "use strict";
  const stage = document.getElementById("hero3d");
  if (!stage) return;
  const THREE_URL = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const AGENTS = [
    ["JARVIS", "#00d9ff"], ["CAPTAIN", "#3b82f6"], ["STARK", "#ff6b00"], ["BLACK WIDOW", "#ff5a5a"],
    ["HAWKEYE", "#a78bfa"], ["HULK", "#22c55e"], ["THOR", "#facc15"], ["VISION", "#e879f9"],
  ];

  function webglOK() {
    try {
      const c = document.createElement("canvas");
      return !!(window.WebGLRenderingContext && (c.getContext("webgl") || c.getContext("experimental-webgl")));
    } catch (e) { return false; }
  }
  if (!webglOK()) { stage.classList.add("no-gl"); return; }

  function loadThree() {
    return new Promise((ok, bad) => {
      if (window.THREE) return ok(window.THREE);
      const s = document.createElement("script");
      s.src = THREE_URL; s.async = true; s.crossOrigin = "anonymous";
      s.onload = () => (window.THREE ? ok(window.THREE) : bad(new Error("three missing")));
      s.onerror = () => bad(new Error("three failed to load"));
      document.head.appendChild(s);
    });
  }

  const near = new IntersectionObserver((es) => {
    if (!es.some((e) => e.isIntersecting)) return;
    near.disconnect();
    loadThree().then(init).catch(() => stage.classList.add("no-gl"));
  }, { rootMargin: "300px" });
  near.observe(stage);

  function glowTexture(THREE) {
    const c = document.createElement("canvas"); c.width = c.height = 128;
    const g = c.getContext("2d"), r = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    r.addColorStop(0, "rgba(255,255,255,1)"); r.addColorStop(.22, "rgba(255,255,255,.55)");
    r.addColorStop(.5, "rgba(255,255,255,.14)"); r.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = r; g.fillRect(0, 0, 128, 128);
    return new THREE.CanvasTexture(c);
  }

  function init(THREE) {
    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    } catch (e) { stage.classList.add("no-gl"); return; }
    const mobile = matchMedia("(max-width: 760px)").matches;
    let dprCap = mobile ? 1.5 : 2;
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, dprCap));
    renderer.setClearColor(0x000000, 0);
    const cv = renderer.domElement;
    cv.setAttribute("aria-hidden", "true");
    cv.className = "hero3d-canvas";
    stage.appendChild(cv);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
    camera.position.set(0, 0.6, 11.5);
    const ADD = THREE.AdditiveBlending, glow = glowTexture(THREE);
    const world = new THREE.Group(); scene.add(world);
    const tilt = new THREE.Group(); world.add(tilt);
    tilt.rotation.x = 0.32; tilt.rotation.z = -0.12;

    /* --- arc reactor core --- */
    const core = new THREE.Mesh(new THREE.SphereGeometry(0.5, 32, 32),
      new THREE.MeshBasicMaterial({ color: 0xcffbff }));
    world.add(core);
    const coreGlow = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow, color: 0x22e4ff, blending: ADD, transparent: true, depthWrite: false }));
    coreGlow.scale.set(4.6, 4.6, 1); world.add(coreGlow);
    const reactor = new THREE.Group(); world.add(reactor);
    const segMat = new THREE.MeshBasicMaterial({ color: 0x3fe6ff, transparent: true, opacity: 0.55, side: THREE.DoubleSide, blending: ADD, depthWrite: false });
    for (let i = 0; i < 10; i++) {
      const seg = new THREE.Mesh(new THREE.RingGeometry(0.78, 1.08, 6, 1, (i / 10) * Math.PI * 2 + 0.06, (Math.PI * 2) / 10 - 0.12), segMat);
      reactor.add(seg);
    }
    const ringMat = (op) => new THREE.MeshBasicMaterial({ color: 0x00d9ff, transparent: true, opacity: op, blending: ADD, depthWrite: false });
    const innerRing = new THREE.Mesh(new THREE.TorusGeometry(1.22, 0.012, 8, 120), ringMat(0.8));
    reactor.add(innerRing);
    const triMat = new THREE.LineBasicMaterial({ color: 0x9ff3ff, transparent: true, opacity: 0.6, blending: ADD });
    const tri = new THREE.BufferGeometry().setFromPoints([0, 1, 2, 3].map((k) => new THREE.Vector3(Math.cos(k * 2.0944 + Math.PI / 2) * 0.66, Math.sin(k * 2.0944 + Math.PI / 2) * 0.66, 0)));
    reactor.add(new THREE.Line(tri, triMat));

    /* --- holographic globe (fibonacci point shell + faint wire lattice) --- */
    const N = mobile ? 900 : 1600, R = 2.3, gp = new Float32Array(N * 3);
    for (let i = 0; i < N; i++) {
      const y = 1 - (i / (N - 1)) * 2, rr = Math.sqrt(1 - y * y), th = i * 2.399963;
      gp[i * 3] = Math.cos(th) * rr * R; gp[i * 3 + 1] = y * R; gp[i * 3 + 2] = Math.sin(th) * rr * R;
    }
    const gGeo = new THREE.BufferGeometry(); gGeo.setAttribute("position", new THREE.BufferAttribute(gp, 3));
    const globe = new THREE.Points(gGeo, new THREE.PointsMaterial({ color: 0x5ceaff, size: 0.035, transparent: true, opacity: 0.75, blending: ADD, depthWrite: false }));
    tilt.add(globe);
    const lattice = new THREE.LineSegments(new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(R * 1.02, 2)),
      new THREE.LineBasicMaterial({ color: 0x00d9ff, transparent: true, opacity: 0.07, blending: ADD }));
    tilt.add(lattice);
    // latitude bands
    [-0.55, 0, 0.55].forEach((lat) => {
      const ring = new THREE.Mesh(new THREE.TorusGeometry(R * Math.cos(lat) * 1.01, 0.006, 6, 140), ringMat(lat === 0 ? 0.45 : 0.18));
      ring.rotation.x = Math.PI / 2; ring.position.y = R * Math.sin(lat); tilt.add(ring);
    });

    /* --- outer gyroscope rings --- */
    const gyro = [];
    [[3.05, 0.4, 0.2, 0.32], [3.45, -0.5, 0.9, 0.2], [3.9, 1.1, -0.4, 0.12]].forEach(([r, rx, ry, op]) => {
      const m = new THREE.Mesh(new THREE.TorusGeometry(r, 0.008, 6, 180), ringMat(op));
      m.rotation.set(rx, ry, 0); world.add(m); gyro.push(m);
    });

    /* --- the eight agents in orbit --- */
    const labels = document.createElement("div");
    labels.className = "hero3d-labels"; labels.setAttribute("aria-hidden", "true");
    stage.appendChild(labels);
    const nodes = AGENTS.map(([name, hex], i) => {
      const color = new THREE.Color(hex);
      const orbit = new THREE.Group();
      orbit.rotation.set((Math.random() - 0.5) * 1.3, i * 0.785, (Math.random() - 0.5) * 0.9);
      world.add(orbit);
      const radius = 3.0 + (i % 4) * 0.42;
      const pts = []; for (let k = 0; k <= 96; k++) pts.push(new THREE.Vector3(Math.cos(k / 96 * Math.PI * 2) * radius, 0, Math.sin(k / 96 * Math.PI * 2) * radius));
      orbit.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
        new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.13, blending: ADD })));
      const body = new THREE.Mesh(new THREE.SphereGeometry(0.1, 16, 16), new THREE.MeshBasicMaterial({ color }));
      const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow, color, blending: ADD, transparent: true, depthWrite: false }));
      halo.scale.set(0.95, 0.95, 1);
      const holder = new THREE.Group(); holder.add(body); holder.add(halo); orbit.add(holder);
      const linkGeo = new THREE.BufferGeometry(); linkGeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6), 3));
      const link = new THREE.Line(linkGeo, new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.22, blending: ADD }));
      world.add(link);
      const pulse = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow, color, blending: ADD, transparent: true, depthWrite: false }));
      pulse.scale.set(0.32, 0.32, 1); world.add(pulse);
      const el = document.createElement("span"); el.textContent = name; el.style.setProperty("--c", hex);
      labels.appendChild(el);
      return { orbit, holder, link, pulse, el, radius, speed: 0.11 + Math.random() * 0.1, phase: (i / AGENTS.length) * Math.PI * 2, pt: Math.random() };
    });

    /* --- particle field --- */
    const P = mobile ? 900 : 1800, pp = new Float32Array(P * 3), pc = new Float32Array(P * 3);
    const cA = new THREE.Color(0x00d9ff), cB = new THREE.Color(0x8b7cf8), tmp = new THREE.Color();
    for (let i = 0; i < P; i++) {
      const r = 5 + Math.random() * 11, th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1);
      pp[i * 3] = r * Math.sin(ph) * Math.cos(th); pp[i * 3 + 1] = r * Math.cos(ph) * 0.7; pp[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th);
      tmp.copy(cA).lerp(cB, Math.random()); pc[i * 3] = tmp.r; pc[i * 3 + 1] = tmp.g; pc[i * 3 + 2] = tmp.b;
    }
    const pGeo = new THREE.BufferGeometry();
    pGeo.setAttribute("position", new THREE.BufferAttribute(pp, 3)); pGeo.setAttribute("color", new THREE.BufferAttribute(pc, 3));
    const field = new THREE.Points(pGeo, new THREE.PointsMaterial({ size: 0.05, vertexColors: true, transparent: true, opacity: 0.7, blending: ADD, depthWrite: false }));
    scene.add(field);

    /* --- sizing, input, visibility --- */
    let W = 1, H = 1;
    function resize() {
      const r = cv.getBoundingClientRect();          // canvas bleeds 8% past the stage; labels share that box
      W = Math.max(1, r.width); H = Math.max(1, r.height);
      renderer.setSize(W, H, false);
      camera.aspect = W / H;
      camera.position.z = W < 520 ? 14.5 : W / H < 0.9 ? 13.5 : 11.5;
      camera.updateProjectionMatrix();
      if (!running) frame(performance.now());
    }
    const mouse = { x: 0, y: 0, tx: 0, ty: 0 };
    addEventListener("pointermove", (e) => {
      mouse.tx = (e.clientX / innerWidth) * 2 - 1;
      mouse.ty = (e.clientY / innerHeight) * 2 - 1;
    }, { passive: true });

    const v = new THREE.Vector3(), corePos = new THREE.Vector3();
    let last = performance.now(), t = 0, running = false, onScreen = true, raf = 0;
    let slowFrames = 0, fpsAcc = 0, fpsN = 0;

    function frame(now) {
      const dt = Math.min(0.05, (now - last) / 1000); last = now;
      if (running) t += dt;
      mouse.x += (mouse.tx - mouse.x) * 0.05; mouse.y += (mouse.ty - mouse.y) * 0.05;
      world.rotation.y = t * 0.08 + mouse.x * 0.35;
      world.rotation.x = mouse.y * 0.18;
      camera.position.x = mouse.x * 0.6; camera.position.y = 0.6 - mouse.y * 0.4;
      camera.lookAt(0, 0, 0);
      globe.rotation.y = t * 0.12; lattice.rotation.y = -t * 0.05;
      reactor.rotation.z = -t * 0.6; innerRing.scale.setScalar(1 + Math.sin(t * 2.2) * 0.03);
      const beat = 1 + Math.sin(t * 2.6) * 0.06; coreGlow.scale.set(4.6 * beat, 4.6 * beat, 1);
      gyro.forEach((g, i) => { g.rotation.z += dt * (0.08 + i * 0.05) * (i % 2 ? -1 : 1); });
      field.rotation.y = t * 0.015;
      core.getWorldPosition(corePos);
      for (const n of nodes) {
        const a = n.phase + t * n.speed;
        n.holder.position.set(Math.cos(a) * n.radius, 0, Math.sin(a) * n.radius);
        n.holder.getWorldPosition(v);
        world.worldToLocal(v);
        const arr = n.link.geometry.attributes.position.array;
        arr[3] = v.x; arr[4] = v.y; arr[5] = v.z;
        n.link.geometry.attributes.position.needsUpdate = true;
        n.pt = (n.pt + dt * 0.45) % 1;
        n.pulse.position.set(v.x * n.pt, v.y * n.pt, v.z * n.pt);
        n.pulse.material.opacity = Math.sin(n.pt * Math.PI);
        // HTML label projection
        n.holder.getWorldPosition(v);
        const depth = v.distanceTo(camera.position), cz = camera.position.z;
        v.project(camera);
        const x = (v.x * 0.5 + 0.5) * W, y = (-v.y * 0.5 + 0.5) * H;
        n.el.style.transform = `translate(${x.toFixed(1)}px,${y.toFixed(1)}px)`;
        n.el.style.opacity = Math.max(0.12, Math.min(1, (cz + 1.2 - depth) / 3)).toFixed(2);
      }
      renderer.render(scene, camera);
      if (running) {
        fpsAcc += dt; fpsN++;
        if (fpsAcc >= 1) { window.__bucsHeroFps = Math.round(fpsN / fpsAcc); fpsAcc = 0; fpsN = 0; }
        if (dt > 0.03) { if (++slowFrames > 90 && dprCap > 1) { dprCap = 1; renderer.setPixelRatio(1); resize(); } }
        else slowFrames = Math.max(0, slowFrames - 1);
        raf = requestAnimationFrame(frame);
      }
    }
    function start() {
      if (running || reduced || !onScreen || document.hidden) return;
      running = true; last = performance.now(); raf = requestAnimationFrame(frame);
    }
    function stop() { running = false; cancelAnimationFrame(raf); }

    if ("ResizeObserver" in window) new ResizeObserver(resize).observe(stage); else addEventListener("resize", resize);
    new IntersectionObserver((es) => { onScreen = es[0].isIntersecting; onScreen ? start() : stop(); }).observe(stage);
    document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));
    cv.addEventListener("webglcontextlost", (e) => { e.preventDefault(); stop(); stage.classList.add("no-gl"); });

    resize();
    t = 2.5; frame(performance.now());          // one still frame (reduced motion stays here)
    stage.classList.add("gl-ready");
    start();
  }
})();
