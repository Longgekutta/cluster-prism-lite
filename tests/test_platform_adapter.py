#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_platform_adapter.py: 通用平台适配器验收神谕（红灯：模块未实现）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platform_adapter import GenericOpenAIAdapter, PlatformRegistry


class TestGenericAdapter(unittest.TestCase):
    def test_one_class_any_platform(self):
        """一个类接入任何 OpenAI 协议平台：仅构造参数不同"""
        sa = GenericOpenAIAdapter(api_key="k1", api_base="https://api.senseaudio.cn")
        ds = GenericOpenAIAdapter(api_key="k2", api_base="https://api.deepseek.com/v1")
        bridge = GenericOpenAIAdapter(api_key="", api_base="http://127.0.0.1:8005/v1")
        for a in (sa, ds, bridge):
            self.assertTrue(hasattr(a, "invoke"))
            self.assertTrue(hasattr(a, "build_request_body"))
            self.assertTrue(hasattr(a, "build_headers"))
            self.assertTrue(hasattr(a, "set_bottom"))

    def test_models_path_configurable(self):
        """models 端点可配置（平台差异：/v1/models 归一）"""
        a = GenericOpenAIAdapter(api_key="k", api_base="https://api.senseaudio.cn")
        self.assertEqual(a.models_url(), "https://api.senseaudio.cn/v1/models")
        b = GenericOpenAIAdapter(api_key="k", api_base="https://api.senseaudio.cn/v1")
        self.assertEqual(b.models_url(), "https://api.senseaudio.cn/v1/models")

    def test_chat_path_configurable(self):
        a = GenericOpenAIAdapter(api_key="k", api_base="http://x/v1")
        self.assertEqual(a.chat_url(), "http://x/v1/chat/completions")

    def test_key_from_env(self):
        """凭据走环境变量（零硬编码）"""
        old = os.environ.get("TEST_PLATFORM_KEY")
        try:
            os.environ["TEST_PLATFORM_KEY"] = "sk-env-test"
            a = GenericOpenAIAdapter(api_base="http://x", key_env="TEST_PLATFORM_KEY")
            self.assertEqual(a.api_key, "sk-env-test")
        finally:
            if old is None:
                os.environ.pop("TEST_PLATFORM_KEY", None)
            else:
                os.environ["TEST_PLATFORM_KEY"] = old

    def test_contract_pass(self):
        """过契约套件（准入红线）"""
        from contract_tests import run_contract_suite
        a = GenericOpenAIAdapter(api_key="k", api_base="http://127.0.0.1:1")
        results = run_contract_suite(a)
        self.assertTrue(results["all_pass"], results)

    def test_replaces_three_adapters(self):
        """兼容替代旧三类：同构造参数同行为"""
        from adapters import UrllibAdapter
        g = GenericOpenAIAdapter(api_key="k", api_base="http://127.0.0.1:1")
        u = UrllibAdapter(api_key="k", api_base="http://127.0.0.1:1")
        from model_port import CanonicalRequest
        req = CanonicalRequest(model_id="m", messages_bytes=b'{"role":"user"}',
                               idempotency_key="k1", trace_id="t1")
        # 两者的请求体都含 messages 原样与幂等键
        self.assertIn(b'{"role":"user"}', g.build_request_body(req))
        self.assertIn(b'{"role":"user"}', u.build_request_body(req))


if __name__ == "__main__":
    unittest.main()
