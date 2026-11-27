/* BUCS downloads (Windows, macOS, Linux): detects the visitor's OS (and Apple Silicon vs Intel where the browser
   allows it) and offers the matching native installer, but only when that exact asset
   exists on the latest GitHub release. Installers are uploaded by CI after they pass
   install tests, so nothing here assumes an installer is available. Without a matching
   asset (or if the API is unreachable) the platform shows "Installer in final testing"
   and the ZIP package stays the working path. */
(() => {
  "use strict";
  const REPO = "saikiranthatikonda-coder/Avengers-Bobbiey-UCS";
  // The list endpoint answers 200 with [] before the first release (releases/latest would 404 and
  // log a console error); the first non-draft, non-prerelease entry is the latest release.
  const API = `https://api.github.com/repos/${REPO}/releases?per_page=10`;
  const DL_PREFIX = `https://github.com/${REPO}/releases/download/`;
  const ASSETS = {
    win: "BUCS-Windows-x64-Setup.exe",
    macArm: "BUCS-macOS-AppleSilicon.dmg",
    macIntel: "BUCS-macOS-Intel.dmg",
    linux: "BUCS-Linux-x64.AppImage",
    sums: "SHA256SUMS.txt",
  };
  const CACHE_KEY = "bucs-release-v2", CACHE_MS = 10 * 60 * 1000;
  const $ = (s, r = document) => r.querySelector(s);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  /* ─── platform detection ─── */
  function detectOS() {
    const uad = navigator.userAgentData, ua = navigator.userAgent || "";
    const plat = (uad && uad.platform) || navigator.platform || "";
    if ((uad && uad.mobile) || /iPhone|iPad|iPod|Android/i.test(ua)) return "mobile";
    if (/Mac/i.test(plat) && navigator.maxTouchPoints > 1) return "mobile";          // iPadOS reports "Macintosh"
    if (/Win/i.test(plat) || /Windows NT/i.test(ua)) return "win";
    if (/Mac/i.test(plat) || /Mac OS X/i.test(ua)) return "mac";
    if (/CrOS/i.test(ua)) return "other";
    if (/Linux/i.test(plat) || /Linux|X11/i.test(ua)) return "linux";
    return "other";
  }
  async function detectMacChip() {
    try {
      const uad = navigator.userAgentData;
      if (uad && uad.getHighEntropyValues) {
        const v = await uad.getHighEntropyValues(["architecture"]);
        if (v.architecture === "arm") return { chip: "arm", sure: true };
        if (v.architecture === "x86") return { chip: "intel", sure: true };
      }
    } catch (e) { /* fall through */ }
    try {
      const c = document.createElement("canvas"), gl = c.getContext("webgl") || c.getContext("experimental-webgl");
      if (gl) {
        let r = "";
        if (/Firefox\//.test(navigator.userAgent)) r = gl.getParameter(gl.RENDERER) || "";
        else { const ext = gl.getExtension("WEBGL_debug_renderer_info"); if (ext) r = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) || ""; }
        const lose = gl.getExtension("WEBGL_lose_context"); if (lose) lose.loseContext();
        if (/Apple M\d/i.test(r)) return { chip: "arm", sure: true };
        if (/Intel|AMD|Radeon|NVIDIA/i.test(r)) return { chip: "intel", sure: true };
      }
    } catch (e) { /* fall through */ }
    return { chip: "arm", sure: false };                                             // most Macs sold since 2021
  }

  /* ─── release data ─── */
  function readCache() {
    try {
      const c = JSON.parse(sessionStorage.getItem(CACHE_KEY) || "null");
      if (c && Date.now() - c.at < CACHE_MS) return c.data;
    } catch (e) { /* storage blocked */ }
    return undefined;
  }
  function writeCache(data) { try { sessionStorage.setItem(CACHE_KEY, JSON.stringify({ at: Date.now(), data })); } catch (e) { /* ignore */ } }

  async function fetchRelease(useCache) {
    if (useCache) { const c = readCache(); if (c !== undefined) return c; }
    let data = null;
    try {
      const ctl = "AbortController" in window ? new AbortController() : null;
      const timer = ctl && setTimeout(() => ctl.abort(), 8000);
      const res = await fetch(API, { headers: { Accept: "application/vnd.github+json" }, signal: ctl && ctl.signal, cache: "no-store" });
      if (timer) clearTimeout(timer);
      if (res.ok) {
        const body = await res.json();
        data = Array.isArray(body) ? body.find((r) => r && !r.draft && !r.prerelease) || null : body;
      }
    } catch (e) { data = null; }
    const rel = normalise(data);
    if (useCache) writeCache(rel);
    return rel;
  }
  function normalise(r) {
    if (!r || typeof r !== "object" || r.draft || r.prerelease || !Array.isArray(r.assets)) return null;
    const found = {};
    for (const [key, name] of Object.entries(ASSETS)) {
      const a = r.assets.find((x) => x && x.name === name);
      if (!a || (a.state && a.state !== "uploaded")) continue;
      const url = String(a.browser_download_url || "");
      if (!url.startsWith(DL_PREFIX)) continue;
      found[key] = { name, url, size: Number(a.size) || 0 };
    }
    return {
      version: String(r.tag_name || r.name || "").replace(/^v/i, ""),
      date: String(r.published_at || "").slice(0, 10),
      page: /^https:\/\/github\.com\//.test(r.html_url || "") ? r.html_url : `https://github.com/${REPO}/releases/latest`,
      assets: found,
    };
  }
  const mb = (b) => (b >= 1048576 ? (b / 1048576).toFixed(b >= 104857600 ? 0 : 1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB");
  const niceDate = (d) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return "";
    try { return new Date(d + "T00:00:00Z").toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }); } catch (e) { return d; }
  };

  /* ─── rendering ─── */
  function button(asset, label, primary) {
    const a = el("a", "btn dl-btn " + (primary ? "primary" : "ghost"));
    a.href = asset.url; a.rel = "noopener";
    a.append(el("span", "dl-btn-l", label), el("span", "dl-btn-s", `${asset.name} · ${mb(asset.size)}`));
    return a;
  }
  function pending(label) {
    const d = el("div", "dl-pending");
    d.setAttribute("aria-disabled", "true");
    d.append(el("span", "dl-pend-l", label), el("span", "dl-pend-s", "Installer in final testing"));
    return d;
  }

  function render(rel, os, mac) {
    const desk = os === "win" || os === "mac" || os === "linux";
    const A = (rel && rel.assets) || {};
    const winBox = $("#dl-win"), macBox = $("#dl-mac"), linBox = $("#dl-linux");
    if (!winBox || !macBox || !linBox) return;

    winBox.replaceChildren(A.win ? button(A.win, "Download for Windows", os === "win") : pending("Windows installer (64-bit)"));

    const intelFirst = os === "mac" && mac.chip === "intel" && mac.sure;
    const arm = A.macArm ? button(A.macArm, "Download for Apple Silicon", os === "mac" && !intelFirst) : pending("Apple Silicon (M1 and later)");
    const intel = A.macIntel ? button(A.macIntel, "Download for Intel Mac", os === "mac" && intelFirst) : pending("Intel Mac");
    macBox.replaceChildren(...(intelFirst ? [intel, arm] : [arm, intel]));

    linBox.replaceChildren(A.linux ? button(A.linux, "Download for Linux", os === "linux") : pending("Linux AppImage (x64)"));

    $("#dl-win-zip").hidden = !!A.win;
    $("#dl-linux-zip").hidden = !!A.linux;
    $("#dl-mac-zip").hidden = !!(A.macArm && A.macIntel);

    // recommended card + chip hint
    document.querySelectorAll(".dl-card").forEach((c) => c.classList.toggle("rec", c.dataset.os === os));
    const chip = $("#dl-chip");
    if (chip) {
      chip.hidden = os !== "mac";
      chip.textContent = mac.sure
        ? (mac.chip === "arm" ? "This Mac appears to use Apple Silicon." : "This Mac appears to use an Intel processor.")
        : "Not sure which chip? Apple menu → About This Mac: “Chip: Apple M…” means Apple Silicon; “Processor: … Intel” means Intel.";
    }

    // release meta
    const meta = $("#dl-meta");
    if (meta) {
      meta.replaceChildren();
      const any = A.win || A.macArm || A.macIntel || A.linux;
      if (rel && rel.version && any) {
        meta.append(el("span", "dl-ver", "v" + rel.version));
        const d = niceDate(rel.date); if (d) meta.append(el("span", null, "Released " + d));
        if (A.sums) { const s = el("a", null, "SHA-256 checksums"); s.href = A.sums.url; s.rel = "noopener"; meta.append(s); }
        const n = el("a", null, "Release notes"); n.href = rel.page; n.target = "_blank"; n.rel = "noopener"; meta.append(n);
      } else {
        meta.append(el("span", "dl-ver", "Native installers"), el("span", null, "in final testing · the ZIP package works today"));
      }
    }

    // hero CTA
    const main = $("#hero-dl"), hint = $("#hero-dl-hint");
    if (main && hint) {
      let asset = null, label = "GET BUCS", sub = "Windows · macOS · Linux";
      if (os === "win") { asset = A.win; label = "DOWNLOAD FOR WINDOWS"; }
      if (os === "mac") { asset = intelFirst ? A.macIntel : A.macArm; label = "DOWNLOAD FOR MACOS"; }
      if (os === "linux") { asset = A.linux; label = "DOWNLOAD FOR LINUX"; }
      if (asset) {
        main.href = asset.url; main.removeAttribute("data-scroll");
        sub = `${os === "mac" ? (intelFirst ? "Intel · " : "Apple Silicon · ") : os === "linux" ? "AppImage · x64 · " : "64-bit · "}${rel.version ? "v" + rel.version + " · " : ""}${mb(asset.size)}`;
      } else {
        main.href = "#get";
        if (os === "win") { label = "GET BUCS FOR WINDOWS"; sub = "Installer in final testing · ZIP available now"; }
        if (os === "mac") { label = "GET BUCS FOR MACOS"; sub = "Installer in final testing · ZIP available now"; }
        if (os === "linux") { label = "GET BUCS FOR LINUX"; sub = "AppImage in final testing · ZIP available now"; }
        if (os === "mobile") { label = "GET BUCS"; sub = "Desktop app for Windows, macOS and Linux"; }
      }
      main.textContent = label;
      hint.replaceChildren(el("span", null, sub + " · "));
      const all = el("a", null, desk ? "other platforms" : "all downloads"); all.href = "#get";
      hint.append(all);
    }

    // open the matching install tab
    if (desk) selectTab(os);
  }

  function selectTab(os) {
    document.querySelectorAll("[data-tab]").forEach((t) => {
      const on = t.dataset.tab === os;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
    });
    document.querySelectorAll("[data-panel]").forEach((p) => { p.hidden = p.dataset.panel !== os; });
  }
  function wireTabs() {
    const tabs = Array.from(document.querySelectorAll("[data-tab]"));
    tabs.forEach((t, i) => {
      t.addEventListener("click", () => selectTab(t.dataset.tab));
      t.addEventListener("keydown", (e) => {
        if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
        const n = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
        selectTab(n.dataset.tab); n.focus();
      });
    });
    selectTab("win");
  }

  /* ZIP package version from the signed manifest this site already publishes */
  async function zipMeta() {
    try {
      const m = await (await fetch("releases.json", { cache: "no-store" })).json();
      const t = `ZIP package v${m.version} · ${mb(m.size)}`;
      document.querySelectorAll(".dl-zipver").forEach((n) => (n.textContent = t));
    } catch (e) { /* keep static text */ }
  }

  const os = detectOS();
  let mac = { chip: "arm", sure: false };
  async function refresh(useCache = true) {
    const rel = await fetchRelease(useCache);
    render(rel, os, mac);
    return rel;
  }
  async function boot() {
    wireTabs();
    if (os === "mac") mac = await detectMacChip();
    render(null, os, mac);                        // honest default immediately, upgraded when the API answers
    zipMeta();
    refresh(true);
  }
  window.__bucsDownloads = { refresh: () => refresh(false), os, get mac() { return mac; } };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
