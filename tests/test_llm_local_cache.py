"""A stopped Ollama must not make /api/models slow (that left the LLM CORE badge stale)."""
import asyncio
import socket
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm_local import LocalLLM  # noqa: E402


def _dead_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


class ListModelsCache(unittest.TestCase):
    def test_unreachable_endpoint_is_cached_and_fast(self):
        llm = LocalLLM(base_url=f"http://127.0.0.1:{_dead_port()}/v1", model="x", isolated=True)
        t = time.perf_counter()
        self.assertEqual(asyncio.run(llm.list_models()), [])
        first = time.perf_counter() - t
        t = time.perf_counter()
        self.assertEqual(asyncio.run(llm.list_models()), [])
        second = time.perf_counter() - t
        self.assertLess(first, 8.0)
        self.assertLess(second, 0.05)          # served from the down-cache, no re-dial

    def test_cache_is_per_endpoint_and_invalidatable(self):
        llm = LocalLLM(base_url=f"http://127.0.0.1:{_dead_port()}/v1", model="x", isolated=True)
        asyncio.run(llm.list_models())
        llm._list_cache = (llm.base_url, time.monotonic(), [{"name": "cached"}])
        self.assertEqual(asyncio.run(llm.list_models()), [{"name": "cached"}])
        llm.base_url = f"http://127.0.0.1:{_dead_port()}/v1"      # endpoint changed → not reused
        self.assertEqual(asyncio.run(llm.list_models()), [])
        llm._list_cache = (llm.base_url, time.monotonic(), [{"name": "cached"}])
        llm.invalidate_models()
        self.assertEqual(asyncio.run(llm.list_models()), [])


if __name__ == "__main__":
    unittest.main()
