#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_platform_integration.py: 配置驱动平台集成回归（SENSE AUDIO 实测链路）"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platform_adapter import PlatformRegistry, GenericOpenAIAdapter
from fleet_setup import build_fleet_from_platforms
from contract_tests import run_contract_suite
from smart_router import ModelFleet, Tier, pareto_dispatch


class TestPlatformIntegration(unittest.TestCase):
    def test_config_driven_registration(self):
        """新平台加配置即接入（零代码改动验证）"""
        cfg = {"platforms": [
            {"name": "brand-new-ai", "api_base": "https://api.brandnew.io/v1",
             "key": "sk-test"},
        ]}
        reg = PlatformRegistry.from_config(cfg)
        a = reg.adapter_for("brand-new-ai")
        self.assertIsInstance(a, GenericOpenAIAdapter)
        self.assertEqual(a.api_base, "https://api.brandnew.io/v1")

    def test_senseaudio_config_driven(self):
        """SENSE AUDIO 走配置驱动（非硬编码类）"""
        cfg = {"platforms": [
            {"name": "senseaudio", "api_base": "https://api.senseaudio.cn",
             "key": "sk-test"},
        ]}
        reg = PlatformRegistry.from_config(cfg)
        a = reg.adapter_for("senseaudio")
        # 请求体与专用 SenseAudioAdapter 行为一致（透传/幂等键）
        from model_port import CanonicalRequest
        req = CanonicalRequest(model_id="glm-5.3-flash",
                               messages_bytes=b'[{"role":"user","content":"hi"}]',
                               idempotency_key="k1", trace_id="t1")
        body = a.build_request_body(req)
        self.assertIn(b'"hi"', body)
        self.assertIn(b'"k1"', body)
        # 契约红线保持
        results = run_contract_suite(a)
        self.assertTrue(results["all_pass"], results)

    def test_discovery_to_fleet_chain(self):
        """解耦链路：discovery 发现 → generic adapter 接入 → fleet 装配 → 帕累托"""
        fake_models = [
            {"id": "glm-5.3-flash", "mode": "llm"},
            {"id": "senseaudio-s2", "mode": "llm"},
            {"id": "asr-model", "mode": "stt"},
        ]
        cfg = {"platforms": [
            {"name": "senseaudio", "api_base": "http://mock/v1", "key": "k"},
        ]}
        with patch("model_discovery.fetch_models", return_value=fake_models):
            registry, fleet = build_fleet_from_platforms(cfg)
        # 文本模型入舰队，语音排除
        self.assertEqual(set(fleet.models), {"glm-5.3-flash", "senseaudio-s2"})
        # 帕累托路由在文本模型内
        d = pareto_dispatch(fleet, Tier.ELITE)
        self.assertIn(d.model_id, {"glm-5.3-flash", "senseaudio-s2"})

    def test_engine_via_generic_adapter(self):
        """engine 经通用适配器全管线（mock 网络）"""
        fake_models = [
            {"id": "flash-m", "mode": "llm"},
            {"id": "elite-m", "mode": "llm"},
        ]
        cfg = {"platforms": [
            {"name": "mock", "api_base": "http://mock/v1", "key": "k"},
        ]}
        with patch("model_discovery.fetch_models", return_value=fake_models):
            registry, fleet = build_fleet_from_platforms(cfg)
        import tempfile
        from engine import ClusterEngine
        from cache_engine import FiveTierCacheEngine
        eng = ClusterEngine(api_key="k",
                            cache=FiveTierCacheEngine(cache_dir=tempfile.mkdtemp()),
                            fleet=fleet, registry=registry)
        with patch.object(GenericOpenAIAdapter, "_send", return_value={
                "choices": [{"message": {"content": "mocked"}}]}):
            r = eng.run("fix a typo")
        self.assertTrue(r.ok)
        self.assertEqual(r.answer, "mocked")


if __name__ == "__main__":
    unittest.main()
