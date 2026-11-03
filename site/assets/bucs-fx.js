/* BUCS site effects: agent constellation, command-console demo, 3D tilt cards,
   count-up stats, scroll-driven dashboard tilt, voice-state demo.
   No dependencies. Everything pauses off-screen and respects prefers-reduced-motion. */
(() => {
  "use strict";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const fine = matchMedia("(hover: hover) and (pointer: fine)").matches;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  /* run cb(true/false) when el enters/leaves the viewport */
  function watch(el, cb, margin = "0px") {
    if (!el) return;
    new IntersectionObserver((es) => cb(es[0].isIntersecting), { rootMargin: margin }).observe(el);
  }

  /* ─── count-up stats (only real, documented numbers live in the markup) ─── */
  $$("[data-count]").forEach((el) => {
    const end = parseFloat(el.dataset.count), suf = el.dataset.suffix || "";
    if (reduced) { el.textContent = end + suf; return; }
    el.textContent = "0" + suf;
    let done = false;
    watch(el, (on) => {
      if (!on || done) return; done = true;
      const t0 = performance.now(), dur = 1400;
      const step = (now) => {
        const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
        el.textContent = Math.round(end * e) + suf;
        if (k < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    });
  });

  /* ─── 3D tilt cards ─── */
  if (fine && !reduced) {
    $$(".tilt").forEach((c) => {
      let raf = 0;
      c.addEventListener("pointermove", (e) => {
        const r = c.getBoundingClientRect(), px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => {
          c.style.setProperty("--rx", ((0.5 - py) * 10).toFixed(2) + "deg");
          c.style.setProperty("--ry", ((px - 0.5) * 12).toFixed(2) + "deg");
          c.style.setProperty("--mx", (px * 100).toFixed(1) + "%");
          c.style.setProperty("--my", (py * 100).toFixed(1) + "%");
        });
      });
      c.addEventListener("pointerleave", () => { c.style.setProperty("--rx", "0deg"); c.style.setProperty("--ry", "0deg"); });
    });
  }

  /* ─── scroll-driven dashboard landing ─── */
  const shot = $(".shot");
  if (shot && !reduced) {
    let live = false, raf = 0;
    const upd = () => {
      raf = 0;
      const r = shot.getBoundingClientRect(), vh = innerHeight;
      const p = Math.max(0, Math.min(1, (vh - r.top) / (vh * 0.75)));
      shot.style.setProperty("--p", p.toFixed(3));
    };
    watch(shot, (on) => { live = on; if (on) upd(); }, "100px");
    addEventListener("scroll", () => { if (live && !raf) raf = requestAnimationFrame(upd); }, { passive: true });
    upd();
  }

  /* ─── agent constellation ─── */
  const AGENTS = [
    { n: "JARVIS", role: "Primary interface", c: "#00d9ff", wake: "“Jarvis …”",
      d: "Your conversation partner and the orchestrator. JARVIS reads live conditions, issues directives and hands work to the right specialist over a shared context blackboard." },
    { n: "CAPTAIN", role: "Schedules & briefings", c: "#3b82f6", wake: "“Hey Cap …”",
      d: "Owns your day: Google Calendar sync, meeting countdowns, conflict and focus-block checks, and spoken daily briefings." },
    { n: "STARK", role: "System automation", c: "#ff6b00", wake: "“Hey Stark …”",
      d: "Runs the machine: system scans, Windows-native actions, smart-device commands and the automations you approve." },
    { n: "BLACK WIDOW", role: "Research & intel", c: "#ff5a5a", wake: "“Hey Widow …”",
      d: "Gathers intelligence: live world news, threat signals and disaster events, condensed into what actually affects you." },
    { n: "HAWKEYE", role: "Vitals monitor", c: "#a78bfa", wake: "“Hey Hawkeye …”",
      d: "Never blinks: CPU, memory, disk, network and fleet nodes, with 15-minute trend forecasts that warn before things break." },
    { n: "HULK", role: "Heavy compute", c: "#22c55e", wake: "“Hey Hulk …”",
      d: "Takes the heavy lifting: long-running jobs and AI inference, routed to whichever local or fleet GPU has headroom." },
    { n: "THOR", role: "Announcements", c: "#facc15", wake: "“Hey Thor …”",
      d: "The voice of the system: spoken alerts and the four-tier emergency broadcast, from info to emergency." },
    { n: "VISION", role: "Synthesizer", c: "#e879f9", wake: "“Hey Vision …”",
      d: "Connects the dots: reads every panel and every agent, and surfaces the single observation that matters right now." },
  ];
  const con = $("#constellation");
  if (con) {
    const svg = $("svg", con), detail = $("#agent-detail");
    const NS = "http://www.w3.org/2000/svg";
    const btns = AGENTS.map((a, i) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "agent-node" + (i === 0 ? " core" : "");
      b.style.setProperty("--c", a.c);
      b.setAttribute("aria-label", `${a.n}: ${a.role}`);
      b.innerHTML = `<i></i><span>${a.n}</span>`;
      con.appendChild(b);
      return b;
    });
    const lines = AGENTS.slice(1).map(() => { const l = document.createElementNS(NS, "line"); l.setAttribute("class", "spoke"); svg.appendChild(l); return l; });
    const ring = AGENTS.slice(1).map(() => { const l = document.createElementNS(NS, "line"); l.setAttribute("class", "mesh"); svg.appendChild(l); return l; });
    let sel = 0, ang = 0, hover = false, tiltX = 0.42, tx = 0.42, ry = 0, rty = 0, running = false, raf = 0, last = 0;

    function show(i) {
      sel = i;
      btns.forEach((b, k) => b.classList.toggle("on", k === i));
      const a = AGENTS[i];
      detail.style.setProperty("--c", a.c);
      detail.innerHTML = `<div class="ad-tag">AGENT ${String(i + 1).padStart(2, "0")} / 08</div>
        <h3>${a.n}</h3><div class="ad-role">${a.role}</div><p>${a.d}</p>
        <div class="ad-wake"><span>WAKE</span>${a.wake}</div>`;
    }
    btns.forEach((b, i) => {
      b.addEventListener("click", () => show(i));
      b.addEventListener("focus", () => show(i));
      b.addEventListener("pointerenter", () => show(i));
    });
    con.addEventListener("pointerenter", () => (hover = true));
    con.addEventListener("pointerleave", () => { hover = false; tx = 0.42; rty = 0; });
    con.addEventListener("pointermove", (e) => {
      const r = con.getBoundingClientRect();
      tx = 0.42 + ((e.clientY - r.top) / r.height - 0.5) * 0.5;
      rty = ((e.clientX - r.left) / r.width - 0.5) * 0.6;
    });

    function layout(dt) {
      const W = con.clientWidth, H = con.clientHeight, cx = W / 2, cy = H / 2;
      const R = Math.min(W * 0.42, H * 0.72);
      if (!hover && !reduced) ang += dt * 0.12;
      tiltX += (tx - tiltX) * 0.06; ry += (rty - ry) * 0.06;
      svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
      const pts = [{ x: cx, y: cy, z: 0 }];
      for (let k = 1; k < AGENTS.length; k++) {
        const a = ang + ry + (k - 1) / (AGENTS.length - 1) * Math.PI * 2;
        const x = Math.cos(a) * R, z = Math.sin(a) * R;
        const y = z * Math.sin(tiltX) + Math.sin(a * 3 + k) * R * 0.06;
        pts.push({ x: cx + x, y: cy + y * 0.9, z: z * Math.cos(tiltX) });
      }
      pts.forEach((p, k) => {
        const s = k === 0 ? 1.25 : 0.78 + (p.z / R + 1) * 0.22;
        const b = btns[k];
        b.style.transform = `translate(${(p.x).toFixed(1)}px,${(p.y).toFixed(1)}px) translate(-50%,-50%) scale(${s.toFixed(3)})`;
        b.style.zIndex = String(k === 0 ? 50 : Math.round(50 + p.z));
        b.style.opacity = k === 0 ? 1 : (0.62 + (p.z / R + 1) * 0.19).toFixed(2);
      });
      for (let k = 1; k < AGENTS.length; k++) {
        const p = pts[k], q = pts[k === AGENTS.length - 1 ? 1 : k + 1], l = lines[k - 1], m = ring[k - 1];
        l.setAttribute("x1", cx); l.setAttribute("y1", cy); l.setAttribute("x2", p.x); l.setAttribute("y2", p.y);
        l.style.stroke = AGENTS[k].c; l.style.opacity = (0.25 + (p.z / R + 1) * 0.25).toFixed(2);
        l.classList.toggle("hot", sel === k);
        m.setAttribute("x1", p.x); m.setAttribute("y1", p.y); m.setAttribute("x2", q.x); m.setAttribute("y2", q.y);
      }
    }
    function loop(now) {
      const dt = Math.min(0.05, (now - last) / 1000); last = now;
      layout(dt);
      if (running) raf = requestAnimationFrame(loop);
    }
    show(0);
    layout(0);
    addEventListener("resize", () => layout(0));
    if (!reduced) {
      watch(con, (on) => {
        if (on && !running) { running = true; last = performance.now(); raf = requestAnimationFrame(loop); }
        else if (!on) { running = false; cancelAnimationFrame(raf); }
      });
    }
  }

  /* ─── command console typing demo (illustrative session) ─── */
  const cb = $("#console-body");
  if (cb) {
    const SCRIPT = [
      ["you", "Jarvis, system status."],
      ["JARVIS", "#00d9ff", "All systems nominal. CPU 18%, memory 54%, network stable. Hawkeye forecasts no pressure for the next 15 minutes."],
      ["you", "Any disasters I should know about?"],
      ["WIDOW", "#ff5a5a", "Monitoring live earthquake, flood and cyclone feeds plus digital outages. One event in your watch zone; briefing pinned to the command map."],
      ["you", "Turn off the living-room lights and lock the porch."],
      ["STARK", "#ff6b00", "Done via Home Assistant: light.living_room → off · lock.porch → locked."],
      ["you", "Scan this machine for security issues."],
      ["ANALYST", "#50fa7b", "2 findings. Fix proposed for each, with impact and rollback. Nothing changes until you approve."],
      ["you", "Approve fix 1. Then go to sleep."],
      ["JARVIS", "#00d9ff", "Fix applied and audited. Microphone sleeping. Say “Jarvis” when you need me."],
    ];
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    function line(who, color) {
      const d = document.createElement("div");
      d.className = "cl " + (who === "you" ? "cl-you" : "cl-ai");
      d.innerHTML = who === "you" ? `<span class="cl-p">operator@bucs ›</span> <span class="cl-t"></span>`
        : `<span class="cl-a" style="--c:${color}">[${who}]</span> <span class="cl-t"></span>`;
      cb.appendChild(d);
      while (cb.children.length > 9) cb.removeChild(cb.firstChild);
      return $(".cl-t", d);
    }
    if (reduced) {
      SCRIPT.forEach((s) => { line(s[0], s[1]).textContent = s.length === 2 ? s[1] : s[2]; });
    } else {
      let visible = false, wake = null;
      watch(cb, (on) => { visible = on; if (on && wake) { wake(); wake = null; } });
      const gate = () => (visible && !document.hidden ? Promise.resolve() : new Promise((r) => (wake = r)));
      document.addEventListener("visibilitychange", () => { if (!document.hidden && visible && wake) { wake(); wake = null; } });
      (async function run() {
        for (;;) {
          cb.innerHTML = "";
          for (const s of SCRIPT) {
            await gate();
            const human = s[0] === "you", text = human ? s[1] : s[2];
            const t = line(s[0], s[1]);
            t.classList.add("typing");
            for (let i = 1; i <= text.length; i++) { t.textContent = text.slice(0, i); await sleep(human ? 38 : 12); }
            t.classList.remove("typing");
            await sleep(human ? 420 : 1100);
          }
          await sleep(2600);
        }
      })();
    }
  }

  /* ─── voice tile: mic on / off / sleep / wake cycle ─── */
  const vs = $("#voice-state");
  if (vs && !reduced) {
    const states = [["listening", "LISTENING"], ["off", "MIC OFF"], ["sleep", "SLEEPING · SAY “JARVIS”"], ["wake", "WAKE WORD HEARD"]];
    let k = 0, timer = 0;
    const tick = () => { k = (k + 1) % states.length; vs.dataset.state = states[k][0]; $(".vs-label", vs).textContent = states[k][1]; };
    watch(vs, (on) => { clearInterval(timer); if (on) timer = setInterval(tick, 2200); });
  }
})();
