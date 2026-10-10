#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fleet_setup.py: 多模型舰队装配 (Fleet Assembly from Config)
==============================================================
从 config.json 装配多厂商舰队：官方 API + 网页逆向桥 + 中转池三层。
每个 model_id 绑定适配器（AdapterRegistry）+ 舰队元信息（ModelFleet 健康约束）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Tuple

from adapters import AdapterRegistry, EchoAdapter, UrllibAdapter, WebBridgeAdapter
from smart_router import ModelFleet, Tier

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 源类型 → 适配器工厂
SOURCE_FACTORIES = {
    "official": None,  # 需要动态 api_key/api_base
    "bridge": None,
    "test": lambda meta: EchoAdapter(),
}


def load_fleet_config(config_path: str = "") -> Dict[str, Any]:
    """读取舰队配置（含默认三层舰队）"""
    path = config_path or os.path.join(BASE_DIR, "config.json")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cfg = {}
    if "fleet" not in cfg:
        cfg["fleet"] = _default_fleet()
    return cfg


def _default_fleet() -> list:
    """默认多厂商舰队：官方 DeepSeek + 逆向桥占位 + 测试替身"""
    return [
        {"model_id": "deepseek-flash", "source": "official", "tier": "FLASH",
         "elo": 100.0,
         "adapter": {"kind": "openai", "api_base": "https://api.deepseek.com/v1"}},
        {"model_id": "deepseek-reasoner", "source": "official", "tier": "ELITE",
         "elo": 130.0,
         "adapter": {"kind": "openai", "api_base": "https://api.deepseek.com/v1"}},
        {"model_id": "gpt-web-bridge", "source": "bridge", "tier": "ELITE",
         "elo": 125.0,
         "adapter": {"kind": "bridge",
                     "endpoint": "http://127.0.0.1:8005/v1/chat/completions"}},
        {"model_id": "claude-web-bridge", "source": "bridge", "tier": "ELITE",
         "elo": 125.0,
         "adapter": {"kind": "bridge",
                     "endpoint": "http://127.0.0.1:8006/v1/chat/completions"}},
        {"model_id": "echo-test", "source": "test", "tier": "FLASH",
         "elo": 10.0,
         "adapter": {"kind": "echo"}},
    ]


def _build_adapter(meta: Dict[str, Any], api_key: str) -> Any:
    """按 kind 构建适配器"""
    kind = meta.get("kind", "openai")
    if kind == "echo":
        return EchoAdapter()
    if kind == "openai":
        return UrllibAdapter(api_key=api_key,
                             api_base=meta.get("api_base", "https://api.deepseek.com/v1"))
    if kind == "bridge":
        return WebBridgeAdapter(endpoint=meta.get("endpoint", ""),
                                token=meta.get("token", ""))
    raise ValueError(f"未知适配器类型: {kind}")


def build_fleet_from_config(cfg: Dict[str, Any],
                            api_key: str = "") -> Tuple[AdapterRegistry, ModelFleet]:
    """装配：注册表（model_id→adapter）+ 舰队（健康约束元信息）"""
    registry = AdapterRegistry()
    fleet = ModelFleet()
    for entry in cfg.get("fleet", []):
        mid = entry["model_id"]
        adapter = _build_adapter(entry.get("adapter", {}), api_key)
        registry.register(mid, adapter)
        fleet.register(mid, {"tier": entry.get("tier", Tier.FLASH.value),
                             "elo": float(entry.get("elo", 0.0)),
                             "source": entry.get("source", "official")})
    return registry, fleet


def refresh_elo_from_leaderboard(fleet: ModelFleet, fetcher: Any = None,
                                 default_elo: float = 1250.0) -> int:
    """用 live 排行榜刷新舰队 ELO 分值（帕累托分发的输入）

    职责边界：本函数只负责"分数从哪来"，"用哪个模型"仍由 smart_router 帕累托求解。
      - 收录模型：ELO 刷新为排行榜值
      - 未收录模型：保持 default（不猜测高分）
      - 排行榜被墙：fetcher 内部降级静态快照，不崩溃
    返回刷新的模型数。
    """
    if fetcher is None:
        from leaderboard import LeaderboardFetcher
        fetcher = LeaderboardFetcher()
    refreshed = 0
    for mid in fleet.models:
        elo = fetcher.elo_for(mid, default=default_elo)
        fleet.models[mid]["elo"] = elo
        refreshed += 1
    return refreshed


def load_platform_config(config_path: str = "") -> Dict[str, Any]:
    """读取含 platforms 声明的配置"""
    return load_fleet_config(config_path)


def build_fleet_from_platforms(cfg: Dict[str, Any],
                               tier_default: str = Tier.FLASH.value,
                               elo_default: float = 1250.0
                               ) -> Tuple[AdapterRegistry, ModelFleet]:
    """平台模型自动入舰队（配置驱动，零硬编码代码）

    解耦链路：model_discovery（发现/过滤文本模型）
      → GenericOpenAIAdapter（平台接入，一个类覆盖任何平台）
      → ModelFleet（舰队，供 smart_router 帕累托分发）

      - 平台从 cfg["platforms"] 声明装配（新平台加配置即接入）
      - 每平台 /v1/models 发现 → mode 过滤文本模型 → 自动注册
      - 单平台失败不阻塞其他平台（记录告警继续）
      - 跨平台模型名去重（m-shared 只保留一次）
    返回 (registry, fleet)：registry 为 model_id→adapter 映射。
    """
    from platform_adapter import GenericOpenAIAdapter
    from model_discovery import filter_text_models, fetch_models

    registry = AdapterRegistry()
    fleet = ModelFleet()
    for p in cfg.get("platforms", []):
        name = p["name"]
        try:
            adapter = GenericOpenAIAdapter(
                api_key=p.get("key", ""),
                api_base=p["api_base"],
                key_env=p.get("key_env", ""),
            )
            models = fetch_models(adapter.api_key, adapter.api_base)
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, OSError, KeyError, ValueError) as exc:
            # 单平台失败：记录告警继续其他平台（不静默吞掉）
            print(f"[fleet_setup] 平台 {name} 发现失败(跳过): "
                  f"{type(exc).__name__}: {exc}", flush=True)
            continue
        text_models = filter_text_models(models)
        for m in text_models:
            mid = m["id"]
            if mid in fleet.models:  # 跨平台去重
                continue
            # 每个模型绑定平台通用适配器（同一个 adapter 实例，协议一致）
            registry.register(mid, adapter)
            fleet.register(mid, {"tier": tier_default, "elo": elo_default,
                                 "source": name})
    return registry, fleet


# urllib/json 显式导入
import urllib.error  # noqa: E402
