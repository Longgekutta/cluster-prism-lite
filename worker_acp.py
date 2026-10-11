#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
worker_acp.py: ACP 执行工兵层 (Agent Client Protocol Worker Layer)
====================================================================
职责单一：把"执行工兵"（Claude Code 等 ACP 兼容 agent）当作集群的外包工兵，
通过标准 ACP v1 协议（line-delimited JSON-RPC over stdio）委派任务并收集结果。

与其它模块的分工（各有思路，不互相侵入）：
  - platform_adapter.py : 管"用哪个 AI 平台模型"（HTTP 调用，无状态）
  - worker_acp.py       : 管"哪个 agent 去改代码"（子进程委派，会话可续）
  - engine.py           : 管七阶编排（路由/缓存/门禁），只依赖本层接口

设计不变量（worker_tests 契约断言）：
  W-INV-A 协议纯度：只说 line-delimited JSON-RPC，一行一消息，禁 emit 非法行
  W-INV-B 错误归一：一切失败（启动失败/超时/协议错误）→ WorkerError
  W-INV-C 禁内部重试：委派失败直接上抛，重试由上层引擎控制
  W-INV-D 会话可续：session_id 透传（对应 mesh 白皮书"长程连续态"映射）
  W-INV-E 无状态进程：本模块不落盘、不缓存，进程生命周期由调用方管理

泛化契约：不硬编码 Claude Code——只要任何程序能说 ACP 协议
（initialize / session/prompt / shutdown），即可作为工兵接入。
Claude Code 经 claude-agent-acp 桥接入（官方 Zed 生态适配器）。
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# ---------------- 规范化错误（W-INV-B） ----------------

class WorkerError(Exception):
    """所有工兵委派失败统一归一为此类型

    KINDS 与 CanonicalError 对齐（engine 可按同一策略处理）：
      LAUNCH   : 工兵可执行文件不存在/无法启动
      TIMEOUT  : 委派超时（不做内部重试，W-INV-C）
      PROTOCOL : ACP 协议错误（非法响应/握手失败）
      REFUSED  : 工兵明确拒绝（auth 失败/权限不足）
    """
    KINDS = ("LAUNCH", "TIMEOUT", "PROTOCOL", "REFUSED")

    def __init__(self, kind: str, message: str, stderr: str = ""):
        if kind not in self.KINDS:
            raise ValueError(f"未知工兵错误类型: {kind}，必须是 {self.KINDS} 之一")
        self.kind = kind
        self.stderr = stderr
        super().__init__(f"[{kind}] {message}")


# ---------------- 委派请求/结果 ----------------

@dataclass(frozen=True)
class WorkerTask:
    """一次委派任务：intent 是唯一必需字段

    session_id : 工兵侧会话 id（长程连续态，W-INV-D）；空 = 新会话
    cwd        : 工兵工作目录（黑盒任务容器，对应 mesh 白皮书设计）
    timeout_s  : 委派超时秒数
    """
    intent: str
    session_id: str = ""
    cwd: str = ""
    timeout_s: int = 300


@dataclass(frozen=True)
class WorkerResult:
    """委派结果：工兵最终文本 + 工兵侧会话 id（供下次续接）"""
    content: str
    session_id: str
    worker: str


# ---------------- 行分帧 JSON-RPC 传输（W-INV-A） ----------------

class LineJsonRpcClient:
    """stdin/stdout 行分帧 JSON-RPC 客户端

    一个 subprocess 一条通道：每行一条 JSON-RPC 2.0 消息（与 dsh-sdk-protocol
    同款分帧规则——带 id+method 是请求，仅 id 是响应，仅 method 是通知）。
    通知（session/update 等）被收集进 self.notifications 供调试，不阻塞请求。
    """

    def __init__(self, proc: subprocess.Popen):
        self._proc = proc
        self.notifications: List[Dict[str, Any]] = []
        # 非阻塞读取状态挂在实例上（不随生成器丢弃——挂死根因教训）
        self._buf = b""
        self._fd: Optional[int] = None
        self._eof = False

    def request(self, method: str, params: Optional[Dict[str, Any]] = None,
                timeout_s: int = 60) -> Dict[str, Any]:
        """发送请求并等待同 id 响应；失败抛 WorkerError（W-INV-B）"""
        req_id = uuid.uuid4().int >> 64
        line = json.dumps({"jsonrpc": "2.0", "id": req_id,
                           "method": method, "params": params or {}},
                          ensure_ascii=False)
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise WorkerError("PROTOCOL", f"worker stdin closed: {exc}",
                              stderr=self._read_stderr()) from exc
        for msg in self._iter_messages(timeout_s):
            if msg.get("id") == req_id:
                if "error" in msg:
                    err = msg["error"]
                    kind = "REFUSED" if err.get("code") in (-32000, -32600) else "PROTOCOL"
                    raise WorkerError(kind, str(err.get("message", err))[:300],
                                      stderr=self._read_stderr())
                return msg.get("result", {})
            if "id" not in msg and "method" in msg:
                self.notifications.append(msg)  # 通知记账，不阻塞
        raise WorkerError("TIMEOUT", f"no response in {timeout_s}s",
                          stderr=self._read_stderr())

    # ---- 非阻塞读取：非阻塞 os.read + 自管行缓冲 ----
    # 关键教训：text=True 的 readline() 走 Python 用户态缓冲，selectors 监听
    # 底层 fd——数据已在缓冲区时 select 永远不亮（挂死根因）。统一用
    # 非阻塞 os.read 自管行缓冲，读取路径单一，缓冲一致性天然成立。

    def _make_nonblocking(self) -> int:
        import fcntl as _fcntl
        if self._fd is None:
            fd = self._proc.stdout.fileno()
            _fcntl.fcntl(fd, _fcntl.F_SETFL,
                         _fcntl.fcntl(fd, _fcntl.F_GETFL) | os.O_NONBLOCK)
            self._fd = fd
        return self._fd

    def _iter_messages(self, timeout_s: int):
        """按行产出 JSON 消息（非阻塞 + 截止时间），EOF/超时终止迭代

        行缓冲挂在实例状态（self._buf），跨生成器调用不丢失——
        上一次迭代读进缓冲的行在这一次继续消化（挂死根因修复）。
        """
        import select as _select, time
        fd = self._make_nonblocking()
        deadline = time.monotonic() + timeout_s
        while True:
            # 先消化已有缓冲中的完整行
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue  # W-INV-A: 非法行按协议忽略
            if self._eof:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            ready, _, _ = _select.select([fd], [], [], min(remaining, 2.0))
            if not ready:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                self._eof = True  # EOF：标记后仍先消化缓冲
                continue
            self._buf += chunk

    def _read_stderr(self) -> str:
        """读取 stderr 已缓冲内容（诊断用，非阻塞尽力而为）"""
        if self._proc.stderr is None:
            return ""
        try:
            import fcntl as _fcntl
            fd = self._proc.stderr.fileno()
            _fcntl.fcntl(fd, _fcntl.F_SETFL,
                         _fcntl.fcntl(fd, _fcntl.F_GETFL) | os.O_NONBLOCK)
            data = b""
            while True:
                import select as _select
                ready, _, _ = _select.select([fd], [], [], 0.2)
                if not ready:
                    break
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                data += chunk
            return data.decode("utf-8", errors="replace")
        except Exception:
            return ""


# ---------------- ACP 工兵（一个类驱动任何 ACP agent） ----------------

class AcpWorker:
    """配置驱动的 ACP 执行工兵：一个类接入任何 ACP 兼容 agent

    差异全部构造参数驱动（照 platform_adapter.GenericOpenAIAdapter 同款思路）：
      command : 工兵启动命令（claude-agent-acp / 任何 ACP server 可执行文件）
      cwd     : 默认工作目录（黑盒任务容器）
      env_key : 认证环境变量名（如 ANTHROPIC_API_KEY），值取自环境

    泛化：接其他 AI 编辑器 = 换 command + env_key，零代码改动。
    """

    def __init__(self, command: List[str], cwd: str = "", env_key: str = "",
                 prompt_timeout_s: int = 300):
        self._command = list(command)
        self._cwd = cwd
        self._env_key = env_key
        self._prompt_timeout_s = prompt_timeout_s

    # ---- 生命周期 ----

    def _spawn(self) -> subprocess.Popen:
        """启动工兵子进程（认证凭据走环境变量，严禁硬编码）

        经验教训：部分桥（如 claude-agent-acp）凭据缺失时会静默等待而非
        退出——env_key 声明了但凭据缺失时直接报 REFUSED，杜绝挂死。
        """
        env = dict(os.environ)
        if self._env_key and not env.get(self._env_key):
            raise WorkerError(
                "REFUSED",
                f"凭据环境变量 {self._env_key} 未设置（工兵启动需要）")
        try:
            return subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, env=env,
                cwd=self._cwd or None,
            )
        except FileNotFoundError as exc:
            raise WorkerError("LAUNCH",
                              f"worker command not found: {self._command[0]}") from exc

    # ---- ACP 三方法 ----

    def _initialize(self, rpc: LineJsonRpcClient) -> None:
        """ACP 握手：protocolVersion=1，能力声明最小化（fs 代理由工兵自理）"""
        rpc.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
            },
        }, timeout_s=30)

    def delegate(self, task: WorkerTask) -> WorkerResult:
        """委派一次任务：握手 → 新会话（或续接）→ 发 intent → 收结果 → 关闭

        W-INV-C：任何失败直接上抛 WorkerError，无内部重试。
        W-INV-D：task.session_id 非空时续接既有会话（不回放历史）。
        """
        if not task.intent.strip():
            raise WorkerError("PROTOCOL", "empty intent")
        proc = self._spawn()
        try:
            rpc = LineJsonRpcClient(proc)
            self._initialize(rpc)
            params: Dict[str, Any] = {
                "cwd": task.cwd or self._cwd or ".",
                "prompt": [{"type": "text", "text": task.intent}],
            }
            if task.session_id:
                params["sessionId"] = task.session_id
            result = rpc.request("session/new" if not task.session_id else
                                 "session/prompt", params,
                                 timeout_s=min(task.timeout_s, 60))
            session_id = result.get("sessionId", task.session_id)
            # session/prompt 持久入队回执后等待 stopReason（长任务）
            content, stop = self._await_stop(rpc, session_id,
                                             task.timeout_s)
            if stop not in ("end_turn", ""):
                raise WorkerError("PROTOCOL",
                                  f"worker stopped: {stop}",
                                  stderr=rpc._read_stderr())
            return WorkerResult(content=content, session_id=session_id,
                                worker=self._command[0])
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
            proc.wait(timeout=10)

    def _await_stop(self, rpc: LineJsonRpcClient, session_id: str,
                    timeout_s: int) -> tuple:
        """等待工兵完成：从通知流提取最终 assistant 文本与 stopReason"""
        stop, texts = "", []
        for msg in rpc._iter_messages(timeout_s):
            if "id" in msg:
                continue
            method = msg.get("method", "")
            params = msg.get("params") or {}
            if method == "session/update":
                upd = params.get("update") or {}
                chunk = (upd.get("content") or {}).get("text", "")
                if upd.get("sessionUpdate") == "agent_message_chunk" and chunk:
                    texts.append(chunk)
            elif method == "stop" or "stopReason" in params:
                stop = params.get("stopReason", params.get("reason", "end_turn"))
                break
        if not stop and not texts:
            raise WorkerError("TIMEOUT", f"no completion in {timeout_s}s",
                              stderr=rpc._read_stderr())
        return "".join(texts), stop or "end_turn"

    def healthcheck(self) -> bool:
        """健康探针：能否启动并完成握手（不委派真实任务，零 token 消耗）"""
        try:
            proc = self._spawn()
        except WorkerError:
            return False
        try:
            self._initialize(LineJsonRpcClient(proc))
            return True
        except WorkerError:
            return False
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


# ---------------- 配置驱动工兵注册表（泛化关键） ----------------

class WorkerRegistry:
    """配置驱动的工兵注册表：新工兵 = config.json workers 加一条 JSON，零代码

    config 格式:
      "workers": [
        {"name": "claude-code", "command": ["claude-agent-acp"],
         "env_key": "ANTHROPIC_API_KEY", "cwd": "/root/gitee"},
        {"name": "任何-acp-agent", "command": ["其他-acp-server"]}
      ]
    """

    def __init__(self, workers: Optional[Dict[str, AcpWorker]] = None):
        self._workers: Dict[str, AcpWorker] = workers or {}

    @classmethod
    def from_config(cls, cfg: Dict[str, Any]) -> "WorkerRegistry":
        """从 config workers 声明装配（零硬编码代码）"""
        workers: Dict[str, AcpWorker] = {}
        for w in cfg.get("workers", []):
            workers[w["name"]] = AcpWorker(
                command=w["command"],
                cwd=w.get("cwd", ""),
                env_key=w.get("env_key", ""),
                prompt_timeout_s=w.get("timeout_s", 300),
            )
        return cls(workers)

    def worker_for(self, name: str) -> AcpWorker:
        worker = self._workers.get(name)
        if worker is None:
            raise WorkerError("LAUNCH",
                              f"未注册工兵: {name}（config.json workers 中声明后即可用）")
        return worker

    def names(self) -> List[str]:
        return list(self._workers.keys())

    def health(self) -> Dict[str, bool]:
        """全部工兵握手探针（name → 可用性）"""
        return {name: w.healthcheck() for name, w in self._workers.items()}
