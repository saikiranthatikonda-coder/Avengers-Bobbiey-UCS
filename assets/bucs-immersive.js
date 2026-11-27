/* BUCS immersive layer.
   1. Background: a fixed MapLibre GL globe over Esri World Imagery that flies from orbit down to
      street level as the page scrolls (mouse / device tilt add a gentle parallax). Lazy: loaded
      after first paint, never with prefers-reduced-motion, without WebGL or if the CDN fails —
      the page then keeps its existing static background.
   2. Location: the browser's own permission prompt is shown when the page opens. Allowed → the
      background flies to the visitor's exact position; declined / unavailable → a demo landmark.
      Coordinates stay in memory (never stored, never in the URL, never sent to our servers).
      Weather comes from Open-Meteo for the shown target.
   3. DOOM emblem, hands-free / map blocks and small, harmless easter eggs.
   Honesty rule: imagery is archival ("not a live feed"); every live reading names its source and
   shows "unavailable" when its source cannot be reached. Nothing is simulated. */
(() => {
  "use strict";

  /* ───────── providers: swap here ───────── */
  const PROVIDERS = {
    maplibre: {
      // 6.4.1 fixes GHSA-jrc7-96c5-q579; ES-module only, so it is loaded with import()
      js: "https://unpkg.com/maplibre-gl@6.4.1/dist/maplibre-gl.mjs",
      css: "https://unpkg.com/maplibre-gl@6.4.1/dist/maplibre-gl.css",
    },
    imagery: {
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
      maxzoom: 19, tileSize: 256,
      attribution: "Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    },
    labels: {
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"],
      maxzoom: 19, tileSize: 256, attribution: "Labels © Esri",
    },
    terrain: {
      tiles: ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"],
      maxzoom: 15, tileSize: 256, encoding: "terrarium",
      attribution: "Terrain: Mapzen/AWS Open Data",
    },
    weather: {
      url: (lat, lon) => `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m,weather_code,wind_speed_10m&timezone=auto`,
      label: "Open-Meteo.com (CC BY 4.0)",
    },
  };
  const DEMO = { name: "Midtown Manhattan, New York", lat: 40.7484, lon: -73.9857, tz: "America/New_York" };

  const $ = (s, r = document) => r.querySelector(s);
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
  const lerp = (a, b, t) => a + (b - a) * t;
  const wrapLon = (d) => ((d + 540) % 360) - 180;

  /* ───────── toast ───────── */
  let toastEl = null, toastT = 0;
  function toast(text, tone) {
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.className = "imm-toast"; toastEl.setAttribute("role", "status"); toastEl.setAttribute("aria-live", "polite");
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = text;
    toastEl.classList.toggle("cy", tone === "cy");
    toastEl.classList.add("show");
    clearTimeout(toastT);
    toastT = setTimeout(() => toastEl.classList.remove("show"), 3600);
  }

  /* ───────── target (demo or visitor), shared by map + readouts ───────── */
  const target = { name: DEMO.name, lat: DEMO.lat, lon: DEMO.lon, tz: DEMO.tz, mine: false };
  const listeners = [];
  const onTarget = (fn) => listeners.push(fn);
  function setTarget(t) { Object.assign(target, t); listeners.forEach((fn) => fn(target)); }

  /* ───────── close-up readouts: local time, sun, weather ───────── */
  // Solar elevation (degrees) from a standard low-precision solar position model.
  function sunElevation(lat, lon, date) {
    const rad = Math.PI / 180, d = date.getTime() / 86400000 - 10957.5; // days since J2000.0
    const g = (357.529 + 0.98560028 * d) * rad;
    const q = 280.459 + 0.98564736 * d;
    const L = (q + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * rad;
    const e = (23.439 - 0.00000036 * d) * rad;
    const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L));
    const dec = Math.asin(Math.sin(e) * Math.sin(L));
    const gmst = ((18.697374558 + 24.06570982441908 * d) % 24 + 24) % 24;
    const H = (gmst * 15 + lon) * rad - ra;
    return Math.asin(Math.sin(lat * rad) * Math.sin(dec) + Math.cos(lat * rad) * Math.cos(dec) * Math.cos(H)) / rad;
  }
  const WMO = { 0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Rain showers", 81: "Rain showers",
    82: "Violent rain showers", 85: "Snow showers", 86: "Snow showers", 95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with hail" };

  (() => {
    const R = { where: $("#orb-where"), time: $("#orb-time"), sun: $("#orb-sun"), wx: $("#orb-wx") };
    if (!R.where) return;
    let wxFor = "", wxSeq = 0, started = false;
    const fmtTime = () => {
      try {
        return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", weekday: "short", timeZone: target.tz || undefined, timeZoneName: "short" }).format(new Date());
      } catch (e) { return new Date().toLocaleTimeString(); }
    };
    const paint = () => {
      R.where.innerHTML = target.mine
        ? `Your location <small>${target.lat.toFixed(3)}, ${target.lon.toFixed(3)} · stays in your browser</small>`
        : `${DEMO.name} <small>demo location · location not shared</small>`;
      R.time.innerHTML = `${fmtTime()} <small>computed in your browser</small>`;
      const el = sunElevation(target.lat, target.lon, new Date());
      const state = el > -0.833 ? ["up", "Sun up"] : el > -6 ? ["down", "Twilight"] : ["down", "Sun down"];
      R.sun.innerHTML = `<span class="${state[0]}">${state[1]}</span> · ${el.toFixed(1)}° elevation <small>computed from solar position</small>`;
    };
    const weather = async () => {
      // ~1 km precision is plenty for current weather and keeps the request coarse
      const lat = target.lat.toFixed(2), lon = target.lon.toFixed(2), key = lat + "," + lon;
      if (key === wxFor) return;
      wxFor = key; const seq = ++wxSeq;
      R.wx.innerHTML = `loading… <small>source: ${PROVIDERS.weather.label}</small>`;
      try {
        const ctl = new AbortController(); const to = setTimeout(() => ctl.abort(), 9000);
        const r = await fetch(PROVIDERS.weather.url(lat, lon), { signal: ctl.signal, referrerPolicy: "no-referrer", credentials: "omit" });
        clearTimeout(to);
        if (!r.ok) throw new Error("http " + r.status);
        const j = await r.json(), c = j && j.current;
        if (seq !== wxSeq) return;
        if (!c || typeof c.temperature_2m !== "number") throw new Error("no data");
        const u = j.current_units || {};
        const desc = WMO[c.weather_code] || ("WMO code " + c.weather_code);
        R.wx.innerHTML = `${Math.round(c.temperature_2m)}${u.temperature_2m || "°C"} · ${desc} · wind ${Math.round(c.wind_speed_10m)} ${u.wind_speed_10m || "km/h"}
          <small>source: ${PROVIDERS.weather.label} · model reading for ${String(c.time || "").replace("T", " ")} local</small>`;
      } catch (e) {
        if (seq !== wxSeq) return;
        wxFor = "";
        R.wx.innerHTML = `unavailable <small>${PROVIDERS.weather.label} could not be reached</small>`;
      }
    };
    paint();
    setInterval(() => { if (!document.hidden) paint(); }, 30000);
    // fetch weather only once the close-up stage is near, never on page load
    const sec = $("#orbit");
    if (sec) new IntersectionObserver((es) => {
      if (es[0].isIntersecting) { started = true; weather(); }
    }, { rootMargin: "200px 0px" }).observe(sec);
    onTarget(() => { paint(); if (started) weather(); });
  })();

  /* ───────── location: asked once when the page opens (browser permission prompt) ───────── */
  function locateOnLoad() {
    if (!("geolocation" in navigator) || !window.isSecureContext) return;      // demo location stays
    navigator.geolocation.getCurrentPosition((p) => {
      let tz;
      try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { tz = undefined; }
      setTarget({ name: "Your location", lat: p.coords.latitude, lon: p.coords.longitude, tz, mine: true });
    }, () => { /* declined or unavailable: keep the demo location */ },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 300000 });
  }
  locateOnLoad();
  const whereLines = () => document.querySelectorAll("[data-imm-where]").forEach((w) => {
    w.innerHTML = target.mine ? `<b class="you">your location · stays in your browser</b>` : `demo location · ${DEMO.name}`;
  });
  whereLines(); onTarget(whereLines);

  /* ───────── background map ───────── */
  function webglOK() {
    try { const c = document.createElement("canvas"); return !!(window.WebGLRenderingContext && (c.getContext("webgl2") || c.getContext("webgl"))); }
    catch (e) { return false; }
  }
  function loadCSS(href) {
    const l = document.createElement("link"); l.rel = "stylesheet"; l.href = href; l.crossOrigin = "anonymous"; document.head.appendChild(l);
  }
  function loadJS(src) {
    return import(src).then((m) => {
      const ml = m.default && m.default.Map ? m.default : m;
      if (!ml || !ml.Map) throw new Error("maplibre missing");
      window.maplibregl = ml;
      return ml;
    });
  }

  function startMap() {
    const host = $("#imm-map");
    if (!host || reduced || !webglOK()) return;
    loadCSS(PROVIDERS.maplibre.css);
    loadJS(PROVIDERS.maplibre.js).then(buildMap).catch(() => { /* keep the static background */ });
  }

  function buildMap(ml) {
    const host = $("#imm-map"), root = document.documentElement, alt = $("#imm-alt");
    const orbitSec = $("#orbit");
    let W = innerWidth, H = innerHeight;
    const z0 = () => clamp(Math.log2(Math.min(W, H) / 512) + 1.05, 0.2, 2.6); // whole globe in view
    const Z1 = 17.4;
    const START_LON = -62, START_LAT = 18; // the globe turns toward the target while you scroll
    const style = {
      version: 8,
      projection: { type: "globe" },
      sources: {
        imagery: { type: "raster", tiles: PROVIDERS.imagery.tiles, tileSize: PROVIDERS.imagery.tileSize, maxzoom: PROVIDERS.imagery.maxzoom, attribution: PROVIDERS.imagery.attribution },
        labels: { type: "raster", tiles: PROVIDERS.labels.tiles, tileSize: PROVIDERS.labels.tileSize, maxzoom: PROVIDERS.labels.maxzoom, attribution: PROVIDERS.labels.attribution },
        dem: { type: "raster-dem", tiles: PROVIDERS.terrain.tiles, tileSize: PROVIDERS.terrain.tileSize, maxzoom: PROVIDERS.terrain.maxzoom, encoding: PROVIDERS.terrain.encoding, attribution: PROVIDERS.terrain.attribution },
      },
      sky: {
        "sky-color": "#04060c", "horizon-color": "#0a3a5c", "fog-color": "#04060c",
        "sky-horizon-blend": 0.6, "horizon-fog-blend": 0.6, "fog-ground-blend": 0.9,
        "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 6, 0.6, 10, 0],
      },
      layers: [
        { id: "imagery", type: "raster", source: "imagery", paint: { "raster-fade-duration": 300, "raster-contrast": 0.08, "raster-saturation": 0.08 } },
        { id: "labels", type: "raster", source: "labels", minzoom: 2.5, paint: { "raster-opacity": 0.7, "raster-fade-duration": 300 } },
      ],
    };
    let map;
    try {
      map = new ml.Map({
        container: host, style, interactive: false, attributionControl: false,
        pixelRatio: Math.min(window.devicePixelRatio || 1, 2),
        center: [wrapLon(target.lon + START_LON), START_LAT], zoom: z0(), pitch: 0, bearing: 0,
        maxPitch: 75, renderWorldCopies: false, fadeDuration: 200, maxTileCacheSize: 400,
      });
    } catch (e) { return; }
    // tile hiccups are expected on flaky networks; keep them out of the console
    map.on("error", () => {});
    let alive = false;
    const failT = setTimeout(() => { if (!alive) teardown(); }, 20000);
    function teardown() {
      try { map.remove(); } catch (e) { /* already gone */ }
      root.classList.remove("imm-on"); host.classList.remove("on");
    }

    // camera state: scroll gives the base, parallax adds a small offset, retarget arcs the flight
    const S = { t: 0, extra: 0, px: 0, py: 0, tpx: 0, tpy: 0,
      cur: null, from: { lat: target.lat, lon: target.lon }, to: { lat: target.lat, lon: target.lon }, k: 1, kStart: 0 };
    let endY = 1, raf = 0, last = 0, terrainOn = false;

    function measure() {
      W = innerWidth; H = innerHeight;
      const max = Math.max(1, document.documentElement.scrollHeight - H);
      endY = orbitSec ? clamp(orbitSec.offsetTop + orbitSec.offsetHeight / 2 - H / 2, H, max) : max;
    }
    function readScroll() {
      const y = window.scrollY || 0;
      S.t = clamp(y / endY, 0, 1);
      S.extra = Math.max(0, (y - endY) / H);
      // scrim: lighter while the close-up stage is on screen so the imagery shows
      let scrim = 0.74;
      if (orbitSec) {
        const r = orbitSec.getBoundingClientRect();
        const vis = clamp(Math.min(r.bottom, H) - Math.max(r.top, 0), 0, H) / H;
        scrim = lerp(0.74, 0.2, smooth(0.15, 0.7, vis));
      }
      root.style.setProperty("--imm-scrim", scrim.toFixed(3));
      root.style.setProperty("--imm-stars", (1 - smooth(0.05, 0.3, S.t)).toFixed(3));
    }
    function desired(now) {
      const t = S.t;
      // retarget: interpolate the target and arc out to a lower zoom mid-flight
      let k = 1;
      if (S.k < 1) { k = clamp((now - S.kStart) / 3200, 0, 1); S.k = k; }
      const ek = k * k * (3 - 2 * k);
      const tLat = lerp(S.from.lat, S.to.lat, ek);
      const tLon = S.from.lon + wrapLon(S.to.lon - S.from.lon) * ek;
      const u = smooth(0, 0.36, t);
      const lon = wrapLon(tLon + START_LON * (1 - u));
      const lat = lerp(START_LAT, tLat, u);
      // at the very top only the planet's limb rises from the bottom edge, like a horizon seen from orbit
      const rise = smooth(0, 0.12, t);
      let zoom = lerp(z0(), Z1, Math.pow(t, 1.12)) + 0.5 * (1 - rise);
      if (k < 1) zoom = Math.max(z0(), zoom - Math.sin(Math.PI * k) * Math.max(0, zoom - 4));
      const pitch = clamp(62 * smooth(0.4, 1, t) + S.py * 6, 0, 72);
      const bearing = -28 * smooth(0.45, 1, t) - S.extra * 22 + S.px * 9;
      return { lon, lat, zoom, pitch, bearing, shift: (W < H ? 0.42 : 0.6) * (1 - rise) };
    }
    function frame(now) {
      raf = 0;
      if (document.hidden) return;
      const dt = Math.min(64, now - (last || now)); last = now;
      const a = 1 - Math.exp(-dt / 140);
      S.px += (S.tpx - S.px) * a; S.py += (S.tpy - S.py) * a;
      const d = desired(now);
      if (!S.cur) S.cur = { ...d };
      const c = S.cur;
      c.lon = wrapLon(c.lon + wrapLon(d.lon - c.lon) * a);
      c.lat += (d.lat - c.lat) * a; c.zoom += (d.zoom - c.zoom) * a;
      c.pitch += (d.pitch - c.pitch) * a; c.bearing += (d.bearing - c.bearing) * a; c.shift += (d.shift - c.shift) * a;
      host.style.transform = c.shift > 0.001 ? `translate3d(0,${(c.shift * H).toFixed(1)}px,0)` : "";
      try { map.jumpTo({ center: [c.lon, c.lat], zoom: c.zoom, pitch: c.pitch, bearing: c.bearing }); } catch (e) { /* map gone */ }
      // terrain only when it adds something (close-up), with hysteresis
      if (!terrainOn && c.zoom > 9.5) { try { map.setTerrain({ source: "dem", exaggeration: 1.25 }); terrainOn = true; } catch (e) { terrainOn = true; } }
      else if (terrainOn && c.zoom < 8.5) { try { map.setTerrain(null); } catch (e) { /* ignore */ } terrainOn = false; }
      if (alt) {
        const mpp = 156543.03 * Math.cos(c.lat * Math.PI / 180) / Math.pow(2, c.zoom);
        const km = (mpp * H * 1.5) / 1000;
        alt.textContent = km >= 10 ? `≈ ${Math.round(km).toLocaleString()} km` : `≈ ${Math.max(0.05, km).toFixed(km < 1 ? 2 : 1)} km`;
      }
      const moving = Math.abs(wrapLon(d.lon - c.lon)) + Math.abs(d.lat - c.lat) > 1e-6 || Math.abs(d.zoom - c.zoom) > 1e-3 ||
        Math.abs(d.pitch - c.pitch) > 0.02 || Math.abs(d.bearing - c.bearing) > 0.02 || Math.abs(d.shift - c.shift) > 1e-3 || S.k < 1 ||
        Math.abs(S.tpx - S.px) > 1e-3 || Math.abs(S.tpy - S.py) > 1e-3;
      if (moving) kick();
    }
    function kick() { if (!raf && !document.hidden && alive) raf = requestAnimationFrame(frame); }

    map.once("load", () => {
      alive = true; clearTimeout(failT);
      measure(); readScroll();
      host.classList.add("on"); root.classList.add("imm-on");
      kick();
    });
    addEventListener("scroll", () => { readScroll(); kick(); }, { passive: true });
    addEventListener("resize", () => { measure(); readScroll(); kick(); }, { passive: true });
    // layout changes (fonts, the sticky showcase) move the anchor: re-measure occasionally
    setTimeout(() => { measure(); readScroll(); kick(); }, 2500);
    if ("ResizeObserver" in window) new ResizeObserver(() => { measure(); readScroll(); kick(); }).observe(document.body);
    document.addEventListener("visibilitychange", () => { last = 0; if (!document.hidden) kick(); });

    onTarget((t) => {
      const now = performance.now();
      const ek = S.k < 1 ? S.k * S.k * (3 - 2 * S.k) : 1;
      S.from = { lat: lerp(S.from.lat, S.to.lat, ek), lon: S.from.lon + wrapLon(S.to.lon - S.from.lon) * ek };
      S.to = { lat: t.lat, lon: t.lon };
      S.k = 0; S.kStart = now;
      kick();
    });

    // parallax: mouse on desktop
    addEventListener("pointermove", (e) => {
      if (e.pointerType !== "mouse") return;
      S.tpx = clamp((e.clientX / W - 0.5) * 2, -1, 1); S.tpy = clamp((e.clientY / H - 0.5) * 2, -1, 1);
      kick();
    }, { passive: true });
    // parallax: device tilt on touch devices (iOS asks permission, only after a tap on the chip)
    let base = null;
    const onTilt = (e) => {
      if (e.gamma == null || e.beta == null) return;
      if (!base) base = { b: e.beta };
      S.tpx = clamp(e.gamma / 35, -1, 1); S.tpy = clamp((e.beta - base.b) / 35, -1, 1);
      kick();
    };
    const coarse = matchMedia("(pointer: coarse)").matches;
    if (coarse && "DeviceOrientationEvent" in window) {
      if (typeof DeviceOrientationEvent.requestPermission === "function") {
        addEventListener("touchend", () => {
          DeviceOrientationEvent.requestPermission().then((r) => {
            if (r === "granted") addEventListener("deviceorientation", onTilt, { passive: true });
          }).catch(() => { /* tilt unavailable: mouse/scroll still drive the globe */ });
        }, { once: true, passive: true });
      } else addEventListener("deviceorientation", onTilt, { passive: true });
    }
  }

  if (!reduced) {
    const go = () => ("requestIdleCallback" in window ? requestIdleCallback(startMap, { timeout: 2500 }) : setTimeout(startMap, 600));
    if (document.readyState === "complete") go(); else addEventListener("load", go, { once: true });
  }

  /* ───────── easter eggs (keyboard-safe, harmless) ───────── */
  const typing = (el) => el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  const KONAMI = ["arrowup", "arrowup", "arrowdown", "arrowdown", "arrowleft", "arrowright", "arrowleft", "arrowright", "b", "a"];
  let kIdx = 0, word = "", doomT = 0;
  addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
    const k = (e.key || "").toLowerCase();
    kIdx = k === KONAMI[kIdx] ? kIdx + 1 : (k === KONAMI[0] ? 1 : 0);
    if (kIdx === KONAMI.length) {
      kIdx = 0;
      document.documentElement.classList.add("doom-mode");
      clearTimeout(doomT); doomT = setTimeout(() => document.documentElement.classList.remove("doom-mode"), 6000);
      toast("Doom approves of your persistence.");
    }
    if (k.length === 1 && /[a-z]/.test(k)) {
      word = (word + k).slice(-12);
      if (word.endsWith("jarvis")) { word = ""; toast("At your service.", "cy"); }
    }
  });
  const emblem = $("#doom-emblem");
  if (emblem) {
    let n = 0, t0 = 0;
    emblem.addEventListener("click", () => {
      const now = Date.now();
      n = now - t0 < 1500 ? n + 1 : 1; t0 = now;
      if (n >= 3) { n = 0; toast("Doom does not do demos. (He does, actually — download BUCS.)"); }
    });
  }
  try {
    console.log("%cBUCS%c  Hello, developer. The command center runs on your machine, and every action waits for its operator.\n" +
      "Hints for the curious: ↑ ↑ ↓ ↓ ← → ← → B A · type a certain butler's name · knock three times on the green emblem.",
      "font:900 14px Orbitron,sans-serif;color:#00d9ff;background:#04060c;padding:4px 8px;border-radius:4px",
      "color:#7e96b3");
  } catch (e) { /* no console */ }
})();
