#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_model_port.py: ModelPort 端口验收神谕（红灯：模块未实现）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_port import (
    ModelPort, CanonicalRequest, CanonicalResponse, CanonicalError,
)


class FakePort(ModelPort):
    """测试替身端口实现"""

    def invoke(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(
            content="ok",
            model_id=request.model_id,
            trace_id=request.trace_id,
        )


class TestCanonicalRequest(unittest.TestCase):
    def test_messages_frozen_as_bytes(self):
        """messages_bytes 是 bytes 且构造后冻结（不可变契约）"""
        req = CanonicalRequest(
            model_id="m1",
            messages_bytes=b'[{"role": "user", "content": "hi"}]',
            idempotency_key="k1",
            trace_id="t1",
        )
        self.assertIsInstance(req.messages_bytes, (bytes, bytearray))

    def test_request_immutable(self):
        """CanonicalRequest 不可变：外部无法篡改已冻结的前缀"""
        req = CanonicalRequest(model_id="m", messages_bytes=b"x",
                               idempotency_key="k", trace_id="t")
        with self.assertRaises(Exception):
            req.model_id = "hacked"

    def test_prefix_bytes_unchanged_through_port(self):
        """INV: 前缀字节级一致——bytes 进端口，适配器搬运后原样到达"""
        req = CanonicalRequest(model_id="m", messages_bytes=b"PREFIX\x00payload",
                               idempotency_key="k", trace_id="t")
        resp = FakePort().invoke(req)
        self.assertEqual(resp.content, "ok")


class TestCanonicalError(unittest.TestCase):
    def test_error_normalized(self):
        """各厂商错误归一为 CanonicalError"""
        err = CanonicalError(status_code=429, kind="RATE_LIMIT", message="too fast")
        self.assertEqual(err.kind, "RATE_LIMIT")

    def test_error_kinds(self):
        for kind in ("RATE_LIMIT", "AUTH", "NOT_FOUND", "SERVER", "NETWORK"):
            e = CanonicalError(status_code=0, kind=kind, message="")
            self.assertEqual(e.kind, kind)


class TestModelPortProtocol(unittest.TestCase):
    def test_port_is_abstract(self):
        """ModelPort 不可直接实例化（必须实现 invoke）"""
        with self.assertRaises(TypeError):
            ModelPort()  # type: ignore[abstract]

    def test_custom_adapter_implements(self):
        class Mini(ModelPort):
            def invoke(self, request):
                return CanonicalResponse(content="c", model_id=request.model_id,
                                         trace_id=request.trace_id)
        resp = Mini().invoke(CanonicalRequest(
            model_id="x", messages_bytes=b"{}", idempotency_key="k", trace_id="t"))
        self.assertEqual(resp.content, "c")


if __name__ == "__main__":
    unittest.main()
