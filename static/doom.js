/* DOOMSDAY PROTOCOL · v8.4 — the DOOM agent's card badge (countdown to Dec 18, 2026, local time)
   and a handful of harmless Easter eggs. Nothing here talks to the server or changes settings,
   except the "doom" theme flash, which is restored afterwards. Keystrokes typed into inputs,
   textareas, selects or editable fields are ignored. */
(function () {
  "use strict";
  const PREMIERE = new Date(2026, 11, 18, 0, 0, 0);            // Dec 18, 2026 · visitor's local midnight
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function eta() {
    const ms = PREMIERE - Date.now();
    if (ms <= 0) return "DOOMSDAY HAS ARRIVED";
    const d = Math.floor(ms / 864e5), h = Math.floor(ms % 864e5 / 36e5), m = Math.floor(ms % 36e5 / 6e4);
    return `DOOMSDAY · T-${d}D ${String(h).padStart(2, "0")}H ${String(m).padStart(2, "0")}M`;
  }

  // ── DOOM card badge (cards are rebuilt on every snapshot, so re-attach) ──
  function badge() {
    const card = document.querySelector('.agent-card[data-agent="doom"] .agent-meta');
    if (!card) return;
    let b = card.querySelector(".doom-eta");
    if (!b) { b = document.createElement("span"); b.className = "doom-eta"; b.title = "Avengers: Doomsday · theatrical release Dec 18, 2026"; card.appendChild(b); }
    b.textContent = eta();
  }
  const list = document.getElementById("avengers-list");
  if (list) new MutationObserver(badge).observe(list, { childList: true });
  setInterval(badge, 30000); setTimeout(badge, 1500);

  // ── toast ──
  let toastT = 0;
  function toast(text, ms = 3600) {
    let t = document.getElementById("doom-toast");
    if (!t) {
      t = document.createElement("div"); t.id = "doom-toast"; t.setAttribute("role", "status");
      t.style.cssText = "position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:9999;max-width:min(92vw,520px);" +
        "padding:.7rem 1.1rem;border:1px solid #2bd46f;border-radius:8px;background:rgba(8,18,12,.94);color:#dff5e7;" +
        "font:600 .8rem/1.4 var(--font-body,system-ui);letter-spacing:.06em;box-shadow:0 0 24px rgba(43,212,111,.35);text-align:center";
      document.body.appendChild(t);
    }
    t.innerHTML = esc(text); t.hidden = false;
    clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, ms);
    try { if (typeof logEvent === "function") logEvent(`<span class="tag">[easter egg]</span> ${esc(text)}`); } catch (e) {}
  }

  // ── theme flash: borrow the DOOM theme for a few seconds, then restore ──
  function flashDoom(ms = 6000) {
    const b = document.body, prev = [...b.classList].find(c => c.startsWith("theme-")) || "theme-jarvis";
    if (prev === "theme-doom") return;
    b.classList.remove(prev); b.classList.add("theme-doom");
    setTimeout(() => { if (b.classList.contains("theme-doom")) { b.classList.remove("theme-doom"); b.classList.add(prev); } }, ms);
  }

  // ── assemble: the agent cards light up one after another ──
  function assemble() {
    const cards = [...document.querySelectorAll(".agent-card")];
    cards.forEach((c, i) => setTimeout(() => {
      c.classList.add("active"); setTimeout(() => c.classList.remove("active"), 900);
    }, i * 140));
    toast(`Team assembled · ${cards.length} agents standing by.`);
  }

  // ── key sequences ──
  const KONAMI = ["arrowup", "arrowup", "arrowdown", "arrowdown", "arrowleft", "arrowright", "arrowleft", "arrowright", "b", "a"];
  const WORDS = {
    doom: () => { flashDoom(); toast("Doom sees you. Doom approves of your curiosity."); },
    jarvis: () => toast("At your service. Always."),
    assemble: assemble,
    "14000605": () => toast("Doom ran the futures too. In this one, you ship on time."),
    vibranium: () => toast("Rarest metal on Earth. Your uptime is rarer."),
    shawarma: () => toast("Mission debrief scheduled. Snacks are on Stark."),
    "i am iron man": () => toast("Everyone says that. Few patch their dependencies."),
  };
  const MAXW = Math.max(...Object.keys(WORDS).map(w => w.length));
  let keys = [], typed = "";
  document.addEventListener("keydown", e => {
    const t = e.target;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (t && (t.isContentEditable || /^(input|textarea|select)$/i.test(t.tagName))) return;
    const k = String(e.key || "").toLowerCase();
    keys = [...keys, k].slice(-KONAMI.length);
    if (keys.join() === KONAMI.join()) { keys = []; flashDoom(8000); toast("Doom approves of your persistence. DOOMSDAY PROTOCOL engaged."); return; }
    if (k.length === 1) {
      typed = (typed + k).slice(-MAXW);
      for (const w of Object.keys(WORDS)) if (typed.endsWith(w)) { typed = ""; WORDS[w](); break; }
    }
  });

  // triple-click the DOOM card's badge
  let clicks = 0, clickT = 0;
  document.addEventListener("click", e => {
    if (!e.target.closest || !e.target.closest(".agent-card[data-agent=\"doom\"] .agent-icon")) return;
    clicks++; clearTimeout(clickT); clickT = setTimeout(() => { clicks = 0; }, 700);
    if (clicks >= 3) { clicks = 0; e.stopPropagation(); toast("Doom does not do demos. (He does, actually. Ask him anything.)"); }
  }, true);

  // for the people who open devtools
  try {
    console.log("%c⬢ DOOMSDAY PROTOCOL%c\nYou found the console. Doom respects those who read the source.\nTry: ↑↑↓↓←→←→BA · type \"assemble\" · type \"doom\".",
      "color:#2bd46f;font:700 14px monospace", "color:#9fb3a6;font:12px monospace");
  } catch (e) {}

  window.DoomProtocol = { eta, toast, flashDoom };
})();
