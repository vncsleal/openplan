import { describe, it, expect, beforeEach } from 'vitest'
import { plan } from '../src/core/planner.ts'
import { checkpoint } from '../src/core/tracker.ts'
import { review } from '../src/core/reviewer.ts'
import { createStore } from '../src/db/store.ts'
import { openInMemoryDatabase } from '../src/db/connection.ts'
import type { DataStore } from '../src/core/ports.ts'

describe('reviewer', () => {
  let store: DataStore

  beforeEach(() => {
    const db = openInMemoryDatabase()
    store = createStore(db, 'test-identity')
  })

  it('generates a review with summary and deviations', () => {
    const route = plan({
      goal: 'Implement user auth',
      project: 'test-project',
      identityId: 'test-identity',
      store,
    })

    for (const phase of route.phases) {
      checkpoint({
        phase: phase.label,
        actualCost: 400,
        routeId: route.id,
        identityId: 'test-identity',
        store,
      })
    }

    const result = review({
      project: 'test-project',
      identityId: 'test-identity',
      store,
    })

    expect(result.summary).toBeDefined()
    expect(result.summary.project).toBe('test-project')
    expect(result.summary.completedCount).toBe(route.phases.length)
    expect(result.deviations.length).toBeGreaterThan(0)
    expect(result.selfDiagnostics).toBeDefined()
    expect(result.meshSyncStatus).toBeDefined()
  })

  it('returns accuracy data', () => {
    const route = plan({
      goal: 'Build a small feature',
      project: 'test-project',
      identityId: 'test-identity',
      store,
    })

    for (const phase of route.phases) {
      checkpoint({
        phase: phase.label,
        actualCost: 300,
        routeId: route.id,
        identityId: 'test-identity',
        store,
      })
    }

    const result = review({
      project: 'test-project',
      identityId: 'test-identity',
      store,
    })

    expect(result.accuracy.length).toBeGreaterThan(0)
    expect(result.accuracy[0].action).toBeDefined()
    expect(result.accuracy[0].sampleCount).toBeGreaterThan(0)
  })

  it('returns empty arrays for a route with no checkpoints', () => {
    plan({
      goal: 'Just planning',
      project: 'test-project',
      identityId: 'test-identity',
      store,
    })

    const result = review({
      project: 'test-project',
      identityId: 'test-identity',
      store,
    })

    expect(result.deviations.length).toBeGreaterThan(0)
    expect(result.accuracy.length).toBe(0)
  })
})
