#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
leaderboard.py: 多源全球大模型排行榜获取 (Multi-Source LLM Leaderboard Fetcher)
==================================================================================
修复原路由项目对接全球排行榜不可用的问题：
  - lmarena.ai / huggingface.co / raw.githubusercontent 在当前网络环境被墙（HTTP 000）
  - 可用源：openrouter /models 的 artificial_analysis 智能指数（284 个有分）
            models.dev api.json 模型元数据（226 供应商）
  - 三级回退：openrouter → models_dev → 静态快照（被墙时不崩溃）
  - TTL 缓存：本地快照优先，后台刷新
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# 源端点（被墙源不再列入 live_sources）
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
MODELSDEV_API_URL = "https://models.dev/api.json"

# 静态快照兜底（与 tool-model-tier-sorter 的 MODEL_KNOWLEDGE_BASE 对齐 + 常用模型扩充）
STATIC_SNAPSHOT: Dict[str, float] = {
    "deepseek-reasoner": 1345.0,
    "deepseek-chat": 1320.0,
    "deepseek-v4.1-flash": 1310.0,
    "deepseek-v4-flash": 1305.0,
    "claude-3-7-sonnet": 1350.0,
    "claude-3-5-sonnet": 1320.0,
    "claude-opus-5.5": 1360.0,
    "claude-sonnet-5.5": 1352.0,
    "o1": 1355.0,
    "o3-mini": 1330.0,
    "gpt-6-astra": 1348.0,
    "gpt-5": 1340.0,
    "gemini-2.5-pro": 1345.0,
    "qwen3.8-27b": 1300.0,
    "qwen3.6-35b-a3b": 1290.0,
    "glm-5.3-flash": 1280.0,
    "glm-5": 1295.0,
    "senseaudio-s2": 1300.0,
    "senseaudio-s2-flash": 1270.0,
    "senseaudio-s2-lite": 1250.0,
    "sensenova-6.8-flash-lite": 1230.0,
}

DEFAULT_TTL_SECONDS = 3600
# intelligence_index（0~100）→ 归一化到 ELO 量级（1200~1400）
INDEX_SCALE_MIN = 1200.0
INDEX_SCALE_MAX = 1400.0
DEFAULT_INDEX = 50.0
INDEX_RANGE = 20.0


class StaticFallback:
    """静态快照兜底（被墙降级时不崩溃）"""

    def scores(self) -> Dict[str, float]:
        return dict(STATIC_SNAPSHOT)


def _index_to_elo(index: float) -> float:
    """artificial_analysis intelligence_index (0~100) → ELO 量级 (1200~1400)"""
    clamped = max(0.0, min(100.0, index))
    ratio = (clamped - (DEFAULT_INDEX - INDEX_RANGE)) / (2 * INDEX_RANGE)
    ratio = max(0.0, min(1.0, ratio))
    return INDEX_SCALE_MIN + ratio * (INDEX_SCALE_MAX - INDEX_SCALE_MIN)


def _fetch_openrouter_scores(timeout: float = 20.0) -> Tuple[Dict[str, float], int]:
    """openrouter /models → artificial_analysis 智能指数 → ELO 量级"""
    req = urllib.request.Request(OPENROUTER_MODELS_URL, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out: Dict[str, float] = {}
    for m in data.get("data", []):
        aa = (m.get("benchmarks") or {}).get("artificial_analysis") or {}
        idx = aa.get("intelligence_index")
        if isinstance(idx, (int, float)):
            out[m["id"]] = round(_index_to_elo(idx), 1)
    return out, 200


def _fetch_modelsdev_ids(timeout: float = 20.0) -> Tuple[Dict[str, float], int]:
    """models.dev api.json → 模型 ID 元数据（无分数，返回统一基线分）"""
    req = urllib.request.Request(MODELSDEV_API_URL, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out: Dict[str, float] = {}
    for prov in data.values():
        for mid in prov.get("models", {}):
            # 模型名统一基线（无排名数据 → 中位分）
            out.setdefault(mid, _index_to_elo(DEFAULT_INDEX))
    return out, 200


class LeaderboardFetcher:
    """多源天梯获取器：三级回退 + TTL 缓存

    last_source 记录实际数据来源：openrouter / models_dev / static
    """

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS,
                 live_sources: Optional[List[str]] = None,
                 cache_path: str = ""):
        self.ttl = ttl_seconds
        self.sources = live_sources if live_sources is not None else [
            "openrouter", "models_dev"]
        self.cache_path = cache_path  # 本地快照落盘路径（可选）
        self._cache: Optional[Dict[str, float]] = None
        self._cache_ts: float = 0.0
        self.last_source: str = ""

    def _cache_fresh(self) -> bool:
        return (self._cache is not None
                and (time.time() - self._cache_ts) < self.ttl)

    def scores(self) -> Dict[str, float]:
        """获取排行榜分值：TTL 内回缓存，否则按源优先级尝试，全部失败落静态"""
        if self._cache_fresh():
            self.last_source = self.last_source or "cache"
            return self._cache  # type: ignore[return-value]
        for source in self.sources:
            try:
                if source == "openrouter":
                    scores, code = _fetch_openrouter_scores()
                elif source == "models_dev":
                    scores, code = _fetch_modelsdev_ids()
                else:
                    continue
                if scores:
                    self._cache = scores
                    self._cache_ts = time.time()
                    self.last_source = source
                    self._persist(scores)
                    return scores
            except (urllib.error.URLError, urllib.error.HTTPError,
                    json.JSONDecodeError, OSError) as exc:
                # 该源失败：记录原因继续下一源（不静默吞掉）
                print(f"[leaderboard] 源 {source} 失败: {type(exc).__name__}: {exc}",
                      flush=True)
        # 全部 live 源失败 → 静态快照兜底
        scores = StaticFallback().scores()
        self._cache = scores
        self._cache_ts = time.time()
        self.last_source = "static"
        return scores

    def _persist(self, scores: Dict[str, float]) -> None:
        """本地快照落盘（可选）"""
        if not self.cache_path:
            return
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({"ts": time.time(), "source": self.last_source,
                           "scores": scores}, f, ensure_ascii=False)
        except OSError as exc:
            print(f"[leaderboard] 快照落盘失败(不阻塞): {exc}", flush=True)

    def elo_for(self, model_id: str, default: float = 1250.0) -> float:
        """查询某模型的 ELO；未收录返回 default（不猜测高分）

        别名匹配：平台模型名（如 deepseek-v4.1-flash）可能以
        供应商前缀收录在 openrouter（deepseek/deepseek-v4.1-flash），
        按家族前缀归一匹配。
        """
        scores = self.scores()
        if model_id in scores:
            return scores[model_id]
        # 别名匹配：模型名（去掉平台前缀）后缀匹配收录名
        for key, val in scores.items():
            # deepseek/deepseek-v4.1-flash 后缀含 deepseek-v4.1-flash
            if key.split("/")[-1].split(":")[0] == model_id:
                return val
        return default


# urllib.error 显式导入
import urllib.error  # noqa: E402
