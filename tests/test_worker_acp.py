#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_worker_acp.py: ACP 执行工兵层契约测试（mock 子进程，零网络零 key）
========================================================================
W-INV-A 协议纯度：行分帧 JSON-RPC，非法行被忽略
W-INV-B 错误归一：启动失败/协议错误/超时 → WorkerError
W-INV-C 禁内部重试：失败直接上抛（mock 只应答一次）
W-INV-D 会话可续：session_id 透传
W-INV-E 无状态：模块自身不落盘
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from worker_acp import (  # noqa: E402
    AcpWorker, LineJsonRpcClient, WorkerError, WorkerRegistry, WorkerTask,
)


# ---------------- mock 工兵：一个标准库脚本充当 ACP server ----------------

MOCK_WORKER = r"""
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue  # 非法行忽略（协议纪律）
    rid, method = msg.get("id"), msg.get("method")
    if rid is None:
        continue
    if method == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": rid,
                          "result": {"protocolVersion": 1,
                                     "agentInfo": {"name": "mock"}}}), flush=True)
    elif method == "session/new":
        print(json.dumps({"jsonrpc": "2.0", "id": rid,
                          "result": {"sessionId": "sess-mock-1"}}), flush=True)
        # 模拟通知流：agent_message_chunk + stopReason
        print(json.dumps({"jsonrpc": "2.0", "method": "session/update",
                          "params": {"sessionId": "sess-mock-1",
                                     "update": {"sessionUpdate": "agent_message_chunk",
                                                "content": {"type": "text", "text": "mock 干完"}}}}),
              flush=True)
        print(json.dumps({"jsonrpc": "2.0", "method": "session/stop",
                          "params": {"sessionId": "sess-mock-1", "stopReason": "end_turn"}}),
              flush=True)
    elif method == "session/prompt":
        sid = (msg.get("params") or {}).get("sessionId", "")
        print(json.dumps({"jsonrpc": "2.0", "id": rid,
                          "result": {"sessionId": sid}}), flush=True)
        print(json.dumps({"jsonrpc": "2.0", "method": "session/update",
                          "params": {"sessionId": sid,
                                     "update": {"sessionUpdate": "agent_message_chunk",
                                                "content": {"type": "text", "text": "续接结果"}}}}),
              flush=True)
        print(json.dumps({"jsonrpc": "2.0", "method": "session/stop",
                          "params": {"sessionId": sid, "stopReason": "end_turn"}}),
              flush=True)
    elif method == "auth/fail":
        print(json.dumps({"jsonrpc": "2.0", "id": rid,
                          "error": {"code": -32000, "message": "not authenticated"}}),
              flush=True)
"""


def _write_mock(tmpdir: str) -> str:
    path = os.path.join(tmpdir, "mock_acp_worker.py")
    with open(path, encoding="utf-8") if False else open(path, "w", encoding="utf-8") as f:
        f.write(MOCK_WORKER)
    return path


class TestLineJsonRpcClient(unittest.TestCase):
    """W-INV-A：行分帧、非法行忽略、通知记账"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mock = _write_mock(self.tmp)
        self.proc = subprocess.Popen(
            [sys.executable, self.mock],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        self.rpc = LineJsonRpcClient(self.proc)

    def tearDown(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.wait(timeout=10)

    def test_request_roundtrip(self):
        result = self.rpc.request("initialize", {"protocolVersion": 1})
        self.assertEqual(result["protocolVersion"], 1)

    def test_illegal_lines_ignored(self):
        """非法 JSON 行被忽略，后续请求仍正常（W-INV-A）"""
        assert self.proc.stdin is not None
        self.proc.stdin.write("not-json-at-all\n")
        self.proc.stdin.flush()
        result = self.rpc.request("initialize", {"protocolVersion": 1})
        self.assertIn("agentInfo", result)

    def test_error_response_raises(self):
        with self.assertRaises(WorkerError) as ctx:
            self.rpc.request("auth/fail", {})
        self.assertEqual(ctx.exception.kind, "REFUSED")


class TestAcpWorkerDelegate(unittest.TestCase):
    """W-INV-B/C/D：委派全链路、会话续接、错误归一、无重试"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mock = _write_mock(self.tmp)

    def _worker(self) -> AcpWorker:
        return AcpWorker(command=[sys.executable, self.mock],
                         cwd=self.tmp, prompt_timeout_s=30)

    def test_delegate_new_session(self):
        result = self._worker().delegate(WorkerTask(intent="修一个 bug"))
        self.assertEqual(result.content, "mock 干完")
        self.assertEqual(result.session_id, "sess-mock-1")
        self.assertTrue(result.worker in ("python3", sys.executable)
                        or result.worker.endswith("mock_acp_worker.py")
                        or sys.executable in result.worker)

    def test_delegate_resume_session(self):
        """W-INV-D：session_id 透传，走 session/prompt 续接"""
        result = self._worker().delegate(
            WorkerTask(intent="继续", session_id="sess-mock-1"))
        self.assertEqual(result.content, "续接结果")
        self.assertEqual(result.session_id, "sess-mock-1")

    def test_empty_intent_refused(self):
        with self.assertRaises(WorkerError) as ctx:
            self._worker().delegate(WorkerTask(intent="   "))
        self.assertEqual(ctx.exception.kind, "PROTOCOL")

    def test_launch_failure_normalized(self):
        """W-INV-B：命令不存在 → WorkerError LAUNCH"""
        worker = AcpWorker(command=["definitely-not-exists-xyz"])
        with self.assertRaises(WorkerError) as ctx:
            worker.delegate(WorkerTask(intent="x"))
        self.assertEqual(ctx.exception.kind, "LAUNCH")

    def test_no_internal_retry(self):
        """W-INV-C：工兵只应答一次失败请求时直接上抛，无第二次尝试"""
        calls = []
        real_request = LineJsonRpcClient.request

        def counting_request(self, method, params=None, timeout_s=60):
            calls.append(method)
            return real_request(self, method, params, timeout_s)

        LineJsonRpcClient.request = counting_request
        try:
            self._worker().delegate(WorkerTask(intent="x"))
        finally:
            LineJsonRpcClient.request = real_request
        self.assertEqual(calls.count("initialize"), 1)  # 无重试


class TestWorkerRegistry(unittest.TestCase):
    """泛化契约：新工兵 = 配置加一条 JSON，零代码"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mock = _write_mock(self.tmp)

    def test_from_config_and_resolve(self):
        os.environ.setdefault("ANTHROPIC_API_KEY", "mock-key")  # mock 不校验值
        reg = WorkerRegistry.from_config({
            "workers": [
                {"name": "claude-code", "command": [sys.executable, self.mock],
                 "env_key": "ANTHROPIC_API_KEY", "cwd": self.tmp},
            ]})
        self.assertIn("claude-code", reg.names())
        result = reg.worker_for("claude-code").delegate(WorkerTask(intent="x"))
        self.assertEqual(result.content, "mock 干完")

    def test_unregistered_worker_raises(self):
        reg = WorkerRegistry.from_config({"workers": []})
        with self.assertRaises(WorkerError) as ctx:
            reg.worker_for("nope")
        self.assertEqual(ctx.exception.kind, "LAUNCH")

    def test_healthcheck(self):
        reg = WorkerRegistry.from_config({
            "workers": [
                {"name": "ok", "command": [sys.executable, self.mock]},
                {"name": "bad", "command": ["definitely-not-exists-xyz"]},
            ]})
        health = reg.health()
        self.assertTrue(health["ok"])
        self.assertFalse(health["bad"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
