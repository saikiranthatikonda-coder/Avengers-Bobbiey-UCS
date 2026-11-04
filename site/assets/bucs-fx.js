/* BUCS site effects: agent roster, command-console session, voice-state tile.
   No dependencies. Everything pauses off-screen and respects prefers-reduced-motion. */
(() => {
  "use strict";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s, r = document) => r.querySelector(s);

  /* run cb(true/false) when el enters/leaves the viewport */
  function watch(el, cb, margin = "0px") {
    if (!el) return;
    new IntersectionObserver((es) => cb(es[0].isIntersecting), { rootMargin: margin }).observe(el);
  }

  /* ─── agent roster ─── */
  const AGENTS = [
    { n: "JARVIS", role: "Primary interface", c: "#00d9ff", wake: "“Jarvis …”",
      d: "Your conversation partner and the orchestrator. JARVIS reads live conditions, issues directives and hands work to the right specialist over a shared context blackboard." },
    { n: "CAPTAIN", role: "Schedules & briefings", c: "#3b82f6", wake: "“Hey Cap …”",
      d: "Owns your day: Google Calendar sync, meeting countdowns, conflict and focus-block checks, and spoken daily briefings." },
    { n: "STARK", role: "System automation", c: "#ff8a3d", wake: "“Hey Stark …”",
      d: "Runs the machine: system scans, native desktop actions, smart-device commands, Computer Use tasks and the automations you approve." },
    { n: "BLACK WIDOW", role: "Research & intel", c: "#ff6b6b", wake: "“Hey Widow …”",
      d: "Gathers intelligence: live world news, threat signals and the Global Activity Map, condensed into what actually affects you." },
    { n: "HAWKEYE", role: "Vitals monitor", c: "#a78bfa", wake: "“Hey Hawkeye …”",
      d: "Never blinks: CPU, memory, disk, network and fleet nodes, with 15-minute trend forecasts that warn before things break." },
    { n: "HULK", role: "Heavy compute", c: "#22c55e", wake: "“Hey Hulk …”",
      d: "Takes the heavy lifting: long-running jobs and AI inference, routed to whichever local or fleet GPU has headroom." },
    { n: "THOR", role: "Announcements", c: "#facc15", wake: "“Hey Thor …”",
      d: "The voice of the system: spoken alerts and the four-tier emergency broadcast, from info to emergency." },
    { n: "VISION", role: "Synthesizer", c: "#e879f9", wake: "“Hey Vision …”",
      d: "Connects the dots: reads every panel and every agent, and surfaces the single observation that matters right now." },
  ];
  const roster = $("#roster"), detail = $("#agent-detail");
  if (roster && detail) {
    const btns = AGENTS.map((a, i) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "ag";
      b.style.setProperty("--c", a.c);
      b.setAttribute("aria-pressed", i === 0 ? "true" : "false");
      b.innerHTML = `<i aria-hidden="true"></i><span class="ag-n">${a.n}</span><span class="ag-r">${a.role}</span>`;
      roster.appendChild(b);
      return b;
    });
    const show = (i) => {
      btns.forEach((b, k) => b.setAttribute("aria-pressed", k === i ? "true" : "false"));
      const a = AGENTS[i];
      detail.style.setProperty("--c", a.c);
      detail.innerHTML = `<div class="ad-tag">AGENT ${String(i + 1).padStart(2, "0")} / 08</div>
        <h3>${a.n}</h3><div class="ad-role">${a.role}</div><p>${a.d}</p>
        <div class="ad-wake"><span>WAKE WORD</span>${a.wake}</div>`;
    };
    btns.forEach((b, i) => b.addEventListener("click", () => show(i)));
    show(0);
  }

  /* ─── command console: an illustrative session, revealed line by line ─── */
  const cb = $("#console-body");
  if (cb) {
    const SCRIPT = [
      ["you", "Open the quarterly report and draft the summary email to finance."],
      ["STARK", "#ff8a3d", "Computer Use: opened the report and drafted the email. Sending is a sensitive action, so it waits for you. Approve, edit or take over?"],
      ["you", "Call the clinic and move my appointment to Friday morning."],
      ["CAPTAIN", "#3b82f6", "Phone Agent: calling from your Twilio number. Moved to Friday 10:30. Transcript and summary saved to the call log."],
      ["you", "Anything on the activity map near my locations?"],
      ["WIDOW", "#ff6b6b", "One earthquake offshore, outside your watch zones. Flights, satellites, space weather and outages look normal."],
      ["you", "Any improvements proposed?"],
      ["JARVIS", "#00d9ff", "One proposal passed its sandbox tests and is waiting for your approval. Nothing is deployed until you say so."],
    ];
    cb.innerHTML = "";
    const rows = SCRIPT.map((s) => {
      const you = s[0] === "you", d = document.createElement("div");
      d.className = "cl " + (you ? "cl-you" : "cl-ai");
      if (you) d.innerHTML = `<span class="cl-p">you</span><span class="cl-t"></span>`;
      else { d.innerHTML = `<span class="cl-a"></span><span class="cl-t"></span>`; d.style.setProperty("--c", s[1]); d.firstChild.textContent = s[0]; }
      d.lastChild.textContent = you ? s[1] : s[2];
      cb.appendChild(d);
      return d;
    });
    if (reduced) rows.forEach((r) => r.classList.add("on"));
    else {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      let visible = false, wake = null;
      watch(cb, (on) => { visible = on; if (on && wake) { wake(); wake = null; } });
      document.addEventListener("visibilitychange", () => { if (!document.hidden && visible && wake) { wake(); wake = null; } });
      const gate = () => (visible && !document.hidden ? Promise.resolve() : new Promise((r) => (wake = r)));
      (async function run() {
        for (;;) {
          rows.forEach((r) => r.classList.remove("on"));
          await sleep(500);
          for (const r of rows) {
            await gate();
            r.classList.add("on");
            await sleep(r.classList.contains("cl-you") ? 900 : 2200);
          }
          await sleep(5000);
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
    watch(vs, (on) => { clearInterval(timer); if (on) timer = setInterval(tick, 2400); });
  }
})();
