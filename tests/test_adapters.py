#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_adapters.py: 多厂商适配器验收神谕（红灯）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_port import CanonicalRequest, CanonicalResponse, CanonicalError
from adapters import UrllibAdapter, EchoAdapter, AdapterRegistry


class TestEchoAdapter(unittest.TestCase):
    def test_echo_passthrough(self):
        """EchoAdapter 纯透传：messages_bytes 不被加工"""
        adapter = EchoAdapter()
        req = CanonicalRequest(model_id="echo", messages_bytes=b"PREFIX\x00data",
                               idempotency_key="k1", trace_id="t1")
        resp = adapter.invoke(req)
        self.assertEqual(resp.content, "PREFIX\x00data")
        self.assertEqual(resp.model_id, "echo")
        self.assertEqual(resp.trace_id, "t1")


class TestContractViaRegistry(unittest.TestCase):
    def test_registry_resolves_model(self):
        registry = AdapterRegistry()
        registry.register("echo-a", EchoAdapter())
        adapter = registry.resolve("echo-a")
        req = CanonicalRequest(model_id="echo-a", messages_bytes=b"{}",
                               idempotency_key="k", trace_id="t")
        self.assertEqual(adapter.invoke(req).content, "{}")

    def test_registry_unknown_model_raises(self):
        """未知 model_id → 明确报错（拒绝 404 假模型静默）"""
        registry = AdapterRegistry()
        with self.assertRaises(CanonicalError):
            registry.resolve("nonexistent")

    def test_registry_health_probe(self):
        registry = AdapterRegistry()
        registry.register("echo-a", EchoAdapter())
        self.assertTrue(registry.is_healthy("echo-a"))
        self.assertFalse(registry.is_healthy("ghost"))

    def test_registry_list(self):
        registry = AdapterRegistry()
        registry.register("a", EchoAdapter())
        registry.register("b", EchoAdapter())
        self.assertEqual(set(registry.models()), {"a", "b"})


class TestUrllibAdapterContract(unittest.TestCase):
    def test_urllib_adapter_builds_request_without_touching_messages(self):
        """UrllibAdapter 构建请求体时对 messages_bytes 零加工（pass-through 红线）"""
        adapter = UrllibAdapter(api_key="test-key", api_base="http://127.0.0.1:1")
        req = CanonicalRequest(
            model_id="m",
            messages_bytes=b'[{"role":"user","content":"PREFIX_XYZ"}]',
            idempotency_key="k", trace_id="t")
        body = adapter.build_request_body(req)
        # 前缀字节必须原样出现在 body 里
        self.assertIn(b"PREFIX_XYZ", body)
        # 幂等键必须透传
        self.assertIn(b'"k"', body)

    def test_urllib_adapter_invoke_network_error_normalized(self):
        """网络不可达 → CanonicalError(kind=NETWORK)，不静默"""
        adapter = UrllibAdapter(api_key="k", api_base="http://127.0.0.1:1",
                                timeout_seconds=1)
        req = CanonicalRequest(model_id="m", messages_bytes=b"{}",
                               idempotency_key="k", trace_id="t")
        with self.assertRaises(CanonicalError):
            adapter.invoke(req)


if __name__ == "__main__":
    unittest.main()
