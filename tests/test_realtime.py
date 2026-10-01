"""Clap summon, conversation mode, network truth and open news — offline tests."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import netspeed  # noqa: E402
import open_news  # noqa: E402
from voice import ClapDetector, VoiceLoop  # noqa: E402

NOISE = 60.0


def _feed(det, blocks, t0=0.0, dt=0.05):
    """blocks: list of (peak, rms) per 50 ms block. Returns block indexes that fired."""
    return [i for i, (pk, r) in enumerate(blocks) if det.feed(pk, r, NOISE, t0 + i * dt)]


class Clap(unittest.TestCase):
    QUIET = (200, 60)
    CLAP = (20000, 4000)

    def test_double_clap_fires_once(self):
        d = ClapDetector(peak_floor=7000)
        blocks = [self.QUIET] * 4 + [self.CLAP, self.QUIET] + [self.QUIET] * 6 + [self.CLAP, self.QUIET] + [self.QUIET] * 4
        self.assertEqual(len(_feed(d, blocks)), 1)

    def test_single_clap_does_not_fire(self):
        d = ClapDetector(peak_floor=7000)
        self.assertEqual(_feed(d, [self.QUIET] * 3 + [self.CLAP, self.QUIET] + [self.QUIET] * 30), [])

    def test_sustained_loud_speech_is_not_a_clap(self):
        d = ClapDetector(peak_floor=7000)
        speech = [(15000, 3500)] * 12          # loud but no fast decay
        self.assertEqual(_feed(d, speech + [self.QUIET] * 4 + speech), [])

    def test_claps_too_far_apart_do_not_fire(self):
        d = ClapDetector(peak_floor=7000)
        blocks = [self.CLAP, self.QUIET] + [self.QUIET] * 30 + [self.CLAP, self.QUIET]   # ~1.6 s gap
        self.assertEqual(_feed(d, blocks), [])


try:
    import numpy  # noqa: F401  (optional voice dependency)
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False


@unittest.skipUnless(HAVE_NUMPY, "numpy (optional voice dependency) not installed")
class ConversationMode(unittest.TestCase):
    def _loop(self):
        hub = mock.Mock(broadcast=mock.AsyncMock())
        jarvis = mock.Mock(name="jarvis", speaker=None, _emit=mock.AsyncMock(),
                           handle=mock.AsyncMock(return_value="All systems nominal."))
        jarvis.name = "jarvis"
        v = VoiceLoop(hub=hub, brain=None, team={"jarvis": jarvis})
        return v, hub, jarvis

    def test_summon_opens_card_greets_and_listens_without_wake_word(self):
        v, hub, jarvis = self._loop()
        v.clap = ClapDetector()
        asyncio.run(v._summon())
        hub.broadcast.assert_any_await({"type": "voice", "event": "summon", "agent": "jarvis", "peak": 0})
        jarvis._emit.assert_awaited()                         # greeting shown in the card
        import numpy as np
        audio = np.zeros(800, dtype=np.int16)
        with mock.patch.object(VoiceLoop, "_transcribe", return_value="what is the cpu load"):
            asyncio.run(v._finish([audio] * 10, None, np, 8))
        jarvis.handle.assert_awaited()                        # routed with NO wake word
        self.assertIn("what is the cpu load", jarvis.handle.await_args.args[0])

    def test_without_followup_window_wake_word_is_required(self):
        v, hub, jarvis = self._loop()
        import numpy as np
        audio = np.zeros(800, dtype=np.int16)
        with mock.patch.object(VoiceLoop, "_transcribe", return_value="what is the cpu load"):
            asyncio.run(v._finish([audio] * 10, None, np, 8))
        jarvis.handle.assert_not_awaited()
        hub.broadcast.assert_any_await({"type": "voice", "event": "unrouted", "text": "what is the cpu load"})


class NetTruth(unittest.TestCase):
    def test_loss_is_measured_from_probe_window(self):
        m = netspeed.NetMonitor(window=10)
        self.assertIsNone(m.loss_pct)                         # no probes → unknown, not 0%
        m.probes.extend([True] * 9 + [False])
        self.assertEqual(m.loss_pct, 10.0)

    def test_probe_uses_tcp_rtt_median(self):
        m = netspeed.NetMonitor()
        with mock.patch.object(netspeed, "tcp_rtt_ms", mock.AsyncMock(side_effect=[10.0, 12.0, None, 30.0])):
            snap = asyncio.run(m.probe())
        self.assertEqual(snap["latency_ms"], 12.0)
        self.assertEqual(snap["loss_pct"], 25.0)

    def test_failed_speed_test_is_reported_not_faked(self):
        m = netspeed.NetMonitor()
        with mock.patch.object(m, "_run_speed_test", mock.AsyncMock(return_value={"ok": False, "error": "x"})):
            self.assertFalse(asyncio.run(m.speed_test())["ok"])


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Alpha &amp; beta</title><link>https://ex.com/a</link><pubDate>Wed, 30 Sep 2026 10:00:00 GMT</pubDate></item>
<item><title><![CDATA[<b>Gamma</b>]]></title><link>https://ex.com/g</link><pubDate>Wed, 30 Sep 2026 12:00:00 GMT</pubDate></item>
<item><title>No link</title></item></channel></rss>"""


class OpenNews(unittest.TestCase):
    def test_rss_parsing_cleans_titles_and_skips_linkless(self):
        arts = open_news.parse_rss(RSS, "Ex", "world")
        self.assertEqual([a["title"] for a in arts], ["Alpha & beta", "Gamma"])
        self.assertEqual(arts[0]["ts"], "2026-09-30T10:00:00Z")

    def test_merge_dedupes_sorts_and_interleaves(self):
        a = [{"title": "Same story", "source": "A", "ts": "2026-09-30T10:00:00Z"},
             {"title": "A2", "source": "A", "ts": "2026-09-30T11:00:00Z"},
             {"title": "A3", "source": "A", "ts": "2026-09-30T12:00:00Z"}]
        b = [{"title": "Same  story!", "source": "B", "ts": "2026-09-30T09:00:00Z"},
             {"title": "B1", "source": "B", "ts": "2026-09-30T08:00:00Z"}]
        out = open_news.merge([a, b])
        self.assertEqual(len([x for x in out if "same" in x["title"].lower()]), 1)
        self.assertEqual([x["source"] for x in out[:2]], ["A", "B"])        # interleaved

    def test_news_service_uses_open_feeds_without_key(self):
        from services import NewsService
        hub = mock.Mock(broadcast=mock.AsyncMock())
        svc = NewsService(api_key=None, hub=hub)
        fake = [{"title": "T", "source": "BBC World", "url": "https://x", "ts": None}]
        with mock.patch("open_news.fetch_open_news", mock.AsyncMock(return_value=(fake, []))):
            self.assertEqual(asyncio.run(svc.fetch_top()), fake)
        self.assertEqual(svc.source, "open")


if __name__ == "__main__":
    unittest.main()
