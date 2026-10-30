import { useState } from 'react'
import {
  api,
  short,
  when,
  runLink,
  type Run,
  type Task,
  type Overview,
  type TemplateOption,
  type Evaluation,
  type EvaluationResult,
  type RuntimeEvent,
} from '../api'
import { useLoad, useHashState } from '../hooks'
import {
  Badge,
  Empty,
  ErrorBox,
  LoadState,
  useConfirmation,
  PageTitle,
  Icon,
} from '../ui'
import { faults, comparisonFaults, names } from '../options'
import { sourceLabel } from '../evidence'
export function RunTable({
  runs,
  tasks,
}: {
  runs: (Run & { execution_source?: string })[]
  tasks: Task[]
}) {
  const titles = new Map(tasks.map((t) => [t.id, t.title]))
  if (!runs.length)
    return <Empty>暂无运行。创建任务，开始验证本地执行流程。</Empty>
  return (
    <div className="table-wrap">
      <table className="run-table">
        <thead>
          <tr>
            <th>任务 / Run</th>
            <th>状态</th>
            <th className="fault-column">故障配置</th>
            <th>测试退出码</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td>
                <a href={runLink(run.id)}>
                  {titles.get(run.task_id) ?? short(run.task_id)}
                </a>
                <small className="mono">
                  {short(run.id)} · 分支 {short(run.branch_id)}
                </small>
                <small>{sourceLabel(run.execution_source)}</small>
              </td>
              <td>
                <Badge value={run.status} />
              </td>
              <td className="fault-column">
                {faults.find((f) => f.id === run.fault)?.label ?? run.fault}
              </td>
              <td className="mono">{run.test_exit_code ?? '尚无结果'}</td>
              <td className="muted">{when(run.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
export function CreateTask({
  failure = false,
  onCreated,
  provider = 'mock',
}: {
  failure?: boolean
  onCreated: (id: string) => void
  provider?: string
}) {
  const { ask, confirmation } = useConfirmation()
  const [title, setTitle] = useState('修复示例加法函数')
  const [template, setTemplate] = useState('fix_addition')
  const templates = useLoad('templates', () =>
    api<TemplateOption[]>('/templates'),
  )
  const [fault, setFault] = useState(failure ? 'response_lost' : 'none')
  const [execution, setExecution] = useState('full')
  const [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  const available =
    execution === 'comparison'
      ? [
          ...faults.filter((f) => f.id !== 'before_failure'),
          ...comparisonFaults,
        ]
      : faults
  const current = available.find((f) => f.id === fault)
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (
      (fault !== 'none' || execution === 'comparison') &&
      !(await ask(
        `${execution === 'comparison' ? '将创建三配置对照，共3条真实观测。' : '将创建一个本地故障任务。'}${current?.note ?? ''} 仅使用本地模拟服务；已发生的副作用不会因取消而撤销。继续？`,
      ))
    )
      return
    setBusy(true)
    setError('')
    try {
      if (failure && execution === 'comparison') {
        const matrixFault =
          (
            {
              after_external_before_receipt: 'commit_without_receipt',
              after_dispatch_before_call: 'worker_restart',
            } as Record<string, string>
          )[fault] ?? fault
        await api('/evaluations/run', {
          templates: [template],
          faults: [matrixFault],
          repeats: 1,
        })
        window.location.hash = '#/evaluations'
      } else {
        const created = await api<{ run_id: string }>('/tasks', {
          title,
          template,
          fault,
        })
        onCreated(created.run_id)
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <form className="panel create-form" onSubmit={submit} aria-busy={busy}>
      <div className="section-head">
        <div>
          <h2>{failure ? '配置受控实验' : '本地修复任务'}</h2>
          <p>
            {provider === 'mock'
              ? 'Mock 产生确定性修改；工作区、Docker 测试和验收真实执行。'
              : '使用已配置的模型接口提出修改，仍须通过本地测试与验收。'}
          </p>
        </div>
        <span className="pill">LOCAL ONLY</span>
      </div>
      <fieldset>
        <legend>
          <span className="step-number">1</span> 选择任务
        </legend>
        <div className="form-grid task-fields">
          <label>
            任务名称
            <input
              value={title}
              disabled={failure && execution === 'comparison'}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
              required
            />
          </label>
          <label>
            示例任务
            <select
              value={template}
              disabled={templates.loading || !templates.data}
              onChange={(e) => setTemplate(e.target.value)}
              aria-label="示例任务"
            >
              {templates.data ? (
                templates.data.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.title} · {row.id}
                  </option>
                ))
              ) : (
                <option value="fix_addition">正在读取任务集…</option>
              )}
            </select>
          </label>
        </div>
      </fieldset>
      <fieldset>
        <legend>
          <span className="step-number">2</span>{' '}
          {failure ? '选择故障边界' : '执行条件'}
        </legend>
        {failure && (
          <div className="fault-options" role="group" aria-label="常用故障">
            {['none', 'response_lost', 'sandbox_terminated'].map((id) => {
              const f = faults.find((row) => row.id === id)!
              return (
                <button
                  type="button"
                  key={id}
                  className={'fault-option ' + (fault === id ? 'selected' : '')}
                  aria-pressed={fault === id}
                  onClick={() => setFault(id)}
                >
                  <Icon name={id === 'none' ? 'check' : 'failure'} />
                  <strong>{f.label}</strong>
                  <small>{f.note}</small>
                </button>
              )
            })}
          </div>
        )}
        <details className="advanced-config">
          <summary>
            {failure ? '更多故障与执行模式' : '故障注入（默认关闭）'}
            <span className="muted">{current?.label}</span>
          </summary>
          <div className="form-grid task-fields">
            <label>
              故障条件
              <select value={fault} onChange={(e) => setFault(e.target.value)}>
                {available.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.label}
                  </option>
                ))}
              </select>
            </label>
            {failure && (
              <label>
                执行模式
                <select
                  aria-label="执行模式"
                  value={execution}
                  onChange={(e) => {
                    setExecution(e.target.value)
                    if (
                      (e.target.value === 'comparison' &&
                        fault === 'before_failure') ||
                      (e.target.value === 'full' &&
                        !faults.some((row) => row.id === fault))
                    )
                      setFault('response_lost')
                  }}
                >
                  <option value="full">普通任务 · Full Runtime</option>
                  <option value="comparison">
                    对照评测 · Baseline / Journal / Full
                  </option>
                </select>
              </label>
            )}
          </div>
        </details>
      </fieldset>
      <div className="form-bottom">
        <span className="muted">
          {current?.note}
          {execution === 'comparison' && ' 三种配置各执行一次，共3条观测。'}
          {fault !== 'none' && ' 提交前需确认故障影响。'}
        </span>
        <button
          className="primary"
          disabled={
            busy ||
            (!title.trim() && execution !== 'comparison') ||
            !templates.data?.length
          }
        >
          {busy
            ? '提交中…'
            : execution === 'comparison'
              ? '启动三配置对照'
              : '创建并排队'}
          <Icon name="arrow" size={15} />
        </button>
      </div>
      <ErrorBox error={error || templates.error} />
      {confirmation}
    </form>
  )
}
export function OverviewActivity({
  runs,
  tick,
}: {
  runs: Run[]
  tick: number
}) {
  const recent = runs.filter((run) => run.fault !== 'none').slice(0, 8)
  const state = useLoad(
    'activity-' + recent.map((run) => run.id).join(','),
    () =>
      Promise.all([
        api<{
          signal: string
          activity?: string
          run_id?: string
          suite_id?: string
        }>('/worker'),
        api<Evaluation[]>('/evaluations'),
        Promise.all(
          recent.map((run) =>
            api<{ events: RuntimeEvent[] }>(
              `/runs/${run.id}/events/history?limit=500`,
            ),
          ),
        ),
      ]),
    tick,
  )
  const events = state.data?.[2]
    .flatMap((row) => row.events)
    .filter(
      (row) =>
        row.type === 'effect.outcome' &&
        /provider_query|stopped_owner|native_|insufficient|quiesc|absence|interrupted/.test(
          String(row.data.method),
        ),
    )
    .sort((a, b) => (BigInt(a.id) < BigInt(b.id) ? 1 : -1))
    .slice(0, 6)
  return (
    <>
      <LoadState {...state} />
      {state.data && (
        <div className="detail-grid">
          <section className="panel">
            <h2>执行服务</h2>
            <p>
              {state.data[0].signal === 'recent_heartbeat'
                ? '收到近期心跳'
                : '未收到近期心跳'}{' '}
              ·{' '}
              {(
                {
                  idle: '等待任务',
                  worker: '执行任务',
                  evaluation: '执行评测',
                  waiting_for_execution_ownership: '等待执行所有权',
                  database_unavailable: '数据库不可用',
                } as Record<string, string>
              )[state.data[0].activity ?? ''] ?? '状态待核验'}
            </p>
            {state.data[0].run_id && (
              <a href={runLink(state.data[0].run_id)}>
                当前运行 {short(state.data[0].run_id)} →
              </a>
            )}
            {state.data[0].suite_id && (
              <a href="#/evaluations">
                当前评测 {short(state.data[0].suite_id)} →
              </a>
            )}
            <p className="muted">心跳是存活线索，不能证明工具操作已经完成。</p>
            <h3>失败与待检查</h3>
            {runs
              .filter((run) => ['FAILED', 'NEEDS_REVIEW'].includes(run.status))
              .slice(0, 4)
              .map((run) => (
                <p key={run.id}>
                  <a href={runLink(run.id)}>{short(run.id)}</a> ·{' '}
                  <Badge value={run.status} />
                </p>
              ))}
          </section>
          <section className="panel">
            <h2>近期恢复事件</h2>
            <p className="muted">最近8条故障运行中的持久化记录。</p>
            {events?.length ? (
              events.map((event) => (
                <p key={event.id}>
                  <a href={runLink(event.run_id)}>
                    #{event.id} · {short(event.run_id)}
                  </a>
                  <small>
                    {String(event.data.method)} → {String(event.data.status)}
                  </small>
                </p>
              ))
            ) : (
              <Empty>这一范围内尚无恢复事件。</Empty>
            )}
            <h3>最近对照评测</h3>
            {state.data[1].length ? (
              state.data[1].slice(0, 3).map((row) => (
                <p key={row.id}>
                  <a href="#/evaluations">
                    {row.mode} · {short(row.suite_id)}
                  </a>{' '}
                  · {row.status}
                </p>
              ))
            ) : (
              <Empty>尚未启动评测。</Empty>
            )}
          </section>
        </div>
      )}
    </>
  )
}
export function OverviewPage({ tick }: { tick: number }) {
  const state = useLoad(
    'overview',
    () =>
      Promise.all([
        api<Overview>('/overview'),
        api<Run[]>('/runs'),
        api<Task[]>('/tasks'),
      ]),
    tick,
  )
  return (
    <>
      <div className="intro">
        <div className="eyebrow">RUNTIME / OVERVIEW</div>
        <h1>运行总览</h1>
        <p>检查本地执行状态，跟进需要证据或审批的操作。</p>
      </div>
      <LoadState {...state} />
      {state.data && (
        <>
          <div className="metrics">
            <div>
              <span>记录的运行</span>
              <strong>
                {Object.values(state.data[0].runs_by_status).reduce(
                  (a, b) => a + b,
                  0,
                )}
              </strong>
              <small>数据库中的全部 Run</small>
            </div>
            <div>
              <span>排队 / 执行中</span>
              <strong>
                {(state.data[0].runs_by_status.QUEUED ?? 0) +
                  (state.data[0].runs_by_status.RUNNING ?? 0)}
              </strong>
              <small>待领取与执行状态</small>
            </div>
            <div>
              <span>未知结果</span>
              <a href="#/journal?status=UNKNOWN">
                <strong className="amber">
                  {state.data[0].unknown_effects}
                </strong>
              </a>
              <small>需要独立证据对账</small>
            </div>
            <div>
              <span>待处理审批</span>
              <strong>{state.data[0].pending_approvals}</strong>
              <small>批准仍需 Run 有效</small>
            </div>
          </div>
          <section className="panel">
            <div className="section-head">
              <h2>最近运行</h2>
              <a href="#/runs">查看全部 →</a>
            </div>
            <RunTable runs={state.data[1].slice(0, 8)} tasks={state.data[2]} />
          </section>
          <OverviewActivity runs={state.data[1]} tick={tick} />
        </>
      )}
      <div className="notice">
        Mock 模式只写本地模拟服务。UNKNOWN
        会保留为未知；“工具已确认”不等于“任务已验收”。
      </div>
    </>
  )
}
export function FaultEvidence({ tick }: { tick: number }) {
  const state = useLoad(
    'fault-evidence',
    async () => {
      const evaluations = await api<Evaluation[]>('/evaluations')
      const suite = evaluations.find((row) => row.scope === 'matrix')?.suite_id
      if (!suite) return []
      const groups = await Promise.all(
        evaluations
          .filter((row) => row.suite_id === suite)
          .map(async (row) => ({
            mode: row.mode,
            results: await api<EvaluationResult[]>(
              `/evaluations/${row.id}/results`,
            ),
          })),
      )
      return groups.flatMap((group) =>
        group.results
          .filter(
            (row) =>
              row.observed.phase === 'COMPLETED' &&
              row.observed.fault !== 'none',
          )
          .slice(0, 3)
          .map((row) => ({ ...row, mode: group.mode })),
      )
    },
    tick,
  )
  return (
    <section className="panel">
      <div className="section-head">
        <h2>已记录的故障观测</h2>
        <a href="#/evaluations">全部评测与原始记录 →</a>
      </div>
      <p>
        当前最新套件每种配置选取至多3条已完成观测。数字来自独立模拟服务；不把
        Journal 行数当作副作用次数。
      </p>
      <LoadState {...state} />
      {state.data?.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>配置 / 故障边界</th>
                <th>Run状态</th>
                <th>请求 / 实际副作用</th>
                <th>Journal状态</th>
              </tr>
            </thead>
            <tbody>
              {state.data.map((row) => (
                <tr key={row.id}>
                  <td>
                    <a href={runLink(row.run_id!)}>
                      {row.mode} · {String(row.observed.fault)}
                    </a>
                    <small>
                      {row.observed.infrastructure_error
                        ? '本次观测中断，保留错误'
                        : String(
                            row.observed.fault_boundary ?? row.observed.fault,
                          )}
                    </small>
                  </td>
                  <td>
                    <Badge value={String(row.observed.run_status)} />
                  </td>
                  <td>
                    {String(row.observed.external_requests ?? '未测量')} /{' '}
                    {String(row.observed.actual_external_effects ?? '未测量')}
                  </td>
                  <td>
                    {row.mode === 'baseline'
                      ? '未启用 Journal'
                      : Array.isArray(row.observed.effect_states)
                        ? [
                            ...new Set(
                              row.observed.effect_states.map((effect) =>
                                String(effect.status),
                              ),
                            ),
                          ].join(' / ')
                        : '尚无记录'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        state.data && (
          <Empty>尚无已保存故障观测。先启动一次三配置对照或完整评测。</Empty>
        )
      )}
    </section>
  )
}
export function RunsPage({
  tick,
  onCreated,
  failure = false,
  provider,
}: {
  tick: number
  onCreated: (id: string) => void
  failure?: boolean
  provider?: string
}) {
  const state = useLoad(
    'runs-' + failure,
    () => Promise.all([api<Run[]>('/runs'), api<Task[]>('/tasks')]),
    tick,
  )
  const [query, setQuery] = useHashState('q')
  const [status, setStatus] = useHashState('status')
  const [sort, setSort] = useHashState('sort', 'newest')
  const loaded =
    state.data?.[0].filter((r) => !failure || r.fault !== 'none') ?? []
  const titles = new Map((state.data?.[1] ?? []).map((t) => [t.id, t.title]))
  const filtered = loaded
    .filter(
      (r) =>
        (!status || r.status === status) &&
        [r.id, r.branch_id, r.task_id, titles.get(r.task_id) ?? '']
          .join(' ')
          .toLowerCase()
          .includes(query.toLowerCase()),
    )
    .sort((a, b) =>
      sort === 'oldest'
        ? a.created_at.localeCompare(b.created_at)
        : sort === 'status'
          ? a.status.localeCompare(b.status) ||
            b.created_at.localeCompare(a.created_at)
          : b.created_at.localeCompare(a.created_at),
    )
  return (
    <>
      <PageTitle
        title={failure ? '故障实验室' : '任务与运行'}
        description={
          failure
            ? '在本地注入真实故障，观察中断、未知结果与证据恢复。'
            : '最近 100 条运行 · 搜索和筛选仅覆盖已加载记录。'
        }
      />
      {failure && (
        <>
          <CreateTask onCreated={onCreated} failure provider={provider} />
          <div className="notice">
            崩溃注入会使 Worker 退出，调度服务随后核验恢复。证据不足时保留
            UNKNOWN；取消不会撤销已有副作用。
          </div>
          <FaultEvidence tick={tick} />
        </>
      )}
      <section className="panel">
        <div className="section-head">
          <h2>{failure ? '最近故障运行' : '运行记录'}</h2>
          <span className="pill">已加载记录</span>
        </div>
        <div className="filter-bar">
          <label className="search-field">
            搜索已加载运行
            <Icon name="search" size={16} />
            <input
              type="search"
              value={query}
              placeholder="任务名称、Run 或分支 ID"
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <label>
            运行状态
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">全部状态</option>
              {[
                ...new Set([
                  ...loaded.map((r) => r.status),
                  ...(status ? [status] : []),
                ]),
              ]
                .sort()
                .map((v) => (
                  <option key={v} value={v}>
                    {names[v] ?? v}
                  </option>
                ))}
            </select>
          </label>
          <label>
            排序
            <select value={sort} onChange={(e) => setSort(e.target.value)}>
              <option value="newest">最新优先</option>
              <option value="oldest">最早优先</option>
              <option value="status">按状态</option>
            </select>
          </label>
          <span className="filter-count">
            {state.data ? `${filtered.length} / ${loaded.length} 条` : '读取中'}
          </span>
          {(query || status) && (
            <button
              className="ghost"
              onClick={() => {
                setQuery('')
                setStatus('')
              }}
            >
              清除筛选
            </button>
          )}
        </div>
        <LoadState {...state} />
        {state.data &&
          (filtered.length ? (
            <RunTable runs={filtered} tasks={state.data[1]} />
          ) : (
            <Empty>
              {query || status
                ? '当前筛选没有匹配记录。'
                : '暂无运行。使用右上方“创建任务”开始。'}
            </Empty>
          ))}
      </section>
    </>
  )
}
