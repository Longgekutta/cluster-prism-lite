#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_multi_protocol_integration.py: 多协议集成回归"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from protocol_adapter import (
    get_protocol_adapter, register_protocol, OpenAIProtocolAdapter,
    AnthropicMessagesAdapter, GeminiAdapter,
)
from platform_adapter import PlatformRegistry
from adapters import AdapterRegistry, EchoAdapter


class TestMultiProtocolIntegration(unittest.TestCase):
    def test_three_protocols_registered(self):
        """三协议声明式注册（openai/anthropic/gemini）"""
        for kind, cls in (("openai", OpenAIProtocolAdapter),
                          ("anthropic", AnthropicMessagesAdapter),
                          ("gemini", GeminiAdapter)):
            a = get_protocol_adapter(kind, api_key="k", api_base="http://m")
            self.assertEqual(a.protocol_kind, kind)

    def test_round_trip_translation(self):
        """IR → 协议 payload → IR 往返转换（语义保持）"""
        ir = {"model": "test", "messages": [{"role": "user", "content": "hi"}],
              "max_tokens": 50}
        for kind in ("openai", "anthropic", "gemini"):
            a = get_protocol_adapter(kind, api_key="k", api_base="http://m")
            payload = a.transform_request(ir)
            self.assertIsInstance(payload, dict)
            # 模拟响应 → IR
            if kind == "openai":
                resp = {"choices": [{"message": {"content": "x"}}]}
            elif kind == "anthropic":
                resp = {"content": [{"type": "text", "text": "x"}],
                        "usage": {"input_tokens": 1, "output_tokens": 1}}
            else:
                resp = {"candidates": [{"content": {"parts": [{"text": "x"}]}}]}
            back = a.transform_response(resp)
            self.assertEqual(back["choices"][0]["message"]["content"], "x")

    def test_runtime_protocol_registration(self):
        """扩展点：运行时注册新协议（非 OpenAI 网关）"""

        class CustomProtocol(OpenAIProtocolAdapter):
            protocol_kind = "custom-grpc"

        register_protocol("custom-grpc", CustomProtocol)
        a = get_protocol_adapter("custom-grpc", api_key="k", api_base="http://m")
        self.assertEqual(a.protocol_kind, "custom-grpc")

    def test_platform_config_with_protocols(self):
        """config 声明多协议平台 + 舰队装配协同"""
        cfg = {"platforms": [
            {"name": "openai-plat", "api_base": "https://api.openai.com/v1",
             "protocol": "openai", "key": "k"},
            {"name": "anthropic-plat", "api_base": "https://api.anthropic.com",
             "protocol": "anthropic", "key": "k"},
        ]}
        reg = PlatformRegistry.from_config(cfg)
        self.assertEqual(reg.protocol_for("openai-plat"), "openai")
        self.assertEqual(reg.protocol_for("anthropic-plat"), "anthropic")


if __name__ == "__main__":
    unittest.main()
