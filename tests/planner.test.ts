import { describe, it, expect, beforeEach } from 'vitest'
import { plan } from '../src/core/planner.ts'
import { createStore } from '../src/db/store.ts'
import { openInMemoryDatabase } from '../src/db/connection.ts'
import type { DataStore } from '../src/core/ports.ts'

describe('planner', () => {
  let store: DataStore

  beforeEach(() => {
    const db = openInMemoryDatabase()
    store = createStore(db, 'test-identity')
  })

  it('creates a route with phases for a standard implement goal', () => {
    const result = plan({
      goal: 'Implement user authentication',
      project: 'test-project',
      identityId: 'test-identity',
      store,
    })

    expect(result.id).toBeDefined()
    expect(result.project).toBe('test-project')
    expect(result.goal).toBe('Implement user authentication')
    expect(result.status).toBe('active')
    expect(result.phases.length).toBeGreaterThan(0)
    expect(result.personalBias).toBeNull()
  })

  it('returns existing route for same goal and project', () => {
    const first = plan({
      goal: 'Implement auth',
      project: 'test-project',
      identityId: 'test-identity',
      store,
    })

    const second = plan({
      goal: 'Implement auth',
      project: 'test-project',
      identityId: 'test-identity',
      store,
    })

    expect(second.id).toBe(first.id)
  })

  it('archives and replans when replan is true', () => {
    const first = plan({
      goal: 'Implement auth',
      project: 'test-project',
      identityId: 'test-identity',
      store,
    })

    const second = plan({
      goal: 'Implement auth',
      project: 'test-project',
      identityId: 'test-identity',
      store,
      replan: true,
    })

    expect(second.id).not.toBe(first.id)
    expect(second.archivedRoutes.length).toBeGreaterThanOrEqual(1)
  })

  it('decomposes refactor goals appropriately', () => {
    const result = plan({
      goal: 'Refactor the database layer',
      project: 'test-project',
      identityId: 'test-identity',
      store,
    })

    const actions = result.phases.map((p) => p.action)
    expect(actions).toContain('refactor')
    expect(actions).toContain('test')
  })

  it('decomposes bugfix goals appropriately', () => {
    const result = plan({
      goal: 'Fix login redirect bug',
      project: 'test-project',
      identityId: 'test-identity',
      store,
    })

    const actions = result.phases.map((p) => p.action)
    expect(actions).toContain('debug')
    expect(actions).toContain('implement')
    expect(actions).toContain('test')
  })

  it('includes context-based phases when context is provided', () => {
    const result = plan({
      goal: 'Build the system',
      project: 'test-project',
      identityId: 'test-identity',
      store,
      context: 'API backend with database',
    })

    const labels = result.phases.map((p) => p.label)
    const apiDesign = labels.find((l) => l.toLowerCase().includes('api'))
    expect(apiDesign).toBeDefined()
  })
})
