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

    def test_waitlist_form_is_netlify_handled(self):
        self.assertIn('data-netlify="true"', DOCS)


if __name__ == "__main__":
    unittest.main()
