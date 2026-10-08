#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
checkpoint.py: Git 事务快照与一键回滚 (Git Checkpoint & Atomic Rollback)
==========================================================================
INV-3 Linearizable SMR: git checkpoint 是会话状态唯一真相源；
任何回滚 = git checkout <ref> + 重建内存状态。
纯 subprocess 调 git，零第三方依赖。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple


class CheckpointError(RuntimeError):
    pass


@dataclass
class Checkpoint:
    ref: str
    message: str


class GitCheckpointManager:
    """事务快照管理器：物理防篡改、一键回滚"""

    def __init__(self, workdir: str = "."):
        self.workdir = workdir

    def _git(self, *args: str) -> Tuple[int, str]:
        try:
            proc = subprocess.run(
                ["git", "-C", self.workdir, *args],
                capture_output=True, text=True, timeout=30
            )
            return proc.returncode, (proc.stdout + proc.stderr).strip()
        except subprocess.TimeoutExpired as exc:
            raise CheckpointError(f"git 超时: {args}") from exc
        except OSError as exc:
            raise CheckpointError(f"git 不可用: {exc}") from exc

    def ensure_repo(self) -> None:
        code, out = self._git("rev-parse", "--is-inside-work-tree")
        if code != 0:
            code2, out2 = self._git("init")
            if code2 != 0:
                raise CheckpointError(f"git init 失败: {out2}")

    def snapshot(self, message: str, paths: Optional[List[str]] = None) -> Checkpoint:
        """创建事务快照（git add + commit）；无变更时返回当前 HEAD"""
        self.ensure_repo()
        if paths:
            self._git("add", "--", *paths)
        else:
            self._git("add", "-A")
        code, out = self._git("commit", "-m", message, "--allow-empty")
        if code != 0:
            raise CheckpointError(f"git commit 失败: {out}")
        return self.head()

    def head(self) -> Checkpoint:
        code, out = self._git("rev-parse", "HEAD")
        if code != 0:
            raise CheckpointError(f"无法读取 HEAD: {out}")
        return Checkpoint(ref=out, message="HEAD")

    def rollback(self, ref: str) -> Checkpoint:
        """一键物理回滚到指定快照（reset --hard，绝不留脏文件）"""
        code, out = self._git("reset", "--hard", ref)
        if code != 0:
            raise CheckpointError(f"回滚失败: {out}")
        return Checkpoint(ref=ref, message="rolled back")

    def log(self, limit: int = 10) -> List[Checkpoint]:
        code, out = self._git("log", "--oneline", f"-n{limit}")
        if code != 0:
            return []
        return [Checkpoint(ref=line.split()[0], message=line)
                for line in out.splitlines() if line.strip()]
