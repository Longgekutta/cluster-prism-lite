#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_tdd_sandbox.py: TDD 沙箱验收神谕"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tdd_sandbox import run_oracle


class TestRunOracle(unittest.TestCase):
    def test_success_exit_zero(self):
        r = run_oracle([sys.executable, "-c", "print('ok')"])
        self.assertTrue(r.ok)
        self.assertEqual(r.exit_code, 0)
        self.assertIn("ok", r.stdout)

    def test_failure_exit_nonzero(self):
        """退出码为唯一物理判据"""
        r = run_oracle([sys.executable, "-c", "raise SystemExit(3)"])
        self.assertFalse(r.ok)
        self.assertEqual(r.exit_code, 3)

    def test_timeout_kills(self):
        """超时硬杀（缩短为 1s 以加速测试）"""
        r = run_oracle([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1.0)
        self.assertFalse(r.ok)
        self.assertTrue(r.timed_out)
        self.assertEqual(r.exit_code, -9)

    def test_invalid_cmd_reports(self):
        r = run_oracle(["/nonexistent/binary/xyz"])
        self.assertFalse(r.ok)
        self.assertIn("失败", r.stderr)


if __name__ == "__main__":
    unittest.main()
