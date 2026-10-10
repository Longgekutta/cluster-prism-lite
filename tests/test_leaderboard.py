#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_leaderboard.py: 多源天梯排行榜验收神谕（红灯：模块未实现）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from leaderboard import LeaderboardFetcher, StaticFallback


class TestStaticFallback(unittest.TestCase):
    def test_static_snapshot_available(self):
        """被墙降级：静态快照兜底可用"""
        fb = StaticFallback()
        scores = fb.scores()
        self.assertGreaterEqual(len(scores), 10)

    def test_static_has_deepseek(self):
        fb = StaticFallback()
        scores = fb.scores()
        self.assertIn("deepseek-reasoner", scores)


class TestLeaderboardFetcher(unittest.TestCase):
    def test_fetch_with_ttl_cache(self):
        """多源获取 + TTL 缓存：第二次调用命中缓存不发请求"""
        fetcher = LeaderboardFetcher(ttl_seconds=3600)
        scores1 = fetcher.scores()
        self.assertGreater(len(scores1), 0)
        source1 = fetcher.last_source
        # 第二次：TTL 内直接回缓存
        scores2 = fetcher.scores()
        self.assertEqual(scores1, scores2)
        self.assertEqual(fetcher.last_source, source1)

    def test_source_priority(self):
        """源优先级：openrouter(可用) > models.dev(元数据) > 静态快照"""
        fetcher = LeaderboardFetcher(ttl_seconds=0)
        scores = fetcher.scores()
        # 在当前网络环境：lmarena/hf 被墙 → 应落到 openrouter 或静态
        self.assertIn(fetcher.last_source, ("openrouter", "models_dev", "static"))

    def test_scores_are_positive_numbers(self):
        fetcher = LeaderboardFetcher()
        scores = fetcher.scores()
        for mid, sc in list(scores.items())[:10]:
            self.assertIsInstance(sc, (int, float))
            self.assertGreater(sc, 0)

    def test_wall_degradation(self):
        """被墙降级：所有 live 源失败 → 静态快照兜底，不崩溃"""
        fetcher = LeaderboardFetcher(ttl_seconds=0, live_sources=[])
        scores = fetcher.scores()
        self.assertGreater(len(scores), 0)
        self.assertEqual(fetcher.last_source, "static")


if __name__ == "__main__":
    unittest.main()
