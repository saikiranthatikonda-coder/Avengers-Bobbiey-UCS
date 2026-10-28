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
    """Owner-signed keys: issued on the owner machine, verified offline anywhere."""

    def setUp(self):
        import base64
        from cryptography.hazmat.primitives import serialization as ser
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        sk = Ed25519PrivateKey.generate()
        (d / "signing.key").write_bytes(sk.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.PKCS8, ser.NoEncryption()))
        self.pub = base64.urlsafe_b64encode(sk.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)).decode().rstrip("=")
        self.kw = dict(path=d / "license.json", public_key=self.pub, signing_key=d / "signing.key", issued=d / "issued.json")
        self.env = mock.patch.dict(os.environ, {"BUCS_OWNER": "0"}); self.env.start()

    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()

    def owner(self):
        with mock.patch.dict(os.environ, {"BUCS_OWNER": "1"}):
            return licensing.Licensing(**self.kw)

    def test_customer_install_is_locked(self):
        lic = licensing.Licensing(**self.kw)
        self.assertFalse(lic.paid)
        self.assertFalse(any(lic.snapshot()["entitlements"].values()))
        self.assertFalse(lic.issue("a@b.c")["ok"])                     # customers can't mint keys

    def test_owner_has_everything_and_can_issue(self):
        o = self.owner()
        self.assertTrue(o.paid and o.snapshot()["can_issue"])
        self.assertTrue(all(o.snapshot()["entitlements"].values()))

    def test_issued_key_unlocks_a_customer_install(self):
        r = self.owner().issue("buyer@example.com", days=31, payment_ref="UTR123")
        self.assertTrue(r["ok"] and r["key"].startswith("BUCS-"))
        lic = licensing.Licensing(**self.kw)
        a = lic.activate(r["key"])
        self.assertTrue(a["ok"])
        self.assertTrue(lic.paid and lic.allows("cloud_brain"))
        self.assertEqual(a["days_left"], 31)                              # rounds up, never "0 days" while valid
        self.assertTrue(licensing.Licensing(**self.kw).paid)              # persisted
        self.assertEqual(self.owner().issued()[0]["payment_ref"], "UTR123")

    def test_forged_or_tampered_keys_are_refused(self):
        key = self.owner().issue("x@y.z", days=31)["key"]
        body, sig = key[5:].split(".")
        import base64, json
        p = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        p["exp"] += 10 ** 8                                               # try to extend it
        forged = "BUCS-" + base64.urlsafe_b64encode(json.dumps(p).encode()).decode().rstrip("=") + "." + sig
        lic = licensing.Licensing(**self.kw)
        self.assertFalse(lic.activate(forged)["ok"])
        self.assertFalse(lic.activate("BUCS-nonsense.zzz")["ok"])
        other = licensing.Licensing(**{**self.kw, "public_key": licensing.PUBLIC_KEY})
        self.assertFalse(other.activate(key)["ok"])                        # signed by a different owner
        self.assertFalse(lic.paid)

    def test_install_binding_and_expiry(self):
        o = self.owner()
        lic = licensing.Licensing(**self.kw)
        wrong = o.issue("a@b.c", iid="00000-00000")["key"]
        self.assertIn("another install", lic.activate(wrong)["error"])
        right = o.issue("a@b.c", iid=lic.install_id)["key"]
        self.assertTrue(lic.activate(right)["ok"])
        with mock.patch.object(licensing.time, "time", return_value=time.time() + 40 * 86400):
            self.assertFalse(lic.paid)                                    # expired → locked again
            self.assertTrue(lic.snapshot()["expired"])

    def test_shipped_public_key_matches_the_owner_signing_key(self):
        key_file = Path(licensing.__file__).with_name("license_signing.key")
        if not key_file.exists():
            self.skipTest("signing key lives only on the owner's machine")
        import base64
        from cryptography.hazmat.primitives import serialization as ser
        sk = ser.load_pem_private_key(key_file.read_bytes(), password=None)
        pub = base64.urlsafe_b64encode(sk.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)).decode().rstrip("=")
        self.assertEqual(pub, licensing.PUBLIC_KEY)


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
