import { MarkerType, Position, type Node, type Edge } from '@xyflow/react'
import { branchLayout, formatRate, paginate } from '../domain'
import { useEffect, useState } from 'react'
import {
  api,
  short,
  when,
  runLink,
  type Branch,
  type Run,
  type Task,
  type Evaluation,
  type EvaluationResult,
} from '../api'
import { useLoad, useHashState } from '../hooks'
import {
  RuntimeGraph,
  Badge,
  Empty,
  ErrorBox,
  LoadState,
  useConfirmation,
  PageTitle,
  Json,
  Icon,
} from '../ui'
export function ForksPage({ tick }: { tick: number }) {
  const state = useLoad(
    'branches',
    () => Promise.all([api<Branch[]>('/branches'), api<Run[]>('/runs'), api<Task[]>('/tasks')]),
    tick,
  )
  const [selected, setSelected] = useHashState('branch')
  const [query, setQuery] = useHashState('q')
  const branches = state.data?.[0] ?? []
  const branch = branches.find((b) => b.id === selected) || (selected ? undefined : branches[0])
  const related = branch ? branches.filter((b) => b.task_id === branch.task_id) : []
  const layout = branchLayout(related)
  const titles = new Map((state.data?.[2] ?? []).map((t) => [t.id, t.title]))
  const taskRuns = (state.data?.[1] ?? []).filter((r) => r.branch_id === branch?.id)
  const filtered = branches.filter((b) =>
    [b.id, b.parent_id ?? '', titles.get(b.task_id) ?? b.task_id]
      .join(' ')
      .toLowerCase()
      .includes(query.toLowerCase()),
  )
  const nodes: Node[] = layout.nodes.map((n) => ({
    ...n,
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    draggable: false,
    className: n.id === branch?.id ? 'branch-selected' : '',
    data: {
      label: (
        <button
          className="graph-step"
          onClick={() => setSelected(n.id, true)}
          aria-label={'选择分支 ' + short(n.id)}
        >
          <Icon name="forks" size={17} />
          <strong>{short(n.id)}</strong>
          <small>
            {branches.find((b) => b.id === n.id)?.parent_id ? '从历史分支派生' : '根分支'}
          </small>
        </button>
      ),
    },
  }))
  const edges: Edge[] = layout.edges.map((e) => ({
    ...e,
    markerEnd: { type: MarkerType.ArrowClosed, color: '#a18d6c' },
    style: { stroke: '#a18d6c' },
  }))
  return (
    <>
      <PageTitle
        title="分支与回放"
        description="最近 100 个分支 · 仅显示真实父子关系，历史操作保持只读。"
      />
      <LoadState {...state} />
      {state.data && !branches.length ? (
        <section className="panel">
          <Empty>尚无分支。创建任务后会生成初始工作区。</Empty>
        </section>
      ) : (
        state.data && (
          <div className="branch-workspace">
            <section className="panel branch-browser">
              <div className="section-head">
                <h2>分支列表</h2>
                <span className="muted">{filtered.length} 条</span>
              </div>
              <label className="search-field">
                搜索已加载分支
                <Icon name="search" size={16} />
                <input
                  type="search"
                  value={query}
                  placeholder="任务、分支或父分支 ID"
                  onChange={(e) => setQuery(e.target.value)}
                />
              </label>
              <div className="branch-choices">
                {filtered.map((b) => (
                  <button
                    key={b.id}
                    onClick={() => setSelected(b.id, true)}
                    className={'branch-choice ' + (b.id === branch?.id ? 'selected' : '')}
                    aria-pressed={b.id === branch?.id}
                  >
                    <Icon name="forks" size={17} />
                    <span>
                      <strong>{titles.get(b.task_id) ?? short(b.task_id)}</strong>
                      <small className="mono">
                        {short(b.id)} · {b.parent_id ? '父 ' + short(b.parent_id) : '根分支'}
                      </small>
                    </span>
                  </button>
                ))}
                {!filtered.length && <Empty>当前范围无匹配分支。</Empty>}
              </div>
            </section>
            <div className="branch-inspector">
              {branch ? (
                <>
                  <section className="panel">
                    <div className="section-head">
                      <div>
                        <h2>{titles.get(branch.task_id) ?? '任务谱系'}</h2>
                        <p>
                          该任务在当前范围内的 {related.length} 个分支。点击图节点或左侧列表查看。
                        </p>
                      </div>
                    </div>
                    <div className="branch-graph">
                      <RuntimeGraph key={branch.task_id} nodes={nodes} edges={edges} />
                    </div>
                  </section>
                  <section className="panel">
                    <div className="section-head">
                      <h2>选中分支</h2>
                      <span className="pill">{branch.parent_id ? 'FORK' : 'ROOT'}</span>
                    </div>
                    <dl className="details">
                      <div>
                        <dt>分支 ID</dt>
                        <dd className="mono wrap">{branch.id}</dd>
                      </div>
                      <div>
                        <dt>父分支</dt>
                        <dd className="mono wrap">
                          {branch.parent_id ? (
                            branches.some((b) => b.id === branch.parent_id) ? (
                              <button
                                className="link mono"
                                onClick={() => setSelected(branch.parent_id!, true)}
                              >
                                {branch.parent_id}
                              </button>
                            ) : (
                              <>
                                {branch.parent_id}
                                <small>不在已加载范围内，未绘制连线。</small>
                              </>
                            )
                          ) : (
                            '无 · 初始分支'
                          )}
                        </dd>
                      </div>
                      <div>
                        <dt>来源检查点</dt>
                        <dd className="mono wrap">{branch.checkpoint_id ?? '无 · 初始工作区'}</dd>
                      </div>
                    </dl>
                    <h3>关联运行</h3>
                    {taskRuns.length ? (
                      taskRuns.map((run) => (
                        <div className="branch-run" key={run.id}>
                          <a className="mono" href={runLink(run.id)}>
                            {short(run.id)} <Icon name="arrow" size={14} />
                          </a>
                          <Badge value={run.status} />
                          <a href={runLink(run.id) + '?tab=checkpoints'}>检查点与可用操作</a>
                        </div>
                      ))
                    ) : (
                      <p>关联运行不在最近 100 条内，不能推断其状态。</p>
                    )}
                    <p className="muted">
                      Replay 和 Fork 通过运行详情的既有入口执行；新分支不继承审批。
                    </p>
                  </section>
                </>
              ) : (
                <section className="panel">
                  <Empty>指定分支不在最近100条记录内。请从列表重新选择。</Empty>
                </section>
              )}
            </div>
          </div>
        )
      )}
    </>
  )
}
export function EvaluationsPage({ tick }: { tick: number }) {
  const { ask, confirmation } = useConfirmation()
  const [revision, setRevision] = useState(0)
  const [preset, setPreset] = useState('smoke')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [queued, setQueued] = useState('')
  useEffect(() => {
    const timer = window.setInterval(() => setRevision((v) => v + 1), 5000)
    return () => window.clearInterval(timer)
  }, [])
  const state = useLoad('evaluations', () => api<Evaluation[]>('/evaluations'), tick + revision)
  const [selected, setSelected] = useHashState('evaluation')
  const [suite, setSuite] = useHashState('suite')
  const [page, setPage] = useHashState('p', '1')
  const suites = [...new Map((state.data ?? []).map((row) => [row.suite_id, row])).values()]
  const activeSuite = suite || suites[0]?.suite_id
  const rows = (state.data ?? [])
    .filter((row) => row.suite_id === activeSuite)
    .sort(
      (a, b) =>
        ['baseline', 'journal', 'full'].indexOf(a.mode) -
        ['baseline', 'journal', 'full'].indexOf(b.mode),
    )
  const rate = formatRate
  const scopedSelection = rows.find((row) => row.id === selected)?.id
  const results = useLoad(
    'evaluation-' + (scopedSelection ?? ''),
    () =>
      scopedSelection
        ? api<EvaluationResult[]>('/evaluations/' + scopedSelection + '/results')
        : Promise.resolve([]),
    tick + revision,
  )
  const pagination = paginate(page, results.data?.length ?? 0)
  const offset = pagination.offset
  function selectRun(id: string) {
    setSelected(id)
    setPage('1')
  }
  async function start() {
    if (
      !(await ask(
        preset === 'full'
          ? '启动完整360条矩阵？包含真实Worker退出和容器终止，将占用本机执行队列。仅本地模拟服务，无模型调用。'
          : '启动6条本地真实观测？任务保存在数据库，关闭页面后仍会继续执行。',
      ))
    )
      return
    setBusy(true)
    setError('')
    setQueued('')
    try {
      const result = await api<{ suite_id: string; planned_observations: number }>(
        '/evaluations/run',
        preset === 'full'
          ? {}
          : {
              templates: ['fix_addition'],
              faults: ['none', 'queryable_non_idempotent_loss'],
              repeats: 1,
            },
      )
      setSuite(result.suite_id)
      setSelected('')
      setRevision((v) => v + 1)
      setQueued(
        `已保存 ${result.planned_observations} 条计划观测，后台按顺序执行。套件 ${short(result.suite_id)}。`,
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      {confirmation}
      <PageTitle
        title="对照评测"
        description="比较同一任务集和故障条件下的真实观测。未知状态、正常失败和缺失能力分别记录。"
      />
      <details className="panel">
        <summary className="section-head">
          <h2>启动本地评测</h2>
          <span className="muted">展开配置＋</span>
        </summary>
        <p>
          使用实际数据库、独立模拟服务和 Docker
          测试。与普通任务共用本机执行顺序；任务保存在数据库，页面关闭后仍会执行。
        </p>
        <div className="actions">
          <label className="inline-label">
            评测范围
            <select
              aria-label="评测范围"
              value={preset}
              onChange={(e) => setPreset(e.target.value)}
            >
              <option value="smoke">快速验证 · 1 种任务 × 2 个条件 × 3 配置 = 6 条</option>
              <option value="full">完整矩阵 · 10 种任务 × 12 个条件 × 3 配置 = 360 条</option>
            </select>
          </label>
          <button className="primary" disabled={busy} onClick={() => void start()}>
            {busy ? '检查环境并保存…' : '启动评测'}
          </button>
        </div>
        <ErrorBox error={error} />
        {queued && <p role="status">{queued}</p>}
        <p className="muted">
          每 5
          秒更新进度。完整矩阵包含真实进程退出与容器终止，执行时间较长。无法运行的基础设施会记为错误。
        </p>
      </details>
      <section className="panel">
        <div className="section-head">
          <h2>实验套件</h2>
          {suites.length > 0 && (
            <label className="inline-label">
              选择实验
              <select
                aria-label="选择实验套件"
                value={activeSuite}
                onChange={(e) => {
                  setSuite(e.target.value)
                  setSelected('')
                }}
              >
                {suites.map((row) => (
                  <option key={row.suite_id} value={row.suite_id}>
                    {short(row.suite_id)} ·{' '}
                    {row.scope === 'matrix'
                      ? `${row.template_count}任务 / ${row.fault_count}条件`
                      : '模式冒烟验证'}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        <LoadState {...state} />
        {state.data && !rows.length && <Empty>尚未生成实验结果。不会用示例数字填充成功率。</Empty>}
        {rows.length > 0 && (
          <>
            {rows[0].review_note && <div className="notice">{rows[0].review_note}</div>}
            <p>
              {rows[0].scope === 'matrix'
                ? `${rows[0].template_count} 种任务 · ${rows[0].fault_count} 个条件 · 每组计划 ${rows[0].planned_cases} 条观测`
                : '当前套件仅验证模式和进程恢复，不是完整的任务集故障矩阵。'}
            </p>
            <p className="table-hint">可横向滚动查看全部指标 · 分子 / 分母与未知值原样保留</p>
            <div
              className="table-wrap evaluation-matrix"
              role="region"
              aria-label="三配置真实评测矩阵"
              tabIndex={0}
            >
              <table>
                <thead>
                  <tr>
                    <th>配置 / 进度</th>
                    <th>任务验收</th>
                    <th>恢复正确</th>
                    <th>人工检查</th>
                    <th>误报成功</th>
                    <th>状态矛盾</th>
                    <th>重复 / 不安全重试</th>
                    <th>副作用 / 请求</th>
                    <th>配对耗时差</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <strong>
                          {{ baseline: 'Baseline', journal: 'Journal', full: 'Full Runtime' }[
                            row.mode
                          ] ?? row.mode}
                        </strong>
                        <small>
                          {row.status === 'COMPLETED'
                            ? '评测已执行（含失败或待检查案例）'
                            : row.status}{' '}
                          ·{' '}
                          {row.metrics
                            ? `${row.metrics.recorded_cases}/${row.planned_cases ?? '计划未记录'}`
                            : '原始模式验证记录'}
                        </small>
                        {row.metrics && row.planned_cases != null && row.planned_cases > 0 && (
                          <progress
                            aria-label={row.mode + ' 评测进度'}
                            value={row.metrics.recorded_cases}
                            max={row.planned_cases}
                          />
                        )}
                      </td>
                      <td>{rate(row.metrics?.coding_task_success)}</td>
                      <td>{rate(row.metrics?.recovery_correctness)}</td>
                      <td>{rate(row.metrics?.manual_review_rate)}</td>
                      <td>{rate(row.metrics?.false_success_rate)}</td>
                      <td>{rate(row.metrics?.state_divergence_rate)}</td>
                      <td>
                        {row.metrics
                          ? `${row.metrics.duplicate_effect_count} / ${row.metrics.unsafe_retry_count ?? '未测量'}`
                          : '未测量'}
                      </td>
                      <td>
                        {row.metrics
                          ? `${row.metrics.actual_external_effect_count} / ${row.metrics.external_request_count}`
                          : '见原始记录'}
                      </td>
                      <td>
                        {row.mode === 'baseline'
                          ? '基线'
                          : row.metrics?.runtime_overhead.median_ms != null
                            ? `${row.metrics.runtime_overhead.median_ms.toFixed(1)} ms`
                            : '无可用配对'}
                        <small>
                          {row.metrics?.runtime_overhead.paired_cases
                            ? `${row.metrics.runtime_overhead.paired_cases} 组配对`
                            : ''}
                        </small>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="notice">
              任务验收需要真实测试与独立检查通过。UNKNOWN 不算外部失败；无分母的比例不填成 0。Fork
              不受支持的配置会明确排除相应比较。请求重发若被原生幂等去重，不算重复副作用。小样本结果不代表真实模型或生产环境的可靠性。
            </div>
            <div className="actions">
              {rows
                .filter(
                  (row) =>
                    row.metrics?.infrastructure_failed_cases || row.metrics?.failed_probe_cases,
                )
                .map((row) => (
                  <span className="error" key={row.id}>
                    {row.mode}：基础设施错误 {row.metrics?.infrastructure_failed_cases ?? '未统计'}{' '}
                    · 探针未通过 {row.metrics?.failed_probe_cases ?? '未统计'}
                  </span>
                ))}
            </div>
            {rows[0].scope === 'matrix' && rows.some((row) => row.metrics) && (
              <div className="actions">
                <a href={`/api/evaluation-suites/${activeSuite}/exports/csv`}>下载 CSV ↓</a>
                <a href={`/api/evaluation-suites/${activeSuite}/exports/jsonl`}>完整 JSONL ↓</a>
                <a href={`/api/evaluation-suites/${activeSuite}/exports/report`}>统计说明 ↓</a>
              </div>
            )}
            <div className="checkpoint-list">
              {rows.map((row) => (
                <div key={row.id}>
                  <div>
                    <strong>{row.mode} · 原始运行记录</strong>
                    <small>
                      {row.metrics?.unsupported_probe_cases
                        ? `${row.metrics.unsupported_probe_cases} 条探针不受此配置支持；不计为任意失败分数。`
                        : when(row.created_at)}
                    </small>
                  </div>
                  <button onClick={() => selectRun(row.id)}>查看原始结果</button>
                </div>
              ))}
            </div>
          </>
        )}
      </section>
      {selected && state.data && !scopedSelection && (
        <ErrorBox error="所选原始观测不属于当前套件，已停止展示。请重新选择配置。" />
      )}
      {scopedSelection && (
        <section className="panel">
          <div className="section-head">
            <h2>
              原始观测 <span className="mono">{short(selected)}</span>
            </h2>
            <span className="muted">
              {results.data ? `共 ${results.data.length} 条 · 每页10条` : '正在读取原始观测…'}
            </span>
          </div>
          <LoadState {...results} />
          {results.data?.length
            ? results.data.slice(offset, offset + 10).map((row) => (
                <details key={row.id}>
                  <summary className="case-summary">
                    <strong>
                      {String(row.observed.template ?? short(row.case_id))} ·{' '}
                      {String(row.observed.fault ?? '')}
                    </strong>
                    <span>
                      {String(
                        row.observed.run_status ?? row.observed.status ?? row.observed.phase ?? '',
                      )}
                    </span>
                  </summary>
                  <p>
                    {row.run_id && <a href={runLink(row.run_id)}>查看实际 Run、测试与事件 ↗</a>}
                  </p>
                  <Json data={row.observed} />
                </details>
              ))
            : results.data && <Empty>本次实验暂无已保存结果。</Empty>}
          <div className="actions">
            <button
              disabled={offset === 0}
              onClick={() => setPage(String(pagination.current - 1), true)}
            >
              上一页
            </button>
            <button
              disabled={!results.data || offset + 10 >= results.data.length}
              onClick={() => setPage(String(pagination.current + 1), true)}
            >
              下一页
            </button>
          </div>
        </section>
      )}
    </>
  )
}
