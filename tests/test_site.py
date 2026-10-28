"""Public website guards: one source, honest claims."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DOCS = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")


class Website(unittest.TestCase):
    def test_site_mirror_matches_docs(self):
        site = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(site, DOCS, "copy docs/index.html -> site/index.html")

    def test_published_prices_match_billing_editions(self):
        from billing import EDITIONS
        shown = re.findall(r'pc-name">([^<]+)</div>\s*<div class="pc-price">([^<]+)', DOCS)
        self.assertEqual([(n.strip().lower(), p.strip().lower()) for n, p in shown],
                         [(e["name"].lower(), e["price"].lower()) for e in EDITIONS])

    def test_no_live_billing_claim_while_payments_are_demo(self):
        self.assertNotIn("live subscription editions", DOCS)

    def test_one_plan_only(self):
        self.assertNotIn("Every edition is live", DOCS)
        self.assertEqual(DOCS.count('class="pc-name"'), 1)

    def test_upi_payment_config_is_honest(self):
        import json
        cfg = json.loads((ROOT / "docs" / "payments.json").read_text(encoding="utf-8"))
        self.assertEqual((cfg["method"], cfg["currency"]), ("upi", "INR"))
        # privacy: no UPI ID / payee name as text anywhere the site serves, only QR images
        for k in ("upi_id", "payee_name", "vpa"):
            self.assertNotIn(k, cfg)
        self.assertNotRegex(DOCS + json.dumps(cfg), r"[\w.\-]+@(axl|ybl|ibl|okaxis|okhdfcbank|okicici|oksbi|paytm|upi)")
        self.assertNotIn("upi://", DOCS)
        for months in cfg["prices"]:
            self.assertTrue((ROOT / "docs" / cfg["qr"][months]).exists())
            self.assertEqual((ROOT / "docs" / cfg["qr"][months]).read_bytes(), (ROOT / "site" / cfg["qr"][months]).read_bytes())
        # exactly two plans, identical in payments.json, billing.py and on the page
        from billing import EDITIONS
        plans = {str(p["months"]): p["price_num"] for p in EDITIONS[0]["plans"]}
        self.assertEqual(plans, {"1": 149, "12": 1499})
        self.assertEqual({k: int(v) for k, v in cfg["prices"].items()}, plans)
        self.assertIn("₹149 · 1 month", DOCS); self.assertIn("₹1,499 · 12 months", DOCS)
        self.assertEqual(DOCS.count('<option value="'), 2)
        self.assertEqual((ROOT / "site" / "payments.json").read_text(encoding="utf-8"),
                         (ROOT / "docs" / "payments.json").read_text(encoding="utf-8"))
        self.assertNotIn("lemonsqueezy", DOCS.lower())

    def test_live_product_has_install_path_not_early_access(self):
        low = DOCS.lower()
        for gone in ("early access", "waitlist", "design partner"):
            self.assertNotIn(gone, low)
        self.assertIn('id="get"', DOCS)
        self.assertIn("/archive/refs/heads/main.zip", DOCS)        # the download customers install from
        self.assertIn("Start BUCS.cmd", DOCS)
        self.assertTrue((ROOT / "Start BUCS.cmd").exists())        # ...and it really ships in the repo


if __name__ == "__main__":
    unittest.main()
