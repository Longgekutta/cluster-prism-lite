#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_cache.py: 五阶缓存引擎验收神谕"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cache_engine import (
    FiveTierCacheEngine, canonical_prefix_hash, request_hash,
    longest_common_prefix_len, jaccard_similarity, strip_dynamic_segments,
    DETERMINISTIC_TEMP_MAX, PREFIX_ALIGNMENT_BASELINE,
)


class TestCanonicalHash(unittest.TestCase):
    def test_prefix_hash_deterministic(self):
        """相同输入 → 相同哈希（字节级一致性）"""
        h1 = canonical_prefix_hash("m1", "system prompt A")
        h2 = canonical_prefix_hash("m1", "system prompt A")
        self.assertEqual(h1, h2)

    def test_prefix_hash_differs_by_model(self):
        """不同 model_id → 不同哈希（路由元数据不污染前缀）"""
        h1 = canonical_prefix_hash("m1", "same text")
        h2 = canonical_prefix_hash("m2", "same text")
        self.assertNotEqual(h1, h2)

    def test_request_hash_differs_by_messages(self):
        h1 = request_hash("m", [{"role": "user", "content": "a"}], 0.0)
        h2 = request_hash("m", [{"role": "user", "content": "b"}], 0.0)
        self.assertNotEqual(h1, h2)


class TestLCPAndJaccard(unittest.TestCase):
    def test_lcp(self):
        self.assertEqual(longest_common_prefix_len("abcdef", "abcxyz"), 3)
        self.assertEqual(longest_common_prefix_len("", "abc"), 0)
        self.assertEqual(longest_common_prefix_len("same", "same"), 4)

    def test_jaccard_identical(self):
        self.assertEqual(jaccard_similarity("a b c", "a b c"), 1.0)

    def test_jaccard_disjoint(self):
        self.assertEqual(jaccard_similarity("a b", "c d"), 0.0)


class TestStripDynamic(unittest.TestCase):
    def test_strip_timestamp(self):
        text = "report at 2026-09-08 12:00 about current_time: xyz"
        stripped, count = strip_dynamic_segments(text)
        self.assertNotIn("2026-09-08", stripped)
        self.assertNotIn("current_time: xyz", stripped)
        self.assertGreaterEqual(count, 2)


class TestDiskCache(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.engine = FiveTierCacheEngine(cache_dir=self.tmp, ttl_seconds=3600)
        self.msgs = [{"role": "user", "content": "hello"}]

    def test_store_then_hit(self):
        """落盘后同请求 0ms 直返"""
        key = self.engine.disk_store("m1", self.msgs, 0.0, {"content": "cached answer"})
        hit = self.engine.disk_lookup("m1", self.msgs, 0.0)
        self.assertTrue(hit.hit)
        self.assertEqual(hit.tier, "I")
        self.assertEqual(hit.response, {"content": "cached answer"})
        self.assertEqual(hit.cache_key, key)
        self.assertLess(hit.elapsed_ms, 50.0)

    def test_miss_returns_not_hit(self):
        hit = self.engine.disk_lookup("m1", self.msgs, 0.0)
        self.assertFalse(hit.hit)

    def test_high_temp_bypasses(self):
        """temperature 超过确定性上限时绕过磁盘缓存"""
        self.engine.disk_store("m1", self.msgs, 0.0, {"content": "x"})
        hit = self.engine.disk_lookup("m1", self.msgs, 0.9)
        self.assertFalse(hit.hit)
        self.assertIn("temperature", hit.reason)

    def test_ttl_expiry(self):
        self.engine.ttl = -1  # 立即过期
        self.engine.disk_store("m1", self.msgs, 0.0, {"content": "x"})
        hit = self.engine.disk_lookup("m1", self.msgs, 0.0)
        self.assertFalse(hit.hit)
        self.assertIn("TTL", hit.reason)


class TestPrefixAlign(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.engine = FiveTierCacheEngine(cache_dir=tempfile.mkdtemp())

    def test_short_prefix_injected(self):
        aligned, injected = self.engine.align_prefix("short")
        self.assertTrue(injected)
        self.assertGreater(len(aligned), len("short"))

    def test_long_prefix_not_injected(self):
        long_text = "x" * (PREFIX_ALIGNMENT_BASELINE * 3)
        aligned, injected = self.engine.align_prefix(long_text)
        self.assertFalse(injected)
        self.assertEqual(aligned, long_text)


class TestSessionPrefix(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.engine = FiveTierCacheEngine(cache_dir=tempfile.mkdtemp())

    def test_session_continuity(self):
        lcp1 = self.engine.session_prefix_update("s1", "base prompt v1")
        self.assertEqual(lcp1, 0)
        lcp2 = self.engine.session_prefix_update("s1", "base prompt v2")
        self.assertEqual(lcp2, len("base prompt v"))


class TestSemanticDedup(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.engine = FiveTierCacheEngine(cache_dir=tempfile.mkdtemp())

    def test_near_identical_dedup(self):
        recent = ["explain the kernel architecture of this system"]
        hit = self.engine.semantic_dedup_check(
            "explain the kernel architecture of this system", recent)
        self.assertTrue(hit.hit)
        self.assertEqual(hit.tier, "IV")

    def test_different_not_dedup(self):
        recent = ["completely unrelated topic about gardening"]
        hit = self.engine.semantic_dedup_check("quantum deadlock proofs", recent)
        self.assertFalse(hit.hit)


class TestCanary(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.engine = FiveTierCacheEngine(cache_dir=tempfile.mkdtemp())

    def test_canary_pass(self):
        self.assertTrue(self.engine.canary_audit("prefix text", "prefix text extended"))

    def test_canary_fail(self):
        self.assertFalse(self.engine.canary_audit("original prefix", "TAMPERED prefix"))


class TestPreflight(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.engine = FiveTierCacheEngine(cache_dir=self.tmp)

    def test_preflight_full_penetration(self):
        """无缓存无相似 → 穿透"""
        msgs = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "unique quantum request"}]
        hit = self.engine.preflight("m1", msgs, 0.0, "s1")
        self.assertFalse(hit.hit)


if __name__ == "__main__":
    unittest.main()
