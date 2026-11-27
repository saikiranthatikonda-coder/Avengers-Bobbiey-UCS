/* BUCS hero: the COMMAND CORE — "the interface between AI and humans", drawn flat so it never
   competes with the 3D globe in the page background.
   You sit at the centre; the ten agents orbit you. Commands pulse out from you to an agent,
   answers pulse back, and agents hand work to each other (JARVIS delegates, DOOM ↔ Widow on
   threats…). Hover or tap an agent for its role. An illustration — not live data.
   Plain 2D canvas, no libraries. Pauses off-screen and in background tabs; a single still frame
   under prefers-reduced-motion. */
(() => {
  "use strict";
  const stage = document.getElementById("hero-core");
  if (!stage) return;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const AG = [
    ["JARVIS", "Primary interface", "#00d9ff"], ["CAPTAIN", "Schedules & briefings", "#3b82f6"],
    ["STARK", "System automation", "#ff6b00"], ["BLACK WIDOW", "Research & intel", "#c74545"],
    ["HAWKEYE", "Vitals monitor", "#a78bfa"], ["HULK", "Heavy compute", "#22c55e"],
    ["THOR", "Announcements", "#facc15"], ["VISION", "Synthesizer", "#e879f9"],
    ["DOOM", "Cyber defense & global awareness", "#2bd46f"],
    ["BOB", "Supreme orchestrator · Bobbiey", "#f2c14e"],
  ];
  const HANDOFF = [[0, 2], [0, 3], [8, 3], [0, 8], [4, 8], [1, 6], [7, 0], [5, 7], [2, 4], [9, 8], [9, 0], [8, 9]];
  const canvas = document.createElement("canvas");
  canvas.className = "hero-core-canvas";
  canvas.setAttribute("aria-hidden", "true");
  stage.appendChild(canvas);
  const tip = document.createElement("div");
  tip.className = "hero-core-tip"; tip.hidden = true;
  stage.appendChild(tip);
  const g = canvas.getContext("2d");
  let W = 0, H = 0, R = 0, cx = 0, cy = 0, dpr = 1, raf = 0, visible = true, last = 0, rot = 0, hover = -1;
  const pulses = [];
  const nodes = AG.map((a, i) => ({ name: a[0], role: a[1], c: a[2], a0: (i / AG.length) * Math.PI * 2 - Math.PI / 2, x: 0, y: 0, glow: 0 }));

  function size() {
    const r = stage.getBoundingClientRect();
    dpr = Math.min(2, window.devicePixelRatio || 1);
    W = r.width; H = r.height; cx = W / 2; cy = H / 2; R = Math.min(W, H) * 0.36;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    canvas.style.width = W + "px"; canvas.style.height = H + "px";
  }
  function place() {
    for (const n of nodes) { const a = n.a0 + rot; n.x = cx + Math.cos(a) * R; n.y = cy + Math.sin(a) * R * 0.92; }
  }
  function spawn() {
    const r = Math.random();
    if (r < 0.55) { const i = (Math.random() * AG.length) | 0; pulses.push({ from: -1, to: i, t: 0, sp: 0.55 + Math.random() * 0.35, back: true }); }
    else { const h = HANDOFF[(Math.random() * HANDOFF.length) | 0]; pulses.push({ from: h[0], to: h[1], t: 0, sp: 0.5 + Math.random() * 0.3, back: false }); }
  }
  const pos = i => i < 0 ? { x: cx, y: cy } : nodes[i];
  function bez(a, b, t, bend) {
    const mx = (a.x + b.x) / 2 + (cy - (a.y + b.y) / 2) * bend, my = (a.y + b.y) / 2 + ((a.x + b.x) / 2 - cx) * bend;
    const u = 1 - t;
    return { x: u * u * a.x + 2 * u * t * mx + t * t * b.x, y: u * u * a.y + 2 * u * t * my + t * t * b.y, mx, my };
  }
  function hexA(h, a) { const n = parseInt(h.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; }

  function draw(now) {
    const t = now / 1000;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    // HUD rings
    g.lineWidth = 1;
    for (const [k, a, dash, sp] of [[1.28, 0.10, [2, 7], 0.05], [0.62, 0.16, [10, 6], -0.12], [0.36, 0.22, [1, 4], 0.3]]) {
      g.save(); g.translate(cx, cy); g.rotate(t * sp * (reduced ? 0 : 1));
      g.strokeStyle = `rgba(126,234,255,${a})`; g.setLineDash(dash);
      g.beginPath(); g.ellipse(0, 0, R * k, R * k * 0.92, 0, 0, Math.PI * 2); g.stroke(); g.restore();
    }
    g.setLineDash([]);
    g.strokeStyle = "rgba(126,234,255,0.14)";
    g.beginPath(); g.ellipse(cx, cy, R, R * 0.92, 0, 0, Math.PI * 2); g.stroke();
    // ticks
    for (let i = 0; i < 72; i++) {
      const a = (i / 72) * Math.PI * 2, r1 = R * 1.12, r2 = R * (i % 6 ? 1.15 : 1.19);
      g.strokeStyle = `rgba(126,234,255,${i % 6 ? 0.12 : 0.3})`;
      g.beginPath(); g.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1 * 0.92); g.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2 * 0.92); g.stroke();
    }
    // spokes
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      const grd = g.createLinearGradient(cx, cy, n.x, n.y);
      grd.addColorStop(0, "rgba(126,234,255,0.05)"); grd.addColorStop(1, hexA(n.c, hover === i ? 0.55 : 0.22));
      g.strokeStyle = grd; g.lineWidth = hover === i ? 1.6 : 1;
      g.beginPath(); g.moveTo(cx, cy); g.lineTo(n.x, n.y); g.stroke();
    }
    // pulses
    for (const p of pulses) {
      const a = pos(p.from), b = pos(p.to), bend = p.from < 0 ? 0 : 0.28;
      const col = p.from < 0 ? (p.phase ? nodes[p.to].c : "#e8fbff") : nodes[p.from].c;
      if (p.from >= 0) {                                       // hand-off arc trail
        const m = bez(a, b, 0, bend);
        g.strokeStyle = hexA(col, 0.12 * (1 - p.t)); g.lineWidth = 1;
        g.beginPath(); g.moveTo(a.x, a.y); g.quadraticCurveTo(m.mx, m.my, b.x, b.y); g.stroke();
      }
      const tt = p.phase ? 1 - p.t : p.t;
      for (let k = 0; k < 5; k++) {
        const q = bez(a, b, Math.max(0, tt - k * (p.phase ? -0.025 : 0.025)), bend);
        g.fillStyle = hexA(col, (1 - k / 5) * 0.9);
        g.beginPath(); g.arc(q.x, q.y, 2.4 - k * 0.35, 0, Math.PI * 2); g.fill();
      }
    }
    // centre: you
    const breathe = reduced ? 0.5 : (Math.sin(t * 1.6) + 1) / 2;
    const cg = g.createRadialGradient(cx, cy, 0, cx, cy, R * 0.34);
    cg.addColorStop(0, `rgba(126,234,255,${0.28 + breathe * 0.12})`); cg.addColorStop(1, "rgba(126,234,255,0)");
    g.fillStyle = cg; g.beginPath(); g.arc(cx, cy, R * 0.34, 0, Math.PI * 2); g.fill();
    // a solid core: pulses and spokes pass UNDER it, so nothing ever crosses the label
    const hr = Math.max(26, R * 0.2);
    const hex = (r) => { g.beginPath(); for (let i = 0; i < 6; i++) { const a = i * Math.PI / 3 + Math.PI / 6; g[i ? "lineTo" : "moveTo"](cx + Math.cos(a) * r, cy + Math.sin(a) * r); } g.closePath(); };
    hex(hr); g.fillStyle = "rgba(3,14,24,0.96)"; g.fill();
    g.strokeStyle = "rgba(126,234,255,0.9)"; g.lineWidth = 1.6; g.stroke();
    hex(hr * 0.84); g.strokeStyle = `rgba(126,234,255,${0.18 + breathe * 0.2})`; g.lineWidth = 1; g.stroke();
    g.textAlign = "center"; g.textBaseline = "middle";
    let fs = Math.max(11, hr * 0.5);
    g.font = `800 ${fs}px Orbitron, "Share Tech Mono", monospace`;
    while (g.measureText("YOU").width > hr * 1.15 && fs > 9) { fs -= 1; g.font = `800 ${fs}px Orbitron, "Share Tech Mono", monospace`; }
    g.shadowColor = "rgba(126,234,255,0.8)"; g.shadowBlur = 10;
    g.fillStyle = "#f2fdff"; g.fillText("YOU", cx, cy + 1);
    g.shadowBlur = 0;
    // caption sits clear of the hexagon, on its own dark pill
    const cap = "COMMANDER · YOU APPROVE", cfs = Math.max(8, R * 0.042);
    g.font = `600 ${cfs}px "Share Tech Mono", monospace`;
    const cw = g.measureText(cap).width + 14, chh = cfs + 8, cyy = cy + hr + chh * 0.5 + 8;
    g.fillStyle = "rgba(3,14,24,0.9)"; g.strokeStyle = "rgba(126,234,255,0.35)"; g.lineWidth = 1;
    g.beginPath(); if (g.roundRect) g.roundRect(cx - cw / 2, cyy - chh / 2, cw, chh, chh / 2); else g.rect(cx - cw / 2, cyy - chh / 2, cw, chh);
    g.fill(); g.stroke();
    g.fillStyle = "rgba(190,230,245,0.9)"; g.fillText(cap, cx, cyy + 0.5);
    // agents
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i], hot = hover === i, r = Math.max(5, R * (hot ? 0.07 : 0.055)) + n.glow * 3;
      const ng = g.createRadialGradient(n.x, n.y, 0, n.x, n.y, r * 3.2);
      ng.addColorStop(0, hexA(n.c, 0.45 + n.glow * 0.4)); ng.addColorStop(1, hexA(n.c, 0));
      g.fillStyle = ng; g.beginPath(); g.arc(n.x, n.y, r * 3.2, 0, Math.PI * 2); g.fill();
      g.fillStyle = "#04101a"; g.strokeStyle = n.c; g.lineWidth = hot ? 2 : 1.4;
      g.beginPath(); g.arc(n.x, n.y, r, 0, Math.PI * 2); g.fill(); g.stroke();
      g.fillStyle = n.c; g.beginPath(); g.arc(n.x, n.y, r * 0.38, 0, Math.PI * 2); g.fill();
      const out = Math.atan2(n.y - cy, n.x - cx);
      const lx = n.x + Math.cos(out) * (r + 12), ly = n.y + Math.sin(out) * (r + 10);
      g.textAlign = Math.cos(out) > 0.3 ? "left" : Math.cos(out) < -0.3 ? "right" : "center";
      g.font = `600 ${Math.max(8, R * 0.048)}px Orbitron, "Share Tech Mono", monospace`;
      g.fillStyle = hot ? "#ffffff" : "rgba(220,240,255,0.82)";
      g.fillText(n.name, lx, ly);
      n.glow *= 0.94;
    }
  }

  function step(now) {
    raf = 0;
    if (!visible || document.hidden) return;
    const dt = last ? Math.min(0.05, (now - last) / 1000) : 0.016; last = now;
    rot += dt * 0.045;
    place();
    if (Math.random() < dt * 2.2 && pulses.length < 14) spawn();
    for (let i = pulses.length - 1; i >= 0; i--) {
      const p = pulses[i];
      p.t += dt * p.sp;
      if (p.t >= 1) {
        if (p.from < 0 && p.back && !p.phase) { p.phase = 1; p.t = 0; nodes[p.to].glow = 1; continue; }  // command arrived → answer heads back
        if (p.from >= 0) nodes[p.to].glow = 1;                                                            // hand-off arrived
        pulses.splice(i, 1);
      }
    }
    draw(now);
    raf = requestAnimationFrame(step);
  }
  function kick() { if (!raf && visible && !document.hidden && !reduced) { last = 0; raf = requestAnimationFrame(step); } }
  function still() { place(); for (let i = 0; i < 5; i++) pulses.push({ from: i % 2 ? -1 : HANDOFF[i][0], to: HANDOFF[i][1], t: 0.3 + i * 0.12, sp: 0, back: false }); draw(0); }

  function pick(ev) {
    const r = canvas.getBoundingClientRect(), x = ev.clientX - r.left, y = ev.clientY - r.top;
    let best = -1, bd = (R * 0.16) ** 2;
    nodes.forEach((n, i) => { const d = (n.x - x) ** 2 + (n.y - y) ** 2; if (d < bd) { bd = d; best = i; } });
    hover = best;
    if (best >= 0) {
      const n = nodes[best];
      tip.innerHTML = `<b style="color:${n.c}">${n.name}</b><span>${n.role}</span>`;
      tip.style.left = Math.min(W - 10, Math.max(10, n.x)) + "px"; tip.style.top = (n.y - R * 0.12) + "px";
      tip.hidden = false; canvas.style.cursor = "pointer";
    } else { tip.hidden = true; canvas.style.cursor = ""; }
    if (reduced) draw(0);
  }
  canvas.addEventListener("pointermove", pick);
  canvas.addEventListener("pointerdown", pick);
  canvas.addEventListener("pointerleave", () => { hover = -1; tip.hidden = true; if (reduced) draw(0); });

  size();
  if (reduced) still(); else kick();
  stage.classList.add("ready");
  new ResizeObserver(() => { size(); place(); if (reduced) still(); else draw(performance.now()); }).observe(stage);
  if ("IntersectionObserver" in window) new IntersectionObserver(es => { visible = es[0].isIntersecting; kick(); }).observe(stage);
  document.addEventListener("visibilitychange", kick);
})();
