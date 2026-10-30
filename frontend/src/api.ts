export type Run = { id: string; mode: string; evaluation_run_id: string | null; task_id: string; branch_id: string; status: string; fault: string; test_exit_code: number | null; owner: string | null; created_at: string; updated_at: string }
export type Task = { id: string; title: string; template: string; created_at: string }
export type Effect = { effect_id: string; run_id: string; branch_id: string; logical_action_id: string; tool_name: string; effect_class: string; status: string; payload_hash: string; payload_summary: Record<string, unknown>; external_receipt: unknown; policy_decision: string; approval_id: string | null; attempt_count: number; reconciliation_method: string | null; reconciliation_result: unknown; created_at: string }
export type RuntimeEvent = { id: string; source_event_id: number; run_id: string; effect_id: string | null; type: string; timestamp: string; data: Record<string, unknown> }
export type Checkpoint = { checkpoint_id: string; stage: string | null; snapshot_id: string | null; next: string[] }
export type Branch = { id: string; task_id: string; parent_id: string | null; checkpoint_id: string | null }
export type Approval = { id: string; effect_id: string; branch_id: string; payload_hash: string; decision: string; created_at: string }
export type Decision = { id: string; decision: string; evidence: unknown; created_at: string }
export type Artifact = { effect_id: string | null; artifact_id: string; tool: string; readonly: boolean; source_run_id: string; result: Record<string, unknown> }
export type Rate = { numerator: number; denominator: number; value: number | null }
export type ModeMetrics = { executed_cases: number; recorded_cases: number; infrastructure_failed_cases: number; duplicate_effect_count: number; unsafe_retry_count: number | null; external_request_count: number; actual_external_effect_count: number; recovery_correctness: Rate; coding_task_success: Rate; manual_review_rate: Rate; false_success_rate: Rate; state_divergence_rate: Rate; unsupported_probe_cases: number; failed_probe_cases: number; runtime_overhead: { paired_cases: number; median_ms: number | null; median_ratio: number | null } }
export type Evaluation = { id: string; suite_id: string; name: string; mode: string; status: string; created_at: string; scope: string; review_note?: string; planned_cases: number | null; template_count: number; fault_count: number; metrics: ModeMetrics | null }
export type EvaluationResult = { id: string; case_id: string; run_id: string | null; observed: Record<string, unknown> }
export type Overview = { runs_by_status: Record<string, number>; unknown_effects: number; pending_approvals: number; provider: string }
export type Health = { status: string; database: string; provider: string }
export type TemplateOption = { id: string; title: string }
export class ApiError extends Error { constructor(public status: number, detail: string) { super(detail) } }
export async function api<T>(path: string, body?: unknown): Promise<T> {
  let response: Response
  try { response = await fetch('/api' + path, body === undefined ? undefined : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) }
  catch { throw new Error('无法连接本地服务，请检查服务是否运行。') }
  const data = await response.json().catch(() => null)
  if (!response.ok) throw new ApiError(response.status, `${response.status} · ${typeof data?.detail === 'string' ? data.detail : '请求未通过服务端检查'}`)
  return data as T
}
export const short = (id: string | null | undefined) => id ? id.slice(0, 8) : '—'
export const when = (date: string) => new Date(date).toLocaleString('zh-CN', { hour12: false })
export const runLink = (id: string) => '#/runs/' + encodeURIComponent(id)
