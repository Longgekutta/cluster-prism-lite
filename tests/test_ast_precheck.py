#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_ast_precheck.py: AST 预检验收神谕"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ast_precheck import precheck


class TestSyntax(unittest.TestCase):
    def test_valid_passes(self):
        r = precheck("import os\nprint(os.getpid())\n")
        self.assertTrue(r.ok)

    def test_syntax_error_caught(self):
        r = precheck("def broken(:\n    pass\n")
        self.assertFalse(r.ok)
        self.assertEqual(r.findings[0].code, "PRB-E003")


class TestNegativeInvariants(unittest.TestCase):
    def test_bare_except_flagged(self):
        """PRB-E108 裸 except 吞异常"""
        r = precheck("try:\n    x = 1\nexcept:\n    pass\n")
        self.assertFalse(r.ok)
        self.assertTrue(any(f.code == "PRB-E108" for f in r.findings))

    def test_wildcard_import_flagged(self):
        """PRB-E502 通配导入"""
        r = precheck("from os import *\n")
        self.assertFalse(r.ok)
        self.assertTrue(any(f.code == "PRB-E502" for f in r.findings))

    def test_hardcoded_secret_flagged(self):
        """PRB-E503 硬编码密钥"""
        r = precheck('API_KEY = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"\n')
        self.assertFalse(r.ok)
        self.assertTrue(any(f.code == "PRB-E503" for f in r.findings))

    def test_specific_except_ok(self):
        """捕获具体异常类型不违规"""
        r = precheck("try:\n    x = int('a')\nexcept ValueError as e:\n    print(e)\n")
        self.assertTrue(r.ok)


if __name__ == "__main__":
    unittest.main()
