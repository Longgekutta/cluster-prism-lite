#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tdd_sandbox.py: TDD 沙箱验证 (TDD Sandbox Oracle Runner)
==========================================================
subprocess 隔离运行测试神谕，30s 硬超时强杀，退出码为唯一物理判据。
杜绝虚假绿灯：以操作系统 Exit Code 为准，不以模型口头声称通过为准。
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional

# G6 硬超时上限
SANDBOX_TIMEOUT_SECONDS = 30


@dataclass
class SandboxResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    timed_out: bool = False


def run_oracle(cmd: List[str], workdir: str = ".",
               timeout: float = SANDBOX_TIMEOUT_SECONDS) -> SandboxResult:
    """在隔离子进程中运行验收神谕，退出码即判据"""
    import time
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, cwd=workdir, capture_output=True, text=True, timeout=timeout
        )
        elapsed = time.perf_counter() - t0
        return SandboxResult(
            ok=(proc.returncode == 0),
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            elapsed_seconds=elapsed,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.perf_counter() - t0
        return SandboxResult(
            ok=False, exit_code=-9,
            stdout=(exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            stderr=(exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
            elapsed_seconds=elapsed, timed_out=True,
        )
    except OSError as exc:
        return SandboxResult(ok=False, exit_code=-1, stdout="",
                             stderr=f"沙箱启动失败: {exc}",
                             elapsed_seconds=time.perf_counter() - t0)


def run_python_tests(test_target: str, workdir: str = ".",
                     timeout: float = SANDBOX_TIMEOUT_SECONDS) -> SandboxResult:
    """便捷入口：python -m unittest <target>"""
    return run_oracle([sys.executable, "-m", "unittest", test_target, "-v"],
                      workdir=workdir, timeout=timeout)
