#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notify.py: ntfy 事件上报 (Event Notification via ntfy)
========================================================
管线 FAIL/缓存命中/断路器熔断 → HTTP PUT 推送到 ntfy 主题（推手机/桌面）。
未配置时优雅降级（不崩溃），本地日志模式记录事件账本。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from collections import deque
from typing import Any, Deque, Dict, List, Optional

DEFAULT_ENDPOINT = "https://ntfy.sh"
MAX_LOCAL_EVENTS = 100


def build_event_message(event: str, detail: str) -> str:
    """构造事件消息（标题 + 详情）"""
    return f"{event}: {detail}"


class Notifier:
    """ntfy 事件上报器：HTTP PUT 推送 + 本地事件账本"""

    def __init__(self, topic: Optional[str] = None,
                 endpoint: str = DEFAULT_ENDPOINT,
                 local_log: bool = False):
        self.topic = (topic or os.environ.get("NTFY_TOPIC") or "").strip() or None
        self.endpoint = endpoint.rstrip("/")
        # local_log=True 为纯本地账本模式（不联网），topic 未配置时优雅降级
        self.enabled = self.topic is not None and not local_log
        self.local_log = local_log
        self._events: Deque[Dict[str, Any]] = deque(maxlen=MAX_LOCAL_EVENTS)

    def send(self, event: str, detail: str) -> bool:
        """上报事件；未配置时优雅降级返回 False（不抛不崩溃）"""
        message = build_event_message(event, detail)
        record = {"event": event, "detail": detail, "ts": time.time()}
        self._events.append(record)
        if not self.enabled:
            return False
        try:
            url = f"{self.endpoint}/{self.topic}"
            req = urllib.request.Request(
                url, data=message.encode("utf-8"), method="PUT",
                headers={"Title": event.encode("utf-8", "ignore")[:120].decode("latin-1", "ignore")},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, OSError) as exc:
            # 网络瞬态：记录但不阻塞管线（上报失败不吞掉，打印告警）
            print(f"[notify] 上报失败(不阻塞): {type(exc).__name__}: {exc}", flush=True)
            return False

    def recent_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        """最近事件账本"""
        return list(self._events)[-limit:]
