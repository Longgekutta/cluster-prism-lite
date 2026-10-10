#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
model_discovery.py: 模型自动发现与文本/多模态区分 (Model Discovery & Mode Filtering)
======================================================================================
职责（与路由分发严格分模块，不耦合）：
  - 连接平台 /v1/models 自动发现模型列表
  - 按 mode 字段区分文本模型（llm/text）与语音/多模态模型（stt/tts/voice_*/image/video/music）
  - 纯函数过滤，不做任何"用哪个模型干什么"的决策（那是 smart_router 的职责）
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List

# 文本模型 mode 值（其余 mode 全部视为非文本：stt/tts/voice_*/image/video/music/...）
TEXT_MODES = ("llm", "text")


def parse_models_response(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """解析 OpenAI /v1/models 格式 {"object":"list","data":[...]}"""
    if resp.get("object") != "list" or not isinstance(resp.get("data"), list):
        raise ValueError(f"非标准 models 响应: {str(resp)[:100]}")
    return resp["data"]


def filter_text_models(models: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """过滤出文本模型：mode ∈ TEXT_MODES；无 mode 字段保守排除（不猜测）"""
    return [m for m in models if m.get("mode") in TEXT_MODES]


def filter_non_text_models(models: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """过滤出非文本模型（语音/多模态）"""
    return [m for m in models if m.get("mode") not in TEXT_MODES]


def fetch_models(api_key: str, api_base: str,
                 timeout: float = 20.0) -> List[Dict[str, Any]]:
    """连接平台 /v1/models 拉取模型列表

    api_base 兼容两种写法：平台根（https://api.senseaudio.cn）或
    已含 /v1（https://api.senseaudio.cn/v1）——自动归一到 /v1/models。
    """
    base = api_base.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    req = urllib.request.Request(
        base + "/models",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return parse_models_response(json.loads(resp.read().decode("utf-8")))


def discover(api_key: str, api_base: str,
             timeout: float = 20.0) -> Dict[str, List[Dict[str, Any]]]:
    """自动发现：返回 {"text": [...], "non_text": [...]}（发现与路由解耦：只发现，不决策）"""
    models = fetch_models(api_key, api_base, timeout=timeout)
    return {"text": filter_text_models(models),
            "non_text": filter_non_text_models(models)}
