export const nav = [
  { key: 'overview', title: '运行总览', sub: 'Runtime Console' },
  { key: 'runs', title: '任务与运行', sub: 'Tasks & Runs' },
  { key: 'journal', title: '操作账本', sub: 'Effect Journal' },
  { key: 'forks', title: '分支与回放', sub: 'Replay / Fork' },
  { key: 'failure', title: '故障实验室', sub: 'Failure Lab' },
  { key: 'evaluations', title: '对照评测', sub: 'Evaluation' },
]
export const names: Record<string, string> = {
  TEST_PASSED: '测试通过',
  TEST_FAILED: '测试失败',
  CONTROLLED: '受控执行',
  QUEUED: '排队中',
  PREPARING: '准备中',
  RUNNING: '执行中',
  CANCEL_REQUESTED: '正在取消',
  CANCELLED: '已取消',
  SUCCEEDED: '已验收',
  FAILED: '失败',
  NEEDS_REVIEW: '待人工检查',
  PREPARED: '已准备',
  IN_FLIGHT: '已发出',
  CONFIRMED: '已确认',
  UNKNOWN: '结果未知',
  APPROVED: '已批准',
  REJECTED: '已拒绝',
  PENDING: '待审批',
}
export const faults = [
  { id: 'none', label: '正常执行', note: '修改示例代码，在 Docker 内运行验收测试。' },
  {
    id: 'before_dispatch',
    label: '派发前中断',
    note: '意图已记录，工具尚未派发；Worker 将真实退出。',
  },
  {
    id: 'after_dispatch_before_call',
    label: '已派发、调用前中断',
    note: '保留 IN_FLIGHT，恢复需证明工具是否实际发生。',
  },
  { id: 'before_failure', label: '外部服务明确拒绝', note: '本地模拟服务提供未产生副作用的证据。' },
  {
    id: 'response_lost',
    label: '外部响应丢失',
    note: '模拟服务已提交，Runtime 保留 UNKNOWN，等待对账。',
  },
  {
    id: 'after_external_before_receipt',
    label: '提交后、保存回执前崩溃',
    note: 'Worker 真实退出，独立模拟服务保留实际操作记录。',
  },
  {
    id: 'sandbox_terminated',
    label: '终止测试容器',
    note: '真正停止 Docker 容器，未完成测试不能记作成功。',
  },
]
export const comparisonFaults = [
  {
    id: 'duplicate_delivery',
    label: '重复投递',
    note: '重复投递8次，分别记录真实请求和实际操作。',
  },
  {
    id: 'changed_arguments',
    label: '相同动作改参数',
    note: '核验冲突，禁止将新参数当作同一已确认操作。',
  },
  {
    id: 'no_idempotency_no_query',
    label: '无幂等、无查询的响应丢失',
    note: '真实操作已发生，恢复无法查询；应保留人工检查。',
  },
  {
    id: 'queryable_non_idempotent_loss',
    label: '可查询、非幂等的响应丢失',
    note: '比较外部对账与安全暂停，不猜测重试。',
  },
  {
    id: 'fork_old_checkpoint',
    label: '从旧检查点分支',
    note: 'Full核验历史快照与只读历史；其他配置明确标为不支持。',
  },
  { id: 'high_risk_unapproved', label: '高风险操作未审批', note: '三种配置都不能未经授权派发。' },
]
