import type { Branch, Rate, RuntimeEvent } from './api'
import type { Node } from '@xyflow/react'

export function mergeMeasuredNodes(next: Node[], previous: Node[]): Node[] {
  const measured = new Map(previous.map((node) => [node.id, node.measured]))
  return next.map((node) => ({ ...node, measured: node.measured ?? measured.get(node.id) }))
}

export function workflowEvidence(events: Pick<RuntimeEvent, 'type' | 'data'>[]) {
  const completed = new Set<string>(),
    failed = new Set<string>()
  for (const event of events) {
    if (
      event.type === 'workflow.node' &&
      event.data.state === 'completed' &&
      typeof event.data.node === 'string'
    ) {
      completed.add(event.data.node)
      if (event.data.passed === false) failed.add(event.data.node)
    }
    if (
      event.type === 'run.acceptance' &&
      ['SUCCEEDED', 'FAILED'].includes(String(event.data.result))
    ) {
      completed.add('report')
      if (event.data.result === 'FAILED') failed.add('report')
    }
  }
  return { completed, failed }
}

export function formatRate(rate: Rate | undefined) {
  if (!rate) return '未统计'
  if (rate.value == null) return `${rate.numerator}/${rate.denominator} · 无可定义比例`
  return `${rate.numerator}/${rate.denominator} · ${(rate.value * 100).toFixed(1)}%`
}

export function paginate(page: string, total: number) {
  const pages = Math.max(1, Math.ceil(total / 10))
  const parsed = Number(page)
  const current = Number.isSafeInteger(parsed) ? Math.max(1, Math.min(pages, parsed)) : 1
  return { current, pages, offset: (current - 1) * 10 }
}

export function branchLayout(branches: Branch[]) {
  const byId = new Map(branches.map((branch) => [branch.id, branch]))
  function depth(id: string, visited = new Set<string>()): number {
    const parent = byId.get(id)?.parent_id
    if (!parent || !byId.has(parent) || visited.has(parent)) return 0
    return 1 + depth(parent, new Set([...visited, id]))
  }
  const levels = new Map<number, number>()
  const nodes = branches.map((branch) => {
    const level = depth(branch.id)
    const row = levels.get(level) ?? 0
    levels.set(level, row + 1)
    return { id: branch.id, position: { x: level * 275, y: row * 120 } }
  })
  // Draw only recorded parent references present in this loaded scope.
  const edges = branches
    .filter((b) => b.parent_id && b.parent_id !== b.id && byId.has(b.parent_id))
    .map((b) => ({ id: `${b.parent_id}/${b.id}`, source: b.parent_id!, target: b.id }))
  return { nodes, edges }
}
