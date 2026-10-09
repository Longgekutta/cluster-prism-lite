#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
model_port.py: ModelPort 端口与规范化契约 (Hexagonal Port & Canonical Contract)
================================================================================
六边形架构端口：管线只依赖此接口，任何外部轮子/厂商只出现在适配器实现里。

五条不变量契约（contract_tests.py 逐条断言）：
  INV-A 前缀字节级一致：messages_bytes 进端口后零加工（禁 strip/encode/重排）
  INV-B 单一序列化边界：wire format 转换只发生在适配器内部且只发生一次
  INV-C 禁内部重试：适配器禁止自动重试（重试由管线用幂等键控制）
  INV-D 错误归一：所有厂商错误 → CanonicalError
  INV-E 幂等键透传：idempotency_key 原样到达厂商请求
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, List, Optional, Protocol, runtime_checkable


# ---------------- 规范化错误（INV-D 错误归一） ----------------

class CanonicalError(Exception):
    """所有厂商错误统一归一为此类型"""

    KINDS: ClassVar[tuple] = ("RATE_LIMIT", "AUTH", "NOT_FOUND", "SERVER", "NETWORK")

    def __init__(self, status_code: int, kind: str, message: str):
        if kind not in self.KINDS:
            raise ValueError(f"未知错误类型: {kind}，必须是 {self.KINDS} 之一")
        self.status_code = status_code
        self.kind = kind
        self.message = message
        super().__init__(f"[{kind}] {status_code}: {message}")


def normalize_http_error(status_code: int, message: str) -> CanonicalError:
    """HTTP 状态码 → CanonicalError 归一（INV-D）"""
    if status_code in (401, 403):
        return CanonicalError(status_code, "AUTH", message)
    if status_code == 404:
        return CanonicalError(status_code, "NOT_FOUND", message)
    if status_code == 429:
        return CanonicalError(status_code, "RATE_LIMIT", message)
    if 500 <= status_code < 600:
        return CanonicalError(status_code, "SERVER", message)
    return CanonicalError(status_code, "NETWORK", message)


# ---------------- 规范化请求/响应（INV-B 单一序列化边界） ----------------

@dataclass(frozen=True)
class CanonicalRequest:
    """规范化请求：messages_bytes 冻结为 bytes（frozen=True 不可变）

    INV-A：适配器对 messages_bytes 只允许 pass-through，
    任何 strip()/encode()/重排都是契约红线（contract_tests 拦截）。
    """
    model_id: str
    messages_bytes: bytes
    idempotency_key: str
    trace_id: str
    temperature: float = 0.0
    max_tokens: int = 2000


@dataclass(frozen=True)
class CanonicalResponse:
    """规范化响应"""
    content: str
    model_id: str
    trace_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


# ---------------- 端口协议 ----------------

@runtime_checkable
class ModelPort(Protocol):
    """六边形端口：管线只依赖此接口

    实现者（适配器）必须：
      1. 只做协议翻译（INV-B），不参与路由/重试/缓存决策
      2. 对 messages_bytes 零加工（INV-A）
      3. 禁止内部自动重试（INV-C）
      4. 一切失败抛 CanonicalError（INV-D）
      5. 幂等键原样透传（INV-E）
    """

    def invoke(self, request: CanonicalRequest) -> CanonicalResponse:
        """同步调用：请求 → 响应，失败抛 CanonicalError"""
        ...
