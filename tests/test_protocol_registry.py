#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_protocol_registry.py: PlatformRegistry protocol 字段验收（红灯）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platform_adapter import PlatformRegistry
from protocol_adapter import get_protocol_adapter


class TestPlatformProtocolField(unittest.TestCase):
    def test_protocol_field_config(self):
        """config 声明 protocol 类型（anthropic/gemini/openai）"""
        cfg = {"platforms": [
            {"name": "claude-direct", "api_base": "https://api.anthropic.com",
             "protocol": "anthropic", "key": "k"},
            {"name": "gemini-direct", "api_base": "https://generativelanguage.googleapis.com",
             "protocol": "gemini", "key": "k"},
        ]}
        reg = PlatformRegistry.from_config(cfg)
        # protocol 声明可查询
        self.assertEqual(reg.protocol_for("claude-direct"), "anthropic")
        self.assertEqual(reg.protocol_for("gemini-direct"), "gemini")
        # 缺省 openai
        self.assertEqual(reg.protocol_for_or("unknown", "openai"), "openai")


if __name__ == "__main__":
    unittest.main()
