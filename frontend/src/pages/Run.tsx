import { useEffect, useRef, useState } from 'react'
import { workflowEvidence } from '../domain'
import { MarkerType, Position, type Node, type Edge } from '@xyflow/react'
import {
  api,
  short,
  when,
  runLink,
  type Run,
  type Task,
  type Effect,
  type Decision,
  type Approval,
  type Checkpoint,
  type RuntimeEvent,
  type Artifact,
} from '../api'
import { useLoad, useEvents, useHashState } from '../hooks'
import {
  RuntimeGraph,
  Badge,
  Empty,
  ErrorBox,
  LoadState,
  useConfirmation,
  PageTitle,
  Json,
  Drawer,
  Icon,
} from '../ui'
import { faults, names } from '../options'
import type { RunEvidence } from '../evidence'
import { AgentEvidence } from './Evidence'
export function Workflow({
  events,
  onSelect,
}: {
  events: RuntimeEvent[]
  onSelect?: (step: string) => void
}) {
  const container = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(600)
  useEffect(() => {
    if (!container.current) return
    const observer = new ResizeObserver((entries) =>
      setWidth(entries[0].contentRect.width),
    )
    observer.observe(container.current)
    return () => observer.disconnect()
  }, [])
  const columns = width > 1100 ? 7 : width > 750 ? 4 : width > 440 ? 3 : 2
  const height = Math.ceil(7 / columns) * 110 + 45
  const steps = [
    'inspect',
    'plan',
    'modify',
    'test',
    'review',
    'publish',
    'report',
  ]
  const labels = [
    '读取代码',
    '制定计划',
    '修改文件',
    '运行测试',
    '检查差异',
    '本地发布',
    '验收报告',
  ]
  const { completed, failed } = workflowEvidence(events)
  const nodes: Node[] = steps.map((step, index) => {
    const row = Math.floor(index / columns)
    const column = index % columns
    return {
      id: step,
      position: {
        x: (row % 2 === 0 ? column : columns - 1 - column) * 185,
        y: row * 110,
      },
      sourcePosition:
        column === columns - 1
          ? Position.Bottom
          : row % 2 === 0
            ? Position.Right
            : Position.Left,
      targetPosition:
        column === 0 && row > 0
          ? Position.Top
          : row % 2 === 0
            ? Position.Left
            : Position.Right,
      data: {
        label: (
          <button
            className="graph-step"
            disabled={!completed.has(step)}
            onClick={() => onSelect?.(step)}
          >
            <strong>{labels[index]}</strong>
            <small>
              {failed.has(step)
                ? '真实结果未通过'
                : completed.has(step)
                  ? '已记录完成'
                  : '本次暂无完成事件'}
            </small>
          </button>
        ),
      },
      className: failed.has(step)
        ? 'node-failed'
        : completed.has(step)
          ? 'node-done'
          : 'node-pending',
      draggable: false,
    }
  })
  const edges: Edge[] = steps.slice(1).map((step, index) => ({
    id: 'edge-' + step,
    source: steps[index],
    target: step,
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: completed.has(step) ? '#879a79' : '#665b4a',
    },
    style: { stroke: completed.has(step) ? '#879a79' : '#665b4a' },
  }))
  return (
    <div
      ref={container}
      style={{ height }}
      className="workflow"
      aria-label="真实事件驱动的工作流图"
    >
      <RuntimeGraph
        key={columns}
        nodes={nodes}
        edges={edges}
        padding={width < 440 ? 0.03 : 0.15}
      />
    </div>
  )
}
export function EffectsTable({
  effects,
  onSelect,
}: {
  effects: Effect[]
  onSelect: (id: string) => void
}) {
  if (!effects.length)
    return <Empty>暂无工具操作记录。任务领取后，受控调用会出现在这里。</Empty>
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>工具 / Effect</th>
            <th>结果状态</th>
            <th>类型</th>
            <th>策略</th>
            <th>派发次数</th>
            <th>Run</th>
          </tr>
        </thead>
        <tbody>
          {effects.map((e) => (
            <tr key={e.effect_id}>
              <td>
                <button className="link" onClick={() => onSelect(e.effect_id)}>
                  {e.tool_name}
                </button>
                <small className="mono">{short(e.effect_id)}</small>
              </td>
              <td>
                <Badge value={e.status} />
              </td>
              <td className="mono small">{e.effect_class}</td>
              <td>{e.policy_decision}</td>
              <td>{e.attempt_count}</td>
              <td>
                <a className="mono" href={runLink(e.run_id)}>
                  {short(e.run_id)} ↗
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
export function EffectDetail({
  id,
  onClose,
  onChanged,
}: {
  id: string
  onClose: () => void
  onChanged: () => void
}) {
  const { ask, confirmation } = useConfirmation()
  const [revision, setRevision] = useState(0)
  const state = useLoad(
    'effect-' + id,
    () =>
      Promise.all([
        api<Effect>('/effects/' + id),
        api<Decision[]>('/effects/' + id + '/decisions'),
        api<Approval | null>('/effects/' + id + '/approval'),
      ]),
    revision,
  )
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  async function act(action: string) {
    if (
      action === 'approve' &&
      !(await ask(
        '批准此分支、动作和参数对应的高风险操作？批准后仍由 Runtime 检查并执行。',
      ))
    )
      return
    if (
      action === 'reject' &&
      !(await ask('拒绝此操作？它将无法凭此审批执行。'))
    )
      return
    setBusy(action)
    setError('')
    try {
      await api('/effects/' + id + '/' + action, {})
      setRevision((v) => v + 1)
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }
  const e = state.data?.[0],
    approval = state.data?.[2]
  return (
    <Drawer title="操作详情" onClose={onClose}>
      {confirmation}
      <LoadState {...state} />
      <ErrorBox error={error} />
      {e && (
        <>
          <div className="section-head">
            <h3>{e.tool_name}</h3>
            <Badge value={e.status} />
          </div>
          <p className="mono wrap">{e.effect_id}</p>
          <dl className="details">
            <div>
              <dt>关联运行</dt>
              <dd>
                <a href={runLink(e.run_id)} onClick={onClose} className="mono">
                  {short(e.run_id)} <Icon name="external" size={12} />
                </a>
              </dd>
            </div>
            <div>
              <dt>分支</dt>
              <dd className="mono">{e.branch_id}</dd>
            </div>
            <div>
              <dt>逻辑动作</dt>
              <dd className="mono wrap">{e.logical_action_id}</dd>
            </div>
            <div>
              <dt>权限策略</dt>
              <dd>{e.policy_decision}</dd>
            </div>
            <div>
              <dt>实际派发</dt>
              <dd>{e.attempt_count} 次 · 不等于外部副作用次数</dd>
            </div>
            <div>
              <dt>审批</dt>
              <dd>
                {approval ? (
                  <Badge value={approval.decision} />
                ) : (
                  '无需审批或尚未请求'
                )}
              </dd>
            </div>
            <div>
              <dt>参数哈希</dt>
              <dd className="mono wrap">{e.payload_hash}</dd>
            </div>
          </dl>
          {approval?.decision === 'PENDING' && (
            <div className="actions">
              <button
                className="primary"
                disabled={!!busy}
                onClick={() => void act('approve')}
              >
                {busy === 'approve' ? '批准中…' : '批准此操作'}
              </button>
              <button disabled={!!busy} onClick={() => void act('reject')}>
                {busy === 'reject' ? '拒绝中…' : '拒绝'}
              </button>
            </div>
          )}
          {['UNKNOWN', 'IN_FLIGHT'].includes(e.status) && (
            <>
              <p className="notice">
                UNKNOWN
                表示证据不足。对账只查询已有操作证据，不能保证结果会变为确认。
              </p>
              <button disabled={!!busy} onClick={() => void act('reconcile')}>
                {busy === 'reconcile' ? '查询证据中…' : '查询证据并对账'}
              </button>
            </>
          )}
          <h3>恢复决策与依据</h3>
          {state.data?.[1].length ? (
            state.data[1].map((d) => (
              <div className="decision" key={d.id}>
                <Badge value={d.decision} />
                <small>{when(d.created_at)}</small>
                <Json data={d.evidence} />
              </div>
            ))
          ) : (
            <Empty>尚无恢复决策。</Empty>
          )}
          <details className="evidence-disclosure">
            <summary>脱敏输入摘要</summary>
            <Json data={e.payload_summary} />
          </details>
          <details
            className="evidence-disclosure"
            open={['CONFIRMED', 'UNKNOWN'].includes(e.status)}
          >
            <summary>外部回执</summary>
            <Json data={e.external_receipt} />
          </details>
        </>
      )}
    </Drawer>
  )
}
export function Checkpoints({
  runId,
  onCreated,
  tick,
  readonly = false,
}: {
  runId: string
  onCreated: (id: string) => void
  tick: number
  readonly?: boolean
}) {
  const state = useLoad(
    'checkpoints-' + runId,
    () => api<Checkpoint[]>('/runs/' + runId + '/checkpoints'),
    tick,
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function fork(checkpointId: string) {
    setBusy(true)
    setError('')
    try {
      const row = await api<{ run_id: string }>('/runs/' + runId + '/fork', {
        checkpoint_id: checkpointId,
      })
      onCreated(row.run_id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="panel">
      <h2>检查点与 Fork</h2>
      <p>从已验证快照创建独立分支。外部历史只读，新操作重新审批。</p>
      <LoadState {...state} />
      <ErrorBox error={error} />
      {state.data &&
        (state.data.length ? (
          <div className="checkpoint-list">
            {state.data.map((row) => (
              <div key={row.checkpoint_id}>
                <div>
                  <strong>{row.stage ?? '初始状态'}</strong>
                  <small className="mono">
                    检查点 {short(row.checkpoint_id)} · 快照{' '}
                    {short(row.snapshot_id)}
                  </small>
                </div>
                <button
                  aria-label={`从 ${row.stage ?? '初始'} 检查点 Fork`}
                  disabled={busy || readonly || !row.snapshot_id}
                  onClick={() => void fork(row.checkpoint_id)}
                >
                  {readonly
                    ? '评测记录只读'
                    : row.snapshot_id
                      ? '从此处 Fork'
                      : '缺少历史快照'}
                </button>
              </div>
            ))}
          </div>
        ) : (
          <Empty>Worker 尚未保存检查点。</Empty>
        ))}
    </section>
  )
}
function TestArtifacts({
  artifacts,
  compact = false,
}: {
  artifacts: Artifact[]
  compact?: boolean
}) {
  const rows = artifacts.filter((a) => a.tool === 'workspace.test')
  const visible = compact
    ? [rows.filter((a) => !a.readonly).at(-1) ?? rows.at(-1)].filter(
        (a): a is Artifact => !!a,
      )
    : rows
  return (
    <>
      {!visible.length ? (
        <Empty>尚无真实测试结果。</Empty>
      ) : (
        visible.map((a) => {
          const r = a.result
          const passed =
            r.passed === true && r.exit_code === 0 && r.timed_out === false
          const known =
            r.timed_out === false &&
            Number.isInteger(r.exit_code) &&
            typeof r.passed === 'boolean'
          return (
            <article className="test-result" key={a.artifact_id}>
              <div className="section-head">
                <h3>{a.readonly ? '历史测试 · 只读' : 'Docker 测试'}</h3>
                <Badge
                  value={
                    !known ? 'UNKNOWN' : passed ? 'TEST_PASSED' : 'TEST_FAILED'
                  }
                />
              </div>
              <div className="test-facts">
                <span>
                  退出码{' '}
                  <b className="mono">{String(r.exit_code ?? '无结果')}</b>
                </span>
                <span>
                  测试项 <b>{String(r.test_count ?? '未统计')}</b>
                </span>
                <span>
                  超时{' '}
                  <b>
                    {r.timed_out === true
                      ? '是'
                      : r.timed_out === false
                        ? '否'
                        : '未记录'}
                  </b>
                </span>
              </div>
              {a.readonly && (
                <p>
                  <a href={runLink(a.source_run_id)}>
                    查看来源运行 {short(a.source_run_id)}
                  </a>
                </p>
              )}
              {!compact && (
                <>
                  <pre className="json" tabIndex={0}>
                    {String(r.output ?? '尚无输出')}
                  </pre>
                  <details className="evidence-disclosure">
                    <summary>完整测试凭证与版本指纹</summary>
                    <Json data={r} />
                  </details>
                </>
              )}
              {compact && (
                <p className="muted">
                  测试证据与版本指纹可在“测试结果”分区查看；任务验收仍以发布门禁为准。
                </p>
              )}
            </article>
          )
        })
      )}
    </>
  )
}
function Timeline({
  events,
  selected,
}: {
  events: RuntimeEvent[]
  selected: string
}) {
  const [query, setQuery] = useState('')
  const shown = [...events]
    .reverse()
    .filter(
      (e) =>
        !query ||
        [e.id, e.type, JSON.stringify(e.data)]
          .join(' ')
          .toLowerCase()
          .includes(query.toLowerCase()),
    )
    .slice(0, 80)
  const target = events.find((e) => e.id === selected)
  if (target && !shown.some((e) => e.id === selected)) shown.unshift(target)
  useEffect(() => {
    if (!selected) return
    const element = document.getElementById('event-' + selected)
    element?.scrollIntoView({
      block: 'nearest',
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches
        ? 'instant'
        : 'smooth',
    })
    element?.focus({ preventScroll: true })
  }, [selected, events.length])
  return (
    <section className="panel timeline">
      <div className="section-head">
        <div>
          <h2>执行事件</h2>
          <p>已接收 {events.length} 条 · 按持久化发布顺序，最新优先。</p>
        </div>
      </div>
      <label className="search-field">
        搜索已接收事件
        <Icon name="search" size={16} />
        <input
          type="search"
          value={query}
          placeholder="事件名称、编号或内容"
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>
      {shown.length ? (
        shown.map((e) => (
          <details
            id={'event-' + e.id}
            key={e.id}
            tabIndex={-1}
            open={selected === e.id || undefined}
            className={selected === e.id ? 'selected-event' : ''}
          >
            <summary>
              <span className="mono event-id">#{e.id}</span>
              <strong>{e.type}</strong>
              <time>{when(e.timestamp)}</time>
            </summary>
            {e.effect_id && <p className="mono">Effect {short(e.effect_id)}</p>}
            <Json data={e.data} />
          </details>
        ))
      ) : (
        <Empty>{query ? '当前筛选没有匹配事件。' : '等待真实执行事件。'}</Empty>
      )}
      {events.length > 80 && (
        <p className="muted">
          最多展示80条匹配记录；完整历史仍可通过事件 API 分页读取。
        </p>
      )}
    </section>
  )
}
export function RunDetail({
  id,
  tick,
  onCreated,
  onEffect,
}: {
  id: string
  tick: number
  onCreated: (id: string) => void
  onEffect: (id: string) => void
}) {
  const { ask, confirmation } = useConfirmation()
  const [revision, setRevision] = useState(0)
  const { events, connection } = useEvents(id)
  const state = useLoad(
    'run-' + id,
    async () => {
      const run = await api<Run>('/runs/' + id)
      const rest = await Promise.all([
        api<Effect[]>('/runs/' + id + '/effects'),
        api<Artifact[]>('/runs/' + id + '/artifacts'),
        api<{ readonly: boolean; effect: Effect }[]>(
          '/runs/' + id + '/effect-references',
        ),
        api<Task>('/tasks/' + run.task_id),
        api<RunEvidence>('/runs/' + id + '/evidence'),
      ])
      return [run, ...rest] as const
    },
    tick + revision,
  )
  const [tab] = useHashState('tab', 'overview')
  const [selectedEvent] = useHashState('event')
  const [selectedSource] = useHashState('source_event')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    const timer = window.setInterval(() => setRevision((v) => v + 1), 5000)
    return () => clearInterval(timer)
  }, [id])
  async function act(action: string) {
    if (
      action === 'cancel' &&
      !(await ask('取消当前任务？已经发生的外部操作不会被撤销。'))
    )
      return
    setBusy(action)
    setError('')
    try {
      await api('/runs/' + id + '/' + action, {})
      setRevision((v) => v + 1)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }
  const run = state.data?.[0],
    task = state.data?.[4],
    controlled = state.data?.[5].controller === 'mcp'
  const tabs = [
    ['overview', '执行概览'],
    ['evidence', 'Agent 证据'],
    ['events', '事件时间线'],
    ['journal', 'Effect Journal'],
    ['tests', '测试结果'],
    ['diff', '代码 Diff'],
    ['checkpoints', '检查点与 Fork'],
  ]
  const active = tabs.some(([key]) => key === tab) ? tab : 'overview'
  function locate(step: string) {
    const event = [...events]
      .reverse()
      .find(
        (e) =>
          (e.type === 'workflow.node' && e.data.node === step) ||
          (step === 'report' && e.type === 'run.acceptance'),
      )
    if (event)
      window.location.hash =
        runLink(id) + '?tab=events&event=' + encodeURIComponent(event.id)
    else setError('该节点尚无完成事件；不会根据相邻步骤推断成功。')
  }
  return (
    <>
      {confirmation}
      <a className="back-link" href="#/runs">
        返回任务与运行
      </a>
      <div className="page-title">
        <div>
          <div className="eyebrow">
            {controlled ? 'MCP / CONTROLLED TASK' : 'RUN / EXECUTION DETAIL'}
          </div>
          <h1>{task?.title ?? '运行详情'}</h1>
          <p className="mono wrap">{id}</p>
        </div>
        <div className="actions">
          {run && (
            <>
              <Badge value={run.status} />
              <button
                disabled={
                  !!busy ||
                  controlled ||
                  !!run.evaluation_run_id ||
                  [
                    'QUEUED',
                    'RUNNING',
                    'PREPARING',
                    'CANCEL_REQUESTED',
                  ].includes(run.status)
                }
                onClick={() => void act('replay')}
              >
                {busy === 'replay' ? '恢复中…' : 'Replay / 恢复'}
              </button>
              <button
                disabled={
                  !!busy ||
                  !!run.evaluation_run_id ||
                  [
                    'SUCCEEDED',
                    'FAILED',
                    'CANCELLED',
                    'CANCEL_REQUESTED',
                  ].includes(run.status)
                }
                onClick={() => void act('cancel')}
              >
                {busy === 'cancel' ? '取消中…' : '取消任务'}
              </button>
            </>
          )}
        </div>
      </div>
      <ErrorBox error={error} />
      <LoadState {...state} />
      {run && (
        <>
          <div className="run-facts">
            <span>
              配置{' '}
              <b>
                {run.mode}
                {run.evaluation_run_id ? ' · 实验记录（只读）' : ''}
              </b>
            </span>
            <span>
              分支 <b className="mono">{short(run.branch_id)}</b>
            </span>
            <span>
              故障{' '}
              <b>
                {faults.find((f) => f.id === run.fault)?.label ?? run.fault}
              </b>
            </span>
            <span>
              测试退出码 <b>{run.test_exit_code ?? '尚无结果'}</b>
            </span>
            <span>
              Worker <b className="mono">{short(run.owner)}</b>
            </span>
            <span className="stream-indicator">
              <i className={connection === '已连接' ? 'live' : ''} />
              {connection} · {events.length} 条事件
            </span>
          </div>
          {controlled && (
            <div className="notice">
              MCP 受控任务不使用固定图
              Replay/Fork；通过原任务上下文和动作身份恢复。实际调用、测试和验收记录见下方分区。
            </div>
          )}
          <nav className="detail-tabs" aria-label="运行详情分区">
            {tabs.map(([key, label]) => (
              <a
                key={key}
                href={runLink(id) + (key === 'overview' ? '' : '?tab=' + key)}
                aria-current={active === key ? 'page' : undefined}
                className={active === key ? 'active' : ''}
              >
                {label}
              </a>
            ))}
          </nav>
          {active === 'overview' && (
            <>
              {controlled && (
                <AgentEvidence
                  evidence={state.data![5]}
                  onEffect={onEffect}
                  compact
                />
              )}
              <section className="panel">
                <div className="section-head">
                  <div>
                    <h2>{controlled ? '受控执行记录' : '工作流'}</h2>
                    <p>
                      {controlled
                        ? '工具与验收的实际事件，不推断固定图节点。'
                        : '既定流程；节点仅依据真实完成事件着色。点击节点定位关联事件。'}
                    </p>
                  </div>
                </div>
                {controlled ? (
                  <div className="checkpoint-list">
                    {events
                      .filter(
                        (e) =>
                          e.type.startsWith('controlled.') ||
                          e.type === 'run.acceptance',
                      )
                      .slice(-8)
                      .reverse()
                      .map((e) => (
                        <div key={e.id}>
                          <span>{e.type}</span>
                          <a
                            href={runLink(id) + '?tab=events&event=' + e.id}
                            className="mono"
                          >
                            事件 #{e.id}
                          </a>
                        </div>
                      ))}
                  </div>
                ) : (
                  <Workflow events={events} onSelect={locate} />
                )}
              </section>
              <div className="detail-grid">
                <section className="panel">
                  <div className="section-head">
                    <h2>最近测试</h2>
                    <a href={runLink(id) + '?tab=tests'}>查看证据</a>
                  </div>
                  <TestArtifacts artifacts={state.data![2]} compact />
                </section>
                <section className="panel">
                  <div className="section-head">
                    <h2>操作摘要</h2>
                    <a href={runLink(id) + '?tab=journal'}>查看账本</a>
                  </div>
                  <p>
                    {run.mode === 'baseline'
                      ? 'Baseline 未启用 Journal；请查看事件与测试产物。'
                      : `${state.data![1].length} 条本次操作 · ${state.data![1].filter((e) => ['UNKNOWN', 'IN_FLIGHT'].includes(e.status)).length} 条尚需核验`}
                  </p>
                  <p className="muted">
                    工具 CONFIRMED 与任务 SUCCEEDED 是不同层次的结果。
                  </p>
                </section>
              </div>
            </>
          )}
          {active === 'evidence' && (
            <AgentEvidence evidence={state.data![5]} onEffect={onEffect} />
          )}
          {active === 'events' && (
            <Timeline
              events={events}
              selected={
                events.find((e) => String(e.source_event_id) === selectedSource)
                  ?.id ?? selectedEvent
              }
            />
          )}
          {active === 'journal' && (
            <>
              <section className="panel">
                <h2>本次运行的工具操作</h2>
                {run.mode === 'baseline' ? (
                  <Empty>
                    Baseline 不启用 Effect
                    Journal。真实调用可查看事件和测试产物。
                  </Empty>
                ) : (
                  <EffectsTable effects={state.data![1]} onSelect={onEffect} />
                )}
              </section>
              {state.data![3].length > 0 && (
                <section className="panel">
                  <h2>父分支历史 · 只读引用</h2>
                  <p>操作归属原分支，不代表本分支重新派发。</p>
                  <EffectsTable
                    effects={state.data![3].map((r) => r.effect)}
                    onSelect={onEffect}
                  />
                </section>
              )}
            </>
          )}
          {active === 'tests' && (
            <section className="panel">
              <h2>测试结果与版本证据</h2>
              <TestArtifacts artifacts={state.data![2]} />
            </section>
          )}
          {active === 'diff' && (
            <section className="panel">
              <h2>实际代码差异</h2>
              {state.data![2].filter((a) => a.tool === 'workspace.diff')
                .length ? (
                state
                  .data![2].filter((a) => a.tool === 'workspace.diff')
                  .map((a) => (
                    <article key={a.artifact_id}>
                      <h3>
                        {a.readonly ? '历史检查点 · 只读差异' : '文件差异'}
                      </h3>
                      {a.readonly && (
                        <a href={runLink(a.source_run_id)}>
                          来源运行 {short(a.source_run_id)}
                        </a>
                      )}
                      <pre className="diff" tabIndex={0}>
                        {String(
                          a.result.diff ?? JSON.stringify(a.result, null, 2),
                        )}
                      </pre>
                    </article>
                  ))
              ) : (
                <Empty>尚无差异产物。</Empty>
              )}
            </section>
          )}
          {active === 'checkpoints' &&
            (controlled ? (
              <section className="panel">
                <Empty>
                  MCP 任务不使用固定图检查点与
                  Fork。保留原上下文和动作身份以恢复受控操作。
                </Empty>
              </section>
            ) : (
              <Checkpoints
                runId={id}
                onCreated={onCreated}
                tick={tick + revision}
                readonly={!!run.evaluation_run_id}
              />
            ))}
        </>
      )}
    </>
  )
}
export function JournalPage({
  tick,
  onEffect,
}: {
  tick: number
  onEffect: (id: string) => void
}) {
  const [query, setQuery] = useHashState('q'),
    [filter, setFilter] = useHashState('status'),
    [policy, setPolicy] = useHashState('policy')
  const state = useLoad(
    'journal',
    () => api<Effect[]>('/effects?limit=200'),
    tick,
  )
  const effects = (state.data ?? []).filter(
    (e) =>
      (!filter || e.status === filter) &&
      (!policy || e.policy_decision === policy) &&
      [e.tool_name, e.effect_id, e.logical_action_id, e.run_id]
        .join(' ')
        .toLowerCase()
        .includes(query.toLowerCase()),
  )
  return (
    <>
      <PageTitle
        title="操作账本"
        description="最近 200 条操作 · 搜索和筛选仅覆盖本次已加载数据。"
      />
      <section className="panel">
        <div className="filter-bar">
          <label className="search-field">
            搜索已加载操作
            <Icon name="search" size={16} />
            <input
              type="search"
              value={query}
              placeholder="工具、动作、Effect 或 Run ID"
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <label>
            结果状态
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="">全部状态</option>
              {['UNKNOWN', 'IN_FLIGHT', 'PREPARED', 'CONFIRMED', 'FAILED'].map(
                (v) => (
                  <option key={v} value={v}>
                    {names[v]}
                  </option>
                ),
              )}
            </select>
          </label>
          <label>
            权限策略
            <select value={policy} onChange={(e) => setPolicy(e.target.value)}>
              <option value="">全部策略</option>
              {['allow', 'ask', 'deny'].map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <span className="filter-count">
            {state.data
              ? `${effects.length} / ${state.data.length} 条`
              : '读取中'}
          </span>
          {(query || filter || policy) && (
            <button
              className="ghost"
              onClick={() => {
                setQuery('')
                setFilter('')
                setPolicy('')
              }}
            >
              清除筛选
            </button>
          )}
        </div>
        <LoadState {...state} />
        {state.data &&
          (effects.length ? (
            <EffectsTable effects={effects} onSelect={onEffect} />
          ) : (
            <Empty>当前范围没有匹配操作。更早的记录可从关联运行查看。</Empty>
          ))}
      </section>
      <div className="notice">
        CONFIRMED 需要工具证据；FAILED 需要明确失败依据；UNKNOWN
        表示证据不足。派发次数不等于实际外部副作用次数。
      </div>
    </>
  )
}
