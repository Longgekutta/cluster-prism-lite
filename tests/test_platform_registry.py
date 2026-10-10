#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_platform_registry.py: 配置驱动平台注册验收神谕（红灯）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platform_adapter import PlatformRegistry, GenericOpenAIAdapter


class TestPlatformRegistry(unittest.TestCase):
    def test_register_from_config(self):
        """从 config platforms 声明装配平台（零硬编码代码）"""
        cfg = {"platforms": [
            {"name": "senseaudio", "api_base": "https://api.senseaudio.cn",
             "key_env": "SENSEAUDIO_API_KEY"},
            {"name": "deepseek", "api_base": "https://api.deepseek.com/v1",
             "key_env": "DEEPSEEK_API_KEY"},
        ]}
        reg = PlatformRegistry.from_config(cfg)
        a1 = reg.adapter_for("senseaudio")
        a2 = reg.adapter_for("deepseek")
        self.assertIsInstance(a1, GenericOpenAIAdapter)
        self.assertEqual(a1.api_base, "https://api.senseaudio.cn/v1")
        self.assertEqual(a2.api_base, "https://api.deepseek.com/v1")

    def test_add_platform_via_config_only(self):
        """新平台：加一条配置即接入（不需要写任何代码）"""
        cfg = {"platforms": [
            {"name": "new-platform-x", "api_base": "https://api.newx.com/v1",
             "key_env": "NEWX_KEY"},
        ]}
        reg = PlatformRegistry.from_config(cfg)
        a = reg.adapter_for("new-platform-x")
        self.assertEqual(a.api_base, "https://api.newx.com/v1")

    def test_unknown_platform_raises(self):
        cfg = {"platforms": []}
        reg = PlatformRegistry.from_config(cfg)
        with self.assertRaises(KeyError):
            reg.adapter_for("nonexistent")

    def test_key_env_resolution(self):
        """凭据解析：环境变量 → 构造参数缺省"""
        old = os.environ.get("PLAT_KEY_TEST")
        try:
            os.environ["PLAT_KEY_TEST"] = "sk-from-env"
            cfg = {"platforms": [
                {"name": "p", "api_base": "http://x/v1", "key_env": "PLAT_KEY_TEST"},
            ]}
            reg = PlatformRegistry.from_config(cfg)
            self.assertEqual(reg.adapter_for("p").api_key, "sk-from-env")
        finally:
            if old is None:
                os.environ.pop("PLAT_KEY_TEST", None)
            else:
                os.environ["PLAT_KEY_TEST"] = old

    def test_explicit_key_override(self):
        """显式 key 优先于环境变量"""
        cfg = {"platforms": [
            {"name": "p", "api_base": "http://x/v1", "key": "sk-explicit",
             "key_env": "NONEXIST_KEY_XYZ"},
        ]}
        reg = PlatformRegistry.from_config(cfg)
        self.assertEqual(reg.adapter_for("p").api_key, "sk-explicit")


if __name__ == "__main__":
    unittest.main()
