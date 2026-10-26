/**
 * Parity: tape.json parsing and loop detection (parse_tape_json, cluster_positions,
 * detect_boundaries, detect_loops, split_loop_by_bars, get_track_activity, detect_tape_format).
 */

import { describe, expect, it } from 'vitest';
import {
  clusterPositions, detectBoundaries, detectLoops, getTrackActivity, splitLoopByBars,
} from '../src/core/detect';
import { detectTapeFormat, parseTapeJson } from '../src/core/tape';
import { Json, expected, readFixtureText, serBoundary, serLoop, serSession } from './helpers';

for (const key of ['field', 'export_tape'] as const) {
  const want: Json = expected[key];

  describe(`${key} tape (${want.tape_file})`, () => {
    const session = parseTapeJson(readFixtureText(want.tape_file));

    it('parse_tape_json', () => {
      expect(serSession(session)).toEqual(want.session);
    });

    for (const [name, boundaries] of Object.entries<Json>(want.boundaries)) {
      it(`detect_boundaries ${name}`, () => {
        const m = /^min(\d+)_tol(.+)$/.exec(name)!;
        const got = detectBoundaries(session, Number(m[1]), Number(m[2]));
        expect(got.map(serBoundary)).toEqual(boundaries);
      });
    }

    for (const [name, loops] of Object.entries<Json>(want.loops)) {
      it(`detect_loops ${name}`, () => {
        const m = /^min(\d+)_bars(.+)$/.exec(name)!;
        const got = detectLoops(session, detectBoundaries(session, Number(m[1])), Number(m[2]));
        expect(got.map(serLoop)).toEqual(loops);
      });
    }

    it(`split_loop_by_bars (from ${want.splits_from})`, () => {
      const m = /^min(\d+)_bars(.+)$/.exec(want.splits_from)!;
      const loops = detectLoops(session, detectBoundaries(session, Number(m[1])), Number(m[2]));
      for (const split of want.splits) {
        const loop = loops.find((l) => l.index === split.loop_index)!;
        expect(splitLoopByBars(loop, split.bars).map(serLoop), `loop ${split.loop_index} / ${split.bars} bars`)
          .toEqual(split.result);
      }
    });

    it('get_track_activity', () => {
      for (const a of want.activity) {
        expect(getTrackActivity(session, a.start, a.end), `${a.start}..${a.end}`).toEqual(a.result);
      }
    });
  });
}

describe('cluster_positions edge cases', () => {
  for (const c of expected.cluster as Json[]) {
    it(`${JSON.stringify(c.positions)} tol=${c.tolerance}`, () => {
      const got = clusterPositions(c.positions.map((p: number[]) => [p[0], p[1]] as [number, number]), c.tolerance);
      expect(got.map(serBoundary)).toEqual(c.result);
    });
  }
});

describe('detect_tape_format', () => {
  for (const c of expected.detect_format as Json[]) {
    const names = new Set<string>(c.files);
    if (c.files.includes('track_1.aiff')) {
      // Documented divergence, not a parity bug: core.py only looks for .aif names
      // (AIFF_TRACK_NAMES) so a folder of .aiff files is 'unknown', while the TS port
      // deliberately also accepts .aiff (AudioLoader in core.py can load .aiff).
      it(`${JSON.stringify(c.files)} (intentional .aiff extension)`, () => {
        expect(c.result).toBe('unknown');
        expect(detectTapeFormat(names)).toBe('og');
      });
      continue;
    }
    it(JSON.stringify(c.files), () => {
      expect(detectTapeFormat(names)).toBe(c.result);
    });
  }
});
