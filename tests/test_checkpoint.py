#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_checkpoint.py: git checkpoint 事务快照验收神谕"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkpoint import GitCheckpointManager, CheckpointError


class TestCheckpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mgr = GitCheckpointManager(workdir=self.tmp)

    def test_ensure_repo_creates(self):
        self.mgr.ensure_repo()
        code, _ = self.mgr._git("rev-parse", "--is-inside-work-tree")
        self.assertEqual(code, 0)

    def test_snapshot_and_rollback(self):
        """快照 → 修改 → 回滚 → 物理还原（INV-3）"""
        p = os.path.join(self.tmp, "state.txt")
        with open(p, "w") as f:
            f.write("v1")
        cp1 = self.mgr.snapshot("snapshot v1")
        with open(p, "w") as f:
            f.write("v2 polluted")
        self.mgr.rollback(cp1.ref)
        with open(p) as f:
            self.assertEqual(f.read(), "v1")

    def test_head_after_snapshots(self):
        self.mgr.snapshot("first")
        self.mgr.snapshot("second")
        head = self.mgr.head()
        self.assertEqual(len(head.ref), 40)  # SHA-1 全长

    def test_log_lists_snapshots(self):
        self.mgr.snapshot("a")
        self.mgr.snapshot("b")
        log = self.mgr.log(limit=5)
        self.assertGreaterEqual(len(log), 2)

    def test_snapshot_empty_repo_ok(self):
        """空仓库 allow-empty 快照不崩溃"""
        cp = self.mgr.snapshot("empty snapshot")
        self.assertEqual(len(cp.ref), 40)


if __name__ == "__main__":
    unittest.main()
