import { useState } from 'react'
import { short, runLink } from '../api'
import type { RunEvidence } from '../evidence'
import { sourceLabel } from '../evidence'
import { Badge, Empty, Json } from '../ui'

export function AgentEvidence({
  evidence: e,
  onEffect,
  compact = false,
}: {
  evidence: RunEvidence
  onEffect: (id: string) => void
  compact?: boolean
}) {
  const eventLink = (id: number) =>
    runLink(e.run_id) + '?tab=events&source_event=' + id
  const [showAllCalls, setShowAllCalls] = useState(false)
  const coding = e.results.coding_task,
    safety = e.results.effect_safety
  const calls = e.client_calls.length
    ? e.client_calls
    : e.calls.map((c) => ({
        event_id: c.event_id,
        call_id: c.runtime_call_id,
        tool: c.operation,
        runtime_event_id: c.event_id,
        action_id: c.action_id,
        effect_ids: c.effect_ids,
        status: c.outcome.status,
        binding: 'runtime_only',
      }))
  return (
    <div className="agent-evidence">
      <section className="panel">
        <div className="section-head">
          <div>
            <h2>Agent 与 Runtime 证据</h2>
            <p>{sourceLabel(e.source)}</p>
          </div>
          {compact && (
            <a href={runLink(e.run_id) + '?tab=evidence'}>查看完整证据</a>
          )}
        </div>
        {e.agent ? (
          <>
            <p>
              <strong>
                {e.agent.provider} / {e.agent.model}
              </strong>{' '}
              · ChatGPT OAuth
            </p>
            <p className="mono wrap">会话 {e.agent.session_id}</p>
            <p className="muted">
              模型身份和公开发言来自已导入的 OpenCode
              会话；不展示私有推理，也不作为成功凭证。
            </p>
            <details>
              <summary>会话来源与摘要哈希</summary>
              <Json data={e.agent} />
            </details>
          </>
        ) : (
          <Empty>
            模型标识和会话未记录。不会把受控工具或预设流程推断成真实模型任务。
          </Empty>
        )}
        <p>
          {e.iteration.patch_count} 次有凭证的补丁 ·{' '}
          {e.iteration.failure_after_patch_then_revision
            ? '已核验补丁失败 → 版本修订 → 再测通过的版本链。'
            : e.iteration.first_patch_passed === true
              ? '首次补丁后的测试通过；本次未发生补丁失败后的再次修复。'
              : '本次未获得补丁失败后再次修复的完整直接证据。'}
        </p>
      </section>
      <div className="evidence-outcomes">
        <section className="panel">
          <h3>Coding Task Success</h3>
          <p>代码任务验收</p>
          <Badge value={coding.state} />
          {coding.state !== 'SUCCEEDED' && coding.last_runtime_outcome && (
            <p>
              最近 Runtime 返回：{coding.last_runtime_outcome.status} ·{' '}
              {coding.last_runtime_outcome.message}
            </p>
          )}
          <p>
            {coding.current_version_matches === true
              ? '当前源码与被接受的测试版本一致。'
              : coding.current_version_matches === false
                ? '当前版本已变化或无法读取，旧验收不能授权新提交。'
                : '当前版本绑定未记录。'}
          </p>
          {coding.acceptance_event_id && (
            <a href={eventLink(coding.acceptance_event_id)}>
              验收事件 #{coding.acceptance_event_id}
            </a>
          )}
        </section>
        <section className="panel">
          <h3>Recovery Correctness</h3>
          <p>故障恢复正确性</p>
          <strong>
            {e.results.recovery_correctness.state === 'not_exercised'
              ? '本次未验证恢复'
              : '需另查故障验收依据'}
          </strong>
          <p>
            故障条件：{e.results.recovery_correctness.fault}
            。普通任务通过不等于恢复通过。
          </p>
        </section>
        <section className="panel">
          <h3>Effect Safety</h3>
          <p>副作用安全</p>
          <strong>
            {(
              {
                observed_no_violation: '本次记录中未观察到违规',
                pending_approval: '等待审批，尚未确认执行',
                unresolved: '存在未确认操作',
                not_exercised: '本次未执行外部写',
                not_verified: '独立证据不足',
              } as Record<string, string>
            )[safety.state] ?? '未记录'}
          </strong>
          <p>
            独立模拟服务：
            {safety.independent_truth.length
              ? `${safety.independent_truth.reduce((n, t) => n + t.requests, 0)} 请求 / ${safety.independent_truth.reduce((n, t) => n + t.operations, 0)} 次副作用`
              : '未核验'}
            。
          </p>
          <p className="muted">
            仅限本次本地模拟操作；不代表所有路径安全或 OS 隔离。
          </p>
          <details>
            <summary>独立记录及未确认状态</summary>
            <Json data={safety} />
          </details>
        </section>
      </div>
      <section className="panel">
        <h2>调用与模型公开发言</h2>
        <p>
          客户端调用通过 Runtime Call
          ID、动作或凭证关联。旧记录无法关联时明确标记；不按 SSE
          到达顺序猜测因果。
        </p>
        {!calls.length && <Empty>尚无可关联的工具调用记录。</Empty>}
        {!compact && (
          <button
            onClick={() => setShowAllCalls((v) => !v)}
            aria-expanded={showAllCalls}
          >
            {showAllCalls
              ? '只看关键执行与模型响应'
              : `展开全部 ${calls.length} 次工具调用`}
          </button>
        )}
        {calls
          .filter(
            (c) =>
              showAllCalls ||
              [
                'begin_task',
                'apply_patch',
                'run_tests',
                'finish_task',
              ].includes(c.tool.replace('relayforge_', '')) ||
              c.status !== 'OK' ||
              e.messages.some((m) => m.after_call_id === c.call_id),
          )
          .map((c) => {
            const runtime = e.calls.find(
              (r) => r.event_id === c.runtime_event_id,
            )
            return (
              <article className="evidence-call" key={c.call_id}>
                <div className="section-head">
                  <h3>{c.tool}</h3>
                  <Badge value={c.status} />
                </div>
                <p className="mono wrap">
                  Client {c.call_id}
                  {c.action_id ? ` · Action ${c.action_id}` : ''}
                </p>
                {c.runtime_event_id ? (
                  <a href={eventLink(c.runtime_event_id)}>
                    Runtime 事件 #{c.runtime_event_id}
                  </a>
                ) : (
                  <p className="muted">
                    Runtime Call ID 未记录 ·{' '}
                    {c.binding === 'legacy_action_or_receipt'
                      ? '已按动作或凭证关联旧操作'
                      : '此调用尚无可靠关联'}
                  </p>
                )}
                <div className="actions">
                  {c.effect_ids.map((id) => (
                    <button key={id} onClick={() => onEffect(id)}>
                      Journal {short(id)}
                    </button>
                  ))}
                </div>
                {runtime && (
                  <details>
                    <summary>受控参数摘要与结构化结果</summary>
                    <Json
                      data={{
                        parameters: runtime.parameters,
                        outcome: runtime.outcome,
                        runtime_call_id: runtime.runtime_call_id,
                        started_at: runtime.started_at,
                        returned_at: runtime.returned_at,
                      }}
                    />
                  </details>
                )}
                {e.messages
                  .filter((m) => m.after_call_id === c.call_id)
                  .map((m) => (
                    <blockquote className="agent-statement" key={m.event_id}>
                      <p className="muted">
                        客户端在此调用后记录的公开发言 · 非 Runtime 判定
                      </p>
                      <p>{m.text}</p>
                      <a href={eventLink(m.event_id)}>发言记录 #{m.event_id}</a>
                    </blockquote>
                  ))}
              </article>
            )
          })}
        {e.messages
          .filter((m) => !m.after_call_id)
          .map((m) => (
            <blockquote className="agent-statement" key={m.event_id}>
              <p>客户端初始公开发言</p>
              <p>{m.text}</p>
            </blockquote>
          ))}
      </section>
      {!compact && (
        <>
          <section className="panel">
            <h2>每次补丁与测试版本</h2>
            {e.artifacts
              .filter((a) =>
                ['workspace.write', 'workspace.test'].includes(a.tool),
              )
              .map((a) => (
                <article key={a.effect_id} className="evidence-call">
                  <div className="section-head">
                    <h3>
                      {a.tool === 'workspace.write' ? '补丁' : 'Docker 测试'}
                    </h3>
                    <button onClick={() => onEffect(a.effect_id)}>
                      Journal {short(a.effect_id)}
                    </button>
                  </div>
                  <p className="mono wrap">
                    Action {a.action_id ?? '未记录'} · Receipt{' '}
                    {a.receipt?.external_id ?? '未记录'}
                  </p>
                  {a.tool === 'workspace.test' ? (
                    <>
                      <p>
                        退出码 {a.result.exit_code ?? '未记录'} · 测试数{' '}
                        {a.result.test_count ?? '未记录'} ·{' '}
                        {a.result.passed === true
                          ? '通过'
                          : a.result.passed === false
                            ? '失败'
                            : '未核验'}
                      </p>
                      <p className="mono wrap">
                        源码版本{' '}
                        {a.result.acceptance?.workspace_hash ?? '未记录'}
                      </p>
                      <p>
                        同版本补丁：
                        {a.matching_patch_actions?.join(', ') ||
                          '未记录或测试发生在补丁之前'}
                      </p>
                      <pre tabIndex={0}>
                        {a.result.stdout}
                        {a.result.stderr}
                      </pre>
                    </>
                  ) : (
                    <>
                      <p className="mono wrap">
                        补丁后版本 {a.result.workspace_hash ?? '旧凭证未记录'}
                      </p>
                      {a.result.diff != null ? (
                        <pre className="diff" tabIndex={0}>
                          {a.result.diff}
                        </pre>
                      ) : (
                        <Empty>
                          旧写入凭证未保留单次 Diff；可在“代码
                          Diff”查看既有产物。
                        </Empty>
                      )}
                      {a.result.diff_truncated && <p>差异输出已截断。</p>}
                    </>
                  )}
                  {a.result.evidence_error && (
                    <p role="alert">{a.result.evidence_error}</p>
                  )}
                  <details>
                    <summary>完整版本与执行凭证</summary>
                    <Json data={a.result} />
                  </details>
                </article>
              ))}
          </section>
          <section className="panel">
            <h2>最终验收与拒绝原因</h2>
            {coding.rejections.map((r) => (
              <p key={r.event_id}>
                <a href={eventLink(r.event_id)}>拒绝事件 #{r.event_id}</a> ·{' '}
                {r.reason}
              </p>
            ))}
            {!coding.evidence && !coding.rejections.length && (
              <Empty>
                尚无最终验收或发布拒绝记录；当前状态为 {coding.state}。
              </Empty>
            )}
            {coding.evidence != null && <Json data={coding.evidence} />}
          </section>
        </>
      )}
    </div>
  )
}
