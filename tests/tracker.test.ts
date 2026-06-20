import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { plan } from '../src/core/planner.ts'
import { checkpoint } from '../src/core/tracker.ts'
import { createStore } from '../src/db/store.ts'
import { openInMemoryDatabase, resetDatabaseForTesting } from '../src/db/connection.ts'
import type { DataStore } from '../src/core/ports.ts'
import type { CheckpointResult } from '../src/core/domain.ts'

describe('tracker', () => {
  let store: DataStore

  beforeEach(() => {
    const db = openInMemoryDatabase()
    store = createStore(db, 'test-identity')
  })

  afterEach(() => {
    resetDatabaseForTesting()
  })

  it('records a checkpoint and returns deviation', () => {
    const route = plan({
      goal: 'Implement user authentication',
      project: 'test-project',
      identityId: 'test-identity',
      store,
    })

    const result = checkpoint({
      phase: route.phases[0].label,
      actualCost: 500,
      routeId: route.id,
      identityId: 'test-identity',
      store,
    })

    expect(result).not.toHaveProperty('error')
    if ('phase' in result) {
      expect(result.phase.label).toBe(route.phases[0].label)
      expect(result.deviation).toBeDefined()
    }
  })

  it('returns route state when called with no arguments', () => {
    const route = plan({
      goal: 'Implement user auth',
      project: 'test-project',
      identityId: 'test-identity',
      store,
    })

    const state = checkpoint({
      project: 'test-project',
      identityId: 'test-identity',
      store,
    })

    if ('route' in state) {
      expect(state.route.id).toBe(route.id)
      expect(state.phases).toHaveLength(route.phases.length)
    }
  })

  it('corrects a previous checkpoint', () => {
    const route = plan({
      goal: 'Implement user auth',
      project: 'test-project',
      identityId: 'test-identity',
      store,
    })

    checkpoint({
      phase: route.phases[0].label,
      actualCost: 500,
      routeId: route.id,
      identityId: 'test-identity',
      store,
    })

    const corrected = checkpoint({
      phase: route.phases[0].label,
      correct: 450,
      routeId: route.id,
      identityId: 'test-identity',
      store,
    })

    if ('phase' in corrected) {
      const phases = store.getPhases(route.id)
      const firstPhase = phases.find((p) => p.label === route.phases[0].label)
      expect(firstPhase?.actualCost).toBe(450)
    }
  })

  it('finds phase by substring subsumption', () => {
    const route = plan({
      goal: 'Implement user auth with full flow',
      project: 'test-project',
      identityId: 'test-identity',
      store,
    })

    const result = checkpoint({
      phase: 'Implement',  // substring
      actualCost: 400,
      routeId: route.id,
      identityId: 'test-identity',
      store,
    })

    expect(result).not.toHaveProperty('error')
    if ('phase' in result) {
      expect(result.phase).toBeDefined()
    }
  })
})
