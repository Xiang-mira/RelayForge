# 架构与执行边界

## 两条入口，共用执行安全机制

预设任务通过 `Tasks → Supervisor → Worker / LangGraph` 执行固定的 inspect、plan、modify、test、review、publish、report 流程。默认 Mock 编程提供者使用预设修复规则，适合不调用模型的功能验证和对照实验。

外部模型通过 `OpenCode → MCP stdio → loopback FastAPI → ControlledTasks` 调用受控工具。模型选择下一步，运行时解析任务上下文、路径、动作身份和预算，并提供真实测试反馈。MCP 任务不会再交给固定图 Worker 代为规划。

两条路径复用 `runtime/executor.py`、权限策略、Effect Journal、工作区、Docker 和验收服务。

## 动作身份、状态与恢复

`branch_id + logical_action_id` 标识逻辑动作；参数哈希独立绑定该动作的输入。恢复同一动作必须保留身份，同一身份更换参数会发生冲突。

| 状态 | 含义 |
| --- | --- |
| PREPARED | 意图已持久化，尚未派发；可能正在等待审批。 |
| IN_FLIGHT | 已持久化派发声明，调用尚无最终凭证。 |
| CONFIRMED | 有可信调用结果或外部核验凭证。测试工具已执行不等于测试通过，还需检查退出码与测试结果。 |
| FAILED | 已确认的失败或未发生结果；超时本身不是此类证明。 |
| UNKNOWN | 无法确定外部结果，需要对账或人工处理。 |

派发声明在数据库事务中提交后才执行工具。进程硬退出时可能来不及写 UNKNOWN，恢复者在获得执行所有权、确认旧进程停止后处理遗留 IN_FLIGHT。

可信外部查询可以确认原操作，复用原回执而不重新提交。查询失败、结果多义或参数不匹配不能伪造凭证。对明确支持原生幂等的工具，只有满足声明的协议和恢复授权时，才允许受控重传；不能把所有 UNKNOWN 一律解释为可重试。

核心代码：`journal/state.py`、`journal/service.py`、`runtime/executor.py`、`worker.py`。

## 测试证据与最终提交

受测内容覆盖源码、可信测试、配置与相关输入，排除缓存、虚拟环境、日志和构建产物。未提交的文件修改也参与指纹，Git commit hash 不代替内容指纹。

Docker 对经过校验的快照运行注册测试配置。凭证保留任务／运行身份、工作区与配置指纹、测试完成状态、退出码及测试数量。发布入口检查当前内容、测试凭证、UNKNOWN 依赖和审批，并在真实模拟服务调用前再次验证。

源码在测试后变化会使旧证据失效，拒绝原因保留在事件中；任务之前显示成功也不能免除恢复后的重新校验。

核心代码：`workspaces/fingerprint.py`、`workspaces/sandbox.py`、`runtime/publication.py`、`runtime/acceptance.py`。

## 存储职责

- **PostgreSQL**：任务、运行、分支、Effects、审批、恢复决策、持久化事件和实验元数据；LangGraph Checkpoint 使用自己的表。
- **工作区与文件产物**：受管源码、历史快照、测试与 Diff 产物，默认位于 `.local/`。
- **独立 SQLite**：本地模拟 provider 的 requests 和 operations，提交事务与 PostgreSQL 分开，用于重现“外部已发生但本地无回执”的窗口。

模拟 provider 在执行进程内同步运行，并非远程 HTTP 业务服务。独立持久化指存储和事务边界独立，不代表另一个服务进程。

## Fork、权限与控制台

Fork 从选定检查点恢复独立工作区，父分支的已确认效果作为只读历史引用。新分支的新写操作使用独立身份，重新执行策略与审批检查，不继承父分支批准。

控制台通过 REST 和持久化 SSE 读取实际状态、测试、Diff 和事件。模型公开回应是来自客户端的归属记录，不是私有思维链，也不是模型身份的密码学证明。

目前采用单机 OS 执行锁与数据库约束；心跳用于显示活跃状态，不授予派发所有权。没有实现分布式调度或跨机器 fencing，不应将本地验证外推为分布式 exactly-once。
