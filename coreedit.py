"""Core dashboard editing endpoints (notes, tasks, local events, world clocks, quick
access, weather location, news sources, incidents, decisions, operators, focus goal).

Every state change is commander-gated, audited (main._audit) and broadcast to the other
open dashboards (main._sync(topic) → app.js uiSyncRun / the "bucs:sync" event).
Storage: corestore.CoreStore (dashboard_store.json) plus the existing notes/operators files.
main.py mounts this with `coreedit.build_router(main_module)`.
"""

import time

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from corestore import StoreError


class NotePatch(BaseModel):
    text: str | None = None
    pinned: bool | None = None


class IdsReq(BaseModel):
    ids: list[str]


class TaskReq(BaseModel):
    title: str | None = None
    priority: str | None = None
    status: str | None = None
    due: str | None = None


class EventReq(BaseModel):
    title: str | None = None
    start: str | None = None            # local ISO date-time, e.g. 2026-11-17T15:30
    start_ts: float | None = None
    duration_min: int | None = None
    location: str | None = None
    priority: str | None = None


class IncidentReq(BaseModel):
    action: str                         # ack | resolve | reopen | note
    note: str | None = None


class WhitelistReq(BaseModel):
    key: str
    on: bool


class KeyReq(BaseModel):
    key: str


class OperatorPatch(BaseModel):
    name: str | None = None
    role: str | None = None


def build_router(m) -> APIRouter:
    """m = the main module (state, _commander, _locked, _audit, _sync, notes/operator helpers)."""
    r = APIRouter()
    state = m.state

    def store():
        return state["corestore"]

    def _apply_agenda() -> None:
        ag = state.get("agenda")
        if ag is not None:
            ag.set_local_events(store().events())
            ag.local_tasks = store().tasks()

    async def _guarded(fn, action: str, detail, topic: str, after=None) -> dict:
        """commander gate → validated store edit → audit → sync broadcast."""
        if not m._commander():
            return m._locked()
        try:
            out = fn()
        except StoreError as e:
            return {"ok": False, "error": str(e)}
        if after:
            after()
        await m._audit(action, detail(out) if callable(detail) else detail)
        await m._sync(topic)
        return {"ok": True, **(out if isinstance(out, dict) and "ok" not in out else {"value": out})}

    # ── preferences ───────────────────────────────────────────────
    @r.get("/api/prefs")
    async def prefs_get():
        from open_news import source_names
        p = store().prefs()
        news = state.get("news")
        p["news"] = {**p["news"], "builtin": source_names(),
                     "active_source": getattr(news, "source", "none"),
                     "selection_applies": getattr(news, "source", "") != "newsapi"}
        return p

    @r.post("/api/prefs")
    async def prefs_set(patch: dict):
        if not m._commander():
            return m._locked()
        from open_news import source_names
        s, done = store(), []
        try:
            if "world_clocks" in patch:
                s.set_world_clocks(patch["world_clocks"])
                done.append(f"world clocks → {', '.join(c['tz'] for c in s.data['world_clocks']) or 'none'}")
            if "quick_access" in patch:
                s.set_quick_access(patch["quick_access"])
                done.append(f"quick access → {len(s.data['quick_access'])} buttons")
            if "weather_location" in patch:
                loc = s.set_weather_location(patch["weather_location"])
                done.append(f"weather location → {loc['name'] if loc else 'automatic'}")
            if "news" in patch:
                n = patch["news"] if isinstance(patch["news"], dict) else {}
                s.set_news(disabled=n.get("disabled"), custom=n.get("custom"), known=source_names())
                done.append(f"news sources: {len(s.data['news']['disabled'])} off, "
                            f"{len(s.data['news']['custom'])} custom")
        except StoreError as e:
            return {"ok": False, "error": str(e)}
        if not done:
            return {"ok": False, "error": "nothing to change"}
        await m._audit("prefs.update", "; ".join(done)[:200])
        if "news" in patch and state.get("news") is not None and getattr(state["news"], "source", "") != "newsapi":
            import asyncio
            asyncio.create_task(state["news"].fetch_open())       # re-read with the new selection
        await m._sync("prefs")
        return {"ok": True, "prefs": await prefs_get()}

    @r.get("/api/weather/search")
    async def weather_search(q: str = ""):
        """Place search for the weather-location override (Open-Meteo geocoding, keyless)."""
        q = q.strip()[:60]
        if len(q) < 2:
            return {"ok": False, "error": "type at least 2 letters", "results": []}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0)) as c:
                res = await c.get("https://geocoding-api.open-meteo.com/v1/search",
                                  params={"name": q, "count": 6, "language": "en", "format": "json"})
                res.raise_for_status()
                rows = res.json().get("results") or []
        except Exception as e:
            return {"ok": False, "error": f"place search unreachable ({type(e).__name__})", "results": []}
        return {"ok": True, "source": "Open-Meteo geocoding", "results": [
            {"name": ", ".join(x for x in (p.get("name"), p.get("admin1"), p.get("country")) if x)[:60],
             "lat": p.get("latitude"), "lon": p.get("longitude")}
            for p in rows if p.get("latitude") is not None and p.get("longitude") is not None]}

    # ── notes: edit in place, pin, reorder ────────────────────────
    @r.post("/api/notes/reorder")
    async def notes_reorder(req: IdsReq):
        if not m._commander():
            return m._locked()
        notes = m._load_notes()
        by = {n.get("id"): n for n in notes}
        if len(req.ids) != len(by) or set(req.ids) != set(by):
            return {"ok": False, "error": "reorder needs every note id exactly once"}
        m._save_notes([by[i] for i in req.ids])
        await m._audit("note.reorder", f"{len(req.ids)} notes")
        await m._sync("notes")
        return {"ok": True}

    @r.post("/api/notes/{note_id}")
    async def notes_edit(note_id: str, req: NotePatch):
        if not m._commander():
            return m._locked()
        notes = m._load_notes()
        n = next((x for x in notes if x.get("id") == note_id), None)
        if n is None:
            return {"ok": False, "error": "unknown note"}
        if req.text is not None:
            text = req.text.strip()[:200]
            if not text:
                return {"ok": False, "error": "a note cannot be empty (delete it instead)"}
            if text != n.get("text"):
                n["text"], n["edited"] = text, time.time()
        if req.pinned is not None:
            n["pinned"] = bool(req.pinned)
            notes.sort(key=lambda x: not x.get("pinned"))      # stable: pinned first
        m._save_notes(notes)
        await m._audit("note.edit", f"note {note_id}")
        await m._sync("notes")
        return {"ok": True, "note": n}

    # ── tasks (local, editable) ───────────────────────────────────
    @r.get("/api/tasks")
    async def tasks_get():
        return {"tasks": store().tasks(), "source": "local (dashboard_store.json)"}

    @r.post("/api/tasks")
    async def tasks_add(req: TaskReq):
        return await _guarded(lambda: {"task": store().add_task(req.model_dump())},
                              "task.add", lambda o: o["task"]["title"][:80], "agenda", _apply_agenda)

    @r.post("/api/tasks/reorder")
    async def tasks_reorder(req: IdsReq):
        return await _guarded(lambda: {"tasks": store().reorder_tasks(req.ids)},
                              "task.reorder", f"{len(req.ids)} tasks", "agenda", _apply_agenda)

    @r.post("/api/tasks/{tid}")
    async def tasks_edit(tid: str, req: TaskReq):
        patch = req.model_dump(exclude_unset=True)
        return await _guarded(lambda: {"task": store().update_task(tid, patch)}, "task.edit",
                              lambda o: f"{o['task']['title'][:60]} ({', '.join(patch) or 'no change'})",
                              "agenda", _apply_agenda)

    @r.delete("/api/tasks/{tid}")
    async def tasks_delete(tid: str):
        return await _guarded(lambda: {"task": store().delete_task(tid)},
                              "task.delete", lambda o: o["task"]["title"][:80], "agenda", _apply_agenda)

    # ── local calendar events (Google stays read-only: its OAuth scope is read-only) ──
    @r.post("/api/events")
    async def events_add(req: EventReq):
        return await _guarded(lambda: {"event": store().add_event(req.model_dump())},
                              "event.add", lambda o: o["event"]["title"][:80], "agenda", _apply_agenda)

    @r.post("/api/events/{eid}")
    async def events_edit(eid: str, req: EventReq):
        patch = req.model_dump(exclude_unset=True)
        return await _guarded(lambda: {"event": store().update_event(eid, patch)},
                              "event.edit", lambda o: o["event"]["title"][:80], "agenda", _apply_agenda)

    @r.delete("/api/events/{eid}")
    async def events_delete(eid: str):
        return await _guarded(lambda: {"event": store().delete_event(eid)},
                              "event.delete", lambda o: o["event"]["title"][:80], "agenda", _apply_agenda)

    # ── threat incidents: acknowledge / resolve / re-open, with a note ──
    @r.post("/api/threats/incident/{event_id}")
    async def incident_state(event_id: str, req: IncidentReq):
        if not m._commander():
            return m._locked()
        res = state["threats"].set_state(event_id, req.action, req.note or "",
                                         by=state.get("rbac", {}).get("operator", "operator"))
        if res.get("ok"):
            await m._audit("incident." + req.action, f"incident {event_id}"
                           + (f": {(req.note or '').strip()[:80]}" if (req.note or "").strip() else ""))
            await state["hub"].broadcast({"type": "log", "level": "info",
                                          "msg": f"incident {event_id}: {req.action} by operator"})
            await m._sync("threats")
        return res

    # ── decisions: narrow the autonomy whitelist, undo a dismissal ──
    @r.post("/api/decisions/whitelist")
    async def decisions_whitelist(req: WhitelistReq):
        if not m._commander():
            return m._locked()
        res = await state["decisions"].set_whitelist(req.key, req.on)
        if res.get("ok"):
            await m._sync("decisions")
        return res

    @r.post("/api/decisions/restore")
    async def decisions_restore(req: KeyReq):
        if not m._commander():
            return m._locked()
        res = await state["decisions"].restore(req.key)
        if res.get("ok"):
            try:
                await state["decisions"].tick()        # the proposal returns only if still warranted
            except Exception:
                pass
            await m._sync("decisions")
        return res

    # ── operators: rename / change role / remove ──────────────────
    def _find(ops: list[dict], name: str):
        return next((o for o in ops if o["name"].lower() == name.strip().lower()), None)

    @r.post("/api/operators/{name}")
    async def operators_edit(name: str, req: OperatorPatch):
        if not m._commander():
            return m._locked()
        ops = m._load_operators()
        o = _find(ops, name)
        if o is None:
            return {"ok": False, "error": "unknown operator"}
        old, changes = o["name"], []
        if req.role is not None and req.role != o["role"]:
            if req.role not in ("commander", "observer"):
                return {"ok": False, "error": "role must be commander or observer"}
            if req.role == "observer" and not any(x["role"] == "commander" for x in ops if x is not o):
                return {"ok": False, "error": "at least one commander must remain"}
            o["role"] = req.role
            changes.append(f"role → {req.role}")
        if req.name is not None and req.name.strip() and req.name.strip() != old:
            new = " ".join(req.name.split())[:40]
            other = _find(ops, new)
            if other is not None and other is not o:
                return {"ok": False, "error": "operator already registered"}
            o["name"] = new
            changes.append(f"renamed → {new}")
        if not changes:
            return {"ok": False, "error": "nothing to change"}
        m._save_operators(ops)
        rb = state.get("rbac", {})
        if rb.get("operator", "").lower() == old.lower():          # the active session follows its operator
            rb["operator"], rb["role"] = o["name"], o["role"]
        await m._audit("operator.edit", f"{old}: {', '.join(changes)}")
        await m._sync("enterprise")
        return {"ok": True, "operators": ops}

    @r.delete("/api/operators/{name}")
    async def operators_delete(name: str):
        if not m._commander():
            return m._locked()
        ops = m._load_operators()
        o = _find(ops, name)
        if o is None:
            return {"ok": False, "error": "unknown operator"}
        if state.get("rbac", {}).get("operator", "").lower() == o["name"].lower():
            return {"ok": False, "error": "that operator is active in this session; switch operator first"}
        if o["role"] == "commander" and not any(x["role"] == "commander" for x in ops if x is not o):
            return {"ok": False, "error": "at least one commander must remain"}
        ops.remove(o)
        m._save_operators(ops)
        await m._audit("operator.remove", o["name"])
        await m._sync("enterprise")
        return {"ok": True, "operators": ops}

    return r
