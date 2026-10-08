# cluster-prism-lite

棱镜集群轻量版 (Prism Cluster Lite)：单进程 asyncio Python 多模型 AI 集群编排引擎。
替代 n8n + OpenClaw 双平台底盘，**保留老集群全部创新点，负载降低一个量级**。

## 核心架构（七阶管线）

```
Phase 1 意图转译 → Phase 2 语义分级路由(FLASH/ELITE) → Phase 3 五阶缓存与前缀对齐
→ Phase 4 git checkpoint 事务快照 → Phase 5 约束帕累托分发与 LLM 执行
→ Phase 6 AST 预检 + TDD 沙箱证据门禁 → Phase 7 jury 评审验收
```

## 保留的老集群全部创新点（一个不少）

| 创新点 | 模块 | 不变量 |
| :--- | :--- | :--- |
| 五阶前缀缓存（磁盘哈希/2048对齐/会话前缀/语义去重/金丝雀审计） | `cache_engine.py` | INV-1 前缀字节级一致 |
| 语义分级路由 FLASH/ELITE 双层（80% 常规走极速层） | `smart_router.py` | — |
| 约束帕累托分发（候选集⊆存活舰队，根除 404 假模型） | `smart_router.py` | — |
| Portkey 级断路器（429 熔断 + 半开自愈） | `smart_router.py` | — |
| 多模型 jury 评审（双层 + 幂等缓存） | `jury.py` | INV-4 幂等 |
| git checkpoint 事务快照与一键回滚 | `checkpoint.py` | INV-3 线性化 SMR |
| AST 语法预检 + 负向不变量（裸except/通配导入/硬编码密钥） | `ast_precheck.py` | — |
| TDD 沙箱（subprocess 隔离 + 30s 硬超时 + 退出码判据） | `tdd_sandbox.py` | — |
| cron 定时触发（引擎进程内嵌，替代 n8n 平台） | `triggers.py` | — |
| Telegram 渠道（urllib 长轮询，替代 OpenClaw 平台） | `channel_bot.py` | — |
| 七阶编排管线闭环 | `engine.py` | — |

## 相比老底盘（OpenClaw + n8n）的收益

- **常驻进程**：n8n + OpenClaw + 网关 3~4 个 → 仅引擎 1 个
- **存储**：n8n DB 全量执行历史 → 零数据库（纯文件缓存）
- **重复请求延迟**：2~10s LLM 全调用 → 0.05ms 磁盘缓存直返
- **质量门禁**：从平台节点里人肉模拟 → 管线原生执行

## 纯标准库承诺

运行时零第三方依赖：`urllib`（HTTP/长轮询）、`asyncio`（事件循环/cron）、
`ast`（预检）、`subprocess`（沙箱/git/checkpoint）、`hashlib`（缓存键）。

## 五大通用操作动词

```bash
python main.py setup           # 初始化配置
python main.py run "任务意图"   # 单任务模式
python main.py run             # 常驻模式（cron + Telegram 同进程）
python main.py test            # 全量测试（76 项验收神谕）
python main.py health          # 健康探针
python main.py clean           # 清理缓存
```

跨平台门面：`run.sh` / `run.bat` / `run.ps1` 零环境变量路径自愈。

## 密钥配置（严禁硬编码，PRB-E503）

```bash
export DEEPSEEK_API_KEY=sk-xxx        # 或写入 secret.env
export TELEGRAM_BOT_TOKEN=123456:xxx  # 可选，缺省渠道降级关闭
```

## 选型溯源（AI 模式重搜验证）

- 形态仲裁：`tool-taxonomy-arbiter` → `cluster-*`（多模型集群中枢，置信度 95%）
- 技术栈：`tool-stack-optima` 帕累托分 92.8，运行时 ~95MB；裁剪为纯 Python 单语言
  （雷达 AI 推演确认：单进程 asyncio 编排全为 I/O-bound，Rust/Go 层为过度设计）
- AI 搜索新洞察：I/O 复用互补（T_wall ≈ max(ΣT_cpu, ΣT_io_ready)）、
  路由元数据独立控制通道、会话状态 checkpoint 唯一真相源、jury 幂等三元组缓存键
