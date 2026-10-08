#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_jury.py: 多模型 jury 评审验收神谕"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jury import JuryCommittee, JuryVerdict, prompt_hash


class TestPromptHash(unittest.TestCase):
    def test_deterministic(self):
        msgs = [{"role": "user", "content": "same"}]
        self.assertEqual(prompt_hash(msgs), prompt_hash(msgs))

    def test_differs(self):
        self.assertNotEqual(
            prompt_hash([{"role": "user", "content": "a"}]),
            prompt_hash([{"role": "user", "content": "b"}]))


class TestParseVote(unittest.TestCase):
    def test_parse_approve(self):
        v = JuryCommittee._parse_vote("m", "APPROVE: good work")
        self.assertEqual(v.verdict, "APPROVE")
        self.assertEqual(v.score, 1.0)

    def test_parse_reject(self):
        v = JuryCommittee._parse_vote("m", "REJECT: broken")
        self.assertEqual(v.verdict, "REJECT")

    def test_parse_unparseable_conservative(self):
        """无法解析时保守 REVISE，不静默放行"""
        v = JuryCommittee._parse_vote("m", "很不错的样子")
        self.assertEqual(v.verdict, "REVISE")


class TestCommittee(unittest.TestCase):
    def _committee(self, responses):
        call_log = []
        def llm_call(mid, msgs):
            call_log.append(mid)
            return responses[mid]
        return JuryCommittee(llm_call=llm_call,
                             tier1_models=["t1a", "t1b"],
                             tier2_models=["t2a"]), call_log

    def test_tier1_unanimous_approve(self):
        c, log = self._committee({"t1a": "APPROVE ok", "t1b": "APPROVE ok"})
        verdict = c.review([{"role": "user", "content": "x"}])
        self.assertEqual(verdict.verdict, "APPROVED")
        self.assertEqual(len(log), 2)  # 不进 tier2

    def test_tier1_reject_goes_tier2(self):
        c, log = self._committee({
            "t1a": "REJECT", "t1b": "APPROVE",
            "t2a": "APPROVE truly good"})
        verdict = c.review([{"role": "user", "content": "x"}])
        self.assertEqual(verdict.verdict, "APPROVED")
        self.assertIn("t2a", log)

    def test_tier2_reject_final_reject(self):
        c, _ = self._committee({
            "t1a": "REJECT", "t1b": "APPROVE",
            "t2a": "REJECT bad"})
        verdict = c.review([{"role": "user", "content": "x"}])
        self.assertEqual(verdict.verdict, "REJECTED")

    def test_idempotent_votes_cached(self):
        """INV-4 幂等：相同 (prompt_hash, model_id) 复用缓存，不重复调用"""
        responses = {"t1a": "APPROVE", "t1b": "APPROVE"}
        c, log = self._committee(responses)
        msgs = [{"role": "user", "content": "same question"}]
        c.review(msgs)
        c.review(msgs)
        self.assertEqual(len(log), 2)  # 第二次全部命中缓存
        self.assertEqual(len(c.cache), 2)

    def test_cache_key_includes_model(self):
        responses = {"t1a": "APPROVE", "t1b": "APPROVE"}
        c, _ = self._committee(responses)
        msgs = [{"role": "user", "content": "q"}]
        c.review(msgs)
        self.assertIn((prompt_hash(msgs), "t1a"), c.cache)
        self.assertIn((prompt_hash(msgs), "t1b"), c.cache)


if __name__ == "__main__":
    unittest.main()
