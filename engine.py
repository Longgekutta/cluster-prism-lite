#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
engine.py: 七阶编排引擎核心 (Seven-Phase Cluster Orchestration Engine)
========================================================================
Phase 1: 意图转译与不变量编译    (intent → invariants)
Phase 2: 全球算力拓扑分级        (semantic tiering FLASH/ELITE)
Phase 3: 上下文感知与前缀对齐    (prefix alignment, INV-1)
Phase 4: 物理安全检查点          (git checkpoint, INV-3)
Phase 5: 约束帕累托分发与执行    (pareto dispatch + LLM call)
Phase 6: 独立证据门禁            (AST precheck + TDD sandbox)
Phase 7: 认知蒸馏与验收          (jury review, INV-4)

五阶缓存拦截在所有 LLM 调用之前（cache_engine.preflight）。
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from cache_engine import FiveTierCacheEngine, canonical_prefix_hash
from smart_router import ModelFleet, SmartRouter, Tier
from jury import JuryCommittee, JuryVerdict
from checkpoint import GitCheckpointManager, CheckpointError
from ast_precheck import precheck
from tdd_sandbox import run_python_tests

DEFAULT_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL_FLASH = "deepseek-chat"
DEFAULT_MODEL_ELITE = "deepseek-reasoner"
CALL_TIMEOUT_SECONDS = 60


def resolve_api_key() -> str:
    """密钥走环境变量或 secret.env，严禁硬编码（PRB-E503）"""
    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if key:
        return key
    secret_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secret.env")
    try:
        with open(secret_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("DEEPSEEK_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""


@dataclass
class PhaseReport:
    """七阶管线阶段账本"""
    phase: str
    ok: bool
    detail: str = ""


@dataclass
class PipelineResult:
    """管线终局结果"""
    ok: bool
    answer: str = ""
    reports: List[PhaseReport] = field(default_factory=list)
    cache_hit: bool = False
    route_tier: str = ""
    model_id: str = ""
    checkpoint_ref: str = ""

    def add(self, phase: str, ok: bool, detail: str = "") -> None:
        self.reports.append(PhaseReport(phase, ok, detail))


def llm_call_via_urllib(api_key: str, model_id: str, messages: List[Dict[str, Any]],
                        api_base: str = DEFAULT_API_BASE,
                        temperature: float = 0.1,
                        max_tokens: int = 2000) -> str:
    """库级直连 OpenAI 协议端点（纯 urllib 标准库，无常驻网关进程）"""
    body = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        api_base.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=CALL_TIMEOUT_SECONDS) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


class ClusterEngine:
    """七阶编排引擎：单进程 asyncio 友好，串联回全部能力模块"""

    def __init__(self, api_key: Optional[str] = None,
                 cache: Optional[FiveTierCacheEngine] = None,
                 router: Optional[SmartRouter] = None,
                 fleet: Optional[ModelFleet] = None,
                 workdir: str = "."):
        self.api_key = api_key if api_key is not None else resolve_api_key()
        self.cache = cache or FiveTierCacheEngine()
        self.fleet = fleet or ModelFleet()
        # 默认舰队：FLASH + ELITE 双层
        if not self.fleet.models:
            self.fleet.register(DEFAULT_MODEL_FLASH, {"tier": Tier.FLASH.value, "elo": 100.0})
            self.fleet.register(DEFAULT_MODEL_ELITE, {"tier": Tier.ELITE.value, "elo": 130.0})
        self.router = router or SmartRouter(self.fleet)
        self.checkpoints = GitCheckpointManager(workdir)

    def _raw_llm(self, model_id: str, messages: List[Dict[str, Any]]) -> str:
        """底层 LLM 调用（jury 与管线共用）"""
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY 未配置（环境变量或 secret.env）")
        return llm_call_via_urllib(self.api_key, model_id, messages)

    def run(self, intent: str, session_id: str = "default",
            target_files: Optional[List[str]] = None,
            oracle_cmd: Optional[str] = None,
            do_checkpoint: bool = False) -> PipelineResult:
        """七阶管线主入口"""
        result = PipelineResult(ok=False)
        messages = [
            {"role": "system", "content": "You are a precise engineering cluster worker."},
            {"role": "user", "content": intent},
        ]

        # ---- Phase 1: 意图转译 ----
        if not intent.strip():
            result.add("Phase1", False, "空意图")
            return result
        result.add("Phase1", True, f"意图定格: {intent[:60]}")

        # ---- Phase 2: 语义分级路由 ----
        try:
            decision = self.router.route(intent)
        except RuntimeError as exc:
            result.add("Phase2", False, str(exc))
            return result
        result.route_tier = decision.tier
        result.model_id = decision.model_id
        result.add("Phase2", True,
                   f"tier={decision.tier} model={decision.model_id} ({decision.reason})")

        # ---- Phase 3: 上下文与前缀对齐（五阶缓存拦截在最前）----
        hit = self.cache.preflight(decision.model_id, messages, temperature=0.0,
                                   session_id=session_id)
        if hit.hit and hit.response is not None:
            result.cache_hit = True
            result.ok = True
            result.answer = hit.response.get("content", "")
            result.add("Phase3", True, f"缓存命中 tier={hit.tier} {hit.elapsed_ms:.2f}ms")
            result.add("Phase5", True, "缓存直返，跳过 LLM 调用")
            return result
        aligned_prefix, injected = self.cache.align_prefix(
            next(m["content"] for m in messages if m["role"] == "system"))
        # INV-1: 前缀一致性金丝雀审计
        if not self.cache.canary_audit(
                next(m["content"] for m in messages if m["role"] == "system"),
                aligned_prefix[:len(next(m["content"] for m in messages if m["role"] == "system"))]):
            result.add("Phase3", False, "金丝雀审计失败：前缀被破坏")
            return result
        result.add("Phase3", True,
                   f"前缀对齐 {'已注入基线' if injected else '无需注入'}")

        # ---- Phase 4: 物理安全检查点 ----
        if do_checkpoint:
            try:
                cp = self.checkpoints.snapshot(f"pre-task: {intent[:50]}",
                                               paths=target_files)
                result.checkpoint_ref = cp.ref
                result.add("Phase4", True, f"checkpoint {cp.ref[:12]}")
            except CheckpointError as exc:
                result.add("Phase4", False, f"checkpoint 失败: {exc}")
                return result
        else:
            result.add("Phase4", True, "跳过 checkpoint（do_checkpoint=False）")

        # ---- Phase 5: 帕累托分发与执行 ----
        try:
            answer = self._raw_llm(decision.model_id, messages)
            self.router.report_success(decision.model_id)
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError,
                KeyError, OSError, RuntimeError) as exc:
            self.router.report_failure(decision.model_id, getattr(exc, "code", 0))
            result.add("Phase5", False, f"LLM 调用失败: {type(exc).__name__}: {exc}")
            return result
        result.answer = answer
        result.add("Phase5", True, f"模型 {decision.model_id} 返回 {len(answer)} 字符")
        # LLM 结果落盘缓存（确定性 temp=0 请求，下次 0ms 直返）
        try:
            self.cache.disk_store(decision.model_id, messages, 0.0, {"content": answer})
        except RuntimeError as exc:
            result.add("Phase5", False, f"缓存落盘失败(不阻塞): {exc}")
            return result

        # ---- Phase 6: 独立证据门禁（AST 预检 + TDD 沙箱）----
        # AST 预检仅针对代码产物：以 ```python 围栏标记的代码块或明确代码意图
        code_blocks = self._extract_code_blocks(answer)
        if code_blocks:
            for block in code_blocks:
                ast_report = precheck(block)
                if not ast_report.ok:
                    result.add("Phase6", False, f"AST 预检拦截: {ast_report.summary()}")
                    return result
        if target_files and oracle_cmd:
            sandbox = run_python_tests(oracle_cmd, workdir=".")
            if not sandbox.ok:
                result.add("Phase6", False,
                           f"TDD 沙箱失败 exit={sandbox.exit_code}: {sandbox.stderr[:200]}")
                return result
            result.add("Phase6", True,
                       f"沙箱通过 exit=0 ({sandbox.elapsed_seconds:.2f}s)")
        else:
            result.add("Phase6", True, "无代码产物，跳过沙箱")

        result.ok = True
        result.add("Phase7", True, "管线闭环完成")
        return result

    @staticmethod
    def _extract_code_blocks(text: str) -> List[str]:
        """从模型回答中提取 ```python 代码块；无围栏则视为纯文本产物"""
        blocks: List[str] = []
        lines = text.splitlines()
        in_block = False
        buf: List[str] = []
        for line in lines:
            stripped = line.strip()
            if not in_block and stripped.startswith("```python"):
                in_block = True
                buf = []
            elif in_block and stripped.startswith("```"):
                blocks.append("\n".join(buf))
                in_block = False
            elif in_block:
                buf.append(line)
        return blocks

    def review(self, intent: str, answer: str,
               tier1_models: List[str], tier2_models: List[str]) -> JuryVerdict:
        """Phase 7 可选 jury 评审（幂等，INV-4）"""
        committee = JuryCommittee(
            llm_call=lambda mid, msgs: self._raw_llm(mid, msgs),
            tier1_models=tier1_models, tier2_models=tier2_models)
        messages = [
            {"role": "system", "content": "You are a jury reviewer. Reply APPROVE / REJECT / REVISE with a short comment."},
            {"role": "user", "content": f"Intent:\n{intent}\n\nCandidate answer:\n{answer}"},
        ]
        return committee.review(messages)


# urllib.error 显式导入
import urllib.error  # noqa: E402
