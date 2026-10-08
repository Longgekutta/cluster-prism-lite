#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
triggers.py: 触发层 —— asyncio 原生 cron 定时器与统一触发事件 (Triggers & Unified TriggerEvent)
================================================================================================
替代 n8n 平台级触发：cron 定时器内嵌引擎进程事件循环，零常驻平台、零数据库。
纯标准库 asyncio，无第三方依赖。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional


@dataclass
class TriggerEvent:
    """统一触发事件：所有触发源（cron/telegram/webhook）产出此结构"""
    source: str          # "cron" | "telegram" | "webhook" | "manual"
    session_id: str
    payload: Dict[str, Any]
    ts: float = field(default_factory=time.time)


def parse_cron_expr(expr: str) -> Dict[str, Any]:
    """极简 cron 解析: "分 时 日 月 周" 或 "*/N" 步进；返回字段字典

    支持: "*/5 * * * *" (每5分钟) / "0 9 * * *" (每日9点) / "@hourly" / "@daily"
    """
    expr = expr.strip()
    aliases = {"@hourly": "0 * * * *", "@daily": "0 0 * * *",
               "@weekly": "0 0 * * 0", "@minutely": "*/1 * * * *"}
    expr = aliases.get(expr, expr)
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron 表达式必须为 5 字段: {expr}")
    names = ("minute", "hour", "day", "month", "weekday")
    out: Dict[str, Any] = {}
    for name, part in zip(names, parts):
        if part == "*":
            out[name] = None
        elif part.startswith("*/"):
            step = int(part[2:])
            if step <= 0:
                raise ValueError(f"步进必须为正: {part}")
            out[name] = {"step": step}
        else:
            out[name] = int(part)  # ValueError 自动冒泡
    return out


def cron_matches(spec: Dict[str, Any], now: Optional[time.struct_time] = None) -> bool:
    """判断当前时间是否匹配 cron 规格"""
    t = now or time.localtime()
    fields = {"minute": t.tm_min, "hour": t.tm_hour, "day": t.tm_mday,
              "month": t.tm_mon, "weekday": (t.tm_wday + 1) % 7}  # cron 周日=0
    for name, val in spec.items():
        actual = fields[name]
        if val is None:
            continue
        if isinstance(val, dict):  # step: */N
            step = val["step"]
            max_map = {"minute": 60, "hour": 24, "day": 32, "month": 13, "weekday": 7}
            if actual % step != 0:
                return False
        elif actual != val:
            return False
    return True


class TriggerBus:
    """统一触发事件总线：所有触发源 → asyncio.Queue（背压）→ 引擎消费"""

    def __init__(self, maxsize: int = 256):
        self.queue: asyncio.Queue[TriggerEvent] = asyncio.Queue(maxsize=maxsize)

    async def emit(self, event: TriggerEvent) -> None:
        """发布触发事件（队列满时等待，天然背压）"""
        await self.queue.put(event)

    async def consume(self) -> TriggerEvent:
        return await self.queue.get()


class CronTrigger:
    """asyncio 原生 cron 定时器：同进程事件循环内运行，零外部平台"""

    def __init__(self, bus: TriggerBus, cron_expr: str,
                 task_payload: Dict[str, Any], session_id: str = "cron"):
        self.spec = parse_cron_expr(cron_expr)
        self.bus = bus
        self.payload = task_payload
        self.session_id = session_id
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False

    async def _loop(self) -> None:
        last_minute = -1
        while self._running:
            now = time.localtime()
            if now.tm_min != last_minute and cron_matches(self.spec, now):
                last_minute = now.tm_min
                await self.bus.emit(TriggerEvent(
                    source="cron", session_id=self.session_id,
                    payload=dict(self.payload)))
            # 每秒检查一次（分钟级 cron 精度足够）
            await asyncio.sleep(1.0)

    def start(self) -> None:
        self._running = True
        self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # 正常取消，非异常吞没
            self._task = None
