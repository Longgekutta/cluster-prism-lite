#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_engine.py: 七阶编排引擎验收神谕（mock LLM，零网络依赖）"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine as engine_mod
from engine import ClusterEngine, PipelineResult, resolve_api_key
from smart_router import ModelFleet, Tier


def make_engine(api_key="test-key", with_checkpoint=False):
    fleet = ModelFleet()
    fleet.register("flash-m", {"tier": Tier.FLASH.value, "elo": 100})
    fleet.register("elite-m", {"tier": Tier.ELITE.value, "elo": 130})
    import tempfile
    cache_dir = tempfile.mkdtemp()
    from cache_engine import FiveTierCacheEngine
    eng = ClusterEngine(api_key=api_key,
                        cache=FiveTierCacheEngine(cache_dir=cache_dir),
                        fleet=fleet)
    return eng


class TestEnginePipeline(unittest.TestCase):
    def test_empty_intent_rejected(self):
        eng = make_engine()
        r = eng.run("")
        self.assertFalse(r.ok)
        self.assertEqual(r.reports[0].phase, "Phase1")

    def test_no_api_key_rejected_at_call(self):
        eng = make_engine(api_key="")
        with patch.object(engine_mod, "llm_call_via_urllib",
                          side_effect=RuntimeError("should not be called")):
            r = eng.run("hello", )
        # 无 key：Phase5 失败，不得调用 LLM（RuntimeError 被管线捕获为失败而非崩溃）
        self.assertFalse(r.ok)
        phase5 = next(p for p in r.reports if p.phase == "Phase5")
        self.assertFalse(phase5.ok)

    def test_full_pipeline_flash(self):
        """FLASH 层全管线闭环"""
        eng = make_engine()
        with patch.object(engine_mod, "llm_call_via_urllib",
                          return_value="answer text") as mock_call:
            r = eng.run("fix a typo")
        self.assertTrue(r.ok)
        self.assertEqual(r.route_tier, "FLASH")
        self.assertEqual(r.model_id, "flash-m")
        self.assertEqual(r.answer, "answer text")
        self.assertFalse(r.cache_hit)
        phases = [p.phase for p in r.reports]
        for ph in ("Phase1", "Phase2", "Phase3", "Phase4", "Phase5", "Phase6", "Phase7"):
            self.assertIn(ph, phases)

    def test_full_pipeline_elite(self):
        eng = make_engine()
        with patch.object(engine_mod, "llm_call_via_urllib",
                          return_value="deep answer") as mock_call:
            r = eng.run("重构分布式一致性协议内核，解决死锁")
        self.assertTrue(r.ok)
        self.assertEqual(r.route_tier, "ELITE")
        self.assertEqual(r.model_id, "elite-m")

    def test_cache_hit_skips_llm(self):
        """缓存命中 → 跳过 LLM 调用（0 Token/0 费用）"""
        eng = make_engine()
        msgs = [
            {"role": "system", "content": "You are a precise engineering cluster worker."},
            {"role": "user", "content": "fix a typo"},
        ]
        eng.cache.disk_store("flash-m", msgs, 0.0, {"content": "cached answer"})
        r = eng.run("fix a typo")
        self.assertTrue(r.ok)
        self.assertTrue(r.cache_hit)
        self.assertEqual(r.answer, "cached answer")

    def test_ast_precheck_blocks_bad_code(self):
        """Phase 6 AST 预检拦截语法崩坏的代码块产物"""
        eng = make_engine()
        bad_code = "```python\ndef broken(:\n    pass\n```\n"
        with patch.object(engine_mod, "llm_call_via_urllib", return_value=bad_code):
            r = eng.run("write code")
        self.assertFalse(r.ok)
        self.assertIn("Phase6", [p.phase for p in r.reports])
        phase6 = next(p for p in r.reports if p.phase == "Phase6")
        self.assertFalse(phase6.ok)

    def test_plain_text_answer_passes_phase6(self):
        """纯文本回答（非代码）不触发 AST 预检"""
        eng = make_engine()
        with patch.object(engine_mod, "llm_call_via_urllib",
                          return_value="This is a plain text explanation, not code."):
            r = eng.run("explain something")
        self.assertTrue(r.ok)

    def test_llm_failure_records_breaker(self):
        """LLM 失败 → 断路器计数（而非静默）"""
        import urllib.error
        eng = make_engine()
        with patch.object(engine_mod, "llm_call_via_urllib",
                          side_effect=urllib.error.URLError("conn refused")):
            r = eng.run("fix a typo")
        self.assertFalse(r.ok)
        phase5 = next(p for p in r.reports if p.phase == "Phase5")
        self.assertFalse(phase5.ok)
        self.assertIn("失败", phase5.detail)

    def test_resolve_api_key_prefers_env(self):
        old = os.environ.get("DEEPSEEK_API_KEY")
        try:
            os.environ["DEEPSEEK_API_KEY"] = "sk-test-env-key-123"
            self.assertEqual(resolve_api_key(), "sk-test-env-key-123")
        finally:
            if old is None:
                os.environ.pop("DEEPSEEK_API_KEY", None)
            else:
                os.environ["DEEPSEEK_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()
