#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_contract.py: 契约测试套件（五不变量准入门槛，红灯）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_port import CanonicalRequest, CanonicalResponse
from contract_tests import run_contract_suite


class GoodAdapter:
    """合规适配器：透传一切，不重试，支持底层注入"""

    def __init__(self):
        self._bottom = None

    def set_bottom(self, bottom):
        """底层注入点：契约测试用（合规适配器的传输调用可替换）"""
        self._bottom = bottom

    def invoke(self, request: CanonicalRequest) -> CanonicalResponse:
        if self._bottom is not None:
            return self._bottom.invoke(request)
        return CanonicalResponse(
            content=request.messages_bytes.decode("utf-8"),
            model_id=request.model_id,
            trace_id=request.trace_id,
        )


class PrefixTamperingAdapter:
    """违规：篡改前缀（注入路由标记到 messages）"""

    def invoke(self, request: CanonicalRequest) -> CanonicalResponse:
        tampered = b"[tier=cheap] " + request.messages_bytes
        return CanonicalResponse(content=tampered.decode("utf-8"),
                                 model_id=request.model_id,
                                 trace_id=request.trace_id)


class RetryAdapter:
    """违规：内部自动重试（支持注入底层，内部静默重试后成功）"""

    def __init__(self):
        self._bottom = None

    def set_bottom(self, bottom):
        self._bottom = bottom

    def invoke(self, request: CanonicalRequest) -> CanonicalResponse:
        last_exc = None
        for attempt in range(3):
            try:
                if self._bottom is not None:
                    return self._bottom.invoke(request)
                if attempt == 0:
                    raise RuntimeError("transient")
                return CanonicalResponse(content="ok", model_id=request.model_id,
                                         trace_id=request.trace_id)
            except Exception as exc:  # noqa: BLE001 内部重试：契约红线
                last_exc = exc
                continue
        raise last_exc if last_exc else RuntimeError("unreachable")


class TestContractSuite(unittest.TestCase):
    def test_good_adapter_passes(self):
        adapter = GoodAdapter()
        results = run_contract_suite(adapter)
        self.assertTrue(results["all_pass"], results)

    def test_prefix_tampering_rejected(self):
        """红线 1：篡改前缀的适配器被契约拦截"""
        results = run_contract_suite(PrefixTamperingAdapter())
        self.assertFalse(results["all_pass"])
        self.assertFalse(results["prefix_bytes_unchanged"])

    def test_internal_retry_rejected(self):
        """红线 2：内部自动重试的适配器被契约拦截"""
        results = run_contract_suite(RetryAdapter())
        self.assertFalse(results["all_pass"])
        self.assertFalse(results["no_internal_retry"])


if __name__ == "__main__":
    unittest.main()
