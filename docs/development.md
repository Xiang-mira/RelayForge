# 开发与运行指南

## 环境与首次启动

参考根目录 README 的安装步骤。已验证脚本面向 Windows / PowerShell 7.2+；Python 要求 `>=3.11,<3.14`，Node.js 使用 24。Docker Desktop 需可运行 Linux 容器。

后端依赖由 `backend/uv.lock` 锁定，前端依赖由 `frontend/package-lock.json` 锁定。后续同步分别使用 `uv sync --project backend --locked` 与 `npm ci --prefix frontend`，保留项目内虚拟环境。

`configure.ps1` 只在 `.env` 不存在时生成随机数据库密码及匹配连接串，不打印密码，不覆盖已有配置。`.env.example` 仅用于解释字段，不能把占位密码当作正式配置。

必须构建 `relayforge-sandbox:local` 和 `relayforge-sandbox:pytest-header-v1` 两个测试镜像；后者用于 MCP 解析器任务。测试禁用网络、采用非 root 用户、只读根目录与受测快照，限制 1 CPU、256MB 内存、64 PIDs、30 秒执行时间。

## 日常命令

| 操作 | 命令 |
| --- | --- |
| 启动数据库、迁移、接口、前端和调度器 | `./scripts/start.ps1` |
| 启动服务但不启动普通调度器 | `./scripts/start.ps1 -NoWorker` |
| 停止自有进程与 PostgreSQL，保留数据 | `./scripts/stop.ps1` |
| 停止自有进程，保留 PostgreSQL 运行 | `./scripts/stop.ps1 -KeepDatabase` |
| 后端纯测试与前端构建 | `./scripts/test.ps1` |
| 完整 PostgreSQL / Docker 集成测试 | `./scripts/test.ps1 -Postgres -Docker` |
| 控制台测试 | `npm test --prefix frontend` |
| 数据库持久化重启验证 | `./scripts/verify-persistence.ps1` |

`start.ps1 -ApiOnly` 只用于诊断，不证明数据库和 Docker 已准备好。`/health/live` 表示接口存活；`/health` 还要求数据库连接可用。Worker 心跳不代表持有执行所有权。

## 恢复演示

先停止普通调度，按 README 启动 `-NoWorker` 模式，再执行 `backend/.venv/Scripts/python.exe scripts/verify-recovery.py`。脚本为自己的独立任务创建真实进程、Docker 测试和本地模拟操作。

- **A**：外部事务提交后、回执返回前硬退出执行进程；新进程按同一动作查询原结果。核对请求与实际操作均为 1。
- **B**：同样的提交后窗口，恢复查询发生故障；保持 UNKNOWN／空回执／NEEDS_REVIEW，不增加写请求。
- **C**：复用确认回执；更换参数产生 CONFLICT；从检查点 Fork，验证独立工作区、新操作待审批且派发 0 次。

记录保存到 `.local/recovery-demos/<新 UUID>/`。中断后可使用脚本的 `--resume` 参数指定它自己创建的目录，不删除记录或重复初始化已完成案例。硬退出是受控进程故障，不是机器断电实验。

## 对照评测

优先使用控制台提供的小规模 smoke 配置。CLI 示例：

```powershell
.\scripts\evaluate.ps1 -Templates fix_addition -Faults none,response_lost
```

默认不带参数会运行较大的任务／故障矩阵；只在确实需要时执行。恢复已有评测使用 `-SuiteId <UUID>`，不要同时更换输入。CLI 包装器会停止并在结束后恢复普通服务，不应与其他活动评测并行。

产物位于 `artifacts/evaluations/<suite-id>/`，包括 JSONL、CSV、汇总和输入版本清单。它们由本地运行产生，不进入 Git。Baseline 是评测专属消融模式，仍保留公共权限与工具边界，不应给普通任务开放绕过 Journal 的入口。

## 备份与恢复

`./scripts/backup.ps1` 停止自有服务后生成冷备份；`./scripts/restore.ps1 -BackupId <UUID>` 验证备份后恢复到**新数据库和新目录**，不覆盖当前数据库。恢复副本不会自动成为活动环境。

备份含任务数据，应由使用者自行保管，不提交 Git。不要执行 `docker compose down -v` 或删除数据库卷来处理普通启动错误。

## 常见问题

- **端口被占用**：检查 5173、8000、5432 的实际使用者；不要按端口强杀其他项目。
- **依赖缺失**：按锁文件同步项目依赖，不安装到全局 Python 或 Anaconda。
- **镜像不存在**：确认 Docker Linux engine 可用，并按 README 构建两种镜像。
- **数据库不可用**：检查 Docker Desktop、`docker compose ps` 和本地日志；503 不是“成功但没有数据”。
- **UNKNOWN**：检查原动作和恢复证据；不要换动作 ID 重试同一个未知操作。
- **启动时已有进程账本**：使用 `stop.ps1` 验证和停止原有自有进程，不删除账本来接管执行。

`.local/`、`.tools/`、虚拟环境、构建目录、日志、实验结果和凭据均保持本地。仓库只保存源码、测试、示例与必要文档。
