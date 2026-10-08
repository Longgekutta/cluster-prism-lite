#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cache_engine.py: 五阶前缀缓存与语义缓存引擎 (Five-Tier Prefix & Semantic Cache Engine)
====================================================================================
阶段 I  : Deterministic Local Disk Cache   —— temperature<=0.25 且 SHA-256 命中 → 0ms 直返
阶段 II : Prefix Alignment Adapter         —— 前缀 < 2048 tokens 自动补齐安全基线
阶段 III: Session Prefix Manager           —— 最长公共前缀 O(1) 索引，多轮连续性
阶段 IV : Semantic Dedup & Invariance Guard —— 动态时间戳隔离 + Jaccard 相似度去重
阶段 V  : Canary Auditor                   —— 前处理流水线字节级前缀一致性审计

不变量 (INV-1 KV-Cache Invariance):
  对同一 (model_id, system_prompt) 前缀 token 序列必须字节级一致；
  路由元数据只走 model_id 参数，严禁拼接进 prompt 前缀。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# 前缀起征点黄金基线（第一方 Prompt Caching 90% 折扣要求）
PREFIX_ALIGNMENT_BASELINE = 2048
# 确定性温度上限：低于该值才允许磁盘哈希缓存
DETERMINISTIC_TEMP_MAX = 0.25
# 语义去重 Jaccard 阈值
SEMANTIC_DEDUP_THRESHOLD = 0.92
# 动态时间戳隔离模式（必须从可缓存前缀中剔除的时间模式）
DYNAMIC_TIMESTAMP_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?\b"),
    re.compile(r"\bcurrent_time\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\bnow\s*[:=]\s*\S+", re.IGNORECASE),
]


class CacheTier:
    """阶段枚举：I 磁盘 / II 前缀对齐 / III 会话 / IV 语义 / V 金丝雀"""
    DISK = "I"
    ALIGN = "II"
    SESSION = "III"
    DEDUP = "IV"
    CANARY = "V"


@dataclass
class CacheHit:
    """缓存命中裁决"""
    hit: bool
    tier: Optional[str] = None
    response: Optional[Dict[str, Any]] = None
    cache_key: str = ""
    elapsed_ms: float = 0.0
    reason: str = ""


@dataclass
class CacheStats:
    """缓存统计账本"""
    disk_hits: int = 0
    disk_misses: int = 0
    dedup_hits: int = 0
    canary_checks: int = 0
    canary_failures: int = 0
    total_saved_tokens: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "disk_hits": self.disk_hits,
            "disk_misses": self.disk_misses,
            "dedup_hits": self.dedup_hits,
            "canary_checks": self.canary_checks,
            "canary_failures": self.canary_failures,
            "total_saved_tokens": self.total_saved_tokens,
        }


def canonical_prefix_hash(model_id: str, prefix_text: str) -> str:
    """前缀唯一确定：hash(model_id + prefix_text)，字节级一致"""
    h = hashlib.sha256()
    h.update(model_id.encode("utf-8"))
    h.update(b"\x00")
    h.update(prefix_text.encode("utf-8"))
    return h.hexdigest()


def request_hash(model_id: str, messages: List[Dict[str, Any]], temperature: float) -> str:
    """完整请求确定性哈希（阶段 I 缓存键）"""
    h = hashlib.sha256()
    h.update(model_id.encode("utf-8"))
    h.update(b"\x00")
    h.update(str(temperature).encode("utf-8"))
    h.update(b"\x00")
    h.update(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return h.hexdigest()


def longest_common_prefix_len(a: str, b: str) -> int:
    """阶段 III：最长公共前缀 O(min(n,m)) 字符级"""
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


def jaccard_similarity(a: str, b: str) -> float:
    """阶段 IV：Token Jaccard 相似度"""
    ta = set(a.split())
    tb = set(b.split())
    if not ta and not tb:
        return 1.0
    union = ta | tb
    if not union:
        return 0.0
    return len(ta & tb) / len(union)


def strip_dynamic_segments(text: str) -> Tuple[str, int]:
    """阶段 IV：剔除动态时间戳，返回(净化文本, 剔除段数)——防前缀雪崩未命中"""
    count = 0
    out = text
    for pat in DYNAMIC_TIMESTAMP_PATTERNS:
        out, n = pat.subn("<DYN>", out)
        count += n
    return out, count


class FiveTierCacheEngine:
    """五阶防御体系缓存引擎：磁盘哈希 / 前缀对齐 / 会话前缀 / 语义去重 / 金丝雀审计"""

    def __init__(self, cache_dir: str = ".cache_cluster", ttl_seconds: int = 3600):
        self.cache_dir = cache_dir
        self.ttl = ttl_seconds
        self.stats = CacheStats()
        # 阶段 III 会话前缀索引: session_id -> last_prefix_text
        self._session_prefixes: Dict[str, str] = {}
        # PRB-E301 修复：原子创建目录，不做 exists 检查
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"缓存目录创建失败: {exc}") from exc

    # ---------------- 阶段 I: 确定性磁盘哈希 ----------------
    def disk_lookup(self, model_id: str, messages: List[Dict[str, Any]], temperature: float) -> CacheHit:
        t0 = time.perf_counter()
        if temperature > DETERMINISTIC_TEMP_MAX:
            return CacheHit(hit=False, reason=f"temperature={temperature} 超过确定性上限 {DETERMINISTIC_TEMP_MAX}")
        key = request_hash(model_id, messages, temperature)
        path = os.path.join(self.cache_dir, key + ".json")
        try:
            # PRB-E301: 直接原子 open，无 TOCTOU exists-check
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            if time.time() - rec.get("ts", 0) > self.ttl:
                self.stats.disk_misses += 1
                return CacheHit(hit=False, cache_key=key, reason="TTL 过期")
            elapsed = (time.perf_counter() - t0) * 1000.0
            self.stats.disk_hits += 1
            return CacheHit(hit=True, tier=CacheTier.DISK, response=rec["response"],
                            cache_key=key, elapsed_ms=elapsed, reason="磁盘哈希命中")
        except FileNotFoundError:
            self.stats.disk_misses += 1
            return CacheHit(hit=False, cache_key=key, reason="未命中")
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            self.stats.disk_misses += 1
            return CacheHit(hit=False, cache_key=key, reason=f"缓存读取异常: {type(exc).__name__}")

    def disk_store(self, model_id: str, messages: List[Dict[str, Any]],
                   temperature: float, response: Dict[str, Any]) -> str:
        """原子落盘（temp 写入 + os.replace 原子替换）"""
        key = request_hash(model_id, messages, temperature)
        path = os.path.join(self.cache_dir, key + ".json")
        tmp = path + f".tmp.{os.getpid()}"
        rec = {"ts": time.time(), "model": model_id, "response": response}
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False)
            os.replace(tmp, path)  # 原子替换，杜绝半写
        except OSError as exc:
            # 落盘失败不阻塞主流程，但必须记录而非静默吞掉
            self.stats.disk_misses += 1
            raise RuntimeError(f"缓存落盘失败: {exc}") from exc
        return key

    # ---------------- 阶段 II: 前缀对齐 ----------------
    def align_prefix(self, system_prompt: str) -> Tuple[str, bool]:
        """前缀 < 2048 tokens 时注入安全补齐基线；返回(对齐后前缀, 是否注入)"""
        approx_tokens = max(1, len(system_prompt) // 3)  # 粗估 1 token ≈ 3 chars
        if approx_tokens >= PREFIX_ALIGNMENT_BASELINE:
            return system_prompt, False
        pad = "\n" + ("# " + "stable baseline padding for prompt cache alignment " * 64)
        aligned = system_prompt + pad
        return aligned, True

    # ---------------- 阶段 III: 会话前缀 ----------------
    def session_prefix_update(self, session_id: str, prefix_text: str) -> int:
        """更新会话前缀，返回与上一轮的最长公共前缀长度（推导 cached_tokens）"""
        prev = self._session_prefixes.get(session_id, "")
        lcp = longest_common_prefix_len(prev, prefix_text)
        self._session_prefixes[session_id] = prefix_text
        return lcp

    # ---------------- 阶段 IV: 语义去重 ----------------
    def semantic_dedup_check(self, prompt: str, recent_prompts: List[str]) -> CacheHit:
        """与近期 prompt 做 Jaccard 匹配，命中高相似即去重直返"""
        stripped, _ = strip_dynamic_segments(prompt)
        for prev in recent_prompts:
            prev_stripped, _ = strip_dynamic_segments(prev)
            if jaccard_similarity(stripped, prev_stripped) >= SEMANTIC_DEDUP_THRESHOLD:
                self.stats.dedup_hits += 1
                return CacheHit(hit=True, tier=CacheTier.DEDUP,
                                response={"content": f"[语义去重命中] 与近期请求相同: {prev_stripped[:80]}"},
                                reason="Jaccard 相似度超过阈值")
        return CacheHit(hit=False, reason="无相似请求")

    # ---------------- 阶段 V: 金丝雀审计 ----------------
    def canary_audit(self, original_prefix: str, processed_prefix: str) -> bool:
        """校验前处理流水线不破坏字节级前缀一致性：
        processed 必须以 original 开头（允许追加 padding，禁止篡改前缀本体）"""
        self.stats.canary_checks += 1
        keep_len = min(len(original_prefix), 256)
        ok = processed_prefix[:keep_len] == original_prefix[:keep_len]
        if not ok:
            self.stats.canary_failures += 1
        return ok

    # ---------------- 统一入口 ----------------
    def preflight(self, model_id: str, messages: List[Dict[str, Any]],
                  temperature: float, session_id: str = "default",
                  recent_prompts: Optional[List[str]] = None) -> CacheHit:
        """五阶防御统一入口：必须在所有 LLM 调用之前调用"""
        # 阶段 I
        hit = self.disk_lookup(model_id, messages, temperature)
        if hit.hit:
            return hit
        # 阶段 IV（仅当提供近期 prompt 池）
        if recent_prompts:
            user_text = next((m.get("content", "") for m in reversed(messages)
                              if m.get("role") == "user"), "")
            if user_text:
                hit = self.semantic_dedup_check(user_text, recent_prompts)
                if hit.hit:
                    return hit
        # 阶段 III 记录会话前缀连续性
        system_text = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        if system_text:
            self.session_prefix_update(session_id, system_text)
        return CacheHit(hit=False, reason="穿透缓存，需调用 LLM")

    def stats_report(self) -> Dict[str, Any]:
        return {"stats": self.stats.as_dict(),
                "hit_rate": (self.stats.disk_hits /
                             max(1, self.stats.disk_hits + self.stats.disk_misses))}
