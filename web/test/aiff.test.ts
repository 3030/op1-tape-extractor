/**
 * Parity: OG OP-1 AIFF region parsing (parse_aiff_regions, _parse_regn_chunk,
 * parse_aiff_tape_session, _estimate_bpm_from_regions, detect_loops_from_aiff_regions).
 */

import { describe, expect, it } from 'vitest';
import { decodeAudio } from '../src/core/decode';
import {
  AiffTrackFile, detectLoopsFromAiffRegions, estimateBpmFromRegions, parseAiffRegions,
  parseAiffTapeSession, parseRegnChunk, parseTapeJson,
} from '../src/core/tape';
import { Json, expected, readFixture, readFixtureText, serLoop, serSession } from './helpers';

describe('parse_aiff_regions', () => {
  for (const c of expected.aiff_regions as Json[]) {
    it(c.file, () => {
      const md = parseAiffRegions(readFixture(c.file));
      const got = md === null ? null : {
        regions: md.regions.map((r) => [r.startSample, r.endSample]),
        sample_rate: md.sampleRate,
      };
      expect(got).toEqual(c.result);
    });
  }
});

describe('_parse_regn_chunk', () => {
  for (const c of expected.regn_chunks as Json[]) {
    it(c.label, () => {
      const bytes = Uint8Array.from(Buffer.from(c.hex, 'hex'));
      const got = parseRegnChunk(new DataView(bytes.buffer));
      expect(got.map((r) => [r.startSample, r.endSample])).toEqual(c.result);
    });
  }
});

describe('parse_aiff_tape_session + detect_loops_from_aiff_regions', () => {
  for (const [key, want] of Object.entries<Json>(expected.og_sessions)) {
    it(key, () => {
      const files: AiffTrackFile[] = (want.files as string[]).map((name) => {
        const data = readFixture(`${want.folder}/${name}`);
        return { channel: Number(/track_(\d)/.exec(name)![1]) - 1, data, frames: () => decodeAudio(data).frames };
      });
      const session = parseAiffTapeSession(files)!;
      expect(serSession(session)).toEqual(want.session);
      expect(detectLoopsFromAiffRegions(session).map(serLoop)).toEqual(want.loops);
    });
  }

  it('no AIFF files -> None', () => {
    expect(parseAiffTapeSession([])).toBeNull();
  });

  it('detect_loops_from_aiff_regions returns [] for a Field session', () => {
    expect(detectLoopsFromAiffRegions(parseTapeJson(readFixtureText(expected.field.tape_file)))).toEqual([]);
  });
});

describe('_estimate_bpm_from_regions', () => {
  for (const c of expected.bpm_estimates as Json[]) {
    const regions = (c.regions as number[][]).map(([startSample, endSample]) => ({ startSample, endSample }));
    if (c.error) {
      // Documented divergence: a zero-length median region makes core.py raise
      // ZeroDivisionError; the TS port divides to Infinity, finds no BPM in 60-200 and
      // falls back to 120. Benign (Python would crash the tape load).
      it(`${JSON.stringify(c.regions)} (Python: ${c.error})`, () => {
        expect(estimateBpmFromRegions(regions)).toBe(120);
      });
      continue;
    }
    it(JSON.stringify(c.regions), () => {
      expect(estimateBpmFromRegions(regions)).toBe(c.result);
    });
  }
});
