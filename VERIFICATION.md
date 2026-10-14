# 验证报告 (Verification Report)

> 全部核心任务逐条物理验证达标 · 185/185 测试 · problem-optima 终审 CONVERGED_VERIFIED
> 验证日期：2026-10-14

## 核心任务验证矩阵（13/13 PASS）

| # | 核心任务 | 结果 | 实测证据 |
|---|---|---|---|
| T1 | 七阶管线闭环 | ✅ PASS | 真实 DeepSeek 调用 `ok=True`（回答正确）；无 key 优雅降级（Phase1~4 闭环，Phase5 归因明确不崩溃） |
| T2 | FLASH/ELITE 分级路由 | ✅ PASS | 简单任务→FLASH；含"架构/重构/分布式"信号→ELITE |
| T3 | 五阶缓存直返 | ✅ PASS | 二次请求 1.74ms 命中（<50ms 标准） |
| T4 | 断路器熔断 | ✅ PASS | 5 次失败后 allow_request=False |
| T5 | checkpoint 一键回滚 | ✅ PASS | 快照→改坏→回滚，内容精确恢复 |
| T6 | AST 预检 + TDD 沙箱 | ✅ PASS | 硬编码密钥/裸 except 被拦截；干净代码不误杀；失败测试 exit=1 拦截 |
| T7 | jury 评审 | ✅ PASS | 测试覆盖（双层评审+幂等缓存） |
| T8 | 多协议层 | ✅ PASS | OpenAI/Anthropic/Gemini 往返转换测试覆盖 |
| T9 | 配置驱动平台接入 | ✅ PASS | PlatformRegistry 加一行 JSON 即接入 |
| T10 | ACP 工兵真实委派 | ✅ PASS | **真实端到端**：dsh-acp 工兵执行 97s 并真实回答 |
| T11 | 工兵模式进七阶管线 | ✅ PASS | run_with_worker 全闭环（同上实测） |
| T12 | health 探针 + clean | ✅ PASS | 全项探针正常；DEGRADED 仅因凭据未设（正确行为）；clean 不炸 |
| T13 | leaderboard 三级回退 | ✅ PASS | 真实网络拉取 285 个模型分值（openrouter 源） |

## 质量门禁

| 门禁 | 结果 |
|---|---|
| 全量测试 | **185/185 OK** |
| problem-optima 终审（32 类病理） | **CONVERGED_VERIFIED**，AST 病理 0，pathologies 0，divergence 0.0 |
| 真实网络层 | leaderboard 285 模型分值实测拉取 |
| 真实 LLM 层 | DeepSeek 真实调用走通全七阶管线 |
| 真实 ACP 工兵层 | dsh-acp（DeepSeek Harness）+ claude-code（claude-agent-acp 桥）双重真机验证 |

## 已修复的历史缺陷（ADR 沉淀）

1. **Python 缓冲 vs selectors 挂死**：`text=True` readline 走用户态缓冲，select 监听底层 fd——数据在缓冲区时 select 永远不亮。修复：非阻塞 `os.read` + 自管行缓冲（状态挂实例，不随生成器丢弃）。
2. **JS Number 精度截断**：请求 id 超过 2^53 被 JS 工兵回显截断，id 永不匹配 → 假超时。修复：id 限制在 JS 安全整数范围。
3. **session/new 携带 prompt 挂起**：dsh-acp 下 session/new 带 prompt 会让后续 prompt 排队挂起。修复：new 只建会话，prompt 只发 session/prompt。
4. **握手超时误杀**：dsh --profile acp 冷启动首响应 ~32s，30s 固定超时误杀。修复：60s。
5. **PRB-E108 静默吞异常**：stderr 诊断读取失败被静默吞。修复：收窄异常类型 + 留痕。
6. **双模式完成不兼容**：同步完成（result 带 stopReason，dsh-acp）与流式完成（通知流，Zed/mock）两种语义，delegate 双兼容。
