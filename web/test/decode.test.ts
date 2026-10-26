/**
 * Parity: decodeAudio vs soundfile.read(dtype='float32') on synthetic WAV / AIFF / AIFF-C files.
 * Every decoded float32 sample must be bit-identical (sha256 over the samples).
 */

import { describe, expect, it } from 'vitest';
import { decodeAudio } from '../src/core/decode';
import { Json, expectSummaryMatch, expected, readFixture, summarizePlanar } from './helpers';

describe('decodeAudio matches soundfile', () => {
  for (const c of expected.decode as Json[]) {
    it(`${c.file} (${c.summary.subtype})`, () => {
      const audio = decodeAudio(readFixture(c.file));
      expect(audio.frames).toBe(c.summary.frames);
      expect(audio.channels.length).toBe(c.summary.channels);
      for (const ch of audio.channels) expect(ch.length).toBe(audio.frames);
      expectSummaryMatch(summarizePlanar(audio.channels, audio.sampleRate), c.summary, c.file);
    });
  }

  it('generator wrote every requested format', () => {
    expect(expected.decode_skipped).toEqual([]);
  });

  it('rejects non-audio input', () => {
    expect(() => decodeAudio(new ArrayBuffer(4))).toThrow();
    expect(() => decodeAudio(new TextEncoder().encode('OggS0000000000000000').buffer)).toThrow();
  });
});
