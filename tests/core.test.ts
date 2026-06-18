import { describe, it, expect } from 'vitest'
import { tokenize, matchLevel } from '../src/core/tokenizer.ts'
import { calculateDeviation, deviationLabel, personalBias, accuracyByAction, ciFromBaseline, efficiency, hazardFromPhases } from '../src/core/costs.ts'
import type { CalibrationEvent, CostBaseline, RoutePhase } from '../src/core/domain.ts'

describe('tokenizer', () => {
  it('lowercases and strips punctuation', () => {
    expect(tokenize('Implement Auth!')).toBe('implement auth')
  })

  it('removes stop words', () => {
    expect(tokenize('the and of for implement')).toBe('implement')
  })

  it('collapses whitespace', () => {
    expect(tokenize('  implement   auth  ')).toBe('implement auth')
  })

  it('trims to 50 tokens', () => {
    const many = Array.from({ length: 60 }, (_, i) => `word${i}`).join(' ')
    const result = tokenize(many)
    expect(result.split(/\s+/).length).toBeLessThanOrEqual(50)
  })

  it('handles empty input', () => {
    expect(tokenize('')).toBe('')
  })

  it('handles input with only stop words', () => {
    expect(tokenize('the and of')).toBe('')
  })
})

describe('costs', () => {
  describe('calculateDeviation', () => {
    it('returns positive deviation when actual > expected', () => {
      expect(calculateDeviation(600, 400)).toBe(200)
    })

    it('returns negative deviation when actual < expected', () => {
      expect(calculateDeviation(200, 400)).toBe(-200)
    })

    it('returns zero when equal', () => {
      expect(calculateDeviation(400, 400)).toBe(0)
    })

    it('handles zero expected cost', () => {
      expect(calculateDeviation(100, 0)).toBe(Number.POSITIVE_INFINITY)
    })
  })

  describe('deviationLabel', () => {
    it('returns over when actual significantly exceeds expected', () => {
      expect(deviationLabel(200, 400)).toBe('over')
    })

    it('returns under when actual is significantly less than expected', () => {
      expect(deviationLabel(-200, 400)).toBe('under')
    })

    it('returns on_track when deviation is within 10%', () => {
      expect(deviationLabel(-30, 400)).toBe('on_track')
      expect(deviationLabel(30, 400)).toBe('on_track')
    })

    it('returns null when no deviation or expected', () => {
      expect(deviationLabel(null, 400)).toBeNull()
      expect(deviationLabel(100, null)).toBeNull()
    })
  })

  describe('personalBias', () => {
    it('calculates mean ratio from calibration events', () => {
      const events = [
        { expectedCost: 400, actualCost: 600 },
        { expectedCost: 300, actualCost: 300 },
        { expectedCost: 200, actualCost: 100 },
      ] as CalibrationEvent[]

      const bias = personalBias(events)
      expect(bias).toBeCloseTo(1.0, 1)
    })

    it('returns null for empty events', () => {
      expect(personalBias([])).toBeNull()
    })
  })

  describe('accuracyByAction', () => {
    it('groups by action and calculates statistics', () => {
      const events = [
        { action: 'implement', expectedCost: 400, actualCost: 600 },
        { action: 'implement', expectedCost: 300, actualCost: 300 },
        { action: 'test', expectedCost: 200, actualCost: 100 },
      ] as CalibrationEvent[]

      const result = accuracyByAction(events)
      expect(result).toHaveLength(2)

      const implement = result.find((r) => r.action === 'implement')
      expect(implement).toBeDefined()
      expect(implement?.sampleCount).toBe(2)
      expect(implement?.meanDeviation).toBe(100)
    })

    it('returns empty array for no events', () => {
      expect(accuracyByAction([])).toEqual([])
    })
  })

  describe('ciFromBaseline', () => {
    const baselines = [
      { id: '1', matchLevel: 'exact', action: 'implement', avgCost: 500, ciLo: 400, ciHi: 600, sampleCount: 10, createdAt: '' },
      { id: '2', matchLevel: 'label_keyword', action: 'implement', avgCost: 550, ciLo: 450, ciHi: 650, sampleCount: 25, createdAt: '' },
      { id: '3', matchLevel: 'action', action: 'implement', avgCost: 600, ciLo: 400, ciHi: 800, sampleCount: 100, createdAt: '' },
    ] as CostBaseline[]

    it('returns exact match when overlap >= 2 and sampleCount >= 5', () => {
      const result = ciFromBaseline(baselines, 'implement auth', 'implement auth', 'implement')
      expect(result).not.toBeNull()
      expect(result?.expected).toBe(500)
    })

    it('returns label_keyword match when exact overlap < 2 but label keyword overlap >= 1', () => {
      const result = ciFromBaseline(baselines, 'implement auth', 'implement', 'implement')
      expect(result).not.toBeNull()
      expect(result?.expected).toBe(550)
    })

    it('returns label_keyword match when at least 1 token overlaps between goal and label', () => {
      const result = ciFromBaseline(baselines, 'implement auth', 'implement', 'implement')
      expect(result).not.toBeNull()
      expect(result?.expected).toBe(550)
    })

    it('skips label_keyword when no token overlap with goal', () => {
      const result = ciFromBaseline(baselines, 'something new', 'unknown', 'implement')
      expect(result).not.toBeNull()
      expect(result?.expected).toBe(600)
    })

    it('returns action fallback when only action baseline exists', () => {
      const actionOnly = [
        { id: '3', matchLevel: 'action' as const, action: 'implement', avgCost: 600, ciLo: 400, ciHi: 800, sampleCount: 100, createdAt: '' },
      ]
      const result = ciFromBaseline(actionOnly, 'something new', 'unknown', 'implement')
      expect(result).not.toBeNull()
      expect(result?.expected).toBe(600)
    })

    it('returns null when no match at any level', () => {
      const result = ciFromBaseline([], 'test', 'test', 'unknown')
      expect(result).toBeNull()
    })
  })

  describe('efficiency', () => {
    it('calculates ratio of total expected to total actual', () => {
      const phases = [
        { status: 'completed', expectedCost: 400, actualCost: 500 },
        { status: 'completed', expectedCost: 200, actualCost: 200 },
      ] as RoutePhase[]

      const eff = efficiency(phases)
      expect(eff).toBeCloseTo(0.857, 2)
    })

    it('returns null when no expected cost', () => {
      const phases = [{ status: 'completed', expectedCost: 0, actualCost: 100 }] as RoutePhase[]
      expect(efficiency(phases)).toBeNull()
    })
  })

  describe('hazardFromPhases', () => {
    it('flags phases with cost ratio > 3.0', () => {
      const phases = [
        { label: 'Test', status: 'completed', expectedCost: 100, actualCost: 500 },
      ] as RoutePhase[]

      const hazards = hazardFromPhases(phases)
      expect(hazards).toHaveLength(1)
      expect(hazards[0]).toContain('Test')
    })

    it('returns empty for normal phases', () => {
      const phases = [
        { label: 'Test', status: 'completed', expectedCost: 100, actualCost: 120 },
      ] as RoutePhase[]

      expect(hazardFromPhases(phases)).toEqual([])
    })
  })
})
