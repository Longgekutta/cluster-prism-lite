#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_pipeline.py: 全管线集成测试（mock 模型端到端）"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine as engine_mod
from engine import ClusterEngine
from smart_router import ModelFleet, Tier
from cache_engine import FiveTierCacheEngine


def make_engine():
    fleet = ModelFleet()
    fleet.register("flash-m", {"tier": Tier.FLASH.value, "elo": 100})
    fleet.register("elite-m", {"tier": Tier.ELITE.value, "elo": 130})
    import tempfile
    return ClusterEngine(api_key="k",
                         cache=FiveTierCacheEngine(cache_dir=tempfile.mkdtemp()),
                         fleet=fleet)


class TestIntegration(unittest.TestCase):
    def test_end_to_end_two_requests_cache_second(self):
        """端到端：两次相同请求，第二次缓存直返"""
        eng = make_engine()
        with patch.object(engine_mod, "llm_call_via_urllib",
                          return_value="stable answer") as mock:
            r1 = eng.run("hello world")
            r2 = eng.run("hello world")
        self.assertTrue(r1.ok)
        self.assertFalse(r1.cache_hit)
        self.assertEqual(mock.call_count, 1)  # 第二次未调用 LLM
        self.assertTrue(r2.ok)
        self.assertTrue(r2.cache_hit)
        self.assertEqual(r2.answer, "stable answer")

    def test_route_dispatch_matches_intent_complexity(self):
        """路由分发随意图复杂度变化"""
        eng = make_engine()
        with patch.object(engine_mod, "llm_call_via_urllib", return_value="ok"):
            r_simple = eng.run("rename variable")
            r_complex = eng.run("架构重构并证明并发死锁消除")
        self.assertEqual(r_simple.route_tier, "FLASH")
        self.assertEqual(r_complex.route_tier, "ELITE")

    def test_cache_hit_rate_tracking(self):
        eng = make_engine()
        with patch.object(engine_mod, "llm_call_via_urllib", return_value="ans"):
            eng.run("unique task alpha")
            eng.run("unique task alpha")
        report = eng.cache.stats_report()
        self.assertGreater(report["hit_rate"], 0.0)
        self.assertGreaterEqual(report["stats"]["disk_hits"], 1)


if __name__ == "__main__":
    unittest.main()
