"""AI CORE · plugin & agent marketplace.

A plugin is a folder with manifest.json + one Python entrypoint:
  {"id": "hn_digest", "name": "...", "version": "1.0.0", "author": "...", "description": "...",
   "permissions": ["network"], "entrypoint": "plugin.py", "sha256": "<sha256 of plugin.py, LF line endings>",
   "tools": [{"name": "...", "description": "...", "parameters": {...}, "tier": "read"}]}
The entrypoint defines register(api) and calls api.tool(name, handler) for each DECLARED tool.

Safety model (honest): permissions gate what the plugin API hands out (api.http_get only with
"network", api.notify only with "notify", memory with "memory:*") and every tool still goes
through the read/action/destructive tiers. But a third-party Python plugin runs inside the
BUCS process with BUCS's privileges — it could import anything. So installs need the
commander, an explicit confirmation after the requested permissions are shown, an integrity
check (manifest sha256 == entrypoint hash, re-verified against the hash pinned at install
before every load), and nothing third-party is enabled automatically.

First-party plugins ship in ./plugins (listed in plugins/catalogue.json). Third-party
installs live in .update/agentos/plugins/<id> (gitignored, kept across updates).
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import shutil
import sys
import time
import uuid
import zipfile
from pathlib import Path

from agentos_tools import TIERS, Tool

PERMISSIONS = {
    "network": "make outbound HTTP(S) requests",
    "notify": "post messages to the dashboard event stream",
    "memory:read": "search the operator's long-term AI memory",
    "memory:write": "add items to the operator's long-term AI memory",
}
MAX_ZIP = 2 * 1024 * 1024
WARNING = ("Third-party Python plugins run inside BUCS with BUCS's full privileges on this PC. "
           "Permissions limit the plugin API, not what its code could do. Install only code you trust.")
_ID = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")
_VER = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")
_ENTRY = re.compile(r"^[A-Za-z0-9_]{1,40}\.py$")


def sha256_file(p: Path) -> str:
    """SHA-256 of the file with line endings normalised to LF, so a git CRLF checkout on
    Windows doesn't look like tampering."""
    return hashlib.sha256(Path(p).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def validate_manifest(m) -> list[str]:
    errs = []
    if not isinstance(m, dict):
        return ["manifest must be a JSON object"]
    if not _ID.match(str(m.get("id", ""))):
        errs.append("id must be lowercase letters/digits/_/- (2–41 chars)")
    for k in ("name", "author"):
        if not str(m.get(k, "")).strip():
            errs.append(f"{k} is required")
    if not _VER.match(str(m.get("version", ""))):
        errs.append("version must be semver (e.g. 1.0.0)")
    perms = m.get("permissions", [])
    if not isinstance(perms, list) or any(p not in PERMISSIONS for p in perms):
        errs.append("permissions must be a list drawn from: " + ", ".join(PERMISSIONS))
    if not _ENTRY.match(str(m.get("entrypoint", ""))):
        errs.append("entrypoint must be a .py file name in the plugin folder")
    if not re.match(r"^[0-9a-f]{64}$", str(m.get("sha256", ""))):
        errs.append("sha256 must be the 64-hex SHA-256 of the entrypoint")
    tools = m.get("tools", [])
    if not isinstance(tools, list) or not tools:
        errs.append("tools must be a non-empty list")
    else:
        for t in tools:
            if not isinstance(t, dict) or not re.match(r"^[a-z][a-z0-9_]{1,48}$", str(t.get("name", ""))):
                errs.append("each tool needs a snake_case name")
            elif t.get("tier", "read") not in TIERS:
                errs.append(f"tool {t['name']}: tier must be one of {TIERS}")
            elif not isinstance(t.get("parameters", {"type": "object"}), dict):
                errs.append(f"tool {t['name']}: parameters must be a JSON schema object")
    return errs


def integrity(folder: Path, m: dict, pinned: str | None = None) -> tuple[bool, str]:
    ep = Path(folder) / str(m.get("entrypoint", ""))
    if not ep.is_file():
        return False, "entrypoint missing"
    h = sha256_file(ep)
    if h != m.get("sha256"):
        return False, "entrypoint hash does not match manifest sha256"
    if pinned and h != pinned:
        return False, "plugin changed since it was installed (hash differs from the pinned one)"
    return True, h


class PluginAPI:
    """What a plugin's register(api) receives. Capabilities follow the granted permissions."""

    def __init__(self, manifest: dict, mgr: "PluginManager") -> None:
        self._m, self._mgr = manifest, mgr
        self.id = manifest["id"]
        self.permissions = set(manifest.get("permissions") or [])
        self._declared = {t["name"]: t for t in manifest["tools"]}
        self.registered: list[str] = []

    def _need(self, perm: str) -> None:
        if perm not in self.permissions:
            raise PermissionError(f"plugin {self.id} lacks the '{perm}' permission")

    def tool(self, name: str, handler) -> None:
        decl = self._declared.get(name)
        if decl is None:
            raise PermissionError(f"tool {name} is not declared in the manifest")
        self._mgr.registry.register(Tool(name, str(decl.get("description", ""))[:300],
                                         decl.get("parameters") or {"type": "object", "properties": {}},
                                         decl.get("tier", "read"), handler, source=f"plugin:{self.id}"))
        self.registered.append(name)

    async def http_get(self, url: str, params: dict | None = None, timeout: float = 10.0):
        self._need("network")
        import httpx
        if not str(url).startswith("https://"):
            raise PermissionError("plugins may only fetch https:// URLs")
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                     headers={"User-Agent": f"BUCS-plugin/{self.id}"}) as c:
            r = await c.get(url, params=params)
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            return r.json() if "json" in ct else r.text[:200_000]

    async def notify(self, msg: str) -> None:
        self._need("notify")
        hub = (self._mgr.state or {}).get("hub")
        if hub:
            await hub.broadcast({"type": "log", "level": "info", "msg": f"[plugin {self.id}] {str(msg)[:300]}"})

    def memory_search(self, q: str, limit: int = 5) -> list[dict]:
        self._need("memory:read")
        mem = self._mgr.memory
        return [] if mem is None else [{k: h[k] for k in ("id", "kind", "title", "text")} for h in mem.search(q, limit)]

    def memory_add(self, text: str) -> dict:
        self._need("memory:write")
        mem = self._mgr.memory
        return {"ok": False} if mem is None else mem.add(text, kind="note", source=f"plugin:{self.id}")


class PluginManager:
    def __init__(self, builtin_dir: Path, data_dir: Path, registry, state: dict | None = None, memory=None) -> None:
        self.builtin_dir = Path(builtin_dir)
        self.data_dir = Path(data_dir)
        self.install_dir = self.data_dir / "plugins"
        self.stage_dir = self.data_dir / "staging"
        self.registry = registry
        self.state = state or {}
        self.memory = memory
        self.db_path = self.data_dir / "plugins.json"
        self.records: dict[str, dict] = {}
        self.loaded: dict[str, dict] = {}
        self.staged: dict[str, dict] = {}
        self._load_records()

    # ── records ───────────────────────────────────────────────
    def _load_records(self) -> None:
        try:
            self.records = json.loads(self.db_path.read_text(encoding="utf-8"))
        except Exception:
            self.records = {}

    def _save(self) -> None:
        from atomicio import atomic_write_text
        self.data_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.db_path, json.dumps(self.records, indent=1), encoding="utf-8")

    def catalogue(self) -> list[dict]:
        try:
            cat = json.loads((self.builtin_dir / "catalogue.json").read_text(encoding="utf-8"))
        except Exception:
            return []
        out = []
        for e in cat.get("plugins", []):
            folder = self.builtin_dir / e.get("folder", "")
            try:
                m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            except Exception:
                continue
            out.append({**{k: m.get(k) for k in ("id", "name", "version", "author", "description", "permissions")},
                        "folder": str(folder), "first_party": True,
                        "tools": [t["name"] for t in m.get("tools", [])], "installed": m.get("id") in self.records})
        return out

    def _folder(self, pid: str) -> Path | None:
        rec = self.records.get(pid)
        if not rec:
            return None
        return Path(rec["path"])

    def _manifest(self, folder: Path) -> dict:
        return json.loads((Path(folder) / "manifest.json").read_text(encoding="utf-8"))

    def bootstrap(self) -> None:
        """First run: first-party plugins are installed + enabled (shipped with BUCS, hash-verified)."""
        changed = False
        for e in self.catalogue():
            if e["id"] not in self.records:
                folder = Path(e["folder"])
                m = self._manifest(folder)
                ok, h = integrity(folder, m)
                if validate_manifest(m) or not ok:
                    continue
                self.records[e["id"]] = {"path": str(folder), "source": "first-party", "sha256": h,
                                         "enabled": True, "installed": time.time(), "first_party": True}
                changed = True
            elif self.records[e["id"]].get("first_party"):
                # shipped code is updated by BUCS releases: re-pin to the shipped manifest hash
                rec = self.records[e["id"]]
                rec["path"] = e["folder"]
                try:
                    m = self._manifest(Path(e["folder"]))
                    ok, h = integrity(Path(e["folder"]), m)
                    if ok and rec.get("sha256") != h:
                        rec["sha256"] = h; changed = True
                except Exception:
                    pass
        if changed:
            self._save()

    # ── load / unload ─────────────────────────────────────────
    def load(self, pid: str) -> dict:
        folder = self._folder(pid)
        if folder is None:
            return {"ok": False, "error": "not installed"}
        self.unload(pid)                  # a failed re-verification must also drop the old tools
        try:
            m = self._manifest(folder)
        except Exception:
            return {"ok": False, "error": "manifest unreadable"}
        errs = validate_manifest(m)
        if errs:
            return {"ok": False, "error": "; ".join(errs)}
        ok, why = integrity(folder, m, self.records[pid].get("sha256"))
        if not ok:
            self.records[pid]["tampered"] = why
            return {"ok": False, "error": f"integrity check failed: {why}"}
        self.records[pid].pop("tampered", None)
        self.unload(pid)
        api = PluginAPI(m, self)
        modname = f"bucs_plugin_{pid.replace('-', '_')}"
        try:
            spec = importlib.util.spec_from_file_location(modname, folder / m["entrypoint"])
            mod = importlib.util.module_from_spec(spec)
            sys.modules[modname] = mod
            spec.loader.exec_module(mod)
            if not callable(getattr(mod, "register", None)):
                raise RuntimeError("entrypoint has no register(api)")
            mod.register(api)
        except Exception as e:
            self.registry.unregister_source(f"plugin:{pid}")
            sys.modules.pop(modname, None)
            return {"ok": False, "error": f"load failed: {type(e).__name__}: {str(e)[:200]}"}
        self.loaded[pid] = {"tools": api.registered, "version": m["version"], "loaded": time.time()}
        return {"ok": True, "tools": api.registered}

    def unload(self, pid: str) -> None:
        self.registry.unregister_source(f"plugin:{pid}")
        self.loaded.pop(pid, None)
        sys.modules.pop(f"bucs_plugin_{pid.replace('-', '_')}", None)

    def load_enabled(self) -> dict:
        return {pid: self.load(pid) for pid, r in self.records.items() if r.get("enabled")}

    def set_enabled(self, pid: str, on: bool) -> dict:
        if pid not in self.records:
            return {"ok": False, "error": "not installed"}
        if on:
            res = self.load(pid)
            if not res["ok"]:
                self._save()
                return res
        else:
            self.unload(pid)
        self.records[pid]["enabled"] = bool(on)
        self._save()
        return {"ok": True, "enabled": bool(on)}

    # ── install ───────────────────────────────────────────────
    def _stage_folder(self, src: Path) -> Path:
        tok = uuid.uuid4().hex[:12]
        dst = self.stage_dir / tok
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        return dst

    @staticmethod
    def safe_extract(data: bytes, dst: Path) -> Path:
        if len(data) > MAX_ZIP:
            raise ValueError("archive larger than 2 MB")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            total = 0
            for info in z.infolist():
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/") or ":" in name:
                    raise ValueError(f"unsafe path in archive: {name}")
                total += info.file_size
                if total > 8 * MAX_ZIP:
                    raise ValueError("archive expands too large")
            z.extractall(dst)
        if (dst / "manifest.json").is_file():
            return dst
        subs = [p for p in dst.iterdir() if p.is_dir()]
        if len(subs) == 1 and (subs[0] / "manifest.json").is_file():
            return subs[0]
        raise ValueError("no manifest.json at the archive root")

    async def preview(self, source: str, value: str) -> dict:
        """Stage a plugin (copy/download), validate it, and describe what it asks for. Installs nothing."""
        try:
            if source == "folder":
                src = Path(value).expanduser()
                if not (src / "manifest.json").is_file():
                    return {"ok": False, "error": "folder has no manifest.json"}
                folder = self._stage_folder(src)
            elif source == "catalogue":
                ent = next((e for e in self.catalogue() if e["id"] == value), None)
                if ent is None:
                    return {"ok": False, "error": "not in the catalogue"}
                folder = Path(ent["folder"])
            elif source == "url":
                if not str(value).startswith("https://"):
                    return {"ok": False, "error": "plugin URLs must be https:// (a .zip)"}
                import httpx
                async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
                    async with c.stream("GET", value) as r:
                        r.raise_for_status()
                        if not str(r.url).startswith("https://"):
                            return {"ok": False, "error": "redirected away from https"}
                        buf = bytearray()
                        async for chunk in r.aiter_bytes():
                            buf += chunk
                            if len(buf) > MAX_ZIP:
                                return {"ok": False, "error": "archive larger than 2 MB"}
                tok_dir = self.stage_dir / uuid.uuid4().hex[:12]
                tok_dir.mkdir(parents=True, exist_ok=True)
                folder = self.safe_extract(bytes(buf), tok_dir)
            else:
                return {"ok": False, "error": "source must be folder, url or catalogue"}
            m = self._manifest(folder)
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        errs = validate_manifest(m)
        ok, why = integrity(folder, m) if not errs else (False, "manifest invalid")
        token = uuid.uuid4().hex
        first_party = source == "catalogue"
        if not errs and ok:
            self.staged[token] = {"folder": str(folder), "source": f"{source}:{str(value)[:200]}",
                                  "id": m["id"], "created": time.time(), "sha256": why, "first_party": first_party}
        existing = self.records.get(m.get("id")) if isinstance(m, dict) else None
        return {"ok": not errs and ok, "token": token if not errs and ok else None,
                "manifest": {k: m.get(k) for k in ("id", "name", "version", "author", "description", "entrypoint", "sha256")}
                if isinstance(m, dict) else {},
                "tools": [{k: t.get(k) for k in ("name", "description", "tier")} for t in (m.get("tools") or [])]
                if isinstance(m, dict) else [],
                "permissions": [{"id": p, "means": PERMISSIONS.get(p, "?")} for p in (m.get("permissions") or [])]
                if isinstance(m, dict) else [],
                "errors": errs, "integrity": "ok" if ok else why, "upgrade_from": (existing or {}).get("version"),
                "first_party": first_party, "warning": None if first_party else WARNING}

    def install(self, token: str, confirm: bool) -> dict:
        st = self.staged.pop(token, None)
        if st is None or time.time() - st["created"] > 900:
            return {"ok": False, "error": "preview expired — preview the plugin again"}
        if not confirm:
            return {"ok": False, "error": "installation needs explicit confirmation of the requested permissions"}
        pid = st["id"]
        cur = self.records.get(pid)
        if cur and cur.get("first_party") and not st["first_party"]:
            return {"ok": False, "error": f"'{pid}' is a first-party plugin id"}
        src = Path(st["folder"])
        if st["first_party"]:
            dst = src
        else:
            dst = self.install_dir / pid
            if dst.exists():
                shutil.rmtree(dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst)
            shutil.rmtree(src, ignore_errors=True)
        m = self._manifest(dst)
        ok, h = integrity(dst, m)
        if not ok:
            return {"ok": False, "error": h}
        self.unload(pid)
        self.records[pid] = {"path": str(dst), "source": st["source"], "sha256": h, "enabled": False,
                             "installed": time.time(), "first_party": st["first_party"], "version": m["version"]}
        self._save()
        return {"ok": True, "id": pid, "enabled": False}

    def uninstall(self, pid: str) -> dict:
        rec = self.records.get(pid)
        if rec is None:
            return {"ok": False, "error": "not installed"}
        self.unload(pid)
        if not rec.get("first_party"):
            folder = Path(rec["path"]).resolve()
            if self.install_dir.resolve() in folder.parents:
                shutil.rmtree(folder, ignore_errors=True)
        self.records.pop(pid)
        self._save()
        return {"ok": True}

    def snapshot(self) -> dict:
        installed = []
        for pid, r in self.records.items():
            try:
                m = self._manifest(Path(r["path"]))
            except Exception:
                m = {"id": pid, "name": pid}
            installed.append({"id": pid, "name": m.get("name"), "version": m.get("version"),
                              "author": m.get("author"), "description": m.get("description"),
                              "permissions": m.get("permissions") or [], "enabled": r.get("enabled", False),
                              "loaded": pid in self.loaded, "tools": (self.loaded.get(pid) or {}).get("tools")
                              or [t.get("name") for t in m.get("tools") or []], "first_party": r.get("first_party", False),
                              "source": r.get("source"), "tampered": r.get("tampered"), "sha256": r.get("sha256", "")[:12]})
        return {"installed": installed, "catalogue": self.catalogue(), "warning": WARNING,
                "permissions": PERMISSIONS}
