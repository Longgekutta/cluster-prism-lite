#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_notify.py: ntfy 事件上报验收神谕（红灯）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from notify import Notifier, build_event_message


class TestBuildMessage(unittest.TestCase):
    def test_fail_event(self):
        msg = build_event_message("pipeline_fail", "Phase5: LLM 调用失败")
        self.assertIn("pipeline_fail", msg)
        self.assertIn("Phase5", msg)

    def test_cache_hit_event(self):
        msg = build_event_message("cache_hit", "tier=I 14ms")
        self.assertIn("cache_hit", msg)

    def test_breaker_event(self):
        msg = build_event_message("breaker_open", "model=deepseek-chat")
        self.assertIn("breaker_open", msg)


class TestNotifier(unittest.TestCase):
    def test_disabled_without_topic(self):
        """未配置 ntfy → 优雅降级不崩溃"""
        n = Notifier(topic=None)
        self.assertFalse(n.enabled)
        self.assertFalse(n.send("cache_hit", "x"))  # 返回 False 不抛

    def test_send_records_log(self):
        """本地日志模式：记录事件账本（不联网）"""
        n = Notifier(topic="test-topic", endpoint="http://127.0.0.1:1",
                     local_log=True)
        ok = n.send("cache_hit", "tier=I")
        self.assertFalse(ok)  # 本地模式不上报网络
        log = n.recent_events()
        self.assertGreaterEqual(len(log), 1)
        self.assertEqual(log[-1]["event"], "cache_hit")
