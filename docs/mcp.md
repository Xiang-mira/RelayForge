# MCP / OpenCode 接入

默认 Mock 工作台不需要 OpenCode。此部分是可选的真实模型接入：模型依据源码和真实测试反馈选择工具，Runtime 负责执行边界与验收。

## 受控工具

| 工具 | 职责 |
| --- | --- |
| begin_task | 创建受管任务，或重新绑定已有任务上下文。 |
| prepare_action | 根据稳定 request_key 准备后续写入／测试／验收的动作身份。 |
| inspect_repository / read_file | 读取受管仓库信息及允许文件。 |
| apply_patch | 在前置内容哈希匹配时替换允许修改的源码。 |
| run_tests | 执行注册的 Docker 测试配置，返回实际结果。 |
| inspect_diff | 查看当前修改。 |
| get_execution_status | 查询执行、预算、审批与恢复状态。 |
| finish_task | 请求 Runtime 验收，不能由模型自我认证成功。 |

路径、可写文件、测试配置、调用次数和总预算均受限制，不允许模型向宿主机提交任意 Shell 命令。

## 配置示例

1. 先按 README 启动 RelayForge。直接验证 stdio 与确定性反馈可运行 `./scripts/verify-mcp-feedback.ps1`，无需模型登录或推理。
2. 如需真实模型，自行准备兼容的 OpenCode 客户端，并通过其官方登录流程配置账户；项目不携带账户凭据。
3. 将 `integrations/opencode/opencode.json` 中的 Python 占位路径改为本次检出的绝对路径：

```powershell
$projectRoot = (Get-Location).Path
$configPath = Join-Path $projectRoot 'integrations\opencode\opencode.json'
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$config.mcp.relayforge.command[0] = (Resolve-Path '.\backend\.venv\Scripts\python.exe').Path
$config | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $configPath -Encoding utf8
$env:OPENCODE_CONFIG = $configPath
New-Item -ItemType Directory -Force '.local\opencode-client','.local\reports' | Out-Null
opencode mcp list
```

该配置默认拒绝客户端原生工具，只允许 `relayforge_*`；关闭额外插件、LSP、formatter 和会话分享。它是客户端配置约束，不是对宿主机的通用 OS 沙箱。

## 模型演示驱动

`scripts/verify-opencode-model.py` 是一个有界的示例驱动，默认查找项目内 `.tools/opencode` 的 Windows 客户端；它记录事件，不代替模型选择补丁或下一步工具。使用前需按当前客户端、账户可用模型调整路径与 `MODEL` 常量。

现有驱动的授权检查仅接受 OpenCode 官方 OpenAI ChatGPT OAuth，拒绝 API Key、端点覆盖及原生工具启用；不会自动购买额度或切换收费 API。模型推理仍受你自己的账户权限、套餐和额度约束，启用前应自行确认。

## 恢复协议

`context_id` 是私密的任务能力标识，不是模型会话 ID；丢失响应后，使用原上下文和原 request_key／动作身份找回同一操作。新补丁或新测试才准备新的动作。

`UNKNOWN`、`NEEDS_APPROVAL`、`CONFLICT`、`BUDGET_EXCEEDED` 等状态独立返回。模型不应因为工具超时就换身份重复提交，也不能把状态查询当作新的预算。审批通过现有控制台完成，MCP 不提供自行批准工具。

受控任务与固定 LangGraph 图任务的恢复入口不同：MCP 使用上下文与动作身份，不支持通过普通 Replay / Fork 路由切换成固定图任务。
