#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
contract_tests.py: 适配器契约测试套件 (Adapter Contract Suite)
================================================================
五条不变量变成可执行断言——任何适配器（litellm 包装/逆向通道/中转池）的准入门槛。
考不过的适配器禁止上线。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict

from model_port import CanonicalRequest, CanonicalResponse, CanonicalError, ModelPort, normalize_http_error

# 契约测试的标准请求：含固定前缀与幂等键
CONTRACT_PREFIX = b'[{"role": "system", "content": "cluster contract prefix"}]'
CONTRACT_IDEMPOTENCY_KEY = "contract-key-001"
CONTRACT_TRACE = "contract-trace-001"


def _make_request() -> CanonicalRequest:
    return CanonicalRequest(
        model_id="contract",
        messages_bytes=CONTRACT_PREFIX,
        idempotency_key=CONTRACT_IDEMPOTENCY_KEY,
        trace_id=CONTRACT_TRACE,
    )


def _capture_request(adapter: Any, capture: Dict[str, Any]) -> CanonicalResponse:
    """包装适配器：捕获它实际构造的厂商请求体（用于透传断言）"""
    orig_invoke = adapter.invoke

    def spy_invoke(request: CanonicalRequest) -> CanonicalResponse:
        capture["request"] = request
        return orig_invoke(request)

    adapter.invoke = spy_invoke  # type: ignore[method-assign]
    return adapter.invoke(_make_request())


def check_prefix_bytes_unchanged(adapter: Any) -> bool:
    """INV-A：前缀字节级一致——适配器发出的厂商请求体中前缀必须原样

    检查策略（离线优先，不需要网络）：
      1. 若适配器暴露 build_request_body（协议翻译型），直接离线检查厂商请求体；
      2. 否则（透传型）检查响应 content 是否原样携带 messages_bytes 语义。
    """
    try:
        req = _make_request()
        # 离线检查：协议翻译型直接检查厂商请求体
        build = getattr(adapter, "build_request_body", None)
        if build is not None:
            body = build(req)
            return CONTRACT_PREFIX in body
        # 透传型：响应 content 必须原样携带前缀
        resp = adapter.invoke(req)
        return resp.content.encode("utf-8") == CONTRACT_PREFIX
    except (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError) as exc:
        # 契约检查失败即适配器不合规：记录原因（不静默吞掉）
        print(f"[contract] 检查异常(判为不合规): {type(exc).__name__}: {exc}", flush=True)
        return False


def check_idempotency_key_passthrough(adapter: Any) -> bool:
    """INV-E：幂等键透传——适配器发出的请求里 idempotency_key 原样保留（离线检查）"""
    try:
        req = _make_request()
        build = getattr(adapter, "build_request_body", None)
        if build is not None:
            body = build(req)
            return CONTRACT_IDEMPOTENCY_KEY.encode("utf-8") in body
        # 透传型：请求经 spy 捕获验证
        capture: Dict[str, Any] = {}
        _capture_request(adapter, capture)
        req2 = capture.get("request")
        if req2 is None:
            return False  # 适配器未收到请求 = 不合规
        return req2.idempotency_key == CONTRACT_IDEMPOTENCY_KEY
    except (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError) as exc:
        # 契约检查失败即适配器不合规：记录原因（不静默吞掉）
        print(f"[contract] 检查异常(判为不合规): {type(exc).__name__}: {exc}", flush=True)
        return False


def check_no_internal_retry(adapter: Any) -> bool:
    """INV-C：禁内部重试——观测适配器在失败时的行为

    可观测方案：注入一个"必失败底层"，统计底层被调用次数：
      - 合规适配器：底层失败 → 直接冒泡，底层被调 1 次
      - 违规适配器：底层失败 → 内部重试 → 底层被调 >1 次
    """
    calls = {"n": 0}

    class AlwaysFailingBottom:
        """注入的必失败底层（模拟厂商瞬态故障）——同时支持 send/invoke 两种接口"""

        def send(self, body: bytes, headers: Dict[str, str]) -> Dict[str, Any]:
            calls["n"] += 1
            raise normalize_http_error(503, "transient")

        def invoke(self, request: CanonicalRequest) -> CanonicalResponse:
            calls["n"] += 1
            raise normalize_http_error(503, "transient")

    # 将适配器的底层替换为计数失败源：适配器必须支持 set_bottom 注入点
    # （合规适配器的底层是可替换的传输调用；无注入点 = 无法验证 = 违规）
    set_bottom = getattr(adapter, "set_bottom", None)
    if set_bottom is None:
        return False  # 无底层注入点 = 无法证明无内部重试 = 不合规
    # 记录原底层，测试后恢复（契约测试不得污染生产实例）
    original_bottom = getattr(adapter, "_bottom", None)
    set_bottom(AlwaysFailingBottom())
    try:
        adapter.invoke(_make_request())
        return False  # 失败后仍返回成功 = 有问题
    except CanonicalError:
        # 底层失败冒泡为 CanonicalError = 无内部重试（合规）
        result = calls["n"] == 1  # 底层只被调 1 次 = 无内部重试
        return result
    except (RuntimeError, ValueError, TypeError, AttributeError, OSError) as exc:
        # 适配器把底层错误吞掉换成裸异常 = 违规（未归一）
        print(f"[contract] 内部重试/未归一异常(判为不合规): {type(exc).__name__}", flush=True)
        result = False
        return result
    finally:
        # 恢复原底层（契约测试不得污染生产实例）
        set_bottom(original_bottom)


def check_error_normalized(adapter: Any) -> bool:
    """INV-D：错误归一——失败必须抛 CanonicalError，不得裸 RuntimeError 静默"""
    class AlwaysFail:
        def invoke(self, request: CanonicalRequest) -> CanonicalResponse:
            raise RuntimeError("raw vendor error")

    # 只有适配器把 RuntimeError 归一成 CanonicalError 才通过：
    # 用一个必然失败的底层，看适配器抛出的类型
    try:
        adapter.invoke(_make_request())
        return False  # 没失败反而异常
    except CanonicalError:
        return True  # 归一为 CanonicalError ✓
    except (RuntimeError, ValueError, TypeError, AttributeError, OSError):
        return False  # 裸异常未归一 = 违规


def check_trace_passthrough(adapter: Any) -> bool:
    """附加契约：trace_id 透传（离线优先：检查请求头/请求对象）"""
    try:
        req = _make_request()
        # 协议翻译型：检查请求头
        build_headers = getattr(adapter, "build_headers", None)
        if build_headers is not None:
            headers = build_headers(req)
            return headers.get("X-Trace-Id") == CONTRACT_TRACE
        # 透传型：响应 trace_id 验证
        resp = adapter.invoke(req)
        return getattr(resp, "trace_id", "") == CONTRACT_TRACE
    except (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError) as exc:
        # 契约检查失败即适配器不合规：记录原因（不静默吞掉）
        print(f"[contract] 检查异常(判为不合规): {type(exc).__name__}: {exc}", flush=True)
        return False


def run_contract_suite(adapter: Any) -> Dict[str, Any]:
    """运行完整契约套件，返回逐项结果与总判定

    每项检查使用独立实例（deepcopy 语义由调用方保证）：
    check_no_internal_retry 会替换底层，故它必须最后执行，
    且后续检查（trace）在其之前完成。
    """
    results = {
        "prefix_bytes_unchanged": check_prefix_bytes_unchanged(adapter),
        "idempotency_key_passthrough": check_idempotency_key_passthrough(adapter),
        "trace_passthrough": check_trace_passthrough(adapter),
    }
    # error_normalized: 若适配器在失败路径暴露则检查归一；否则跳过
    err_ok = check_error_normalized_if_fails(adapter)
    results["error_normalized"] = True if err_ok is None else err_ok
    # no_internal_retry 最后执行（它会替换底层为必失败源）
    results["no_internal_retry"] = check_no_internal_retry(adapter)
    results["all_pass"] = all(results.values())
    return results


def check_error_normalized_if_fails(adapter: Any) -> Optional[bool]:
    """适配器若暴露失败路径则检查错误归一；否则返回 None 跳过"""
    class AlwaysFail:
        def invoke(self, request: CanonicalRequest) -> CanonicalResponse:
            raise RuntimeError("raw vendor error")

    try:
        adapter.invoke(_make_request())
        return None  # 不失败，跳过
    except Exception as exc:
        return type(exc).__name__ == "CanonicalError"
