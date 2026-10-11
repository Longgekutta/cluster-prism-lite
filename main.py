#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py: cluster-prism-lite 调度中枢入口 (UCFS v1.0 Standard Entry)
====================================================================
五大通用操作动词: setup / run / test / health / clean
单进程 asyncio 事件循环：cron 触发 + Telegram 渠道 + 手动任务，零外部平台。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine import ClusterEngine  # noqa: E402
from triggers import TriggerBus, CronTrigger, TriggerEvent  # noqa: E402
from channel_bot import TelegramChannel  # noqa: E402


def load_config() -> dict:
    cfg_path = os.path.join(BASE_DIR, "config.json")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"[main] 配置读取失败({type(exc).__name__})，使用默认配置", flush=True)
        return {}


def cmd_setup() -> int:
    cfg = load_config()
    cfg_path = os.path.join(BASE_DIR, "config.json")
    if not os.path.exists(cfg_path):
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({"api_base": "https://api.deepseek.com/v1",
                       "cron": {"expr": "*/30 * * * *", "task": "health probe"},
                       "telegram_enabled": True}, f, indent=2)
        print(f"[setup] 默认配置已写入: {cfg_path}")
    else:
        print(f"[setup] 配置已存在: {cfg_path}")
    print(f"[setup] DEEPSEEK_API_KEY: {'已配置' if os.environ.get('DEEPSEEK_API_KEY') else '未配置(可写入 secret.env)'}")
    print(f"[setup] TELEGRAM_BOT_TOKEN: {'已配置' if os.environ.get('TELEGRAM_BOT_TOKEN') else '未配置(渠道将降级关闭)'}")
    return 0


def cmd_run_once(intent: str) -> int:
    """单任务模式：直接跑一次七阶管线"""
    engine = ClusterEngine()
    result = engine.run(intent)
    for report in result.reports:
        mark = "PASS" if report.ok else "FAIL"
        print(f"  [{mark}] {report.phase}: {report.detail}")
    print(f"\n[answer] {result.answer[:400]}")
    print(f"[stats] {engine.cache.stats_report()}")
    return 0 if result.ok else 1


async def cmd_run_daemon() -> int:
    """常驻模式：cron + Telegram 渠道同进程事件循环"""
    cfg = load_config()
    bus = TriggerBus()
    engine = ClusterEngine()
    cron_cfg = cfg.get("cron", {})
    triggers: list = []
    cron_expr = cron_cfg.get("expr")
    if cron_expr:
        trig = CronTrigger(bus, cron_expr, {"task": cron_cfg.get("task", "tick")})
        trig.start()
        triggers.append(trig)
        print(f"[daemon] cron 已启动: {cron_expr}", flush=True)
    channel = TelegramChannel(bus)
    if cfg.get("telegram_enabled", True):
        if channel.start():
            print("[daemon] Telegram 渠道已启动", flush=True)
    print("[daemon] 集群中枢运行中，Ctrl+C 退出", flush=True)
    try:
        while True:
            event = await bus.consume()
            intent = str(event.payload.get("text") or event.payload.get("task") or "")
            print(f"[daemon] 触发 {event.source}/{event.session_id}: {intent[:60]}", flush=True)
            result = engine.run(intent, session_id=event.session_id)
            print(f"[daemon] 管线 {'OK' if result.ok else 'FAIL'}: {result.answer[:120]}", flush=True)
    except asyncio.CancelledError:
        pass
    finally:
        for trig in triggers:
            await trig.stop()
        await channel.stop()
    return 0


def cmd_test() -> int:
    import subprocess
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests", "-v"],
                          cwd=BASE_DIR)
    return proc.returncode


def cmd_health() -> int:
    cfg = load_config()
    healthy = True
    checks = {
        "engine_import": True,
        "api_key": bool(os.environ.get("DEEPSEEK_API_KEY") or
                        os.path.exists(os.path.join(BASE_DIR, "secret.env"))),
        "config_file": os.path.exists(os.path.join(BASE_DIR, "config.json")),
        "cache_dir": os.path.isdir(os.path.join(BASE_DIR, ".cache_cluster")),
    }
    for name, ok in checks.items():
        print(f"  {'✓' if ok else '✗'} {name}")
        healthy = healthy and ok
    # 执行工兵层（ACP）：凭据感知探针——凭据缺失只标 DEGRADED 不挂死
    try:
        from worker_acp import WorkerRegistry
        workers = WorkerRegistry.from_config(cfg)
        for name, ok in workers.health().items():
            print(f"  {'✓' if ok else '✗'} worker:{name}")
    except Exception as exc:  # 工兵层可选：配置缺失不阻塞基础健康
        print(f"  - worker层跳过: {type(exc).__name__}")
    print(f"[health] {'HEALTHY' if healthy else 'DEGRADED'}")
    return 0 if healthy else 1


def cmd_clean() -> int:
    cleaned = 0
    cache_dir = os.path.join(BASE_DIR, ".cache_cluster")
    if os.path.isdir(cache_dir):
        shutil.rmtree(cache_dir)
        cleaned += 1
    for root, dirs, _files in os.walk(BASE_DIR):
        for d in list(dirs):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(root, d))
                dirs.remove(d)
                cleaned += 1
    print(f"[clean] 清理 {cleaned} 个目录")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "setup":
        return cmd_setup()
    if cmd == "run":
        if len(sys.argv) > 2:
            return cmd_run_once(" ".join(sys.argv[2:]))
        return asyncio.run(cmd_run_daemon())
    if cmd == "test":
        return cmd_test()
    if cmd == "health":
        return cmd_health()
    if cmd == "clean":
        return cmd_clean()
    print("用法: python main.py <setup|run [intent]|test|health|clean>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
