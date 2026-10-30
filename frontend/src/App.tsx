import { useCallback, useEffect, useRef, useState } from 'react'
import '@xyflow/react/dist/style.css'
import { api, runLink, type Health } from './api'
import { nav } from './options'
import { Empty, ErrorBox, Logo, Icon, Drawer, CommandPalette } from './ui'
import { OverviewPage, RunsPage, CreateTask } from './pages/Tasks'
import { RunDetail, JournalPage, EffectDetail } from './pages/Run'
import { ForksPage, EvaluationsPage } from './pages/Analysis'
import './style.css'

export default function App() {
  const [hash, setHash] = useState(window.location.hash || '#/overview')
  const lastPath = useRef(window.location.hash.split('?')[0])
  const [tick, setTick] = useState(0)
  const [effect, setEffect] = useState('')
  const [creating, setCreating] = useState(false)
  const [commands, setCommands] = useState(false)
  const [mobile, setMobile] = useState(false)
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem('relayforge:sidebar') === 'collapsed'
    } catch {
      return false
    }
  })
  const [health, setHealth] = useState<Health | null>(null)
  const [healthError, setHealthError] = useState('')
  const [workerSignal, setWorkerSignal] = useState('checking')
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        if (document.querySelector('dialog[open]') && !commands) return
        setCommands((v) => !v)
      }
    }
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  }, [commands])
  useEffect(() => {
    let active = true
    const check = () =>
      api<{ signal: string }>('/worker')
        .then((row) => {
          if (active) setWorkerSignal(row.signal)
        })
        .catch(() => {
          if (active) setWorkerSignal('unavailable')
        })
    void check()
    const timer = window.setInterval(() => void check(), 3000)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [])
  useEffect(() => {
    const listener = () => {
      const path = window.location.hash.split('?')[0]
      if (path !== lastPath.current) {
        setEffect('')
        setCreating(false)
        setCommands(false)
        lastPath.current = path
      }
      setHash(window.location.hash)
      setMobile(false)
    }
    window.addEventListener('hashchange', listener)
    return () => window.removeEventListener('hashchange', listener)
  }, [])
  const refresh = useCallback(() => setTick((t) => t + 1), [])
  useEffect(() => {
    let active = true
    const check = () =>
      fetch('/health')
        .then(async (r) => {
          const data = await r.json()
          if (active) {
            setHealth(data)
            setHealthError(r.ok ? '' : '数据库尚未就绪，操作暂不可用。')
          }
        })
        .catch(() => {
          if (active) {
            setHealth(null)
            setHealthError('无法连接本地后端。')
          }
        })
    void check()
    const timer = window.setInterval(() => void check(), 5000)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [tick])
  const route = hash.split('?')[0].replace(/^#\/?/, '').split('/')
  const page = route[0] || 'overview'
  const current = nav.find((n) => n.key === page)
  function created(id: string) {
    setCreating(false)
    setEffect('')
    refresh()
    window.location.hash = runLink(id)
  }
  function toggleSidebar() {
    setCollapsed((v) => {
      try {
        localStorage.setItem('relayforge:sidebar', v ? 'full' : 'collapsed')
      } catch {}
      return !v
    })
  }
  return (
    <div
      className={'shell' + (collapsed ? ' sidebar-collapsed' : '') + (mobile ? ' mobile-open' : '')}
    >
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault()
          document.getElementById('main-content')?.focus()
        }}
      >
        跳到内容
      </a>
      <aside className="sidebar">
        <div className="brand-row">
          <a className="brand" href="#/overview" aria-label="RelayForge 运行总览">
            <Logo />
            <span>RelayForge</span>
          </a>
          <button
            className="icon-button ghost sidebar-toggle"
            onClick={toggleSidebar}
            aria-label={collapsed ? '展开侧栏' : '折叠侧栏'}
            aria-expanded={!collapsed}
          >
            <Icon name="collapse" />
          </button>
          <button
            className="icon-button ghost mobile-toggle"
            onClick={() => setMobile((v) => !v)}
            aria-label="切换主导航"
            aria-expanded={mobile}
          >
            <Icon name={mobile ? 'close' : 'menu'} />
          </button>
        </div>
        <div className="sidebar-caption">执行控制台</div>
        <nav aria-label="主导航">
          {nav.map((n) => (
            <a
              key={n.key}
              href={'#/' + n.key}
              aria-current={page === n.key ? 'page' : undefined}
              title={n.title}
              className={page === n.key ? 'active' : ''}
            >
              <Icon name={n.key} />
              <span>{n.title}</span>
            </a>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="local-context">
            <span className={'health-dot ' + (health?.status === 'ready' ? 'ready' : '')} />
            <div>
              <strong>本地 Runtime</strong>
              <small>{health?.provider ?? '检查中'} · PostgreSQL</small>
            </div>
          </div>
          <div className="sidebar-description">
            <p>
              {health?.provider === 'mock'
                ? 'Mock 模式 · 无模型调用费用'
                : '使用已配置的模型提供方'}
              <br />
              真实执行，依据证据恢复。
            </p>
            <a href="http://127.0.0.1:8000/docs" target="_blank" rel="noreferrer">
              API 文档 <Icon name="external" size={13} />
            </a>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            <span className="muted">本地执行</span>
            <span className="slash">/</span>
            {route[1] ? (
              <>
                <a href="#/runs">任务与运行</a>
                <span className="slash">/</span>
                <span>运行详情</span>
              </>
            ) : (
              <span>{current?.title ?? '页面'}</span>
            )}
          </div>
          <div className="actions">
            <button
              className="ghost command-trigger"
              onClick={() => setCommands(true)}
              aria-label="打开快捷操作"
            >
              <Icon name="search" size={16} />
              <span>快捷操作</span>
              <kbd>Ctrl K</kbd>
            </button>
            <span className={'health ' + (health?.status === 'ready' ? 'ready' : '')}>
              <i />
              {health?.status === 'ready' ? '数据库已连接' : '连接未就绪'}
            </span>
            <button
              className="icon-button ghost"
              onClick={refresh}
              aria-label="刷新实际数据"
              title="刷新实际数据"
            >
              <Icon name="refresh" />
            </button>
            <button className="primary" onClick={() => setCreating(true)}>
              <Icon name="plus" size={16} />
              <span>创建任务</span>
            </button>
          </div>
        </header>
        <main id="main-content" tabIndex={-1}>
          <ErrorBox error={healthError} />
          {workerSignal !== 'checking' && workerSignal !== 'recent_heartbeat' && (
            <div className="notice">
              执行服务暂无近期心跳，任务仍保存在数据库。请检查本地服务；心跳不能证明操作是否完成。
            </div>
          )}
          {page === 'overview' ? (
            <OverviewPage tick={tick} />
          ) : page === 'runs' && route[1] ? (
            <RunDetail
              key={route[1]}
              id={route[1]}
              tick={tick}
              onCreated={created}
              onEffect={setEffect}
            />
          ) : page === 'runs' ? (
            <RunsPage tick={tick} onCreated={created} provider={health?.provider} />
          ) : page === 'failure' ? (
            <RunsPage failure tick={tick} onCreated={created} provider={health?.provider} />
          ) : page === 'journal' ? (
            <JournalPage tick={tick} onEffect={setEffect} />
          ) : page === 'forks' ? (
            <ForksPage tick={tick} />
          ) : page === 'evaluations' ? (
            <EvaluationsPage tick={tick} />
          ) : (
            <Empty>
              页面不存在。<a href="#/overview">返回运行总览</a>
            </Empty>
          )}
          <footer>
            <span>RelayForge</span>
            <span>本地可观测执行 · 结果以真实证据为准</span>
          </footer>
        </main>
      </div>
      {creating && (
        <Drawer title="创建本地任务" onClose={() => setCreating(false)}>
          <CreateTask onCreated={created} provider={health?.provider} />
        </Drawer>
      )}
      {commands && (
        <CommandPalette
          onClose={() => setCommands(false)}
          onCreate={() => setCreating(true)}
          onRefresh={refresh}
        />
      )}
      {effect && (
        <EffectDetail key={effect} id={effect} onClose={() => setEffect('')} onChanged={refresh} />
      )}
    </div>
  )
}
