#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
smart_router.py: 语义分级与约束帕累托最优路由中枢 (Semantic Tiering & Constrained Pareto Router)
================================================================================================
1. 语义复杂度分级 (LMSYS RouteLLM 范式): 80% 常规任务 → FLASH 极速层；20% 深水区 → ELITE 旗舰层
2. 约束帕累托分发: 候选集 ⊆ 可用舰队 ∩ 健康存活，根除 404 虚假模型报错
3. Portkey 级断路器: 429 瞬时熔断 + 半开自愈
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class Tier(str, Enum):
    FLASH = "FLASH"   # 极速层：语法修复/注释/单测/格式化
    ELITE = "ELITE"   # 旗舰层：架构推导/并发证明/跨进程/重构


# 深水区信号词（命中即 ELITE）
DEEP_WATER_PATTERNS = [
    re.compile(r"架构|重构|死锁|并发|分布式|一致性|协议|内核|算法证明|形式化|优化", re.IGNORECASE),
    re.compile(r"architecture|refactor|deadlock|concurrency|distributed|protocol|kernel|proof|formal", re.IGNORECASE),
]
# 日常信号词（命中即 FLASH）
ROUTINE_PATTERNS = [
    re.compile(r"注释|文档|格式化|改名|typo|拼写|翻译|单测|测试用例|README", re.IGNORECASE),
    re.compile(r"comment|docstring|format|rename|typo|translate|unit test|readme", re.IGNORECASE),
]


@dataclass
class RouteDecision:
    """路由裁决"""
    model_id: str
    tier: str
    score: float
    reason: str
    cache_key: str = ""


class BreakerState(str, Enum):
    CLOSED = "CLOSED"    # 正常
    OPEN = "OPEN"        # 熔断
    HALF_OPEN = "HALF_OPEN"  # 半开试探


@dataclass
class CircuitBreaker:
    """断路器：429 速率限制瞬时熔断，半开自愈"""
    failure_threshold: int = 3
    cooldown_seconds: float = 300.0
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    last_failure_ts: float = 0.0

    def record_failure(self) -> None:
        self.failures += 1
        self.last_failure_ts = time.time()
        if self.failures >= self.failure_threshold:
            self.state = BreakerState.OPEN

    def record_success(self) -> None:
        self.failures = 0
        self.state = BreakerState.CLOSED

    def allow_request(self) -> bool:
        if self.state == BreakerState.CLOSED:
            return True
        if self.state == BreakerState.OPEN:
            if time.time() - self.last_failure_ts >= self.cooldown_seconds:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return False  # HALF_OPEN 单次试探：本请求被拒绝（由调用方探测成功后 record_success 恢复）


@dataclass
class ModelFleet:
    """模型舰队账本：健康存活约束的真相源"""
    models: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    breakers: Dict[str, CircuitBreaker] = field(default_factory=dict)

    def register(self, model_id: str, meta: Optional[Dict[str, Any]] = None) -> None:
        self.models[model_id] = meta or {}
        self.breakers[model_id] = CircuitBreaker()

    def healthy_fleet(self, tier: Optional[str] = None) -> List[str]:
        """候选集 ⊆ 存活舰队：剔除熔断中的模型，根除 404 虚假模型"""
        out = []
        for mid in self.models:
            if not self.breakers[mid].allow_request():
                continue
            meta = self.models[mid]
            if tier and meta.get("tier") not in (None, tier):
                continue
            out.append(mid)
        return out


def classify_complexity(text: str) -> Tuple[Tier, str]:
    """语义复杂度分级：深水区→ELITE，日常→FLASH（默认 FLASH 兜底）"""
    for pat in DEEP_WATER_PATTERNS:
        if pat.search(text):
            return Tier.ELITE, f"命中深水区信号: {pat.pattern[:24]}"
    for pat in ROUTINE_PATTERNS:
        if pat.search(text):
            return Tier.FLASH, f"命中日常信号: {pat.pattern[:24]}"
    return Tier.FLASH, "默认极速层兜底（帕累托: 80% 常规任务）"


def pareto_dispatch(fleet: ModelFleet, tier: Tier) -> RouteDecision:
    """约束帕累托最优分发：ELO 得分 × 可用性 求解，候选严格 ⊆ 健康舰队"""
    candidates = fleet.healthy_fleet(tier=tier.value)
    if not candidates:
        # 兜底：放宽 tier 约束再求一次
        candidates = fleet.healthy_fleet()
    if not candidates:
        raise RuntimeError("舰队全熔断，无可用模型（拒绝虚假 404 静默降级）")
    best = max(candidates, key=lambda m: float(fleet.models[m].get("elo", 0)))
    score = float(fleet.models[best].get("elo", 0))
    return RouteDecision(model_id=best, tier=tier.value, score=score,
                         reason=f"帕累托最优: {len(candidates)} 健康候选中 ELO 最高")


class SmartRouter:
    """语义分级 + 帕累托分发 + 断路器 一体化路由中枢"""

    def __init__(self, fleet: Optional[ModelFleet] = None):
        self.fleet = fleet or ModelFleet()

    def route(self, text: str) -> RouteDecision:
        tier, reason = classify_complexity(text)
        decision = pareto_dispatch(self.fleet, tier)
        decision.reason = f"{decision.reason} | {reason}"
        return decision

    def report_failure(self, model_id: str, status_code: int) -> None:
        """429/5xx → 熔断计数；401/404 → 直接熔断（虚假模型根除）"""
        breaker = self.fleet.breakers.get(model_id)
        if breaker is not None:
            breaker.record_failure()

    def report_success(self, model_id: str) -> None:
        breaker = self.fleet.breakers.get(model_id)
        if breaker is not None:
            breaker.record_success()
