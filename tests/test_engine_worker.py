#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_engine_worker.py: engine 工兵模式闭环测试（mock 工兵，零网络零 key）
========================================================================
验证：七阶管线 Phase5 可换 ACP 工兵委派；Phase1~4/6~7 语义不变；
缓存拦截在最前；工兵未注册/委派失败不炸管线。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import ClusterEngine  # noqa: E402
from cache_engine import FiveTierCacheEngine  # noqa: E402
from smart_router import ModelFleet, SmartRouter  # noqa: E402


MOCK_WORKER = r"""
import json, sys
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    rid, method = msg.get("id"), msg.get("method")
    if rid is None:
        continue
    if method == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": rid,
                          "result": {"protocolVersion": 1}}), flush=True)
    elif method == "session/new":
        print(json.dumps({"jsonrpc": "2.0", "id": rid,
                          "result": {"sessionId": "sess-engine-1"}}), flush=True)
    elif method == "session/prompt":
        print(json.dumps({"jsonrpc": "2.0", "id": rid,
                          "result": {"sessionId": (msg.get("params") or {}).get("sessionId", ""),
                                     "stopReason": "end_turn"}}), flush=True)
        print(json.dumps({"jsonrpc": "2.0", "method": "session/update",
                          "params": {"update": {"sessionUpdate": "agent_message_chunk",
                                                "content": {"type": "text", "text": "worker done"}}}}),
              flush=True)
"""


class TestEngineWorkerMode(unittest.TestCase):
    """工兵模式闭环：管线语义保持 + 缓存直返 + 失败不炸"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mock_path = os.path.join(self.tmp, "mock_worker.py")
        with open(self.mock_path, "w", encoding="utf-8") as f:
            f.write(MOCK_WORKER)
        self.workers_cfg = {
            "workers": [{"name": "mock", "command": [sys.executable, self.mock_path],
                         "cwd": self.tmp}],
        }
        self.engine = ClusterEngine(api_key="mock-key")

    def test_worker_mode_pipeline_closure(self):
        """Phase1~4 + Phase5(工兵) + Phase7 全闭环"""
        engine = ClusterEngine(api_key="mock-key")
        # 注入 workers 配置路径（mock 侧用临时 config）
        self._patch_config(self.workers_cfg)
        result = engine.run_with_worker("修一个 bug", worker_name="mock",
                                        cache_lookup=False)
        self.assertTrue(result.ok)
        self.assertEqual(result.answer, "worker done")
        self.assertEqual(result.route_tier, "ELITE")
        phases = {r.phase: r.ok for r in result.reports}
        self.assertTrue(phases.get("Phase1"))
        self.assertTrue(phases.get("Phase5"))

    def test_worker_cache_roundtrip(self):
        """工兵结果落盘缓存，第二次 0ms 直返"""
        self._patch_config(self.workers_cfg)
        r1 = self.engine.run_with_worker("缓存任务", worker_name="mock",
                                         cache_lookup=True)
        self.assertTrue(r1.ok)
        r2 = self.engine.run_with_worker("缓存任务", worker_name="mock",
                                         cache_lookup=True)
        self.assertTrue(r2.ok)
        self.assertTrue(r2.cache_hit)
        self.assertEqual(r2.answer, "worker done")

    def test_unregistered_worker_no_crash(self):
        """工兵未注册：Phase5 报 False，管线不炸"""
        self._patch_config({"workers": []})
        result = self.engine.run_with_worker("x", worker_name="nope",
                                             cache_lookup=False)
        self.assertFalse(result.ok)
        phase5 = [r for r in result.reports if r.phase == "Phase5"]
        self.assertTrue(phase5 and not phase5[0].ok)

    def test_empty_intent(self):
        result = self.engine.run_with_worker("   ", worker_name="mock")
        self.assertFalse(result.ok)

    # ---- helpers ----

    def _patch_config(self, cfg: dict):
        """让 engine._load_worker_config 读到临时 workers 声明"""
        import engine as engine_mod
        original = engine_mod.ClusterEngine.__dict__["_load_worker_config"]
        engine_mod.ClusterEngine._load_worker_config = staticmethod(
            lambda: dict(cfg))
        self.addCleanup(setattr, engine_mod.ClusterEngine,
                        "_load_worker_config", original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
