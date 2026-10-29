"""In-app updates + shutdown: signed manifests, safe package install, clean exit."""

import base64
import hashlib
import io
import json
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import updater  # noqa: E402
from harness import IsolatedServer  # noqa: E402


def keypair(d: Path):
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sk = Ed25519PrivateKey.generate()
    (d / "k.pem").write_bytes(sk.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.PKCS8, ser.NoEncryption()))
    pub = base64.urlsafe_b64encode(sk.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)).decode().rstrip("=")
    return d / "k.pem", pub


def package(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


class Manifest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key, self.pub = keypair(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_signed_manifest_verifies_and_tampering_is_caught(self):
        m = updater.sign_manifest({"kind": "bucs-release", "version": "7.6.0", "notes": ["x"],
                                   "url": "releases/bucs-7.6.0.zip", "sha256": "ab", "size": 1}, self.key)
        self.assertTrue(updater.verify_manifest(m, self.pub))
        for field, value in (("version", "9.9.9"), ("sha256", "cd"), ("notes", ["evil"]), ("url", "https://evil/x.zip")):
            self.assertFalse(updater.verify_manifest({**m, field: value}, self.pub), field)
        self.assertFalse(updater.verify_manifest(m, updater.licensing.PUBLIC_KEY))   # other owner's key
        self.assertFalse(updater.verify_manifest({**m, "kind": "x"}, self.pub))

    def test_version_ordering(self):
        self.assertGreater(updater.vtuple("7.10.0"), updater.vtuple("7.9.9"))
        self.assertEqual(updater.vtuple("v7.5"), (7, 5, 0))
        self.assertGreater(updater.vtuple("7.5.1"), updater.vtuple("7.5.0"))


class ApplyPackage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "bucs"
        self.root.mkdir()
        (self.root / "main.py").write_text("OLD", encoding="utf-8")
        (self.root / "VERSION").write_text("7.5.0", encoding="utf-8")
        (self.root / ".env").write_text("SECRET=keep", encoding="utf-8")
        (self.root / "license.json").write_text('{"key":"mine"}', encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_updates_code_keeps_user_data_and_backs_up(self):
        data = package({"main.py": "NEW", "VERSION": "7.6.0", "static/new.js": "x",
                        ".env": "SECRET=stolen", "license.json": "{}"})
        r = updater.apply_package(self.root, data, hashlib.sha256(data).hexdigest(), "7.5.0", "7.6.0")
        self.assertTrue(r["ok"])
        self.assertEqual((self.root / "main.py").read_text(), "NEW")
        self.assertEqual((self.root / ".env").read_text(), "SECRET=keep")           # user data never touched
        self.assertEqual((self.root / "license.json").read_text(), '{"key":"mine"}')
        self.assertEqual((Path(r["backup"]) / "main.py").read_text(), "OLD")         # rollback material
        self.assertIn("static/new.js", r["created"])
        rec = json.loads((self.root / ".update" / "verify.json").read_text())
        self.assertEqual((rec["from"], rec["to"]), ("7.5.0", "7.6.0"))

    def test_checksum_mismatch_and_path_traversal_are_refused(self):
        data = package({"main.py": "NEW"})
        self.assertFalse(updater.apply_package(self.root, data, "0" * 64, "7.5.0", "7.6.0")["ok"])
        self.assertEqual((self.root / "main.py").read_text(), "OLD")
        evil = package({"../escape.txt": "x"})
        with self.assertRaises(ValueError):
            updater.apply_package(self.root, evil, hashlib.sha256(evil).hexdigest(), "7.5.0", "7.6.0")
        self.assertFalse((self.root.parent / "escape.txt").exists())

    def test_git_checkout_is_never_modified(self):
        (self.root / ".git").mkdir()
        data = package({"main.py": "NEW"})
        r = updater.apply_package(self.root, data, hashlib.sha256(data).hexdigest(), "7.5.0", "7.6.0")
        self.assertFalse(r["ok"])
        self.assertEqual((self.root / "main.py").read_text(), "OLD")

    def test_mark_healthy_disarms_rollback(self):
        data = package({"main.py": "NEW"})
        updater.apply_package(self.root, data, hashlib.sha256(data).hexdigest(), "7.5.0", "7.6.0")
        u = updater.Updater(root=self.root, url="http://127.0.0.1:9/none.json")
        u.mark_healthy()
        self.assertFalse((self.root / ".update" / "verify.json").exists())
        self.assertEqual(u.state["just_updated"], {"from": "7.5.0", "to": "7.6.0"})


class Shutdown(unittest.TestCase):
    def test_dashboard_shutdown_stops_the_server_cleanly(self):
        srv = IsolatedServer().start()
        try:
            v = srv.get("/api/system/version")
            self.assertEqual(v["version"], (Path(updater.__file__).with_name("VERSION")).read_text().strip())
            self.assertFalse(v["managed_by_git"])                      # an install copy, not the repo
            self.assertTrue(srv.post("/api/system/shutdown", {})["ok"])
            self.assertEqual(srv.proc.wait(timeout=30), 0)
            self.assertIn("Application shutdown complete", srv.log())  # lifespan ran its cleanup
            self.assertTrue((srv.dir / ".update" / "shutdown.flag").exists())   # launcher closes its window
        finally:
            srv.stop()


if __name__ == "__main__":
    unittest.main()
