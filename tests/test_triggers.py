#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_triggers.py: 触发层验收神谕"""
import asyncio
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from triggers import (
    TriggerEvent, TriggerBus, CronTrigger,
    parse_cron_expr, cron_matches,
)


class TestCronParse(unittest.TestCase):
    def test_parse_step(self):
        spec = parse_cron_expr("*/5 * * * *")
        self.assertEqual(spec["minute"], {"step": 5})
        self.assertIsNone(spec["hour"])

    def test_parse_fixed(self):
        spec = parse_cron_expr("0 9 * * *")
        self.assertEqual(spec["minute"], 0)
        self.assertEqual(spec["hour"], 9)

    def test_parse_alias(self):
        spec = parse_cron_expr("@daily")
        self.assertEqual(spec["hour"], 0)
        self.assertEqual(spec["minute"], 0)

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            parse_cron_expr("bad expr")
        with self.assertRaises(ValueError):
            parse_cron_expr("*/0 * * * *")


class TestCronMatch(unittest.TestCase):
    def test_match_fixed(self):
        spec = parse_cron_expr("0 9 * * *")
        t = time.struct_time((2026, 1, 1, 9, 0, 0, 3, 1, 0))
        self.assertTrue(cron_matches(spec, t))
        t2 = time.struct_time((2026, 1, 1, 10, 0, 0, 3, 1, 0))
        self.assertFalse(cron_matches(spec, t2))

    def test_match_step(self):
        spec = parse_cron_expr("*/10 * * * *")
        t = time.struct_time((2026, 1, 1, 9, 20, 0, 3, 1, 0))
        self.assertTrue(cron_matches(spec, t))
        t2 = time.struct_time((2026, 1, 1, 9, 21, 0, 3, 1, 0))
        self.assertFalse(cron_matches(spec, t2))


class TestTriggerBus(unittest.TestCase):
    def test_emit_consume(self):
        async def scenario():
            bus = TriggerBus()
            await bus.emit(TriggerEvent(source="manual", session_id="s1",
                                        payload={"text": "hi"}))
            ev = await bus.consume()
            return ev
        ev = asyncio.run(scenario())
        self.assertEqual(ev.source, "manual")
        self.assertEqual(ev.payload["text"], "hi")


class TestCronTrigger(unittest.TestCase):
    def test_start_stop(self):
        async def scenario():
            bus = TriggerBus()
            trig = CronTrigger(bus, "*/1 * * * *", {"task": "tick"})
            trig.start()
            await asyncio.sleep(1.2)  # 跨过整分界，触发一次
            await trig.stop()
            return bus.queue.qsize() >= 1
        self.assertTrue(asyncio.run(scenario()))
