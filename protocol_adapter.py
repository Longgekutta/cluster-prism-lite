#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
protocol_adapter.py: 多协议适配器层 (Multi-Protocol Adapter Layer)
====================================================================
照 any-llm/litellm 的世界设计：
  - 统一内部表示（IR = OpenAI ChatCompletion 格式当中间语言）
  - 协议适配器是纯函数：transform_request(IR)->provider_payload
                        transform_response(provider_payload)->IR
    不碰网络、不碰状态，所以好测
  - capabilities 声明：每个协议声明 stream/tool_call/json_mode，
    上层据此决策（不 try 看报错）
  - protocol_kind 声明式注册（get_protocol_adapter，非 if-else 硬编码）

网络调用仍走 ModelPort（GenericOpenAIAdapter 等负责 HTTP），
本层只负责"翻译"——接入层管翻译，网关管调度与保安。
"""
from __future__ import annotations

from typing import Any, Callable, Dict

# ---------------- 协议基类 ----------------

class ProtocolAdapter:
    """协议适配器基类：纯函数转换（照 any-llm 的 transform 设计）

    子类必须实现：
      protocol_kind   : 协议类型声明（openai/anthropic/gemini/...）
      capabilities    : 能力声明（stream/tool_call/json_mode/...）
      transform_request(IR) -> provider_payload
      transform_response(provider_payload) -> IR
    """

    protocol_kind: str = ""
    capabilities: Dict[str, bool] = {}

    def __init__(self, api_key: str = "", api_base: str = "", **kwargs: Any):
        self.api_key = api_key
        self.api_base = api_base

    def transform_request(self, ir: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def transform_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def transform_headers(self, ir: Dict[str, Any], api_key: str = "") -> Dict[str, str]:
        """协议专属请求头（缺省 Bearer；Anthropic 覆盖为 x-api-key）"""
        key = api_key or self.api_key
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


# ---------------- OpenAI 协议（IR 本身，恒等转换） ----------------

class OpenAIProtocolAdapter(ProtocolAdapter):
    """OpenAI ChatCompletion 协议：IR 即本协议，恒等转换"""

    protocol_kind = "openai"
    capabilities = {"stream": True, "tool_call": True, "json_mode": True}

    def transform_request(self, ir: Dict[str, Any]) -> Dict[str, Any]:
        return ir  # IR 即 OpenAI 格式，恒等

    def transform_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return payload


# ---------------- Anthropic Messages 协议 ----------------

class AnthropicMessagesAdapter(ProtocolAdapter):
    """Anthropic Messages 协议转换（纯函数）

    差异点：system 独立字段、max_tokens 必需、content 是数组、
    usage 字段名不同（input_tokens/output_tokens）。
    """

    protocol_kind = "anthropic"
    capabilities = {"stream": True, "tool_call": True, "json_mode": False}

    def transform_request(self, ir: Dict[str, Any]) -> Dict[str, Any]:
        messages = list(ir.get("messages", []))
        # system 从 messages 中抽出（Anthropic 独立 system 字段）
        system_text = ""
        chat: list = []
        for m in messages:
            if m.get("role") == "system":
                system_text = m.get("content", "")
            else:
                chat.append({"role": m.get("role"), "content": m.get("content", "")})
        payload: Dict[str, Any] = {
            "model": ir.get("model", ""),
            "messages": chat,
            "max_tokens": ir.get("max_tokens", 1024),
        }
        if system_text:
            payload["system"] = system_text
        if "temperature" in ir:
            payload["temperature"] = ir["temperature"]
        return payload

    def transform_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # content 数组 → OpenAI choices 格式
        content = "".join(
            block.get("text", "") for block in payload.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text")
        usage = payload.get("usage", {})
        return {
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": usage.get("input_tokens", 0),
                      "completion_tokens": usage.get("output_tokens", 0)},
        }

    def transform_headers(self, ir: Dict[str, Any], api_key: str) -> Dict[str, str]:
        # Anthropic: x-api-key + anthropic-version（非 Bearer）
        return {"x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"}


# ---------------- Gemini 原生协议 ----------------

class GeminiAdapter(ProtocolAdapter):
    """Gemini generateContent 协议转换（纯函数）

    差异点：contents[].parts[].text 结构、candidates 响应、usageMetadata 字段。
    """

    protocol_kind = "gemini"
    capabilities = {"stream": True, "tool_call": True, "json_mode": True}

    def transform_request(self, ir: Dict[str, Any]) -> Dict[str, Any]:
        contents = []
        for m in ir.get("messages", []):
            contents.append({"role": "user" if m.get("role") == "user" else "model",
                             "parts": [{"text": m.get("content", "")}]})
        payload: Dict[str, Any] = {"contents": contents}
        if "max_tokens" in ir:
            payload.setdefault("generationConfig", {})["maxOutputTokens"] = ir["max_tokens"]
        if "temperature" in ir:
            payload.setdefault("generationConfig", {})["temperature"] = ir["temperature"]
        return payload

    def transform_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        candidates = payload.get("candidates", [])
        text = ""
        if candidates:
            first = candidates[0] if isinstance(candidates[0], dict) else {}
            content = first.get("content")
            content = content if isinstance(content, dict) else {}
            parts = content.get("parts", [])
            text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        usage = payload.get("usageMetadata", {})
        return {
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": usage.get("promptTokenCount", 0),
                      "completion_tokens": usage.get("candidatesTokenCount", 0)},
        }


# ---------------- 声明式协议注册表 ----------------

_PROTOCOL_REGISTRY: Dict[str, Callable[[], ProtocolAdapter]] = {
    "openai": OpenAIProtocolAdapter,
    "anthropic": AnthropicMessagesAdapter,
    "gemini": GeminiAdapter,
}


def get_protocol_adapter(protocol_kind: str, **kwargs: Any) -> ProtocolAdapter:
    """声明式协议获取：kind → 适配器实例（非 if-else 硬编码）"""
    cls = _PROTOCOL_REGISTRY.get(protocol_kind)
    if cls is None:
        raise ValueError(
            f"未知协议类型: {protocol_kind}（已注册: {sorted(_PROTOCOL_REGISTRY)}）")
    return cls(**kwargs)


def register_protocol(kind: str, cls: type) -> None:
    """运行时注册新协议（扩展点：非 OpenAI 协议的新网关）"""
    _PROTOCOL_REGISTRY[kind] = cls
