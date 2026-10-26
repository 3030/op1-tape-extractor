/**
 * Shared helpers for the Python parity suites. Fixtures and expected.json are
 * produced by scripts/gen_fixtures.py from the original core.py.
 */

import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect } from 'vitest';
import { DetectedLoop, LoopBoundary, TapeSession } from '../src/core/models';

export const TEST_DIR = dirname(fileURLToPath(import.meta.url));
export const FIXTURES = join(TEST_DIR, 'fixtures');
export const WEB_DIR = resolve(TEST_DIR, '..');
export const PYTHON = resolve(WEB_DIR, '..', '.venv', 'bin', 'python');
export const HAS_PYTHON = existsSync(PYTHON);

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Json = any;

export const expected: Json = JSON.parse(readFileSync(join(FIXTURES, 'expected.json'), 'utf-8'));

export function fixturePath(rel: string): string {
  return join(FIXTURES, rel);
}

/** Read a fixture as a standalone ArrayBuffer (copied, so byteOffset is 0). */
export function readFixture(rel: string): ArrayBuffer {
  return new Uint8Array(readFileSync(fixturePath(rel))).buffer;
}

export function readFixtureText(rel: string): string {
  return readFileSync(fixturePath(rel), 'utf-8');
}

// ---------------------------------------------------------------------------
// Serialization mirroring gen_fixtures.py
// ---------------------------------------------------------------------------

const sortedNums = (s: Iterable<number>) => [...s].sort((a, b) => a - b);

export function serBoundary(b: LoopBoundary | null): Json {
  if (b === null) return null;
  return {
    position: b.position,
    tracks: sortedNums(b.tracksStarting),
    confidence: b.confidence,
    raw_positions: [...b.rawPositions],
  };
}

export function serLoop(lp: DetectedLoop): Json {
  return {
    index: lp.index,
    start_sample: lp.startSample,
    end_sample: lp.endSample,
    bpm: lp.bpm,
    length_samples: lp.lengthSamples,
    length_seconds: lp.lengthSeconds,
    length_bars: lp.lengthBars,
    time_str: lp.timeStr(),
    tracks_active: sortedNums(lp.tracksActive),
    start_boundary: serBoundary(lp.startBoundary),
    end_boundary: serBoundary(lp.endBoundary),
  };
}

export function serSession(s: TapeSession): Json {
  return {
    clips: s.clips.map((c) => ({ channel: c.channel, clip_id: c.clipId, start: c.start, stop: c.stop })),
    bpm: s.bpm,
    loop_in: s.loopIn,
    loop_out: s.loopOut,
    tape_type: s.tapeType,
    is_aiff_source: s.isAiffSource,
  };
}

/** Rebuild a DetectedLoop from gen_fixtures.py's loop_def(). */
export function loopFromDef(def: Json): DetectedLoop {
  const boundary: LoopBoundary = {
    position: def.start_sample,
    tracksStarting: new Set<number>(def.tracks),
    confidence: def.confidence,
    rawPositions: [def.start_sample],
  };
  return new DetectedLoop(def.index, def.start_sample, def.end_sample, boundary, null, def.bpm, def.custom_name ?? null);
}

// ---------------------------------------------------------------------------
// Audio summaries (mirror of summarize() in gen_fixtures.py)
// ---------------------------------------------------------------------------

export function sha256F32(ch: Float32Array): string {
  return createHash('sha256').update(new Uint8Array(ch.buffer, ch.byteOffset, ch.byteLength)).digest('hex');
}

export function summarizePlanar(channels: Float32Array[], sampleRate?: number): Json {
  const frames = channels[0]?.length ?? 0;
  const stride = Math.max(1, Math.floor(frames / 48));
  const perChannel = channels.map((ch) => {
    const strided: number[] = [];
    for (let i = 0; i < frames; i += stride) strided.push(ch[i]);
    let sum = 0;
    let absmax = 0;
    for (let i = 0; i < frames; i++) {
      sum += ch[i];
      const a = Math.abs(ch[i]);
      if (a > absmax) absmax = a;
    }
    return { strided, last: frames ? ch[frames - 1] : null, sum, absmax, sha256: sha256F32(ch) };
  });
  const out: Json = { frames, channels: channels.length, stride, per_channel: perChannel };
  if (sampleRate !== undefined) out.sample_rate = sampleRate;
  return out;
}

/**
 * Compare a TS summary to a Python one. Sample values, abs-max and the sha256 of the
 * float32 bytes must match exactly; the sum only within float64 accumulation error
 * (Python uses math.fsum, the TS side a plain running sum).
 */
export function expectSummaryMatch(actual: Json, want: Json, label: string): void {
  expect(actual.frames, `${label}: frames`).toBe(want.frames);
  expect(actual.channels, `${label}: channels`).toBe(want.channels);
  if (want.sample_rate !== undefined) expect(actual.sample_rate, `${label}: sample rate`).toBe(want.sample_rate);
  for (let c = 0; c < want.channels; c++) {
    const a = actual.per_channel[c];
    const w = want.per_channel[c];
    expect(a.strided, `${label}: ch${c} strided samples`).toEqual(w.strided);
    expect(a.last, `${label}: ch${c} last sample`).toBe(w.last);
    expect(a.absmax, `${label}: ch${c} abs-max`).toBe(w.absmax);
    expect(Math.abs(a.sum - w.sum), `${label}: ch${c} sum ${a.sum} vs ${w.sum}`).toBeLessThanOrEqual(
      1e-9 * Math.max(1, Math.abs(w.sum)),
    );
    expect(a.sha256, `${label}: ch${c} sha256 of float32 samples`).toBe(w.sha256);
  }
}
