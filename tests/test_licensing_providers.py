"""Licensing + LLM provider registry tests — no network, temp files."""

import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import licensing  # noqa: E402
import llm_providers  # noqa: E402
from llm_local import LocalLLM  # noqa: E402


def run(c):
    return asyncio.run(c)


class Licence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "license.json"
        self.env = mock.patch.dict(os.environ, {"BUCS_OWNER": "0"}); self.env.start()

    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()

    def test_customer_starts_on_community_with_paid_features_locked(self):
        lic = licensing.Licensing(self.path)
        self.assertEqual(lic.edition, "community")
        self.assertFalse(lic.allows("cloud_brain"))
        self.assertTrue(lic.allows("telemetry"))

    def test_owner_has_everything(self):
        with mock.patch.dict(os.environ, {"BUCS_OWNER": "1"}):
            lic = licensing.Licensing(self.path)
        self.assertEqual(lic.edition, "enterprise")
        self.assertTrue(all(lic.snapshot()["entitlements"].values()))

    def test_activation_unlocks_the_purchased_edition(self):
        lic = licensing.Licensing(self.path)
        ok = {"activated": True, "license_key": {"status": "active"}, "instance": {"id": "inst-1"},
              "meta": {"product_name": "BUCS", "variant_name": "Team (monthly)", "customer_email": "buyer@example.com"}}
        with mock.patch.object(lic, "_post", mock.AsyncMock(return_value=ok)):
            r = run(lic.activate("ABCD-1234-EFGH-5678"))
        self.assertTrue(r["ok"])
        self.assertEqual(lic.edition, "team")
        self.assertTrue(lic.allows("fleet") and not lic.allows("sso"))
        self.assertEqual(r["key"], "ABCD…5678")                       # never echoed in full
        self.assertEqual(licensing.Licensing(self.path).edition, "team")  # persisted

    def test_refused_key_unlocks_nothing(self):
        lic = licensing.Licensing(self.path)
        with mock.patch.object(lic, "_post", mock.AsyncMock(return_value={"activated": False, "error": "license_key not found"})):
            r = run(lic.activate("WRONG-KEY-0000"))
        self.assertFalse(r["ok"])
        self.assertEqual(lic.edition, "community")

    def test_offline_grace_then_honest_downgrade(self):
        lic = licensing.Licensing(self.path)
        lic.data = {"key": "K" * 12, "status": "active", "edition": "pro", "validated_at": time.time() - 3 * 86400}
        self.assertEqual(lic.edition, "pro")                            # within 24 h + 7 d grace
        lic.data["validated_at"] = time.time() - 9 * 86400
        self.assertEqual(lic.edition, "community")

    def test_edition_from_product_names(self):
        self.assertEqual(licensing.edition_from_names("BUCS", "Enterprise"), "enterprise")
        self.assertEqual(licensing.edition_from_names("BUCS Pro", None), "pro")


class FakeBrain:
    mode = "llm"

    def __init__(self):
        self.force_local, self.provider, self.provider_name = False, None, None


class Providers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "providers.json"
        self.local = LocalLLM(base_url="http://127.0.0.1:11434/v1", model="llama3.2:3b")

    def tearDown(self):
        self.tmp.cleanup()

    def reg(self):
        return llm_providers.ProviderRegistry(FakeBrain(), self.local, self.path)

    def test_custom_clients_never_inherit_local_key_or_model(self):
        with mock.patch.dict(os.environ, {"LOCAL_LLM_KEY": "local-secret", "LOCAL_LLM_MODEL": "llama"}):
            c = LocalLLM(base_url="https://api.example.com/v1", model="m-1", isolated=True)
            c2 = LocalLLM(base_url="https://api.example.com/v1", model=None, isolated=True)
        self.assertIsNone(c.api_key)
        self.assertEqual(c.model, "m-1")
        self.assertIsNone(c2.model)

    def test_add_validate_switch_and_mask(self):
        r = self.reg()
        self.assertFalse(r.upsert("Bad", "http://api.example.com/v1", "m")["ok"])     # remote must be https
        self.assertFalse(r.upsert("NoModel", "https://api.example.com/v1", "")["ok"])
        ok = r.upsert("Groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "gsk_abcdefghijklmnop")
        self.assertTrue(ok["ok"])
        p = r.get("groq")
        self.assertEqual((p["tier"], p["api_key"]), ("cloud", "gsk…mnop"))
        self.assertTrue(r.set_active("groq")["ok"])
        self.assertEqual(r.brain.provider_name, "groq")
        self.assertEqual(r.brain.provider.api_key, "gsk_abcdefghijklmnop")
        again = llm_providers.ProviderRegistry(FakeBrain(), self.local, self.path)      # persisted
        self.assertEqual(again.active, "groq")
        self.assertEqual(again.brain.provider_name, "groq")

    def test_local_endpoint_is_local_tier_and_auto_restores_chain(self):
        r = self.reg()
        r.upsert("LM Studio", "http://127.0.0.1:1234/v1", "qwen2.5-7b")
        self.assertEqual(r.get("lm-studio")["tier"], "local")
        r.set_active("lm-studio"); r.set_active("auto")
        self.assertIsNone(r.brain.provider)
        self.assertFalse(r.brain.force_local)

    def test_unlicensed_install_keeps_cloud_brains_locked(self):
        r = self.reg()
        r.upsert("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini", "sk-abcdefghijklmnop")
        r.upsert("LM Studio", "http://127.0.0.1:1234/v1", "qwen")
        r.set_active("openai")
        r.gate = lambda feat: False                          # community customer
        r._apply()
        self.assertIsNone(r.brain.provider)                  # no paid cloud call
        self.assertTrue(r.brain.force_local and r.brain.cloud_locked)
        locked = {p["id"]: p["locked"] for p in r.list()}
        self.assertTrue(locked["openai"] and locked["auto"] and locked["claude"])
        self.assertFalse(locked["ollama"] or locked["lm-studio"])
        r.gate = lambda feat: True                           # licence activated
        r._apply()
        self.assertEqual(r.brain.provider_name, "openai")
        self.assertFalse(r.brain.cloud_locked)

    def test_locked_brain_never_calls_claude(self):
        from brain import Brain
        b = Brain(claude_bin="__none__", local_brain=None, local_llm=None)
        b.cloud_locked = True
        self.assertTrue(run(b._claude_stream("hi", None, 5)).startswith("[brain locked"))

    def test_failed_provider_falls_back_to_chain(self):
        from brain import Brain
        from local_brain import LocalBrain
        b = Brain(claude_bin="__none__", local_brain=LocalBrain(), local_llm=None)
        b.mode = "local"
        b.provider = mock.Mock(chat_stream=mock.AsyncMock(return_value=None))
        b.provider_name = "broken"
        reply, path = run(b.stream("hello there"))
        self.assertEqual(path, "template")                              # never silent
        self.assertTrue(reply)


if __name__ == "__main__":
    unittest.main()
