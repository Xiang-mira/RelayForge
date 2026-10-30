import { useEffect, useId, useRef, useState, type ReactNode, type KeyboardEvent } from 'react'
import { names, nav } from './options'
import { Background, Controls, ReactFlow, useNodesState, type Node, type Edge } from '@xyflow/react'
import { mergeMeasuredNodes } from './domain'

export function RuntimeGraph({
  nodes,
  edges,
  padding = 0.15,
}: {
  nodes: Node[]
  edges: Edge[]
  padding?: number
}) {
  const [measured, setNodes, onNodesChange] = useNodesState<Node>(nodes)
  useEffect(() => {
    setNodes((previous) => mergeMeasuredNodes(nodes, previous))
  }, [nodes, setNodes])
  return (
    <ReactFlow
      nodes={measured}
      onNodesChange={onNodesChange}
      edges={edges}
      fitView
      fitViewOptions={{ maxZoom: 1.1, padding }}
      nodesConnectable={false}
      nodesDraggable={false}
    >
      <Background gap={20} color="#302b23" />
      <Controls showInteractive={false} />
    </ReactFlow>
  )
}

// One stroke vocabulary for navigation and actions; no remote fonts or icon dependency.
export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    overview: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1.5" />
        <rect x="14" y="3" width="7" height="7" rx="1.5" />
        <rect x="3" y="14" width="7" height="7" rx="1.5" />
        <path d="M14 17h7m-3.5-3.5v7" />
      </>
    ),
    runs: (
      <>
        <rect x="4" y="3" width="16" height="18" rx="2" />
        <path d="M8 8h8M8 12h8M8 16h5" />
      </>
    ),
    journal: (
      <>
        <path d="M5 4h14v16H5zM8 8h8M8 12h8M8 16h4" />
        <path d="M2 7h3M2 12h3M2 17h3" />
      </>
    ),
    forks: (
      <>
        <circle cx="6" cy="5" r="2" />
        <circle cx="6" cy="19" r="2" />
        <circle cx="18" cy="5" r="2" />
        <path d="M6 7v10M6 13h5a7 7 0 0 0 7-6" />
      </>
    ),
    failure: <path d="m13 2-8 12h6l-1 8 9-13h-6z" />,
    evaluations: (
      <>
        <path d="M4 3v18h17M9 16v-5m5 5V7m5 9V4" />
      </>
    ),
    plus: <path d="M12 5v14M5 12h14" />,
    refresh: (
      <>
        <path d="M20 7v5h-5M4 17v-5h5" />
        <path d="M6 6a8 8 0 0 1 13 4M5 14a8 8 0 0 0 13 4" />
      </>
    ),
    collapse: (
      <>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M9 4v16m7-10-2 2 2 2" />
      </>
    ),
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    search: (
      <>
        <circle cx="10.5" cy="10.5" r="6.5" />
        <path d="m16 16 4 4" />
      </>
    ),
    arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
    external: (
      <>
        <path d="M14 3h7v7m0-7L10 14M10 5H5v14h14v-5" />
      </>
    ),
    box: (
      <>
        <path d="m12 3 9 5-9 5-9-5zM3 8v9l9 5 9-5V8M12 13v9" />
      </>
    ),
    check: <path d="m5 12 4 4L19 6" />,
  }
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="icon"
    >
      {paths[name] ?? paths.box}
    </svg>
  )
}

export function Logo() {
  return (
    <svg viewBox="0 0 32 32" width="30" height="30" fill="none" aria-hidden="true" className="logo">
      <path
        d="M5 23V9a4 4 0 0 1 4-4h3m-7 12h11a5 5 0 0 0 5-5V5m-5 12 7 8"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
      <rect x="1" y="19" width="8" height="8" rx="2" fill="currentColor" />
      <circle cx="16" cy="17" r="4" fill="currentColor" />
      <circle cx="22" cy="5" r="3" fill="currentColor" />
      <path d="m22 24 3-3 4 4-3 3z" fill="currentColor" />
    </svg>
  )
}
export function Badge({ value }: { value: string }) {
  return (
    <span className={'badge status-' + value}>
      <i />
      {names[value] ?? value}
    </span>
  )
}
export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="empty">
      <Icon name="box" size={26} />
      <div>{children}</div>
    </div>
  )
}
export function ErrorBox({ error }: { error: string }) {
  return error ? (
    <div role="alert" className="error">
      {error}
    </div>
  ) : null
}
export function Json({ data }: { data: unknown }) {
  return (
    <pre className="json" tabIndex={0}>
      {JSON.stringify(data, null, 2)}
    </pre>
  )
}
export function LoadState({ loading, error }: { loading: boolean; error: string }) {
  return (
    <>
      {loading && (
        <p role="status" className="loading">
          正在读取实际记录…
        </p>
      )}
      <ErrorBox error={error} />
    </>
  )
}
export function PageTitle({
  title,
  description,
  actions,
}: {
  title: string
  description: string
  actions?: ReactNode
}) {
  return (
    <div className="page-title">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  )
}

export function Drawer({
  title,
  onClose,
  children,
  variant = 'drawer',
}: {
  title: string
  onClose: () => void
  children: ReactNode
  variant?: 'drawer' | 'command'
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const close = useRef(onClose)
  close.current = onClose
  const titleId = useId()
  useEffect(() => {
    const dialog = ref.current!
    const origin = document.activeElement instanceof HTMLElement ? document.activeElement : null
    dialog.showModal()
    dialog.querySelector<HTMLElement>('[data-autofocus]')?.focus()
    const original = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      dialog.close()
      document.body.style.overflow = original
      if (origin?.isConnected) origin.focus()
    }
  }, [])
  return (
    <dialog
      ref={ref}
      className={variant}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault()
        close.current()
      }}
      onClick={(event) => {
        if (event.target === ref.current) close.current()
      }}
    >
      <div className="dialog-content">
        <header className="section-head">
          <h2 id={titleId}>{title}</h2>
          <button
            type="button"
            className="icon-button ghost"
            onClick={onClose}
            aria-label={'关闭' + title}
          >
            <Icon name="close" />
          </button>
        </header>
        {children}
      </div>
    </dialog>
  )
}

export function useConfirmation() {
  const [message, setMessage] = useState('')
  const resolve = useRef<((value: boolean) => void) | null>(null)
  useEffect(
    () => () => {
      resolve.current?.(false)
    },
    [],
  )
  function answer(value: boolean) {
    const done = resolve.current
    resolve.current = null
    setMessage('')
    done?.(value)
  }
  function ask(text: string) {
    return new Promise<boolean>((done) => {
      resolve.current?.(false)
      resolve.current = done
      setMessage(text)
    })
  }
  const confirmation = message ? (
    <Drawer variant="command" title="确认操作" onClose={() => answer(false)}>
      <p>{message}</p>
      <div className="actions confirm-actions">
        <button type="button" onClick={() => answer(false)}>
          返回检查
        </button>
        <button type="button" className="primary" onClick={() => answer(true)}>
          确认继续
        </button>
      </div>
    </Drawer>
  ) : null
  return { ask, confirmation }
}

export function CommandPalette({
  onClose,
  onCreate,
  onRefresh,
}: {
  onClose: () => void
  onCreate: () => void
  onRefresh: () => void
}) {
  const [query, setQuery] = useState('')
  const list = useRef<HTMLDivElement>(null)
  const match = (text: string) => text.toLowerCase().includes(query.toLowerCase())
  const pages = nav.filter((n) => match(n.title + ' ' + n.key + ' ' + n.sub))
  const create = match('创建任务 new task'),
    refresh = match('刷新当前数据 refresh')
  function keyboard(event: KeyboardEvent) {
    const items = [...list.current!.querySelectorAll<HTMLElement>('.command-item')]
    if (!items.length) return
    const index = items.indexOf(document.activeElement as HTMLElement)
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      items[
        index < 0
          ? event.key === 'ArrowDown'
            ? 0
            : items.length - 1
          : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
      ].focus()
    } else if (event.key === 'Enter' && (event.target as HTMLElement).tagName === 'INPUT') {
      event.preventDefault()
      items[0].click()
    }
  }
  return (
    <Drawer title="快捷操作" variant="command" onClose={onClose}>
      <div onKeyDown={keyboard} ref={list}>
        <label className="search-field">
          搜索页面与操作
          <Icon name="search" size={17} />
          <input
            data-autofocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="输入页面名称、创建或刷新…"
          />
        </label>
        <div className="command-results">
          {pages.map((n) => (
            <a className="command-item" href={'#/' + n.key} key={n.key} onClick={onClose}>
              <Icon name={n.key} />
              <span>{n.title}</span>
              <small>{n.sub}</small>
            </a>
          ))}
          {create && (
            <button
              className="command-item"
              onClick={() => {
                onClose()
                onCreate()
              }}
            >
              <Icon name="plus" />
              <span>创建任务</span>
              <small>打开配置</small>
            </button>
          )}
          {refresh && (
            <button
              className="command-item"
              onClick={() => {
                onClose()
                onRefresh()
              }}
            >
              <Icon name="refresh" />
              <span>刷新当前数据</span>
              <small>重新读取接口</small>
            </button>
          )}
          {!pages.length && !create && !refresh && <Empty>没有匹配的页面或操作。</Empty>}
        </div>
        <div className="command-help">方向键选择 · Enter 打开 · Escape 关闭</div>
      </div>
    </Drawer>
  )
}
