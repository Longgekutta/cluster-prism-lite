#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_hex_integration.py: 六边形架构全管线集成回归"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine as engine_mod
from engine import ClusterEngine
from smart_router import ModelFleet, Tier
from cache_engine import FiveTierCacheEngine
from adapters import AdapterRegistry, EchoAdapter, UrllibAdapter
from contract_tests import run_contract_suite
from fleet_setup import load_fleet_config, build_fleet_from_config


def make_engine_with_registry():
    """引擎 + 端口注册表（EchoAdapter 替身，零网络）"""
    cfg = {"fleet": [
        {"model_id": "flash-m", "source": "test", "tier": "FLASH", "elo": 100.0,
         "adapter": {"kind": "echo"}},
        {"model_id": "elite-m", "source": "test", "tier": "ELITE", "elo": 130.0,
         "adapter": {"kind": "echo"}},
    ]}
    registry, fleet = build_fleet_from_config(cfg)
    import tempfile
    return ClusterEngine(api_key="k",
                         cache=FiveTierCacheEngine(cache_dir=tempfile.mkdtemp()),
                         fleet=fleet, registry=registry), registry


class TestHexIntegration(unittest.TestCase):
    def test_all_registered_adapters_pass_contract(self):
        """全部注册适配器过契约套件（准入红线）"""
        cfg = load_fleet_config()
        registry, _ = build_fleet_from_config(cfg)
        for mid in registry.models():
            results = run_contract_suite(registry.resolve(mid))
            self.assertTrue(results["all_pass"], f"{mid}: {results}")

    def test_engine_routes_via_port(self):
        """engine._raw_llm 走端口适配器（EchoAdapter 透传验证）"""
        eng, registry = make_engine_with_registry()
        messages = [{"role": "user", "content": "port test"}]
        answer = eng._raw_llm("flash-m", messages)
        self.assertEqual(answer, json.dumps(messages, ensure_ascii=False))

    def test_engine_full_pipeline_with_registry(self):
        """七阶管线经端口全闭环（EchoAdapter 替身）"""
        eng, _ = make_engine_with_registry()
        r = eng.run("fix a typo")
        self.assertTrue(r.ok)
        self.assertEqual(r.route_tier, "FLASH")
        # EchoAdapter 透传 → answer 是 messages JSON
        self.assertIn("fix a typo", r.answer)

    def test_prefix_cache_invariant_with_registry(self):
        """INV: 前缀字节级一致——缓存与端口共存时第二次命中缓存"""
        eng, _ = make_engine_with_registry()
        r1 = eng.run("same unique request alpha")
        r2 = eng.run("same unique request alpha")
        self.assertTrue(r1.ok)
        self.assertTrue(r2.ok)
        self.assertTrue(r2.cache_hit)  # 第二次缓存直返
        self.assertEqual(r1.answer, r2.answer)  # 字节级一致

    def test_idempotent_jury_with_port(self):
        """INV: jury 幂等——评审经端口，相同输入复用缓存"""
        eng, _ = make_engine_with_registry()
        verdict1 = eng.review("intent", "answer",
                              tier1_models=["flash-m"], tier2_models=["elite-m"])
        # 幂等：同输入再评 → 同裁决（缓存键命中）
        verdict2 = eng.review("intent", "answer",
                              tier1_models=["flash-m"], tier2_models=["elite-m"])
        self.assertEqual(verdict1.verdict, verdict2.verdict)

    def test_registry_unknown_model_rejected(self):
        """未知 model_id → CanonicalError（拒绝静默降级）"""
        from model_port import CanonicalError
        eng, _ = make_engine_with_registry()
        with self.assertRaises(CanonicalError):
            eng._raw_llm("ghost-model", [{"role": "user", "content": "x"}])


if __name__ == "__main__":
    unittest.main()
