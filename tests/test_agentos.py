"""AI CORE (agentos*.py) tests — stdlib unittest, no network, no credentials.

Run:  .venv\\Scripts\\python.exe -m unittest tests.test_agentos -v
"""

import asyncio
import hashlib
import io
import json
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agentos  # noqa: E402
import llm_providers  # noqa: E402
from agentos_memory import BM25, MemoryStore, tokens  # noqa: E402
from agentos_plugins import PluginManager, integrity, sha256_file, validate_manifest  # noqa: E402
from agentos_providers import (Adapter, AnthropicAdapter, ClaudeCLIAdapter, ModelSpec, OllamaAdapter,  # noqa: E402
                               OpenAICompatAdapter, infer_spec, result, scrub)
from agentos_router import ProviderHub, Router, classify  # noqa: E402
from agentos_tools import (Tool, ToolContext, ToolRegistry, extract_json, parse_json_calls,  # noqa: E402
                           run_tool_loop, safe_path, validate)
from agentos_workflows import Studio, check_condition, render  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class FakeAdapter(Adapter):
    """Scripted provider: `script` is a list of replies (dicts) consumed per call."""

    def __init__(self, aid, specs, script=None, native=True, fail=False, local=True):
        self.id, self.name, self.kind, self.local = aid, aid, "fake", local
        self.native_tools = native
        self.specs = specs
        self.script = list(script or [])
        self.fail = fail
        self.calls = []

    async def discover(self):
        return self.specs

    async def health(self):
        return {"ok": True, "detail": "fake"}

    async def chat(self, spec, messages, *, tools=None, json_mode=False, max_tokens=800, timeout=90,
                   on_delta=None, images=None):
        self.calls.append({"model": spec.model, "messages": [dict(m) for m in messages], "tools": tools})
        if self.fail:
            return result(False, error="HTTP 500: boom sk-abcdefghijklmnopqrstuvwxyz")
        r = self.script.pop(0) if self.script else {"text": "ok"}
        if on_delta and r.get("text"):
            await on_delta(r["text"])
        return result(True, r.get("text", ""), r.get("tool_calls"), ms=5)


def spec(provider, model, caps=("chat", "streaming"), q=3, speed=2, cost=1, local=True, ctx=8192):
    return ModelSpec(provider, model, set(caps), ctx, q, speed, cost, local)


def make_router(adapters, cloud=True):
    hub = ProviderHub(adapters)
    run(hub.discover())
    return Router(hub, None, cloud_allowed=lambda: cloud)


# ── capability registry ─────────────────────────────────────────
class Capabilities(unittest.TestCase):
    def test_ollama_reported_caps_win(self):
        s = infer_spec("ollama", "llama3.2:3b", local=True, kind="ollama",
                       meta={"capabilities": ["completion", "tools"], "params": "3.2B", "context": 131072})
        self.assertIn("tools", s.caps)
        self.assertNotIn("vision", s.caps)
        self.assertTrue(s.local)
        self.assertEqual(s.cost, 0)
        self.assertLessEqual(s.context, 8192)            # Ollama serves a smaller window by default

    def test_embedding_models_are_not_chat(self):
        s = infer_spec("ollama", "nomic-embed-text:latest", local=True, kind="ollama",
                       meta={"capabilities": ["embedding"]})
        self.assertEqual(s.caps, {"embeddings"})

    def test_cloud_heuristics(self):
        g = infer_spec("gemini", "gemini-2.0-flash", local=False, kind="openai")
        self.assertIn("vision", g.caps)
        self.assertGreaterEqual(g.context, 1_000_000)
        o = infer_spec("openai", "gpt-4o", local=False, kind="openai")
        self.assertEqual(o.quality, 5)
        self.assertIn("tools", o.caps)

    def test_ollama_discovery_via_api_tags(self):
        def handler(req):
            if req.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [
                    {"name": "qwen2.5:7b", "size": 4.7e9, "capabilities": ["completion", "tools"],
                     "details": {"parameter_size": "7.6B", "family": "qwen2", "context_length": 32768}},
                    {"name": "nomic-embed-text:latest", "capabilities": ["embedding"], "details": {}}]})
            if req.url.path == "/api/version":
                return httpx.Response(200, json={"version": "0.9.0"})
            return httpx.Response(404)
        a = OllamaAdapter("http://127.0.0.1:11434/v1")
        a.transport = httpx.MockTransport(handler)
        specs = {s.model: s for s in run(a.discover())}
        self.assertEqual(a.version, "0.9.0")
        self.assertIn("tools", specs["qwen2.5:7b"].caps)
        self.assertEqual(specs["qwen2.5:7b"].quality, 3)
        self.assertEqual(specs["nomic-embed-text:latest"].caps, {"embeddings"})

    def test_openai_native_tool_call_parsing(self):
        def handler(req):
            body = json.loads(req.content)
            self.assertEqual(body["tools"][0]["function"]["name"], "weather")
            self.assertEqual(req.headers["authorization"], "Bearer k-123")
            return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "weather", "arguments": "{\"city\": \"Pune\"}"}}]}}]})
        a = OpenAICompatAdapter("x", "X", "https://api.example.com/v1", "k-123", ["m"])
        a.transport = httpx.MockTransport(handler)
        r = run(a.chat(spec("x", "m"), [{"role": "user", "content": "hi"}],
                       tools=[{"type": "function", "function": {"name": "weather", "parameters": {}}}]))
        self.assertTrue(r["ok"])
        self.assertEqual(r["tool_calls"], [{"id": "c1", "name": "weather", "arguments": {"city": "Pune"}}])

    def test_anthropic_message_conversion(self):
        sys_, msgs = AnthropicAdapter.convert([
            {"role": "system", "content": "S"}, {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "type": "function",
                                                                 "function": {"name": "w", "arguments": "{\"a\":1}"}}]},
            {"role": "tool", "tool_call_id": "t1", "name": "w", "content": "R1"},
        ])
        self.assertEqual(sys_, "S")
        self.assertEqual(msgs[1]["content"][0], {"type": "tool_use", "id": "t1", "name": "w", "input": {"a": 1}})
        self.assertEqual(msgs[2]["content"][0]["type"], "tool_result")

    def test_claude_cli_flatten_and_no_native_tools(self):
        self.assertFalse(ClaudeCLIAdapter(None).native_tools)
        sys_, p = ClaudeCLIAdapter.flatten([{"role": "system", "content": "S"}, {"role": "user", "content": "q"}])
        self.assertEqual((sys_, p), ("S", "q"))

    def test_errors_never_carry_keys(self):
        self.assertNotIn("sk-abcdef", scrub("HTTP 401: Incorrect API key provided: sk-abcdefghijk123"))
        self.assertNotIn("gsk_", result(False, error="bad gsk_ABCDEFGHIJKL")["error"])


# ── routing ─────────────────────────────────────────────────────
class Routing(unittest.TestCase):
    def setUp(self):
        self.local = FakeAdapter("local", [spec("local", "small", ("chat", "streaming", "tools"), q=2, speed=1, cost=0)])
        self.cloud = FakeAdapter("cloud", [spec("cloud", "big", ("chat", "streaming", "tools", "vision", "reasoning", "code"),
                                                q=5, speed=2, cost=2, local=False, ctx=200_000)], local=False)

    def test_classify(self):
        self.assertEqual(classify("hi there")[0], "quick")
        self.assertEqual(classify("why does this design fail? compare the trade-offs")[0], "reasoning")
        self.assertEqual(classify("fix this python traceback")[0], "code")
        self.assertEqual(classify("keep this private please")[0], "private")
        self.assertEqual(classify("x", images=True)[0], "vision")
        self.assertEqual(classify("x " * 20000)[0], "long_context")

    def test_policies_change_the_pick(self):
        r = make_router([self.local, self.cloud])
        self.assertEqual(r.decide("hello", policy="fastest")["chosen"], "local/small")
        self.assertEqual(r.decide("hello", policy="best_quality")["chosen"], "cloud/big")
        self.assertEqual(r.decide("hello", policy="cheapest")["chosen"], "local/small")
        self.assertEqual(r.decide("analyse and compare these strategies in depth")["chosen"], "cloud/big")

    def test_private_and_vision_are_hard_constraints(self):
        r = make_router([self.local, self.cloud])
        d = r.decide("this is confidential", policy="best_quality")
        self.assertEqual(d["chosen"], "local/small")
        self.assertTrue(any(e["id"] == "cloud/big" and "private" in e["why"] for e in d["excluded"]))
        d = r.decide("what is in this picture", images=True, policy="cheapest")
        self.assertEqual(d["chosen"], "cloud/big")

    def test_licence_lock_excludes_cloud(self):
        r = make_router([self.local, self.cloud], cloud=False)
        d = r.decide("hello", policy="best_quality")
        self.assertEqual(d["chosen"], "local/small")
        self.assertTrue(any("licence" in e["why"] for e in d["excluded"]))

    def test_pins_and_disabled_overrides(self):
        r = make_router([self.local, self.cloud])
        r.update({"pins": {"quick": "cloud/big"}})
        d = r.decide("hello", policy="fastest")
        self.assertEqual(d["chosen"], "cloud/big")
        self.assertIn("operator pin", d["explain"])
        r.update({"pins": {}, "disabled": ["local/small"]})
        self.assertEqual(r.decide("hello", policy="fastest")["chosen"], "cloud/big")

    def test_fallback_on_failure_and_cooldown(self):
        bad = FakeAdapter("bad", [spec("bad", "m", q=5, speed=1, cost=0)], fail=True)
        good = FakeAdapter("good", [spec("good", "m", q=2, speed=2, cost=0)], script=[{"text": "from good"}])
        r = make_router([bad, good])
        res, d = run(r.chat([{"role": "user", "content": "hello"}], policy="best_quality"))
        self.assertTrue(res["ok"])
        self.assertEqual(res["text"], "from good")
        self.assertEqual([a["model"] for a in d["attempts"]], ["bad/m", "good/m"])
        self.assertIn("Fell back", d["explain"])
        self.assertNotIn("sk-abc", json.dumps(d))                 # provider errors are scrubbed
        d2 = r.decide("hello", policy="best_quality")             # failed model now cooling down
        self.assertEqual(d2["chosen"], "good/m")
        self.assertTrue(any("cooling" in e["why"] for e in d2["excluded"]))

    def test_measured_latency_feeds_speed(self):
        r = make_router([self.local, self.cloud])
        r.hub.health.record("local/small", True, 60000)          # measured: very slow
        r.hub.health.record("cloud/big", True, 400)
        self.assertEqual(r.decide("hello", policy="fastest")["chosen"], "cloud/big")

    def test_no_models_is_explained(self):
        r = make_router([])
        d = r.decide("hello")
        self.assertIsNone(d["chosen"])
        res, d = run(r.chat([{"role": "user", "content": "hi"}]))
        self.assertFalse(res["ok"])


# ── tools & permissions ─────────────────────────────────────────
def registry_with(calls):
    reg = ToolRegistry()
    reg.register(Tool("weather", "w", {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
                      "read", lambda a, t: calls.append(("weather", a)) or {"temp_c": 30, "city": a["city"]}))
    reg.register(Tool("lamp", "l", {"type": "object", "properties": {}}, "action",
                      lambda a, t: calls.append(("lamp", a)) or {"on": True}))
    reg.register(Tool("unlock", "u", {"type": "object", "properties": {}}, "destructive",
                      lambda a, t: calls.append(("unlock", a)) or {"unlocked": True}))
    reg.register(Tool("device", "d", {"type": "object", "properties": {"action": {"type": "string"}}}, "action",
                      lambda a, t: calls.append(("device", a)) or {"ok": True},
                      tier_fn=lambda a: "destructive" if a.get("action") == "unlock" else "read"))
    return reg


class Permissions(unittest.TestCase):
    def test_tiers(self):
        calls = []
        reg = registry_with(calls)
        obs = ToolContext(commander=False)
        cmd = ToolContext(commander=True)
        self.assertTrue(run(reg.run("weather", {"city": "Pune"}, obs))["ok"])
        r = run(reg.run("lamp", {}, obs))
        self.assertFalse(r["ok"]); self.assertIn("commander", r["error"])
        self.assertTrue(run(reg.run("lamp", {}, cmd))["ok"])
        r = run(reg.run("unlock", {}, cmd))
        self.assertFalse(r["ok"]); self.assertTrue(r["approval_id"])
        self.assertNotIn(("unlock", {}), calls)                   # nothing executed before approval
        self.assertEqual(len(reg.pending()), 1)
        # observers can't approve; commander approval executes exactly that call
        self.assertFalse(run(reg.decide_approval(r["approval_id"], True, obs))["ok"])
        out = run(reg.decide_approval(r["approval_id"], True, cmd))
        self.assertTrue(out["ok"] and out["result"]["ok"])
        self.assertIn(("unlock", {}), calls)
        self.assertEqual(reg.pending(), [])

    def test_tier_escalation_never_lowers(self):
        calls = []
        reg = registry_with(calls)
        self.assertFalse(run(reg.run("device", {"action": "on"}, ToolContext(commander=False)))["ok"])  # stays action
        r = run(reg.run("device", {"action": "unlock"}, ToolContext(commander=True)))
        self.assertTrue(r.get("approval_id"))

    def test_allow_list_and_validation(self):
        reg = registry_with([])
        r = run(reg.run("lamp", {}, ToolContext(commander=True, allowed={"weather"})))
        self.assertIn("not in this agent's allowed tools", r["error"])
        self.assertIn("missing required", run(reg.run("weather", {}, ToolContext()))["error"])
        self.assertIsNone(validate({"type": "object", "properties": {"n": {"type": "integer"}}}, {"n": "5"}))
        self.assertIsNotNone(validate({"type": "object", "properties": {"f": {"type": "string", "enum": ["a"]}}}, {"f": "b"}))

    def test_safe_path(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.txt").write_text("x")
            (Path(d) / ".env").write_text("SECRET=1")
            self.assertEqual(safe_path([d], "a.txt").name, "a.txt")
            for bad in ("../outside.txt", ".env", str(Path(d).parent / "x.txt")):
                with self.assertRaises(PermissionError):
                    safe_path([d], bad)
            with self.assertRaises(PermissionError):
                safe_path([], "a.txt")

    def test_json_extraction(self):
        self.assertEqual(extract_json('sure! ```json\n{"final": "a {b}"}\n```'), {"final": "a {b}"})
        calls, final = parse_json_calls('{"tools": [{"tool": "weather", "arguments": {"city": "X"}}, {"tool": "nope"}]}', {"weather"})
        self.assertEqual([c["name"] for c in calls], ["weather"])
        self.assertIsNone(final)
        calls, final = parse_json_calls('{"name": "weather", "parameters": {"city": "Y"}}', {"weather"})
        self.assertEqual(calls[0]["arguments"], {"city": "Y"})
        self.assertEqual(parse_json_calls("plain answer", {"weather"}), ([], "plain answer"))


class ToolLoop(unittest.TestCase):
    def test_native_loop(self):
        calls = []
        reg = registry_with(calls)
        a = FakeAdapter("n", [spec("n", "m", ("chat", "tools"))], script=[
            {"tool_calls": [{"id": "c1", "name": "weather", "arguments": {"city": "Hyderabad"}}]},
            {"text": "It is 30°C in Hyderabad."}])
        r = make_router([a])
        out = run(run_tool_loop(r, reg, "weather in Hyderabad?", tctx=ToolContext()))
        self.assertTrue(out["ok"])
        self.assertEqual(out["answer"], "It is 30°C in Hyderabad.")
        self.assertTrue(out["native"])
        self.assertEqual(calls, [("weather", {"city": "Hyderabad"})])
        self.assertIsNotNone(a.calls[0]["tools"])                 # schemas sent natively
        second = a.calls[1]["messages"]
        self.assertEqual(second[-1]["role"], "tool")
        self.assertEqual(second[-2]["tool_calls"][0]["function"]["name"], "weather")

    def test_json_protocol_loop(self):
        calls = []
        reg = registry_with(calls)
        a = FakeAdapter("j", [spec("j", "m", ("chat",))], native=False, script=[
            {"text": 'I will check. {"tools": [{"tool": "weather", "arguments": {"city": "Pune"}}]}'},
            {"text": '{"final": "Pune is 30°C (weather tool)."}'}])
        r = make_router([a])
        out = run(run_tool_loop(r, reg, "weather in Pune", tctx=ToolContext()))
        self.assertEqual(out["answer"], "Pune is 30°C (weather tool).")
        self.assertFalse(out["native"])
        self.assertIsNone(a.calls[0]["tools"])
        self.assertIn('"final"', a.calls[0]["messages"][0]["content"])      # protocol in the system prompt
        self.assertIn("TOOL RESULT weather", a.calls[1]["messages"][-1]["content"])

    def test_native_failure_falls_back_to_json_on_same_model(self):
        class Flaky(FakeAdapter):
            async def chat(self, spec_, messages, *, tools=None, **kw):
                if tools:
                    self.calls.append({"tools": tools})
                    return result(False, error="HTTP 400: tools not supported")
                return await super().chat(spec_, messages, tools=None, **kw)
        a = Flaky("f", [spec("f", "m", ("chat", "tools"))], script=[{"text": '{"final": "done"}'}])
        out = run(run_tool_loop(make_router([a]), registry_with([]), "x", tctx=ToolContext()))
        self.assertEqual(out["answer"], "done")
        self.assertFalse(out["native"])

    def test_destructive_call_in_loop_is_queued_not_run(self):
        calls = []
        reg = registry_with(calls)
        a = FakeAdapter("n", [spec("n", "m", ("chat", "tools"))], script=[
            {"tool_calls": [{"id": "c1", "name": "unlock", "arguments": {}}]},
            {"text": "Waiting for your approval."}])
        out = run(run_tool_loop(make_router([a]), reg, "unlock the door", tctx=ToolContext(commander=True)))
        self.assertEqual(len(out["approvals"]), 1)
        self.assertEqual(calls, [])
        self.assertIn("approval", a.calls[1]["messages"][-1]["content"])

    def test_observer_cannot_run_action_tools_through_the_model(self):
        calls = []
        a = FakeAdapter("n", [spec("n", "m", ("chat", "tools"))], script=[
            {"tool_calls": [{"id": "c1", "name": "lamp", "arguments": {}}]}, {"text": "can't"}])
        out = run(run_tool_loop(make_router([a]), registry_with(calls), "lamp on", tctx=ToolContext(commander=False)))
        self.assertEqual(calls, [])
        self.assertIn("commander", out["steps"][0]["error"])


# ── memory ──────────────────────────────────────────────────────
class Memory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = MemoryStore(Path(self.tmp.name) / "m.db")

    def tearDown(self):
        self.m.close()
        self.tmp.cleanup()

    def test_bm25_ranks_relevant_first(self):
        self.m.add("The operator prefers dark roast coffee in the morning", kind="fact")
        self.m.add("Quarterly budget review meeting with finance on Friday", kind="note")
        self.m.add("Server backups run nightly at 2am to the NAS", kind="fact")
        hits = self.m.search("when is the budget meeting?")
        self.assertIn("budget", hits[0]["text"])
        self.assertEqual(self.m.search("meet")[0]["kind"], "note")           # prefix match
        self.assertEqual(self.m.search("zzzz"), [])

    def test_crud_pin_dedupe_and_scope(self):
        a = self.m.add("alpha fact about rockets")
        self.assertTrue(self.m.add("dup", source_key="k1")["ok"])
        self.assertTrue(self.m.add("dup", source_key="k1").get("duplicate"))
        self.m.add("rockets secret of agent x", scope="agent-x")
        self.assertEqual(len(self.m.search("rockets", scope="agent-y")), 1)
        self.assertEqual(len(self.m.search("rockets", scope="agent-x")), 2)
        self.assertTrue(self.m.update(a["id"], text="beta fact about rockets", pinned=True)["ok"])
        self.assertTrue(self.m.get(a["id"])["pinned"])
        self.assertEqual(self.m.search("beta")[0]["id"], a["id"])
        self.assertTrue(self.m.forget(a["id"])["ok"])
        self.assertIsNone(self.m.get(a["id"]))

    def test_documents_chunk_and_forget_together(self):
        text = "\n\n".join(f"Paragraph {i} about orbital mechanics and launch windows." * 8 for i in range(12))
        r = self.m.add_document("Launch handbook", text)
        self.assertGreater(r["chunks"], 1)
        hit = self.m.search("launch windows")[0]
        self.assertEqual(self.m.forget(hit["id"])["removed"], r["chunks"])
        self.assertEqual(self.m.stats()["counts"]["document"], 0)

    def test_context_block_has_citations_and_budget(self):
        for i in range(10):
            self.m.add(f"Fact {i}: the satellite telemetry link uses band {i} " + "x" * 300)
        block, cites = run(self.m.context_block("satellite telemetry", budget=500))
        self.assertIn("[M", block)
        self.assertLessEqual(len(block), 500 + 200)
        self.assertTrue(cites and cites[0]["id"].startswith("M"))

    def test_hybrid_with_embeddings(self):
        vec = {"apple": [1.0, 0.0], "fruit": [0.9, 0.1], "car": [0.0, 1.0]}

        async def emb(texts):
            return "fake-embed", [vec["fruit" if "fruit" in t or "apple" in t else "car"] for t in texts]
        self.m.embedder = emb
        self.m.add("I like apple pie")
        self.m.add("The car needs new tyres")
        self.assertEqual(run(self.m.embed_pending()), 2)
        hits = run(self.m.asearch("fruit"))                      # no keyword overlap → vector match only
        self.assertEqual(hits[0]["text"], "I like apple pie")
        self.assertIn("embeddings", self.m.stats()["retrieval"])

    def test_legacy_import_is_idempotent_and_non_destructive(self):
        class Op:
            data = {"facts": [{"text": "Prefers concise answers", "source": "manual"}], "profile": {"name": "Sai"}}

        class Team:
            entries = [{"ts": 1.0, "agent": "widow", "kind": "reply", "text": "ISRO launch confirmed for Friday"}]
        notes = Path(self.tmp.name) / "notes.json"
        notes.write_text(json.dumps([{"text": "buy NAS drives"}]))
        before = notes.read_text()
        r1 = self.m.import_legacy(Op(), Team(), notes)
        r2 = self.m.import_legacy(Op(), Team(), notes)
        self.assertEqual(r1["imported"], {"facts": 1, "team": 1, "notes": 1, "profile": 1})
        self.assertEqual(r2["imported"], {"facts": 0, "team": 0, "notes": 0, "profile": 0})
        self.assertEqual(notes.read_text(), before)
        ep = self.m.list(kind="episode")[0]
        self.assertEqual(ep["created"], 1.0)                       # original time kept, not the import time
        self.assertEqual(Op.data["facts"][0]["text"], "Prefers concise answers")

    def test_tokens(self):
        self.assertEqual(tokens("The C++ build failed"), ["c++", "build", "failed"])
        self.assertTrue(BM25({}).scores(["x"]) == {})


# ── plugins ─────────────────────────────────────────────────────
PLUGIN_CODE = b'''def register(api):
    def add(args, ctx=None):
        return {"sum": args["a"] + args["b"]}
    api.tool("add_numbers", add)
'''


def manifest(code=PLUGIN_CODE, **over):
    m = {"id": "adder", "name": "Adder", "version": "1.0.0", "author": "test", "description": "adds",
         "permissions": [], "entrypoint": "plugin.py", "sha256": hashlib.sha256(code).hexdigest(),
         "tools": [{"name": "add_numbers", "tier": "read", "description": "add",
                    "parameters": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                                   "required": ["a", "b"]}}]}
    m.update(over)
    return m


class Plugins(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "src"
        self.src.mkdir()
        (self.src / "plugin.py").write_bytes(PLUGIN_CODE)
        (self.src / "manifest.json").write_text(json.dumps(manifest()))
        self.reg = ToolRegistry()
        self.pm = PluginManager(ROOT / "plugins", self.tmp / "data", self.reg, {})

    def tearDown(self):
        for pid in list(self.pm.loaded):
            self.pm.unload(pid)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_manifest_validation(self):
        self.assertEqual(validate_manifest(manifest()), [])
        self.assertTrue(validate_manifest(manifest(id="Bad Id")))
        self.assertTrue(validate_manifest(manifest(version="one")))
        self.assertTrue(validate_manifest(manifest(permissions=["root"])))
        self.assertTrue(validate_manifest(manifest(entrypoint="../x.py")))
        self.assertTrue(validate_manifest(manifest(tools=[])))

    def test_integrity_and_crlf_normalisation(self):
        self.assertTrue(integrity(self.src, manifest())[0])
        (self.src / "plugin.py").write_bytes(PLUGIN_CODE.replace(b"\n", b"\r\n"))
        self.assertTrue(integrity(self.src, manifest())[0])       # a CRLF checkout is not tampering
        (self.src / "plugin.py").write_bytes(PLUGIN_CODE + b"\nimport os\n")
        self.assertFalse(integrity(self.src, manifest())[0])

    def test_install_requires_preview_and_confirmation_then_enable(self):
        pv = run(self.pm.preview("folder", str(self.src)))
        self.assertTrue(pv["ok"])
        self.assertIn("full privileges", pv["warning"])
        self.assertFalse(self.pm.install(pv["token"], confirm=False)["ok"])
        pv = run(self.pm.preview("folder", str(self.src)))
        r = self.pm.install(pv["token"], confirm=True)
        self.assertTrue(r["ok"])
        self.assertFalse(r["enabled"])                             # third-party never auto-enabled
        self.assertNotIn("add_numbers", self.reg.tools)
        self.assertTrue(self.pm.set_enabled("adder", True)["ok"])
        out = run(self.reg.run("add_numbers", {"a": 2, "b": 3}, ToolContext()))
        self.assertEqual(out["result"], {"sum": 5})
        # tampering after install → refused at next load
        installed = Path(self.pm.records["adder"]["path"])
        (installed / "plugin.py").write_bytes(PLUGIN_CODE + b"\n# evil\n")
        m = json.loads((installed / "manifest.json").read_text())
        m["sha256"] = sha256_file(installed / "plugin.py")         # attacker also rewrites the manifest
        (installed / "manifest.json").write_text(json.dumps(m))
        r = self.pm.set_enabled("adder", True)
        self.assertFalse(r["ok"])
        self.assertIn("pinned", r["error"])
        self.assertNotIn("add_numbers", self.reg.tools)
        self.assertTrue(self.pm.uninstall("adder")["ok"])
        self.assertFalse(installed.exists())

    def test_undeclared_tools_and_missing_permissions_are_refused(self):
        code = b'def register(api):\n    api.tool("sneaky", lambda a, c=None: 1)\n'
        (self.src / "plugin.py").write_bytes(code)
        (self.src / "manifest.json").write_text(json.dumps(manifest(code)))
        pv = run(self.pm.preview("folder", str(self.src)))
        self.pm.install(pv["token"], True)
        r = self.pm.set_enabled("adder", True)
        self.assertFalse(r["ok"])
        self.assertIn("not declared", r["error"])
        code2 = b'async def _x(a, c=None):\n    return 1\ndef register(api):\n    api.tool("add_numbers", _x)\n    api.notify\n    import asyncio\n    asyncio.run(api.http_get("https://example.com"))\n'
        (self.src / "plugin.py").write_bytes(code2)
        (self.src / "manifest.json").write_text(json.dumps(manifest(code2)))
        pv = run(self.pm.preview("folder", str(self.src)))
        self.pm.install(pv["token"], True)
        r = self.pm.set_enabled("adder", True)
        self.assertFalse(r["ok"])
        self.assertIn("network", r["error"])

    def test_zip_extraction_rejects_traversal(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../evil.py", "x")
        with self.assertRaises(ValueError):
            PluginManager.safe_extract(buf.getvalue(), self.tmp / "z")

    def test_first_party_plugins_ship_valid_and_work(self):
        self.pm.bootstrap()
        res = self.pm.load_enabled()
        self.assertTrue(res["hn_digest"]["ok"], res)
        self.assertTrue(res["unit_converter"]["ok"], res)
        conv = lambda v, f, t: run(self.reg.run("convert_units", {"value": v, "from_unit": f, "to_unit": t},  # noqa: E731
                                                 ToolContext()))["result"]["result"]
        self.assertAlmostEqual(conv(5, "mi", "km"), 8.04672)
        self.assertAlmostEqual(conv(212, "F", "C"), 100.0)
        self.assertAlmostEqual(conv(1, "GiB", "MB"), 1073.741824)
        self.assertFalse(run(self.reg.run("convert_units", {"value": 1, "from_unit": "kg", "to_unit": "km"},
                                          ToolContext()))["ok"])
        self.assertEqual(self.reg.tools["hn_top_stories"].tier, "read")


# ── workflows ───────────────────────────────────────────────────
class Workflows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []
        self.reg = registry_with(self.calls)
        self.asked = []
        self.notes = []

        async def ask(prompt, agent, policy):
            self.asked.append((prompt, (agent or {}).get("id")))
            return {"ok": True, "answer": f"ANSWER[{prompt}]", "decision": {"used": "fake/m"}, "steps": []}

        async def tool(name, args, run_):
            return await self.reg.run(name, args, ToolContext(commander=True, requester="wf"))

        async def notify(msg):
            self.notes.append(msg)
        self.st = Studio(Path(self.tmp.name), ask=ask, run_tool=tool, notify=notify,
                         request_approval=lambda r, s: self.reg.request_approval("workflow", {}, None, kind="workflow",
                                                                                 run_id=r["id"], message=s["message"]),
                         tool_names=lambda: set(self.reg.tools), policies={"balanced", "fastest"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_helpers(self):
        self.assertEqual(render({"q": "{{input}}-{{x}}"}, {"input": "a", "x": 1}), {"q": "a-1"})
        self.assertTrue(check_condition({"var": "t", "op": "gt", "value": 30}, {"t": "temp 31.5 C"}))
        self.assertTrue(check_condition({"var": "t", "op": "empty"}, {}))

    def test_validation(self):
        self.assertFalse(self.st.upsert_agent({"name": "A", "tools": ["nope"]})["ok"])
        self.assertFalse(self.st.upsert_agent({"name": "A", "schedule": {"every_min": 1}})["ok"])
        self.assertTrue(self.st.upsert_agent({"name": "Scout", "tools": ["weather"]})["ok"])
        self.assertFalse(self.st.upsert_workflow({"name": "W", "steps": [{"type": "tool", "tool": "nope"}]})["ok"])
        self.assertFalse(self.st.upsert_workflow({"name": "W", "steps": [
            {"id": "c", "type": "condition", "if": {"op": "exists"}, "then": "ghost"}]})["ok"])

    def test_branching_approval_and_resume(self):
        self.st.upsert_agent({"name": "Writer"})
        w = self.st.upsert_workflow({"name": "Brief", "steps": [
            {"id": "wx", "type": "tool", "tool": "weather", "args": {"city": "{{input}}"}, "save_as": "wx"},
            {"id": "c", "type": "condition", "if": {"var": "wx", "op": "contains", "value": "Mars"}, "then": "end", "else": "w"},
            {"id": "w", "type": "agent", "agent": "writer", "prompt": "brief: {{wx}}", "save_as": "brief"},
            {"id": "ok", "type": "approval", "message": "post it?"},
            {"id": "n", "type": "notify", "message": "{{brief}}"}]})
        self.assertTrue(w["ok"], w)
        r = run(self.st.run_workflow("brief", "Pune", wait=True))["run"]
        self.assertEqual(r["status"], "waiting_approval")
        self.assertEqual(self.calls, [("weather", {"city": "Pune"})])
        self.assertEqual(self.asked[0][1], "writer")
        self.assertEqual(self.notes, [])                            # nothing posted before approval
        ap = self.reg.pending()[0]
        self.assertEqual(ap["run_id"], r["id"])
        run(self.st.resume(ap["id"], True, wait=True))
        self.assertEqual(r["status"], "done")
        self.assertEqual(len(self.notes), 1)
        self.assertIn("ANSWER[brief:", self.notes[0])

    def test_rejection_stops_and_condition_can_end(self):
        self.st.upsert_workflow({"name": "Gate", "steps": [{"id": "a", "type": "approval", "message": "go?"},
                                                           {"id": "n", "type": "notify", "message": "x"}]})
        r = run(self.st.run_workflow("gate", wait=True))["run"]
        run(self.st.resume(r["approval_id"], False, wait=True))
        self.assertEqual(r["status"], "rejected")
        self.assertEqual(self.notes, [])
        self.st.upsert_workflow({"name": "Ender", "steps": [
            {"id": "c", "type": "condition", "if": {"var": "input", "op": "equals", "value": "stop"}, "then": "end"},
            {"id": "n", "type": "notify", "message": "ran"}]})
        self.assertEqual(run(self.st.run_workflow("ender", "stop", wait=True))["run"]["status"], "done")
        self.assertEqual(self.notes, [])

    def test_destructive_tool_step_waits_and_resumes_with_value(self):
        self.st.upsert_workflow({"name": "Door", "steps": [
            {"id": "u", "type": "tool", "tool": "unlock", "save_as": "res"},
            {"id": "n", "type": "notify", "message": "door: {{res}}"}]})
        r = run(self.st.run_workflow("door", wait=True))["run"]
        self.assertEqual(r["status"], "waiting_approval")
        self.assertEqual(self.calls, [])
        out = run(self.reg.decide_approval(r["approval_id"], True, ToolContext(commander=True)))
        run(self.st.resume(out["approval"]["id"], True, wait=True, value=out["result"]["result"]))
        self.assertEqual(r["status"], "done")
        self.assertIn("unlocked", self.notes[0])

    def test_schedule_tick_and_loop_guard(self):
        self.st.upsert_agent({"name": "Pulse", "schedule": {"every_min": 5}, "heartbeat": "status?"})
        fired = run(self.st.tick(now=time.time()))
        self.assertEqual(fired, ["agent:pulse"])
        self.assertEqual(run(self.st.tick(now=time.time() + 60)), [])        # not due yet
        self.st.upsert_workflow({"name": "Loop", "steps": [
            {"id": "a", "type": "notify", "message": "x"},
            {"id": "c", "type": "condition", "if": {"var": "input", "op": "empty"}, "then": "a"}]})
        r = run(self.st.run_workflow("loop", wait=True))["run"]
        self.assertEqual(r["status"], "failed")
        self.assertIn("loop", r["log"][-1]["msg"])

    def test_persistence(self):
        self.st.upsert_agent({"name": "Keeper"})
        st2 = Studio(Path(self.tmp.name), ask=None, run_tool=None, request_approval=None)
        self.assertIn("keeper", st2.agents)


# ── integration: AICore + smart provider hook ───────────────────
class Core(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        llm_providers.SMART["client"] = None
        self.tmp.cleanup()

    def test_core_complete_with_memory_and_smart_client(self):
        a = FakeAdapter("f", [spec("f", "m")], script=[{"text": "routed reply"}, {"text": "via brain"}])
        core = agentos.AICore({}, {}, data_dir=Path(self.tmp.name), adapters=[a])
        run(core.boot())
        self.assertIn("convert_units", core.tools.tools)              # first-party plugins loaded
        core.memory.add("The operator's rocket is called Falcon Nine", pinned=True)
        r = run(core.complete("what is my rocket called?", use_memory=True))
        self.assertTrue(r["ok"])
        self.assertEqual(r["text"], "routed reply")
        self.assertIn("Falcon Nine", a.calls[0]["messages"][0]["content"])
        self.assertTrue(r["citations"])
        state = {"agentos": core}
        smart = agentos.SmartClient(state)
        self.assertEqual(run(smart.chat("hi")), "via brain")
        core.memory.close()

    def test_registry_offers_smart_only_when_loaded(self):
        from llm_local import LocalLLM

        class B:
            mode = "llm"
            force_local, provider, provider_name = False, None, None
        path = Path(self.tmp.name) / "providers.json"
        reg = llm_providers.ProviderRegistry(B(), LocalLLM(base_url="http://127.0.0.1:11434/v1", model="x"), path)
        self.assertNotIn("smart", [p["id"] for p in reg.list()])
        self.assertFalse(reg.set_active("smart")["ok"])
        llm_providers.SMART["client"] = agentos.SmartClient({})
        self.assertIn("smart", [p["id"] for p in reg.list()])
        self.assertTrue(reg.set_active("smart")["ok"])
        self.assertEqual(reg.brain.provider_name, "smart")
        reg.gate = lambda f: False                                     # unlicensed: router gates cloud per model
        reg._apply()
        self.assertEqual(reg.brain.provider_name, "smart")
        self.assertTrue(reg.brain.cloud_locked)
        llm_providers.SMART["client"] = None                           # module not loaded → original chain
        again = llm_providers.ProviderRegistry(B(), LocalLLM(base_url="http://127.0.0.1:11434/v1", model="x"), path)
        self.assertIsNone(again.brain.provider)

    def test_router_api_shape_never_leaks_keys(self):
        a = OpenAICompatAdapter("openai", "OpenAI", "https://api.openai.com/v1", "sk-supersecretvalue123456", ["gpt-4o-mini"])
        core = agentos.AICore({}, {}, data_dir=Path(self.tmp.name), adapters=[a])
        core.hub.add_models(run(a.discover()))
        self.assertNotIn("supersecret", json.dumps(core.snapshot(), default=str))
        core.memory.close()

    def test_folder_approval_refuses_install_dir(self):
        core = agentos.AICore({}, {}, data_dir=Path(self.tmp.name), adapters=[])
        self.assertFalse(core.set_folders([str(ROOT)])["ok"])
        self.assertFalse(core.set_folders([str(ROOT.parent)])["ok"])
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(core.set_folders([d])["ok"])
            self.assertEqual(len(core.folders()), 2)
        core.memory.close()


if __name__ == "__main__":
    unittest.main()
