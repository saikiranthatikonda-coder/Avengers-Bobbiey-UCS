/* BUCS hero: a shaded 3D Earth in the style of the Global Activity Map.
   Land comes from Natural Earth via world-atlas (public domain, pinned on jsdelivr) and is
   rasterised in the browser; a procedural pattern stands in if that request fails.
   Day/night terminator, Fresnel atmosphere, city lights on the night side, arcs with
   travelling pulses, activity markers and two satellite orbits.
   three.js r128 is loaded lazily from cdnjs only when the hero is near the viewport.
   Pauses off-screen and in background tabs, adapts pixel ratio to frame time, renders a
   single still frame under prefers-reduced-motion, and keeps a CSS globe without WebGL. */
(() => {
  "use strict";
  const stage = document.getElementById("hero3d");
  if (!stage) return;
  const THREE_URL = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js";
  const LAND_URL = "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/land-110m.json";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const mobile = matchMedia("(max-width: 760px)").matches;

  // [lat, lon, colour] · colours match the legend under the globe (illustrative positions)
  const MARKS = [
    [35.7, 139.7, "#ff6b6b"], [17.4, 78.5, "#00d9ff"], [37.8, -122.4, "#e879f9"], [51.5, -0.1, "#00d9ff"],
    [-6.2, 106.8, "#ff6b6b"], [-33.4, -70.6, "#ffb35c"], [1.3, 103.8, "#00d9ff"], [64.1, -21.9, "#ffb35c"],
    [40.7, -74.0, "#00d9ff"], [25.2, 55.3, "#00d9ff"], [-1.3, 36.8, "#e879f9"],
  ];
  const ARCS = [[1, 3], [1, 6], [3, 8], [8, 2], [9, 1], [6, 0], [3, 9], [10, 9]];
  // major metropolitan areas, used only as soft night-side light points
  const CITIES = [
    [35.7, 139.7], [34.7, 135.5], [37.6, 127.0], [31.2, 121.5], [39.9, 116.4], [22.5, 114.1], [23.1, 113.3], [30.6, 104.1],
    [25.0, 121.5], [14.6, 121.0], [13.8, 100.5], [10.8, 106.7], [21.0, 105.8], [1.3, 103.8], [3.1, 101.7], [-6.2, 106.8],
    [28.6, 77.2], [19.1, 72.9], [12.97, 77.6], [17.4, 78.5], [13.1, 80.3], [22.6, 88.4], [23.8, 90.4], [24.9, 67.0],
    [31.5, 74.3], [33.7, 73.0], [35.7, 51.4], [25.2, 55.3], [24.7, 46.7], [41.0, 29.0], [30.0, 31.2], [6.5, 3.4],
    [-1.3, 36.8], [-26.2, 28.0], [-33.9, 18.4], [9.0, 38.7], [33.6, -7.6], [55.8, 37.6], [59.9, 30.3], [50.4, 30.5],
    [52.2, 21.0], [52.5, 13.4], [48.1, 11.6], [50.1, 8.7], [48.9, 2.35], [51.5, -0.1], [53.5, -2.2], [52.4, 4.9],
    [50.8, 4.4], [40.4, -3.7], [41.4, 2.2], [45.5, 9.2], [41.9, 12.5], [37.98, 23.7], [59.3, 18.1], [55.7, 12.6],
    [40.7, -74.0], [42.4, -71.1], [38.9, -77.0], [41.9, -87.6], [33.7, -84.4], [25.8, -80.2], [29.8, -95.4], [32.8, -96.8],
    [34.1, -118.2], [37.8, -122.4], [47.6, -122.3], [49.3, -123.1], [43.7, -79.4], [45.5, -73.6], [19.4, -99.1], [20.7, -103.3],
    [4.7, -74.1], [-12.0, -77.0], [-33.4, -70.6], [-34.6, -58.4], [-23.5, -46.6], [-22.9, -43.2], [-15.8, -47.9], [-33.9, 151.2],
    [-37.8, 145.0], [-27.5, 153.0], [-31.95, 115.9], [-36.8, 174.8],
  ];

  function webglOK() {
    try {
      const c = document.createElement("canvas");
      return !!(window.WebGLRenderingContext && (c.getContext("webgl") || c.getContext("experimental-webgl")));
    } catch (e) { return false; }
  }
  if (!webglOK()) { stage.classList.add("no-gl"); return; }

  /* one shared, lazy three.js loader for every 3D scene on the page */
  window.__bucsThree = window.__bucsThree || (() => {
    let p = null;
    return () => p || (p = new Promise((ok, bad) => {
      if (window.THREE) return ok(window.THREE);
      const s = document.createElement("script");
      s.src = THREE_URL; s.async = true; s.crossOrigin = "anonymous";
      s.onload = () => (window.THREE ? ok(window.THREE) : bad(new Error("three missing")));
      s.onerror = () => { p = null; bad(new Error("three failed to load")); };
      document.head.appendChild(s);
    }));
  })();

  /* ─── land mask: decode the TopoJSON and rasterise it to an equirectangular canvas ─── */
  async function landCanvas(W, H) {
    const c = document.createElement("canvas"); c.width = W; c.height = H;
    const g = c.getContext("2d", { willReadFrequently: true });
    g.fillStyle = "#000"; g.fillRect(0, 0, W, H);
    try {
      const ctl = "AbortController" in window ? new AbortController() : null;
      const tm = ctl && setTimeout(() => ctl.abort(), 7000);
      const topo = await (await fetch(LAND_URL, { signal: ctl && ctl.signal })).json();
      if (tm) clearTimeout(tm);
      const [sx, sy] = topo.transform.scale, [tx, ty] = topo.transform.translate;
      const arcs = topo.arcs.map((arc) => { let x = 0, y = 0; return arc.map(([dx, dy]) => { x += dx; y += dy; return [x * sx + tx, y * sy + ty]; }); });
      const ring = (ids) => { const out = []; ids.forEach((i, k) => { const a = i >= 0 ? arcs[i] : arcs[~i].slice().reverse(); out.push(...(k ? a.slice(1) : a)); }); return out; };
      const polys = [];
      const walk = (geo) => {
        if (geo.type === "GeometryCollection") geo.geometries.forEach(walk);
        else if (geo.type === "Polygon") polys.push(geo.arcs.map(ring));
        else if (geo.type === "MultiPolygon") geo.arcs.forEach((p) => polys.push(p.map(ring)));
      };
      walk(topo.objects.land);
      g.fillStyle = "#fff";
      for (const rings of polys) {
        g.beginPath();
        for (const r of rings) {
          r.forEach(([lon, lat], k) => {
            const x = (lon + 180) / 360 * W, y = (90 - lat) / 180 * H;
            k ? g.lineTo(x, y) : g.moveTo(x, y);
          });
          g.closePath();
        }
        g.fill("evenodd");
      }
      return { canvas: c, real: true };
    } catch (e) {
      // procedural stand-in: soft blobs, never shown as "real" geography
      const img = g.createImageData(W, H);
      for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
        const lo = x / W * 6.283, la = (y / H - 0.5) * 3.1416;
        const n = Math.sin(lo * 2 + 1.3) * Math.cos(la * 2.7 - 0.4) + Math.sin(lo * 3 + la * 1.9) * 0.8 + Math.cos(lo * 5 - la * 4) * 0.35;
        const v = n > 0.45 ? 255 : 0, i = (y * W + x) * 4;
        img.data[i] = img.data[i + 1] = img.data[i + 2] = v; img.data[i + 3] = 255;
      }
      g.putImageData(img, 0, 0);
      return { canvas: c, real: false };
    }
  }

  const near = new IntersectionObserver((es) => {
    if (!es.some((e) => e.isIntersecting)) return;
    near.disconnect();
    Promise.all([window.__bucsThree(), landCanvas(mobile ? 1024 : 2048, mobile ? 512 : 1024)])
      .then(([THREE, land]) => { if (land.real) window.__bucsLandCanvas = land.canvas; init(THREE, land); })
      .catch(() => stage.classList.add("no-gl"));
  }, { rootMargin: "300px" });
  near.observe(stage);

  function softDot(THREE) {
    const c = document.createElement("canvas"); c.width = c.height = 64;
    const g = c.getContext("2d"), r = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    r.addColorStop(0, "rgba(255,255,255,1)"); r.addColorStop(.35, "rgba(255,255,255,.45)"); r.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = r; g.fillRect(0, 0, 64, 64);
    return new THREE.CanvasTexture(c);
  }

  function init(THREE, land) {
    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    } catch (e) { stage.classList.add("no-gl"); return; }
    let dprCap = mobile ? 1.5 : 2;
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, dprCap));
    renderer.setClearColor(0x000000, 0);
    const cv = renderer.domElement;
    cv.setAttribute("aria-hidden", "true");
    cv.className = "hero3d-canvas";
    stage.appendChild(cv);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 100);
    const R = 2.4, ADD = THREE.AdditiveBlending;
    const SUN = new THREE.Vector3(-0.85, 0.35, 0.55).normalize();
    const world = new THREE.Group(); scene.add(world);
    world.rotation.x = 0.36; world.rotation.z = -0.08;
    const globe = new THREE.Group(); world.add(globe);
    const toVec = (lat, lon, r) => {
      const phi = (90 - lat) * Math.PI / 180, th = (lon + 180) * Math.PI / 180;
      return new THREE.Vector3(-r * Math.sin(phi) * Math.cos(th), r * Math.cos(phi), r * Math.sin(phi) * Math.sin(th));
    };

    /* Earth: shaded land/ocean with a soft terminator and a Fresnel rim */
    const landTex = new THREE.CanvasTexture(land.canvas);
    landTex.anisotropy = Math.min(4, renderer.capabilities.getMaxAnisotropy());
    const uni = { landMap: { value: landTex }, sun: { value: SUN }, time: { value: 0 } };
    const earthMat = new THREE.ShaderMaterial({
      uniforms: uni,
      vertexShader: `
        varying vec2 vUv; varying vec3 vN; varying vec3 vP;
        void main(){ vUv = uv; vN = normalize(mat3(modelMatrix) * normal); vec4 wp = modelMatrix * vec4(position,1.0); vP = wp.xyz;
          gl_Position = projectionMatrix * viewMatrix * wp; }`,
      fragmentShader: `
        uniform sampler2D landMap; uniform vec3 sun;
        varying vec2 vUv; varying vec3 vN; varying vec3 vP;
        void main(){
          vec3 n = normalize(vN); vec3 v = normalize(cameraPosition - vP);
          float landA = texture2D(landMap, vUv).r;
          float ndl = dot(n, sun);
          float day = smoothstep(-0.18, 0.32, ndl);
          vec3 ocean = mix(vec3(0.006,0.018,0.04), vec3(0.02,0.09,0.17), day);
          vec3 landC = mix(vec3(0.02,0.05,0.08), vec3(0.09,0.33,0.42), day);
          vec3 col = mix(ocean, landC, landA);
          // fine graticule
          float lat = vUv.y * 180.0, lon = vUv.x * 360.0;
          float fa = fract(lat/15.0), fo = fract(lon/15.0);
          float grid = max(1.0 - smoothstep(0.0, 0.025, min(fa, 1.0-fa)), 1.0 - smoothstep(0.0, 0.0125, min(fo, 1.0-fo)));
          col += vec3(0.0,0.35,0.5) * grid * 0.06 * (0.4 + day);
          // ocean glint
          vec3 h = normalize(sun + v);
          col += vec3(0.35,0.75,1.0) * pow(max(dot(n,h),0.0), 60.0) * (1.0 - landA) * 0.35 * day;
          // terminator warmth + rim
          col += vec3(0.25,0.12,0.04) * exp(-pow(ndl*7.0,2.0)) * 0.25;
          float fres = pow(1.0 - max(dot(n, v), 0.0), 2.6);
          col += vec3(0.12,0.62,1.0) * fres * (0.18 + 0.5*day);
          gl_FragColor = vec4(col, 1.0);
        }`,
    });
    const SEGS = mobile ? 64 : 96;
    globe.add(new THREE.Mesh(new THREE.SphereGeometry(R, SEGS, SEGS / 2), earthMat));

    /* atmosphere: back-side Fresnel shell */
    const atm = new THREE.Mesh(new THREE.SphereGeometry(R * 1.14, 64, 32), new THREE.ShaderMaterial({
      uniforms: { sun: { value: SUN } },
      vertexShader: `varying vec3 vN; varying vec3 vNW; void main(){ vN = normalize(normalMatrix * normal); vNW = normalize(mat3(modelMatrix)*normal); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
      fragmentShader: `uniform vec3 sun; varying vec3 vN; varying vec3 vNW;
        void main(){ float i = pow(max(0.0, 0.66 - dot(vN, vec3(0.0,0.0,1.0))), 3.2);
          float lit = 0.35 + 0.65 * smoothstep(-0.4, 0.6, dot(vNW, sun));
          gl_FragColor = vec4(vec3(0.16,0.62,1.0) * i * 2.6 * lit, i * lit); }`,
      side: THREE.BackSide, blending: ADD, transparent: true, depthWrite: false,
    }));
    world.add(atm);

    /* land dot matrix (sampled from the mask) */
    const mctx = land.canvas.getContext("2d"), MW = land.canvas.width, MH = land.canvas.height;
    const mask = mctx.getImageData(0, 0, MW, MH).data;
    const onLand = (lat, lon) => mask[(Math.min(MH - 1, Math.floor((90 - lat) / 180 * MH)) * MW + Math.min(MW - 1, Math.floor((lon + 180) / 360 * MW))) * 4] > 128;
    const NF = mobile ? 9000 : 16000, dots = [];
    for (let i = 0; i < NF; i++) {
      const y = 1 - (i / (NF - 1)) * 2, lat = Math.asin(y) * 180 / Math.PI, lon = ((i * 137.50776) % 360) - 180;
      if (onLand(lat, lon)) dots.push(toVec(lat, lon, R * 1.003));
    }
    const dotGeo = new THREE.BufferGeometry().setFromPoints(dots);
    const pointShader = (color, nightOnly, size) => new THREE.ShaderMaterial({
      uniforms: { sun: { value: SUN }, color: { value: new THREE.Color(color) }, size: { value: size * renderer.getPixelRatio() } },
      vertexShader: `uniform float size; uniform vec3 sun; varying float vL;
        void main(){ vec3 nw = normalize(mat3(modelMatrix) * position); vL = dot(nw, sun);
          vec4 mv = modelViewMatrix * vec4(position,1.0); gl_PointSize = size * (10.0 / -mv.z); gl_Position = projectionMatrix * mv; }`,
      fragmentShader: `uniform vec3 color; varying float vL;
        void main(){ vec2 c = gl_PointCoord - 0.5; float d = length(c); if (d > 0.5) discard;
          float a = smoothstep(0.5, 0.1, d) * ${nightOnly ? "smoothstep(0.12, -0.25, vL)" : "(0.35 + 0.65*smoothstep(-0.3, 0.4, vL))"};
          gl_FragColor = vec4(color * a, a); }`,
      transparent: true, depthWrite: false, blending: ADD,
    });
    globe.add(new THREE.Points(dotGeo, pointShader(0x7fe9ff, false, mobile ? 2.0 : 1.7)));

    /* city lights on the night side */
    const cityPts = [];
    CITIES.forEach(([la, lo], i) => {
      cityPts.push(toVec(la, lo, R * 1.004));
      for (let k = 0; k < 3; k++) cityPts.push(toVec(la + Math.sin(i * 7.1 + k * 2.3) * 0.9, lo + Math.cos(i * 3.7 + k * 1.9) * 1.1, R * 1.004));
    });
    globe.add(new THREE.Points(new THREE.BufferGeometry().setFromPoints(cityPts), pointShader(0xffc979, true, mobile ? 4.2 : 3.6)));

    /* activity markers */
    const dot = softDot(THREE);
    const marks = MARKS.map(([lat, lon, hex], i) => {
      const p = toVec(lat, lon, R * 1.006), color = new THREE.Color(hex);
      const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: dot, color, transparent: true, blending: ADD, depthWrite: false }));
      s.position.copy(p); s.scale.set(0.2, 0.2, 1); globe.add(s);
      const ring = new THREE.Mesh(new THREE.RingGeometry(0.055, 0.07, 48),
        new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.8, side: THREE.DoubleSide, depthWrite: false, blending: ADD }));
      ring.position.copy(p); ring.lookAt(p.clone().multiplyScalar(2)); globe.add(ring);
      return { p, ring, phase: i * 0.31 };
    });

    /* arcs: thin tubes with a travelling pulse */
    const arcUni = { time: uni.time };
    const arcMat = (phase, speed) => new THREE.ShaderMaterial({
      uniforms: { time: arcUni.time, phase: { value: phase }, speed: { value: speed } },
      vertexShader: `varying float vU; void main(){ vU = uv.x; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
      fragmentShader: `uniform float time; uniform float phase; uniform float speed; varying float vU;
        void main(){ float h = fract(time*speed + phase) * 1.35 - 0.15; float k = vU - h;
          float tail = k < 0.0 ? exp(k * 16.0) : exp(-k * 120.0);
          float a = 0.10 + tail * 0.95; vec3 c = mix(vec3(0.0,0.75,1.0), vec3(0.85,0.98,1.0), tail);
          gl_FragColor = vec4(c * a, a); }`,
      transparent: true, depthWrite: false, blending: ADD,
    });
    ARCS.forEach(([a, b], i) => {
      const A = marks[a].p, B = marks[b].p, d = A.distanceTo(B);
      const mid = A.clone().add(B).normalize().multiplyScalar(R * (1.06 + d * 0.11));
      const c1 = A.clone().lerp(mid, 0.66).normalize().multiplyScalar(mid.length());
      const c2 = B.clone().lerp(mid, 0.66).normalize().multiplyScalar(mid.length());
      const curve = new THREE.CubicBezierCurve3(A, c1, c2, B);
      globe.add(new THREE.Mesh(new THREE.TubeGeometry(curve, mobile ? 48 : 80, mobile ? 0.008 : 0.0065, 5, false), arcMat(i * 0.37, 0.16 + (i % 3) * 0.03)));
    });

    /* two satellite orbits */
    const sats = [[R * 1.3, 1.12, 0.5, 0.33, 0xb9adff], [R * 1.42, -0.5, -0.9, -0.24, 0x7fe9ff]].map(([r, rx, rz, sp, col]) => {
      const o = new THREE.Group(); o.rotation.set(rx, 0, rz); world.add(o);
      const pts = []; for (let k = 0; k <= 180; k++) pts.push(new THREE.Vector3(Math.cos(k / 180 * Math.PI * 2) * r, 0, Math.sin(k / 180 * Math.PI * 2) * r));
      o.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: 0.16 })));
      const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: dot, color: col, transparent: true, blending: ADD, depthWrite: false }));
      s.scale.set(0.22, 0.22, 1); o.add(s);
      return { s, r, sp };
    });

    /* sizing, input, scroll, visibility */
    let W = 1, H = 1;
    function resize() {
      const r = stage.getBoundingClientRect();
      W = Math.max(1, r.width); H = Math.max(1, r.height);
      renderer.setSize(W, H, false);
      camera.aspect = W / H; camera.updateProjectionMatrix();
      if (!running) frame(performance.now());
    }
    const mouse = { x: 0, y: 0, tx: 0, ty: 0 };
    let scrollP = 0;
    if (!reduced) {
      addEventListener("pointermove", (e) => { mouse.tx = (e.clientX / innerWidth) * 2 - 1; mouse.ty = (e.clientY / innerHeight) * 2 - 1; }, { passive: true });
      addEventListener("scroll", () => { scrollP = Math.max(0, Math.min(1, scrollY / (innerHeight * 0.9))); }, { passive: true });
    }

    let last = performance.now(), t = 0, running = false, onScreen = true, raf = 0, slow = 0, sp = 0;
    let fpsAcc = 0, fpsN = 0;
    function frame(now) {
      const dt = Math.min(0.05, (now - last) / 1000); last = now;
      if (running) t += dt;
      uni.time.value = t;
      mouse.x += (mouse.tx - mouse.x) * 0.04; mouse.y += (mouse.ty - mouse.y) * 0.04;
      sp += (scrollP - sp) * 0.08;
      globe.rotation.y = 3.55 + t * 0.045 + mouse.x * 0.15 + sp * 0.9;
      world.rotation.x = 0.36 + mouse.y * 0.05 - sp * 0.18;
      camera.position.set(0, sp * 0.9, 11.6 - sp * 1.4);
      camera.lookAt(0, sp * 0.35, 0);
      for (const m of marks) {
        const k = (t * 0.42 + m.phase) % 1;
        m.ring.scale.setScalar(1 + k * 3.4); m.ring.material.opacity = 0.7 * (1 - k);
      }
      for (const s of sats) { const a = t * s.sp + 1.2; s.s.position.set(Math.cos(a) * s.r, 0, Math.sin(a) * s.r); }
      renderer.render(scene, camera);
      if (running) {
        fpsAcc += dt; fpsN++;
        if (fpsAcc >= 1) { (window.__bucsFps = window.__bucsFps || {}).hero = Math.round(fpsN / fpsAcc); fpsAcc = 0; fpsN = 0; }
        // adaptive pixel ratio: step down in 0.5 steps on sustained slow frames
        if (dt > 0.028) { if (++slow > 60 && dprCap > 1) { dprCap = Math.max(1, dprCap - 0.5); slow = 0; renderer.setPixelRatio(Math.min(devicePixelRatio || 1, dprCap)); resize(); } }
        else { slow = Math.max(0, slow - 1); }
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
    cv.addEventListener("webglcontextlost", (e) => { e.preventDefault(); stop(); stage.classList.remove("gl-ready"); stage.classList.add("no-gl"); });

    resize();
    t = 2.2; frame(performance.now());          // one composed still frame (reduced motion stays here)
    stage.classList.add("gl-ready");
    stage.dataset.land = land.real ? "natural-earth" : "procedural";
    start();
  }
})();
