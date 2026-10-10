#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_protocol_adapter.py: 多协议适配器验收神谕（红灯：模块未实现）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from protocol_adapter import (
    ProtocolAdapter, AnthropicMessagesAdapter, GeminiAdapter,
    get_protocol_adapter,
)


class TestAnthropicTransform(unittest.TestCase):
    def setUp(self):
        self.a = AnthropicMessagesAdapter(api_key="k", api_base="http://mock")

    def test_transform_request(self):
        """OpenAI IR → Anthropic Messages payload（纯函数转换）"""
        ir = {"model": "claude-3-7-sonnet",
              "messages": [{"role": "user", "content": "hi"}],
              "max_tokens": 100, "temperature": 0.1}
        payload = self.a.transform_request(ir)
        # Anthropic: system 独立字段 + max_tokens 必需
        self.assertEqual(payload["model"], "claude-3-7-sonnet")
        self.assertIn("messages", payload)
        self.assertEqual(payload["max_tokens"], 100)

    def test_transform_response(self):
        """Anthropic 响应 → OpenAI IR（content 数组归一）"""
        anthropic_resp = {"content": [{"type": "text", "text": "hello"}],
                          "usage": {"input_tokens": 5, "output_tokens": 3}}
        ir = self.a.transform_response(anthropic_resp)
        self.assertEqual(ir["choices"][0]["message"]["content"], "hello")
        self.assertEqual(ir["usage"]["prompt_tokens"], 5)

    def test_capabilities(self):
        """capabilities 声明（上层据此决策，不 try 看报错）"""
        caps = self.a.capabilities
        self.assertIn("stream", caps)
        self.assertIn("tool_call", caps)

    def test_protocol_kind(self):
        self.assertEqual(self.a.protocol_kind, "anthropic")


class TestGeminiTransform(unittest.TestCase):
    def setUp(self):
        self.g = GeminiAdapter(api_key="k", api_base="http://mock")

    def test_transform_request(self):
        """OpenAI IR → Gemini generateContent payload"""
        ir = {"model": "gemini-2.5-pro",
              "messages": [{"role": "user", "content": "hi"}],
              "max_tokens": 100}
        payload = self.g.transform_request(ir)
        # Gemini: contents[].parts[].text
        self.assertIn("contents", payload)
        self.assertEqual(payload["contents"][0]["parts"][0]["text"], "hi")

    def test_transform_response(self):
        """Gemini 响应 → OpenAI IR（candidates 归一）"""
        gemini_resp = {"candidates": [{"content": {"parts": [{"text": "answer"}]}}],
                       "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2}}
        ir = self.g.transform_response(gemini_resp)
        self.assertEqual(ir["choices"][0]["message"]["content"], "answer")
        self.assertEqual(ir["usage"]["prompt_tokens"], 4)

    def test_protocol_kind(self):
        self.assertEqual(self.g.protocol_kind, "gemini")


class TestRegistry(unittest.TestCase):
    def test_get_protocol_adapter(self):
        """协议注册表：kind 声明式获取（非 if-else 硬编码）"""
        a = get_protocol_adapter("anthropic", api_key="k", api_base="http://m")
        self.assertEqual(a.protocol_kind, "anthropic")
        g = get_protocol_adapter("gemini", api_key="k", api_base="http://m")
        self.assertEqual(g.protocol_kind, "gemini")
        o = get_protocol_adapter("openai", api_key="k", api_base="http://m")
        self.assertEqual(o.protocol_kind, "openai")

    def test_unknown_protocol_raises(self):
        with self.assertRaises(ValueError):
            get_protocol_adapter("grpc-unknown", api_key="k", api_base="http://m")


class TestContractCompat(unittest.TestCase):
    def test_protocol_adapters_are_pure_translation(self):
        """协议适配器是纯翻译（不碰网络），契约套件留给 ModelPort HTTP 适配器"""
        # 纯翻译性验证：transform 不做任何网络 IO（无 _send/set_bottom）
        a = AnthropicMessagesAdapter(api_key="k", api_base="http://mock")
        g = GeminiAdapter(api_key="k", api_base="http://mock")
        for adapter in (a, g):
            self.assertFalse(hasattr(adapter, "set_bottom"),
                             "协议适配器不碰网络，不应有传输注入点")
            self.assertFalse(hasattr(adapter, "_send"),
                             "协议适配器不碰网络，不应有发送方法")


if __name__ == "__main__":
    unittest.main()
