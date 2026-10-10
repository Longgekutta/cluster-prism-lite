#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_senseaudio.py: SenseAudioAdapter 验收神谕（红灯）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from senseaudio_adapter import SenseAudioAdapter, SENSEAUDIO_BASE


class TestSenseAudioAdapter(unittest.TestCase):
    def test_base_url(self):
        self.assertEqual(SENSEAUDIO_BASE, "https://api.senseaudio.cn")

    def test_implements_port(self):
        """实现 ModelPort 端口协议（build_request_body/build_headers/invoke）"""
        a = SenseAudioAdapter(api_key="test")
        self.assertTrue(hasattr(a, "invoke"))
        self.assertTrue(hasattr(a, "build_request_body"))
        self.assertTrue(hasattr(a, "build_headers"))
        self.assertTrue(hasattr(a, "set_bottom"))

    def test_build_request_body_passthrough(self):
        """messages_bytes 纯透传（INV-A 红线）"""
        a = SenseAudioAdapter(api_key="k")
        from model_port import CanonicalRequest
        req = CanonicalRequest(model_id="glm-5.3-flash",
                               messages_bytes=b'[{"role":"user","content":"hi"}]',
                               idempotency_key="k1", trace_id="t1")
        body = a.build_request_body(req)
        self.assertIn(b'"hi"', body)
        self.assertIn(b'"k1"', body)

    def test_build_headers_transparent(self):
        a = SenseAudioAdapter(api_key="mykey")
        from model_port import CanonicalRequest
        req = CanonicalRequest(model_id="m", messages_bytes=b"{}",
                               idempotency_key="k", trace_id="t9")
        headers = a.build_headers(req)
        self.assertEqual(headers["Authorization"], "Bearer mykey")
        self.assertEqual(headers["X-Trace-Id"], "t9")

    def test_contract_pass(self):
        """过契约套件（准入红线）"""
        from contract_tests import run_contract_suite
        a = SenseAudioAdapter(api_key="k", api_base="http://127.0.0.1:1")
        results = run_contract_suite(a)
        self.assertTrue(results["all_pass"], results)


if __name__ == "__main__":
    unittest.main()
