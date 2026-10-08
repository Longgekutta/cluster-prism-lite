#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
channel_bot.py: Telegram 渠道层 —— urllib 长轮询 (Telegram Channel via Long Polling)
======================================================================================
替代 OpenClaw 平台级渠道：urllib 标准库长轮询 getUpdates，消息 → TriggerEvent，
与触发层共用同一 asyncio 事件循环。无 token 时优雅降级（不崩溃）。
纯标准库，零第三方依赖。
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from typing import Any, Dict, List, Optional

from triggers import TriggerEvent, TriggerBus

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/getUpdates"
# 长轮询超时（Telegram 服务端挂起秒数）
LONG_POLL_SECONDS = 25
POLL_ERROR_BACKOFF_SECONDS = 10.0


def resolve_bot_token(env_var: str = "TELEGRAM_BOT_TOKEN") -> Optional[str]:
    """从环境变量解析 bot token；缺失返回 None（优雅降级，不崩溃）"""
    return (os.environ.get(env_var) or "").strip() or None


def fetch_updates(token: str, offset: int, timeout: float = LONG_POLL_SECONDS + 5) -> List[Dict[str, Any]]:
    """同步长轮询一次 getUpdates；返回更新列表"""
    url = TELEGRAM_API_BASE.format(token=token) + f"?timeout={LONG_POLL_SECONDS}&offset={offset}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API 返回异常: {data.get('description', 'unknown')}")
    return data.get("result", [])


def extract_message(update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从 update 提取文本消息与 session 信息"""
    msg = update.get("message") or {}
    chat = msg.get("chat", {})
    text = msg.get("text", "")
    if not text:
        return None
    return {
        "chat_id": chat.get("id"),
        "session_id": f"tg:{chat.get('id')}",
        "text": text,
        "update_id": update.get("update_id"),
    }


class TelegramChannel:
    """Telegram 渠道轮询器：同进程事件循环，消息 → TriggerEvent 总线"""

    def __init__(self, bus: TriggerBus, token: Optional[str] = None):
        self.token = token or resolve_bot_token()
        self.bus = bus
        self._offset = 0
        self._running = False
        self._task: Optional[asyncio.Task[None]] = None
        self.enabled = self.token is not None

    async def _loop(self) -> None:
        assert self.token is not None  # enabled 已守卫
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                # 同步长轮询放入线程池，不阻塞事件循环
                updates = await loop.run_in_executor(
                    None, fetch_updates, self.token, self._offset)
                for upd in updates:
                    self._offset = max(self._offset, upd.get("update_id", 0) + 1)
                    info = extract_message(upd)
                    if info is None:
                        continue
                    await self.bus.emit(TriggerEvent(
                        source="telegram",
                        session_id=info["session_id"],
                        payload={"chat_id": info["chat_id"], "text": info["text"]},
                    ))
            except (RuntimeError, urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
                # 网络瞬态故障：退避后重试（记录而非静默吞掉）
                print(f"[channel_bot] 轮询异常，{POLL_ERROR_BACKOFF_SECONDS}s 后重试: {type(exc).__name__}: {exc}",
                      flush=True)
                await asyncio.sleep(POLL_ERROR_BACKOFF_SECONDS)

    def start(self) -> bool:
        """启动轮询；无 token 时优雅降级返回 False"""
        if not self.enabled:
            print("[channel_bot] 未配置 TELEGRAM_BOT_TOKEN，Telegram 渠道降级关闭", flush=True)
            return False
        self._running = True
        self._task = asyncio.ensure_future(self._loop())
        return True

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # 正常取消
            self._task = None


# urllib.error 需显式导入以捕获
import urllib.error  # noqa: E402
