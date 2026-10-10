#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_fleet_platforms.py: 平台模型自动入舰队验收神谕（红灯）"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platform_adapter import PlatformRegistry
from fleet_setup import build_fleet_from_platforms, load_platform_config


class TestFleetFromPlatforms(unittest.TestCase):
    def test_load_platform_config(self):
        """config.json 含 platforms 声明"""
        cfg = load_platform_config()
        self.assertIn("platforms", cfg)
        self.assertGreaterEqual(len(cfg["platforms"]), 2)

    def test_build_fleet_from_platforms(self):
        """平台发现的文本模型自动入舰队（discovery→registry→fleet 解耦链路）"""
        fake_models = [
            {"id": "glm-5.3-flash", "mode": "llm"},
            {"id": "senseaudio-s2", "mode": "llm"},
            {"id": "senseaudio-asr-1.5", "mode": "stt"},  # 语音模型排除
        ]
        cfg = {"platforms": [
            {"name": "p1", "api_base": "http://mock/v1", "key": "k"},
        ]}
        with patch("model_discovery.fetch_models", return_value=fake_models):
            registry, fleet = build_fleet_from_platforms(cfg)
        # 只有文本模型入舰队
        self.assertEqual(set(fleet.models), {"glm-5.3-flash", "senseaudio-s2"})
        # 每个模型都有适配器（平台通用适配器）
        self.assertGreaterEqual(len(registry.models()), 2)  # 文本模型已注册

    def test_multi_platform_merge(self):
        """多平台模型合并入舰队，模型名去重"""
        fake_by_platform = {
            "p1": [{"id": "m-shared", "mode": "llm"}, {"id": "m1", "mode": "llm"}],
            "p2": [{"id": "m-shared", "mode": "llm"}, {"id": "m2", "mode": "llm"}],
        }
        cfg = {"platforms": [
            {"name": "p1", "api_base": "http://a/v1", "key": "k"},
            {"name": "p2", "api_base": "http://b/v1", "key": "k"},
        ]}
        with patch("model_discovery.fetch_models",
                   side_effect=lambda api_key, api_base, **kw:
                       fake_by_platform["p1"] if api_base.startswith("http://a")
                       else fake_by_platform["p2"]):
            registry, fleet = build_fleet_from_platforms(cfg)
        # m-shared 去重：只保留一次
        self.assertEqual(len([m for m in fleet.models if m == "m-shared"]), 1)
        self.assertEqual(set(fleet.models), {"m-shared", "m1", "m2"})

    def test_one_platform_fail_does_not_break(self):
        """单平台发现失败不阻塞其他平台（记录告警继续）"""
        def fake_fetch(api_key, api_base, **kw):
            if api_base.startswith("http://bad"):
                raise OSError("network down")
            return [{"id": "m1", "mode": "llm"}]
        cfg = {"platforms": [
            {"name": "bad", "api_base": "http://bad/v1", "key": "k"},
            {"name": "good", "api_base": "http://good/v1", "key": "k"},
        ]}
        with patch("model_discovery.fetch_models", side_effect=fake_fetch):
            registry, fleet = build_fleet_from_platforms(cfg)
        self.assertEqual(set(fleet.models), {"m1"})


if __name__ == "__main__":
    unittest.main()
