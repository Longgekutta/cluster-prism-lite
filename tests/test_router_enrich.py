#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_router_enrich.py: smart_router 接入 live 排行榜验收神谕（红灯）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fleet_setup import refresh_elo_from_leaderboard
from smart_router import ModelFleet, Tier


class TestRefreshElo(unittest.TestCase):
    def test_refresh_updates_elo(self):
        """live 排行榜刷新舰队 ELO 分值"""
        fleet = ModelFleet()
        fleet.register("claude-opus-5.5", {"tier": Tier.ELITE.value, "elo": 100.0})
        fleet.register("unknown-model-xyz", {"tier": Tier.FLASH.value, "elo": 100.0})
        refresh_elo_from_leaderboard(fleet)
        # 收录模型：ELO 已刷新（不再是 100.0 占位值）
        self.assertNotEqual(fleet.models["claude-opus-5.5"]["elo"], 100.0)
        # 未收录模型：保持 default（不猜测高分）
        self.assertEqual(fleet.models["unknown-model-xyz"]["elo"], 1250.0)

    def test_refresh_preserves_tier(self):
        """刷新只动 elo，tier/source 元信息保留"""
        fleet = ModelFleet()
        fleet.register("deepseek-reasoner", {"tier": Tier.ELITE.value, "elo": 100.0,
                                             "source": "official"})
        refresh_elo_from_leaderboard(fleet)
        self.assertEqual(fleet.models["deepseek-reasoner"]["tier"], "ELITE")
        self.assertEqual(fleet.models["deepseek-reasoner"]["source"], "official")

    def test_refresh_with_leaderboard_fetcher(self):
        """显式传入 fetcher（测试可控）"""
        from leaderboard import LeaderboardFetcher
        fetcher = LeaderboardFetcher(ttl_seconds=3600)
        fleet = ModelFleet()
        fleet.register("deepseek-reasoner", {"tier": Tier.ELITE.value, "elo": 100.0})
        refresh_elo_from_leaderboard(fleet, fetcher=fetcher)
        self.assertNotEqual(fleet.models["deepseek-reasoner"]["elo"], 100.0)


if __name__ == "__main__":
    unittest.main()
