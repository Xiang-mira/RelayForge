export type EvidenceArtifact = {
  effect_id: string
  action_id?: string
  tool: string
  created_at?: string
  receipt?: { external_id: string }
  matching_patch_actions?: string[]
  result: {
    passed?: boolean
    exit_code?: number
    test_count?: number
    stdout?: string
    stderr?: string
    diff?: string
    diff_truncated?: boolean
    workspace_hash?: string
    before_snapshot_id?: string
    content_hash?: string
    evidence_error?: string
    acceptance?: {
      test_run_id: string
      workspace_hash: string
      configuration: unknown
    }
  }
}
export type RunEvidence = {
  run_id: string
  controller: string
  source: string
  agent: null | {
    provider: string
    model: string
    session_id: string
    source_hash: string
    event_id: number
    origin: string
  }
  calls: {
    event_id: number
    runtime_call_id: string
    operation: string
    action_id: string | null
    started_at: string
    returned_at: string
    parameters: Record<string, unknown>
    outcome: Record<string, unknown> & { status: string }
    effect_ids: string[]
  }[]
  client_calls: {
    event_id: number
    call_id: string
    tool: string
    runtime_event_id: number | null
    runtime_call_id?: string
    action_id?: string
    effect_ids: string[]
    status: string
    binding: string
  }[]
  messages: {
    event_id: number
    message_id: string
    text: string
    after_call_id: string | null
  }[]
  artifacts: EvidenceArtifact[]
  iteration: {
    patch_count: number
    failure_after_patch_then_revision: boolean
    first_patch_passed: boolean | null
    basis: string
  }
  results: {
    coding_task: {
      state: string
      acceptance_event_id: number | null
      evidence: unknown
      current_version_matches: boolean | null
      last_runtime_outcome?: { status: string; message?: string } | null
      rejections: { event_id: number; reason: string }[]
    }
    recovery_correctness: { state: string; fault: string }
    effect_safety: {
      state: string
      independent_truth: {
        effect_id: string
        requests: number
        operations: number
      }[]
      duplicate_operations: number | null
      unauthorized_effects: string[]
      unresolved_effects: string[]
    }
  }
}

export const sourceLabel = (source: string | undefined) =>
  ({
    model_client_export: '真实模型 · 客户端会话记录',
    controlled_tools: '受控工具 · 模型未记录',
    controlled_tools_model_unrecorded: '受控工具 · 模型未记录',
    deterministic_evaluation: '历史确定性评测',
    preset_task: '预设任务',
  })[source ?? ''] ?? '来源未记录'
