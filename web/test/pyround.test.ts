/**
 * Parity: pyround.ts helpers vs Python's round().
 */

import { describe, expect, it } from 'vitest';
import { pyRound, roundHalfEven } from '../src/core/pyround';
import { Json, expected } from './helpers';

describe('pyRound(x, n) matches Python round(x, n)', () => {
  for (const c of expected.pyround as Json[]) {
    it(`round(${c.x}, ${c.n}) == ${c.result}`, () => {
      expect(pyRound(c.x, c.n)).toBe(c.result);
    });
  }
});

describe('roundHalfEven(x) matches Python round(x)', () => {
  for (const c of expected.round_half_even as Json[]) {
    it(`round(${c.x}) == ${c.result}`, () => {
      // Python returns an int; -0 vs 0 does not matter for an int
      expect(roundHalfEven(c.x) + 0).toBe(c.result);
    });
  }
});
