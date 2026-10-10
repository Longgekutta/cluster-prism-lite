#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_sa_integration.py: SENSE AUDIO 全链路集成回归（发现→接入→文本模型路由）"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters import AdapterRegistry
from senseaudio_adapter import SenseAudioAdapter, SENSEAUDIO_BASE
from model_discovery import discover, filter_text_models
from smart_router import ModelFleet, Tier, pareto_dispatch
from fleet_setup import refresh_elo_from_leaderboard
from contract_tests import run_contract_suite


def make_sa_engine():
    """SENSE AUDIO 引擎：注册表 + 真实平台适配器（mock 网络）"""
    registry = AdapterRegistry()
    registry.register("senseaudio-s2-flash", SenseAudioAdapter(api_key="k"))
    registry.register("senseaudio-s2", SenseAudioAdapter(api_key="k"))
    fleet = ModelFleet()
    fleet.register("senseaudio-s2-flash", {"tier": Tier.FLASH.value, "elo": 100.0})
    fleet.register("senseaudio-s2", {"tier": Tier.ELITE.value, "elo": 130.0})
    import tempfile
    from engine import ClusterEngine
    from cache_engine import FiveTierCacheEngine
    eng = ClusterEngine(api_key="k",
                        cache=FiveTierCacheEngine(cache_dir=tempfile.mkdtemp()),
                        fleet=fleet, registry=registry)
    return eng, registry, fleet


class TestSenseAudioIntegration(unittest.TestCase):
    def test_platform_constant(self):
        self.assertEqual(SENSEAUDIO_BASE, "https://api.senseaudio.cn")

    def test_sa_adapter_passes_contract(self):
        """SENSE AUDIO 适配器过契约套件（准入红线）"""
        a = SenseAudioAdapter(api_key="k", api_base="http://127.0.0.1:1")
        results = run_contract_suite(a)
        self.assertTrue(results["all_pass"], results)

    def test_discovery_separates_text_and_voice(self):
        """发现：文本模型与语音模型严格区分（mock 平台响应）"""
        fake_resp = {"object": "list", "data": [
            {"id": "senseaudio-s2", "mode": "llm"},
            {"id": "senseaudio-asr-1.5", "mode": "stt"},
            {"id": "sensenova-tts-2.0", "mode": "tts"},
            {"id": "deepseek-v4.1-flash", "mode": "llm"},
            {"id": "voice-isolation", "mode": "voice_isolation"},
        ]}
        with patch("model_discovery.fetch_models", return_value=fake_resp["data"]):
            result = discover(api_key="k", api_base="http://mock")
        self.assertEqual({m["id"] for m in result["text"]},
                         {"senseaudio-s2", "deepseek-v4.1-flash"})
        self.assertEqual(len(result["non_text"]), 3)

    def test_text_models_routed_by_pareto(self):
        """文本模型内帕累托路由（语音模型不入舰队不参与分发）"""
        fleet = ModelFleet()
        # 只有文本模型入舰队（语音模型被 discovery 排除在外）
        fleet.register("senseaudio-s2-flash", {"tier": Tier.FLASH.value, "elo": 100.0})
        fleet.register("senseaudio-s2", {"tier": Tier.ELITE.value, "elo": 130.0})
        d = pareto_dispatch(fleet, Tier.ELITE)
        self.assertEqual(d.model_id, "senseaudio-s2")
        d2 = pareto_dispatch(fleet, Tier.FLASH)
        self.assertEqual(d2.model_id, "senseaudio-s2-flash")

    def test_engine_routes_via_sa_port(self):
        """engine 经 SENSE AUDIO 端口全管线（mock 网络）"""
        eng, _, _ = make_sa_engine()
        with patch.object(SenseAudioAdapter, "_send", return_value={
                "choices": [{"message": {"content": "mocked answer"}}]}):
            r = eng.run("fix a typo")
            self.assertTrue(r.ok)
            self.assertEqual(r.route_tier, "FLASH")
            answer = eng._raw_llm("senseaudio-s2-flash",
                                  [{"role": "user", "content": "hi"}])
        self.assertEqual(answer, "mocked answer")

    def test_elo_refresh_integrated(self):
        """live 排行榜刷新 → 文本模型帕累托分发分值来自真实天梯"""
        fleet = ModelFleet()
        fleet.register("senseaudio-s2", {"tier": Tier.ELITE.value, "elo": 0.0})
        fleet.register("deepseek-reasoner", {"tier": Tier.ELITE.value, "elo": 0.0})
        refresh_elo_from_leaderboard(fleet)
        self.assertGreater(fleet.models["senseaudio-s2"]["elo"], 0)
        self.assertGreater(fleet.models["deepseek-reasoner"]["elo"], 0)


if __name__ == "__main__":
    unittest.main()
