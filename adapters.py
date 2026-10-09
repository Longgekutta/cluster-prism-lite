#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adapters.py: 多厂商适配器层 (Multi-Provider Adapters)
======================================================
所有适配器实现 ModelPort 端口，只做协议翻译（INV-B）：
  - UrllibAdapter  : OpenAI 协议官方端点（纯 urllib 标准库）
  - EchoAdapter    : 测试替身（纯透传）
  - WebBridgeAdapter : 网页逆向通道平级适配器（用户自建桥 HTTP 端点）
  - AdapterRegistry : model_id → adapter 映射 + 健康探测

红线（contract_tests.py 拦截）：对 messages_bytes 零加工、禁内部重试、
一切失败抛 CanonicalError、幂等键透传。
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List, Optional

from model_port import (
    CanonicalRequest, CanonicalResponse, CanonicalError,
    ModelPort, normalize_http_error,
)


class EchoAdapter:
    """测试替身：纯透传，messages_bytes 原样返回；底层可注入（契约测试用）"""

    def __init__(self):
        self._bottom = None

    def set_bottom(self, bottom: Any) -> None:
        """底层注入点：契约测试用"""
        self._bottom = bottom

    def invoke(self, request: CanonicalRequest) -> CanonicalResponse:
        if self._bottom is not None:
            return self._bottom.invoke(request)
        return CanonicalResponse(
            content=request.messages_bytes.decode("utf-8"),
            model_id=request.model_id,
            trace_id=request.trace_id,
        )


class UrllibAdapter:
    """OpenAI 协议官方端点适配器（纯 urllib，无第三方依赖）

    INV-B 单一序列化边界：wire format 转换只发生在 build_request_body 内且只一次；
    messages_bytes 只做 bytes 拼接（零解码再编码）。
    底层传输可替换（set_bottom 注入点）：契约测试用，生产为 HTTP 传输。
    """

    def __init__(self, api_key: str, api_base: str,
                 timeout_seconds: float = 60.0):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._bottom = None  # 可替换传输层（生产 None = 真实 HTTP）

    def set_bottom(self, bottom: Any) -> None:
        """底层注入点：契约测试替换传输层（INV-C 可观测）"""
        self._bottom = bottom

    def build_request_body(self, request: CanonicalRequest) -> bytes:
        """组装厂商请求体：messages_bytes 纯透传（INV-A 红线），幂等键透传（INV-E）"""
        # messages_bytes 只做 bytes 拼接，不 decode/strip/重排
        header = json.dumps({
            "model": request.model_id,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "idempotency_key": request.idempotency_key,
        }).encode("utf-8")
        # 单一序列化边界：字段注入 + messages_bytes 原样嵌入
        return header[:-1] + b', "messages": ' + request.messages_bytes + b"}"

    def build_headers(self, request: CanonicalRequest) -> Dict[str, str]:
        """组装厂商请求头：幂等键/trace 透传（INV-E）"""
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-Idempotency-Key": request.idempotency_key,
            "X-Trace-Id": request.trace_id,
        }

    def _send(self, body: bytes, headers: Dict[str, str]) -> Dict[str, Any]:
        """底层传输（可替换注入点）"""
        if self._bottom is not None:
            return self._bottom.send(body, headers)
        req = urllib.request.Request(
            self.api_base + "/chat/completions",
            data=body, headers=headers, method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def invoke(self, request: CanonicalRequest) -> CanonicalResponse:
        body = self.build_request_body(request)
        headers = self.build_headers(request)
        try:
            data = self._send(body, headers)
        except urllib.error.HTTPError as exc:
            raise normalize_http_error(exc.code, str(exc.reason)) from exc
        except urllib.error.URLError as exc:
            raise normalize_http_error(0, f"network: {exc.reason}") from exc
        except (json.JSONDecodeError, OSError) as exc:
            raise normalize_http_error(0, f"malformed: {exc}") from exc
        try:
            choice = data["choices"][0]["message"]
            usage = data.get("usage", {})
            return CanonicalResponse(
                content=choice.get("content", ""),
                model_id=request.model_id,
                trace_id=request.trace_id,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise normalize_http_error(0, f"unexpected response shape: {exc}") from exc


class WebBridgeAdapter:
    """网页逆向通道平级适配器：用户自建桥 HTTP 端点（svc-gpt/claude-web-bridge）

    桥端点接受 OpenAI 格式 POST，返回 OpenAI 格式 JSON。
    协议归一与 UrllibAdapter 同构——本就同构，独立成类以隔离依赖与升级风险。
    底层传输可替换（set_bottom 注入点）。
    """

    def __init__(self, endpoint: str, token: str = "",
                 timeout_seconds: float = 60.0):
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self._bottom = None

    def set_bottom(self, bottom: Any) -> None:
        """底层注入点：契约测试用"""
        self._bottom = bottom

    def build_request_body(self, request: CanonicalRequest) -> bytes:
        return UrllibAdapter.build_request_body(self, request)

    def build_headers(self, request: CanonicalRequest) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Idempotency-Key": request.idempotency_key,
            "X-Trace-Id": request.trace_id,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _send(self, body: bytes, headers: Dict[str, str]) -> Dict[str, Any]:
        if self._bottom is not None:
            return self._bottom.send(body, headers)
        req = urllib.request.Request(self.endpoint, data=body,
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def invoke(self, request: CanonicalRequest) -> CanonicalResponse:
        body = self.build_request_body(request)
        headers = self.build_headers(request)
        try:
            data = self._send(body, headers)
        except urllib.error.HTTPError as exc:
            raise normalize_http_error(exc.code, str(exc.reason)) from exc
        except urllib.error.URLError as exc:
            raise normalize_http_error(0, f"network: {exc.reason}") from exc
        except (json.JSONDecodeError, OSError) as exc:
            raise normalize_http_error(0, f"malformed: {exc}") from exc
        try:
            choice = data["choices"][0]["message"]
            return CanonicalResponse(
                content=choice.get("content", ""),
                model_id=request.model_id,
                trace_id=request.trace_id,
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise normalize_http_error(0, f"unexpected response shape: {exc}") from exc


class AdapterRegistry:
    """适配器注册表：model_id → adapter 映射 + 健康探测"""

    def __init__(self):
        self._adapters: Dict[str, Any] = {}
        self._healthy: Dict[str, bool] = {}

    def register(self, model_id: str, adapter: Any) -> None:
        self._adapters[model_id] = adapter
        self._healthy[model_id] = True

    def resolve(self, model_id: str) -> Any:
        adapter = self._adapters.get(model_id)
        if adapter is None:
            raise CanonicalError(404, "NOT_FOUND",
                                 f"未知模型/适配器: {model_id}（拒绝静默降级）")
        return adapter

    def is_healthy(self, model_id: str) -> bool:
        return self._healthy.get(model_id, False)

    def mark_unhealthy(self, model_id: str) -> None:
        if model_id in self._healthy:
            self._healthy[model_id] = False

    def mark_healthy(self, model_id: str) -> None:
        if model_id in self._healthy:
            self._healthy[model_id] = True

    def models(self) -> List[str]:
        return list(self._adapters.keys())

    def healthy_models(self) -> List[str]:
        return [m for m in self._adapters if self._healthy[m]]


# urllib.error 显式导入
import urllib.error  # noqa: E402
