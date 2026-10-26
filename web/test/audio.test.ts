/**
 * Parity: slice_loop, create_mix, apply_zero_fade, export_loop, plus WAV encode round trips.
 */

import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, describe, expect, it } from 'vitest';
import {
  Planar, TapeAudio, applyZeroFade, createMix, encodeWavFloat32, sliceLoop,
} from '../src/core/audio';
import { decodeAudio } from '../src/core/decode';
import { exportLoop } from '../src/core/export';
import { parseTapeJson } from '../src/core/tape';
import {
  HAS_PYTHON, Json, PYTHON, expectSummaryMatch, expected, loopFromDef, readFixture, readFixtureText,
  sha256F32, summarizePlanar,
} from './helpers';

const A: Json = expected.audio;
const session = parseTapeJson(readFixtureText(`${A.folder}/tape.json`));
const tapeAudio = new TapeAudio([1, 2, 3, 4].map((n) => decodeAudio(readFixture(`${A.folder}/track_${n}.wav`))));

describe('slice_loop / create_mix / apply_zero_fade per detected loop', () => {
  for (const entry of A.slices as Json[]) {
    const loop = loopFromDef(entry.loop);
    const label = `loop ${loop.index} (${loop.startSample}..${loop.endSample})`;

    it(`${label}: slices + default fade`, () => {
      const slices = sliceLoop(tapeAudio, loop, session);
      expect([...slices.keys()].map(String)).toEqual(Object.keys(entry.tracks));
      for (const [tn, slice] of slices) {
        const want = entry.tracks[String(tn)];
        expectSummaryMatch(summarizePlanar(slice), want.slice, `${label} track ${tn} slice`);
        expectSummaryMatch(summarizePlanar(applyZeroFade(slice)), want.fade, `${label} track ${tn} fade`);
      }
    });

    it(`${label}: mix (Python normalized: ${entry.mix_normalized}, raw peak ${entry.mix_raw_peak})`, () => {
      const mix = createMix(sliceLoop(tapeAudio, loop, session));
      if (entry.mix === null) {
        expect(mix).toBeNull();
        return;
      }
      expectSummaryMatch(summarizePlanar(mix!), entry.mix, `${label} mix`);
      expectSummaryMatch(summarizePlanar(applyZeroFade(mix!)), entry.mix_fade, `${label} mix fade`);
    });
  }

  it('create_mix of no slices is None', () => {
    expect(createMix(new Map())).toBeNull();
  });
});

describe('apply_zero_fade short lengths', () => {
  const slices1 = sliceLoop(tapeAudio, loopFromDef(A.loops[0]), session);
  for (const f of A.fades as Json[]) {
    it(`track ${f.track}, length ${f.length}, fade_samples ${f.fade_samples}`, () => {
      const src: Planar = slices1.get(f.track)!.map((ch) => ch.slice(0, f.length));
      const out = applyZeroFade(src, f.fade_samples);
      if (f.length === 0) {
        expect(out.length).toBe(f.result.channels);
        expect(out[0].length).toBe(0);
        return;
      }
      expectSummaryMatch(summarizePlanar(out), f.result, `fade ${f.length}/${f.fade_samples}`);
      // apply_zero_fade copies; the input must be untouched
      expect(sha256F32(src[0])).toBe(sha256F32(slices1.get(f.track)![0].slice(0, f.length)));
    });
  }
});

describe('create_mix with a mono slice before a stereo one', () => {
  it('documented divergence: Python raises, TS broadcasts', () => {
    // core.py: mix = np.zeros_like(first) is (L, 1); adding an (L, 2) slice cannot broadcast
    // into it, so create_mix raises. Only reachable when track N is mono and a later track
    // is stereo. The TS port instead sizes the mix to the widest slice.
    expect(A.mix_mono_first.error).toMatch(/^ValueError: non-broadcastable output operand/);
    const s = sliceLoop(tapeAudio, loopFromDef(A.loops[0]), session);
    const mix = createMix(new Map([[2, s.get(2)!], [1, s.get(1)!]]))!;
    expect(mix.length).toBe(2);
  });
});

describe('export_loop', () => {
  for (const ex of A.exports as Json[]) {
    const loop = loopFromDef(ex.loop);
    const o = ex.options;
    const label = `${ex.folder_name} (${JSON.stringify(o)})`;

    it(label, () => {
      const result = exportLoop(tapeAudio, loop, session, {
        includeMix: o.include_mix ?? true,
        includeMetadata: o.include_metadata ?? true,
        customFolderName: o.custom_folder_name ?? null,
        zeroFade: o.zero_fade ?? false,
      });
      expect(result.folderName).toBe(ex.folder_name);
      const files = [...result.files].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
      expect(files.map((f) => f.name)).toEqual(ex.files.map((f: Json) => f.name));

      for (const want of ex.files as Json[]) {
        const got = files.find((f) => f.name === want.name)!;
        if (want.text !== undefined) {
          expect(got.data, `${ex.folder_name}/${want.name}`).toBe(want.text);
        } else {
          expect(got.data).toBeInstanceOf(ArrayBuffer);
          const audio = decodeAudio(got.data as ArrayBuffer);
          expectSummaryMatch(summarizePlanar(audio.channels, audio.sampleRate), want.summary, `${ex.folder_name}/${want.name}`);
        }
      }
    });
  }
});

describe('encodeWavFloat32', () => {
  function noise(frames: number, seed: number): Float32Array {
    const out = new Float32Array(frames);
    let x = seed >>> 0;
    for (let i = 0; i < frames; i++) {
      x = (Math.imul(x, 1664525) + 1013904223) >>> 0;
      out[i] = (x / 2 ** 32) * 4 - 2;
    }
    out[0] = -0;
    out[1] = 1e-40; // subnormal
    out[2] = 3.4028234663852886e38; // float32 max
    out[3] = -1;
    return out;
  }
  const cases: [string, Planar][] = [
    ['mono', [noise(3001, 1)]],
    ['stereo', [noise(2000, 2), noise(2000, 3)]],
    ['4 channels', [noise(17, 4), noise(17, 5), noise(17, 6), noise(17, 7)]],
    ['empty mono', [new Float32Array(0)]],
  ];
  const tmp = mkdtempSync(join(tmpdir(), 'op1-wav-'));
  afterAll(() => rmSync(tmp, { recursive: true, force: true }));

  for (const [name, planar] of cases) {
    it(`${name}: decodes back bit-identically via decodeAudio`, () => {
      const wav = encodeWavFloat32(planar, 44100);
      const audio = decodeAudio(wav);
      expect(audio.sampleRate).toBe(44100);
      expect(audio.frames).toBe(planar[0].length);
      expect(audio.channels.length).toBe(planar.length);
      planar.forEach((ch, c) => expect(sha256F32(audio.channels[c])).toBe(sha256F32(ch)));
    });

    it.skipIf(!HAS_PYTHON || planar[0].length === 0)(`${name}: soundfile reads it bit-identically`, () => {
      const wav = encodeWavFloat32(planar, 48000);
      const path = join(tmp, `${name.replace(/\s/g, '_')}.wav`);
      writeFileSync(path, new Uint8Array(wav));
      const script = [
        'import sys, json, hashlib, soundfile as sf',
        "d, sr = sf.read(sys.argv[1], dtype='float32', always_2d=True)",
        'info = sf.info(sys.argv[1])',
        "print(json.dumps({'sr': sr, 'frames': d.shape[0], 'channels': d.shape[1], 'subtype': info.subtype,",
        "  'sha': [hashlib.sha256(d[:, c].astype('<f4').tobytes()).hexdigest() for c in range(d.shape[1])]}))",
      ].join('\n');
      const r = spawnSync(PYTHON, ['-c', script, path], { encoding: 'utf-8' });
      expect(r.status, r.stderr).toBe(0);
      const py = JSON.parse(r.stdout);
      expect(py).toEqual({
        sr: 48000,
        frames: planar[0].length,
        channels: planar.length,
        subtype: 'FLOAT',
        sha: planar.map(sha256F32),
      });
    });
  }
});
