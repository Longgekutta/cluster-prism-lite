#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_fleet_setup.py: 多模型舰队配置验收神谕（红灯）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters import AdapterRegistry, EchoAdapter
from fleet_setup import build_fleet_from_config, load_fleet_config


class TestFleetSetup(unittest.TestCase):
    def test_config_loads(self):
        cfg = load_fleet_config()
        self.assertIn("fleet", cfg)
        self.assertGreaterEqual(len(cfg["fleet"]), 3)  # 官方+逆向+中转三层

    def test_build_fleet(self):
        cfg = load_fleet_config()
        registry, fleet = build_fleet_from_config(cfg)
        self.assertGreaterEqual(len(fleet.models), 3)
        # 舰队每个 model_id 都有适配器
        for mid in fleet.models:
            self.assertIsNotNone(registry.resolve(mid))

    def test_tier_preserved(self):
        """分层的 tier 元信息保留（FLASH/ELITE + 源类型 official/bridge/relay）"""
        cfg = load_fleet_config()
        registry, fleet = build_fleet_from_config(cfg)
        sources = {fleet.models[m].get("source") for m in fleet.models}
        self.assertIn("official", sources)
        self.assertIn("bridge", sources)

    def test_registry_contract_pass_for_all(self):
        """所有注册适配器过契约套件（准入门槛）"""
        cfg = load_fleet_config()
        registry, _ = build_fleet_from_config(cfg)
        for mid in registry.models():
            results = run_contract_suite(registry.resolve(mid))
            self.assertTrue(results["all_pass"], f"{mid}: {results}")


from contract_tests import run_contract_suite  # noqa: E402


if __name__ == "__main__":
    unittest.main()
