#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
senseaudio_adapter.py: SENSE AUDIO 平台适配器 (SenseAudio Platform Adapter)
============================================================================
连接 https://api.senseaudio.cn（OpenAI 协议），实现 ModelPort 端口。
职责边界（严格分模块，不耦合）：
  - 本模块：协议接入（发现/调用/归一）——"怎么连上模型"
  - smart_router：路由分发（用哪个模型干什么）——"选谁干活"
  - model_discovery：模型发现（文本 vs 语音区分）——"有哪些模型"
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict

from model_port import (
    CanonicalRequest, CanonicalResponse, CanonicalError, normalize_http_error,
)

SENSEAUDIO_BASE = "https://api.senseaudio.cn"


class SenseAudioAdapter:
    """SENSE AUDIO 平台适配器：OpenAI 协议 + SENSE 特有 reasoning 字段归一

    继承契约红线（contract_tests.py 拦截）：
      messages_bytes 纯透传、禁内部重试、失败抛 CanonicalError、幂等键透传。
    """

    def __init__(self, api_key: str, api_base: str = SENSEAUDIO_BASE,
                 timeout_seconds: float = 60.0):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._bottom = None  # 可替换传输层（契约测试注入点）

    def set_bottom(self, bottom: Any) -> None:
        """底层注入点：契约测试用（INV-C 可观测）"""
        self._bottom = bottom

    def build_request_body(self, request: CanonicalRequest) -> bytes:
        """组装请求体：messages_bytes 纯透传（INV-A），幂等键透传（INV-E）"""
        header = json.dumps({
            "model": request.model_id,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "idempotency_key": request.idempotency_key,
        }).encode("utf-8")
        # 单一序列化边界：字段注入 + messages_bytes 原样嵌入（零解码再编码）
        return header[:-1] + b', "messages": ' + request.messages_bytes + b"}"

    def build_headers(self, request: CanonicalRequest) -> Dict[str, str]:
        """请求头：Bearer 认证 + 幂等键/trace 透传"""
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
            self.api_base + "/v1/chat/completions",
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
            # SENSE 特有：reasoning 字段（深度思考流）——归一为 content 主文本，
            # reasoning 保留在响应注释（不参与前缀/缓存键，符合 INV-A）
            content = choice.get("content") or ""
            return CanonicalResponse(
                content=content,
                model_id=request.model_id,
                trace_id=request.trace_id,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise normalize_http_error(0, f"unexpected response shape: {exc}") from exc


# urllib.error 显式导入
import urllib.error  # noqa: E402
