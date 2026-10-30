import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

const source = await readFile(new URL('../src/domain.ts', import.meta.url), 'utf8')
const js = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText
const { branchLayout, formatRate, paginate, workflowEvidence, mergeMeasuredNodes } = await import(
  'data:text/javascript;base64,' + Buffer.from(js).toString('base64')
)
const branch = (id, parent_id = null) => ({ id, parent_id, task_id: 'task', checkpoint_id: null })

test('only recorded loaded parents create edges, unrelated roots remain disconnected', () => {
  const graph = branchLayout([
    branch('root'),
    branch('child', 'root'),
    branch('orphan', 'unloaded'),
    branch('other'),
  ])
  assert.deepEqual(graph.edges, [{ id: 'root/child', source: 'root', target: 'child' }])
  assert.equal(graph.nodes.length, 4)
  assert.ok(
    graph.nodes.find((n) => n.id === 'child').position.x >
      graph.nodes.find((n) => n.id === 'root').position.x,
  )
})
test('malformed cyclic ancestry terminates and never creates self edges', () => {
  const graph = branchLayout([branch('a', 'b'), branch('b', 'a'), branch('self', 'self')])
  assert.ok(graph.nodes.every((n) => Number.isFinite(n.position.x)))
  assert.ok(graph.edges.every((e) => e.source !== e.target))
})
test('missing and undefined rates retain evidence gaps, known zero retains denominator', () => {
  assert.equal(formatRate(undefined), '未统计')
  assert.equal(formatRate({ numerator: 0, denominator: 0, value: null }), '0/0 · 无可定义比例')
  assert.equal(formatRate({ numerator: 0, denominator: 8, value: 0 }), '0/8 · 0.0%')
  assert.equal(formatRate({ numerator: 90, denominator: 120, value: 0.75 }), '90/120 · 75.0%')
})

test('URL pagination clamps invalid or stale pages without dropping a valid last page', () => {
  assert.deepEqual(paginate('2', 21), { current: 2, pages: 3, offset: 10 })
  assert.deepEqual(paginate('999', 21), { current: 3, pages: 3, offset: 20 })
  for (const value of ['0', '-4', 'NaN', '1.2', 'Infinity']) {
    assert.deepEqual(paginate(value, 21), { current: 1, pages: 3, offset: 0 })
  }
  assert.deepEqual(paginate('2', 0), { current: 1, pages: 1, offset: 0 })
})

test('workflow completion requires explicit completed events and retains test/acceptance failure', () => {
  const pending = workflowEvidence([
    { type: 'workflow.node', data: { node: 'test', state: 'started' } },
    { type: 'effect.outcome', data: { node: 'publish', status: 'CONFIRMED' } },
    { type: 'run.acceptance', data: { result: 'UNKNOWN' } },
  ])
  assert.equal(pending.completed.size, 0)
  const actual = workflowEvidence([
    { type: 'workflow.node', data: { node: 'test', state: 'completed', passed: false } },
    { type: 'run.acceptance', data: { result: 'FAILED' } },
  ])
  assert.deepEqual([...actual.completed], ['test', 'report'])
  assert.deepEqual([...actual.failed], ['test', 'report'])
})

// Regression for nodes disappearing after an SSE/poll-driven controlled graph refresh.
test('graph refresh preserves measured dimensions while updating actual status and positions', () => {
  const previous = [
    {
      id: 'test',
      position: { x: 0, y: 0 },
      data: { label: 'pending' },
      measured: { width: 145, height: 98 },
    },
  ]
  const next = [
    { id: 'test', position: { x: 185, y: 110 }, data: { label: 'failed' } },
    { id: 'new', position: { x: 0, y: 0 }, data: {} },
  ]
  const merged = mergeMeasuredNodes(next, previous)
  assert.deepEqual(merged[0].measured, { width: 145, height: 98 })
  assert.deepEqual(merged[0].position, { x: 185, y: 110 })
  assert.equal(merged[0].data.label, 'failed')
  assert.equal(merged[1].measured, undefined)
  assert.equal(next[0].measured, undefined)
})
