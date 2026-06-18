import { describe, it, expect, beforeEach } from 'vitest'
import { handlePlan } from '../src/handlers/plan-handler.ts'
import { handleCheckpoint } from '../src/handlers/checkpoint-handler.ts'
import { handleReview } from '../src/handlers/review-handler.ts'
import { createStore } from '../src/db/store.ts'
import { openInMemoryDatabase } from '../src/db/connection.ts'
import type { DataStore } from '../src/core/ports.ts'

describe('handlers', () => {
  let store: DataStore

  beforeEach(() => {
    const db = openInMemoryDatabase()
    store = createStore(db, 'test-identity')
  })

  describe('handlePlan', () => {
    it('returns a PlanResult for valid input', () => {
      const result = handlePlan({
        goal: 'Implement auth',
        project: 'test-project',
        store,
      })

      expect(result).not.toHaveProperty('error')
      if (!('error' in result)) {
        expect(result.id).toBeDefined()
        expect(result.phases.length).toBeGreaterThan(0)
      }
    })

    it('returns structured error for empty goal', () => {
      const result = handlePlan({
        goal: '',
        project: 'test-project',
        store,
      })

      expect(result).toHaveProperty('error')
      if ('error' in result) {
        expect(result.error.code).toBe('INVALID_ARGUMENT')
      }
    })

    it('returns structured error for whitespace-only goal', () => {
      const result = handlePlan({
        goal: '   ',
        project: 'test-project',
        store,
      })

      expect(result).toHaveProperty('error')
      if ('error' in result) {
        expect(result.error.code).toBe('INVALID_ARGUMENT')
      }
    })
  })

  describe('handleCheckpoint', () => {
    it('returns structured error when no route exists', () => {
      const result = handleCheckpoint({
        phase: 'Test',
        actualCost: 400,
        store,
      })

      expect(result).toHaveProperty('error')
      if ('error' in result) {
        expect(result.error.code).toBe('INVALID_ARGUMENT')
      }
    })

    it('returns route state for status check with project', () => {
      handlePlan({ goal: 'Test feature', project: 'test-project', store })

      const result = handleCheckpoint({
        project: 'test-project',
        store,
      })

      if (!('error' in result)) {
        expect((result as Record<string, unknown>).route).toBeDefined()
      }
    })
  })

  describe('handleReview', () => {
    it('returns NOT_FOUND when no route exists', () => {
      const result = handleReview({
        project: 'nonexistent',
        store,
      })

      expect(result).toHaveProperty('error')
      if ('error' in result) {
        expect(result.error.code).toBe('NOT_FOUND')
      }
    })
  })
})
