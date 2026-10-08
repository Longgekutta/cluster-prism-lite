#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ast_precheck.py: AST 语法编译预检与负向不变量检查 (AST Syntax Precheck & Negative Invariants)
================================================================================================
拦截语法崩坏与违规模式：
  PRB-E103/E003 语法崩坏 | PRB-E108 裸 except 吞异常 | PRB-E502 通配导入 | PRB-E503 硬编码密钥
纯 ast 标准库，零第三方。
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class PrecheckFinding:
    code: str       # PRB-E***
    message: str
    line: int = 0


@dataclass
class PrecheckReport:
    ok: bool
    findings: List[PrecheckFinding] = field(default_factory=list)

    def summary(self) -> str:
        if self.ok:
            return "AST 预检通过：零语法错误，零负向不变量违规"
        return "; ".join(f"[{f.code}] L{f.line}: {f.message}" for f in self.findings)


# 硬编码密钥启发式（PRB-E503）
SECRET_PATTERNS = [
    re.compile(r"""(sk|xoxb|ghp|gho|AKIA)[_-][A-Za-z0-9]{16,}"""),
    re.compile(r"""(?i)(api_key|apikey|secret|token|password)\s*=\s*["'][A-Za-z0-9_\-]{16,}["']"""),
]


def precheck(source: str) -> PrecheckReport:
    """AST 预检：语法 + 负向不变量，返回结构化报告"""
    findings: List[PrecheckFinding] = []
    # 1. 语法解析（PRB-E003/E103 语法崩坏）
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return PrecheckReport(ok=False, findings=[PrecheckFinding(
            code="PRB-E003", message=f"语法崩坏: {exc.msg}", line=exc.lineno or 0)])

    # 2. AST 遍历负向不变量
    for node in ast.walk(tree):
        # PRB-E108 裸 except 吞异常
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                findings.append(PrecheckFinding(
                    code="PRB-E108", message="裸 except 吞异常，必须捕获具体异常类型", line=node.lineno))
        # PRB-E502 通配导入
        if isinstance(node, (ast.ImportFrom,)):
            for alias in node.names:
                if alias.name == "*":
                    findings.append(PrecheckFinding(
                        code="PRB-E502", message=f"通配导入 from {node.module} import *", line=node.lineno))
        # PRB-E503 硬编码密钥
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for pat in SECRET_PATTERNS:
                if pat.search(node.value):
                    findings.append(PrecheckFinding(
                        code="PRB-E503", message="疑似硬编码密钥/凭证", line=node.lineno))
                    break

    return PrecheckReport(ok=not findings, findings=findings)


def precheck_file(path: str) -> PrecheckReport:
    """对已落盘文件执行预检"""
    with open(path, encoding="utf-8") as f:
        return precheck(f.read())
