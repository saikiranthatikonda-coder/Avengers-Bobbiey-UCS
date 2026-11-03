"""Live grounding: real clock always; live sources only for time-sensitive questions; never hangs."""
import asyncio
import sys
import time
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import livefacts  # noqa: E402

RSS = """<rss><channel><item><title>ISRO launches NVS-03 - The Hindu</title><source>The Hindu</source>
<pubDate>Fri, 30 Oct 2026 05:43:00 GMT</pubDate></item></channel></rss>"""


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class Grounding(unittest.TestCase):
    def setUp(self):
        livefacts._cache.clear()

    def test_every_answer_gets_the_real_clock(self):
        b = asyncio.run(livefacts.ground({}, "tell me a joke", client=client(lambda r: httpx.Response(500))))
        self.assertIn(time.strftime("%Y"), b)
        self.assertNotIn("LIVE SOURCES", b)

    def test_time_sensitive_question_gets_cited_live_sources(self):
        def h(req):
            if "news.google.com" in req.url.host:
                return httpx.Response(200, text=RSS)
            if "coingecko" in req.url.host:
                return httpx.Response(200, json={"bitcoin": {"usd": 81000, "inr": 6800000, "usd_24h_change": 1.5,
                                                              "last_updated_at": int(time.time())}})
            return httpx.Response(404, json={})
        b = asyncio.run(livefacts.ground({}, "latest bitcoin price and ISRO news", client=client(h)))
        self.assertIn("ISRO launches NVS-03", b)
        self.assertIn("$81,000", b)
        self.assertIn("answer ONLY from these sources", b)

    def test_unreachable_sources_say_so_instead_of_guessing(self):
        b = asyncio.run(livefacts.ground({}, "who won the match today", client=client(lambda r: httpx.Response(503))))
        self.assertIn("can't verify them live", b)

    def test_slow_sources_never_exceed_the_budget(self):
        async def slow(req):
            await asyncio.sleep(10)
            return httpx.Response(200, text=RSS)
        livefacts.BUDGET_S, old = 0.3, livefacts.BUDGET_S
        try:
            t = time.time()
            asyncio.run(livefacts.ground({}, "latest news today", client=client(slow)))
            self.assertLess(time.time() - t, 2.0)
        finally:
            livefacts.BUDGET_S = old

    def test_detection(self):
        for q in ("what's the latest on the election", "bitcoin price", "who is the current CEO of Tesla", "who won yesterday"):
            self.assertTrue(livefacts.is_time_sensitive(q), q)
        for q in ("write a poem about rain", "explain recursion"):
            self.assertFalse(livefacts.is_time_sensitive(q), q)


if __name__ == "__main__":
    unittest.main()
