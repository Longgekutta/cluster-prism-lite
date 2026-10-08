#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jury.py: 多模型评审委员会 (Multi-Model Jury Review Committee)
================================================================
INV-4 幂等性: 相同输入 → 相同输出；temperature=0 固定；
缓存键 = (prompt_hash, model_id)；双层 jury (tier1 快筛 + tier2 旗舰终审)。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class JuryVote:
    """单个评审者的投票"""
    model_id: str
    verdict: str          # APPROVE / REJECT / REVISE
    score: float
    comment: str = ""


@dataclass
class JuryVerdict:
    """委员会终审裁决"""
    verdict: str          # APPROVED / REJECTED / REVISE
    votes: List[JuryVote] = field(default_factory=list)
    quorum_reached: bool = False
    cache_key: str = ""


def prompt_hash(messages: List[Dict[str, Any]]) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return h.hexdigest()


class JuryCommittee:
    """双层评审委员会：tier1 快筛（2 票）→ tier2 旗舰终审（全票）

    llm_call 回调签名: (model_id, messages) -> str
    幂等保障: temperature 固定 0（由调用方引擎保证），缓存键 (prompt_hash, model_id)。
    """

    def __init__(self, llm_call: Callable[[str, List[Dict[str, Any]]], str],
                 tier1_models: List[str], tier2_models: List[str],
                 cache: Optional[Dict[str, JuryVote]] = None,
                 quorum: int = 2):
        self.llm_call = llm_call
        self.tier1_models = tier1_models
        self.tier2_models = tier2_models
        # 幂等缓存键 (prompt_hash, model_id) -> Vote
        self.cache: Dict[Tuple[str, str], JuryVote] = cache if cache is not None else {}
        self.quorum = quorum

    def _vote_once(self, model_id: str, messages: List[Dict[str, Any]]) -> JuryVote:
        """幂等投票：相同 (prompt_hash, model_id) 直接复用缓存"""
        key = (prompt_hash(messages), model_id)
        if key in self.cache:
            return self.cache[key]
        raw = self.llm_call(model_id, messages)
        vote = self._parse_vote(model_id, raw)
        self.cache[key] = vote
        return vote

    @staticmethod
    def _parse_vote(model_id: str, raw: str) -> JuryVote:
        """解析评审输出（宽松：包含关键词即判定）"""
        text = (raw or "").strip()
        upper = text.upper()
        if "APPROVE" in upper:
            verdict, score = "APPROVE", 1.0
        elif "REJECT" in upper:
            verdict, score = "REJECT", 0.0
        elif "REVISE" in upper:
            verdict, score = "REVISE", 0.5
        else:
            verdict, score = "REVISE", 0.5  # 无法解析时保守 REVISE，不静默放行
        return JuryVote(model_id=model_id, verdict=verdict, score=score, comment=text[:200])

    def review(self, messages: List[Dict[str, Any]]) -> JuryVerdict:
        """双层 jury 终审"""
        votes: List[JuryVote] = []
        # tier1 快筛
        for mid in self.tier1_models:
            votes.append(self._vote_once(mid, messages))
        approves = sum(1 for v in votes if v.verdict == "APPROVE")
        rejects = sum(1 for v in votes if v.verdict == "REJECT")
        # tier1 全票 APPROVE → 终审通过
        if len(votes) >= self.quorum and approves == len(votes) and rejects == 0:
            return JuryVerdict(verdict="APPROVED", votes=votes, quorum_reached=True)
        # tier1 任一 REJECT → 进入 tier2 旗舰终审
        if rejects > 0:
            tier2_votes = [self._vote_once(mid, messages) for mid in self.tier2_models]
            votes.extend(tier2_votes)
            t2_approves = sum(1 for v in tier2_votes if v.verdict == "APPROVE")
            if t2_approves == len(tier2_votes):
                return JuryVerdict(verdict="APPROVED", votes=votes, quorum_reached=True)
            if any(v.verdict == "REJECT" for v in tier2_votes):
                return JuryVerdict(verdict="REJECTED", votes=votes, quorum_reached=True)
            return JuryVerdict(verdict="REVISE", votes=votes, quorum_reached=True)
        # tier1 无 REJECT 但非全票 → REVISE
        return JuryVerdict(verdict="REVISE", votes=votes,
                           quorum_reached=len(votes) >= self.quorum)
