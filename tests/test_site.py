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

    def test_editions_are_not_claimed_live_without_a_licence(self):
        self.assertNotIn("Every edition is live", DOCS)
        self.assertIn("LICENSE", DOCS)

    def test_checkout_config_is_honest(self):
        import json
        cfg = json.loads((ROOT / "docs" / "payments.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["provider"], "lemonsqueezy")
        for plan, url in cfg["checkout"].items():
            self.assertIn(plan, ("pro", "team"))
            # empty = waitlist; otherwise only a real LemonSqueezy checkout link
            self.assertRegex(url, r"^$|^https://[a-z0-9-]+\.lemonsqueezy\.com/(buy|checkout)/")
        self.assertEqual((ROOT / "site" / "payments.json").read_text(encoding="utf-8"),
                         (ROOT / "docs" / "payments.json").read_text(encoding="utf-8"))
        for plan in ("pro", "team"):
            self.assertIn(f'data-plan="{plan}"', DOCS)

    def test_waitlist_form_is_netlify_handled(self):
        self.assertIn('data-netlify="true"', DOCS)


if __name__ == "__main__":
    unittest.main()
