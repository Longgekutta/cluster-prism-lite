#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
platform_adapter.py: 配置驱动的通用平台适配器 (Config-Driven Generic Platform Adapter)
========================================================================================
解决"每接一个平台就写一个硬编码类"的问题：
  - GenericOpenAIAdapter：一个类覆盖任何 OpenAI 协议平台
    （api_base / 端点路径 / 凭据环境变量名全部构造参数 + 配置驱动）
  - PlatformRegistry：从 config.json platforms 声明装配平台
    新平台 = 配置加一条 JSON，零代码改动

契约红线不变（contract_tests.py 拦截）：messages_bytes 纯透传、
禁内部重试、失败抛 CanonicalError、幂等键透传。
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Dict, List, Optional

from model_port import (
    CanonicalRequest, CanonicalResponse, CanonicalError, normalize_http_error,
)


class GenericOpenAIAdapter:
    """通用 OpenAI 协议平台适配器：一个类接入任何 OpenAI 协议平台

    差异全部配置/参数驱动：
      api_base   : 平台根或 /v1 根（自动归一）
      api_key    : 凭据（缺省从 key_env 环境变量解析，零硬编码）
      models_path: 模型列表端点（缺省 /models）
      chat_path  : 对话端点（缺省 /chat/completions）

    兼容替代：UrllibAdapter / WebBridgeAdapter / SenseAudioAdapter 三类本质重复，
    本类是其配置驱动收敛。
    """

    def __init__(self, api_key: str = "", api_base: str = "",
                 models_path: str = "/models",
                 chat_path: str = "/chat/completions",
                 key_env: str = "",
                 timeout_seconds: float = 60.0):
        base = api_base.rstrip("/")
        # 归一：平台根自动补 /v1（OpenAI 标准）；已含 /v1 或显式路径则不动
        if not base.endswith("/v1") and "/v1" not in base.split("//")[-1].split("/", 2)[-1]:
            base += "/v1"
        self.api_base = base
        # 凭据解析：显式 key 优先，缺省从环境变量解析（零硬编码）
        self.api_key = api_key or ((os.environ.get(key_env) or "").strip() if key_env else "")
        self.models_path = models_path
        self.chat_path = chat_path
        self.timeout_seconds = timeout_seconds
        self._bottom = None  # 可替换传输层（契约测试注入点）

    def set_bottom(self, bottom: Any) -> None:
        """底层注入点：契约测试用（INV-C 可观测）"""
        self._bottom = bottom

    def models_url(self) -> str:
        return self.api_base + self.models_path

    def chat_url(self) -> str:
        return self.api_base + self.chat_path

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
        headers = {
            "Content-Type": "application/json",
            "X-Idempotency-Key": request.idempotency_key,
            "X-Trace-Id": request.trace_id,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _send(self, body: bytes, headers: Dict[str, str]) -> Dict[str, Any]:
        """底层传输（可替换注入点）"""
        if self._bottom is not None:
            return self._bottom.send(body, headers)
        req = urllib.request.Request(
            self.chat_url(), data=body, headers=headers, method="POST",
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
                content=choice.get("content") or "",
                model_id=request.model_id,
                trace_id=request.trace_id,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise normalize_http_error(0, f"unexpected response shape: {exc}") from exc


class PlatformRegistry:
    """配置驱动的平台注册表：新平台 = config.json 加一条 JSON，零代码改动

    config 格式:
      "platforms": [
        {"name": "senseaudio", "api_base": "https://api.senseaudio.cn",
         "key_env": "SENSEAUDIO_API_KEY"},
        {"name": "deepseek", "api_base": "https://api.deepseek.com/v1",
         "key": "sk-explicit"}     # key 显式优先于 key_env
      ]
    """

    def __init__(self, platforms: Optional[Dict[str, GenericOpenAIAdapter]] = None):
        self._platforms: Dict[str, GenericOpenAIAdapter] = platforms or {}
        # protocol 声明：platform name → 协议类型（openai/anthropic/gemini）
        self._protocols: Dict[str, str] = {}

    @classmethod
    def from_config(cls, cfg: Dict[str, Any]) -> "PlatformRegistry":
        """从 config platforms 声明装配（零硬编码代码）"""
        platforms: Dict[str, GenericOpenAIAdapter] = {}
        protocols: Dict[str, str] = {}
        for p in cfg.get("platforms", []):
            name = p["name"]
            platforms[name] = GenericOpenAIAdapter(
                api_key=p.get("key", ""),
                api_base=p["api_base"],
                models_path=p.get("models_path", "/models"),
                chat_path=p.get("chat_path", "/chat/completions"),
                key_env=p.get("key_env", ""),
            )
            protocols[name] = p.get("protocol", "openai")
        inst = cls(platforms)
        inst._protocols = protocols
        return inst

    def protocol_for(self, name: str) -> str:
        """查询平台协议类型"""
        if name not in self._protocols:
            raise KeyError(f"未注册平台: {name}")
        return self._protocols[name]

    def protocol_for_or(self, name: str, default: str = "openai") -> str:
        """查询平台协议类型（未注册返回 default）"""
        return self._protocols.get(name, default)

    def adapter_for(self, name: str) -> GenericOpenAIAdapter:
        adapter = self._platforms.get(name)
        if adapter is None:
            raise KeyError(f"未注册平台: {name}（config.json platforms 中声明后即可用）")
        return adapter

    def names(self) -> List[str]:
        return list(self._platforms.keys())


# urllib.error 显式导入
import urllib.error  # noqa: E402
