#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_router.py: 语义分级路由与帕累托分发验收神谕"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_router import (
    SmartRouter, ModelFleet, CircuitBreaker, BreakerState,
    classify_complexity, pareto_dispatch, Tier,
)


class TestClassify(unittest.TestCase):
    def test_deep_water_to_elite(self):
        tier, _ = classify_complexity("重构整个内核并发架构，解决死锁")
        self.assertEqual(tier, Tier.ELITE)

    def test_routine_to_flash(self):
        tier, _ = classify_complexity("给这个函数加注释并修个 typo")
        self.assertEqual(tier, Tier.FLASH)

    def test_default_flash(self):
        tier, reason = classify_complexity("hello world")
        self.assertEqual(tier, Tier.FLASH)
        self.assertIn("兜底", reason)


class TestBreaker(unittest.TestCase):
    def test_opens_after_threshold(self):
        b = CircuitBreaker(failure_threshold=3)
        b.record_failure()
        b.record_failure()
        self.assertTrue(b.allow_request())
        b.record_failure()
        self.assertEqual(b.state, BreakerState.OPEN)
        self.assertFalse(b.allow_request())

    def test_half_open_after_cooldown(self):
        b = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.0)
        b.record_failure()
        self.assertEqual(b.state, BreakerState.OPEN)
        # cooldown=0 → 第一次 allow 转为半开试探
        self.assertTrue(b.allow_request())
        self.assertEqual(b.state, BreakerState.HALF_OPEN)
        # 半开态下后续请求被拒（单次试探），直至 record_success 恢复
        self.assertFalse(b.allow_request())
        b.record_success()
        self.assertEqual(b.state, BreakerState.CLOSED)
        self.assertTrue(b.allow_request())

    def test_success_resets(self):
        b = CircuitBreaker(failure_threshold=2)
        b.record_failure()
        b.record_failure()
        b.state = BreakerState.HALF_OPEN
        b.record_success()
        self.assertEqual(b.state, BreakerState.CLOSED)
        self.assertEqual(b.failures, 0)


class TestFleet(unittest.TestCase):
    def test_healthy_fleet_excludes_open_breakers(self):
        fleet = ModelFleet()
        fleet.register("a", {"tier": "FLASH", "elo": 90})
        fleet.register("b", {"tier": "FLASH", "elo": 100})
        fleet.breakers["b"].record_failure()
        fleet.breakers["b"].record_failure()
        fleet.breakers["b"].record_failure()
        healthy = fleet.healthy_fleet(tier="FLASH")
        self.assertIn("a", healthy)
        self.assertNotIn("b", healthy)

    def test_pareto_picks_highest_elo(self):
        fleet = ModelFleet()
        fleet.register("fast", {"tier": "FLASH", "elo": 90})
        fleet.register("faster", {"tier": "FLASH", "elo": 110})
        decision = pareto_dispatch(fleet, Tier.FLASH)
        self.assertEqual(decision.model_id, "faster")

    def test_pareto_all_broken_raises(self):
        """舰队全熔断 → 明确报错，拒绝静默降级"""
        fleet = ModelFleet()
        fleet.register("a", {"tier": "FLASH", "elo": 90})
        fleet.breakers["a"].record_failure()
        fleet.breakers["a"].record_failure()
        fleet.breakers["a"].record_failure()
        with self.assertRaises(RuntimeError):
            pareto_dispatch(fleet, Tier.FLASH)


class TestSmartRouter(unittest.TestCase):
    def test_route_elite(self):
        router = SmartRouter()
        router.fleet.register("elite-m", {"tier": "ELITE", "elo": 130})
        d = router.route("重构分布式一致性协议内核")
        self.assertEqual(d.tier, "ELITE")
        self.assertEqual(d.model_id, "elite-m")

    def test_route_flash(self):
        router = SmartRouter()
        router.fleet.register("flash-m", {"tier": "FLASH", "elo": 100})
        d = router.route("fix a typo")
        self.assertEqual(d.tier, "FLASH")
        self.assertEqual(d.model_id, "flash-m")

    def test_report_failure_then_success(self):
        router = SmartRouter()
        router.fleet.register("m", {"tier": "FLASH", "elo": 100})
        router.report_failure("m", 429)
        router.report_failure("m", 429)
        router.report_failure("m", 429)
        self.assertEqual(router.fleet.breakers["m"].state, BreakerState.OPEN)
        router.report_success("m")
        self.assertEqual(router.fleet.breakers["m"].state, BreakerState.CLOSED)


if __name__ == "__main__":
    unittest.main()
