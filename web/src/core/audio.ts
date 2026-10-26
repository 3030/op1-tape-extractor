/**
 * Slicing, mixing, fades and WAV encoding (port of core.py).
 * All math stays in float32 like numpy does, so exports match the desktop app.
 */

import { getTrackActivity } from './detect';
import { AudioData } from './decode';
import { DetectedLoop, SAMPLE_RATE, TapeSession } from './models';

const f32 = Math.fround;

/** Decoded tape tracks, indexed by track number - 1. Missing tracks are null. */
export class TapeAudio {
  constructor(public readonly tracks: (AudioData | null)[]) {}

  track(trackNum: number): AudioData | null {
    return this.tracks[trackNum - 1] ?? null;
  }

  get sampleRate(): number {
    return this.tracks.find((t) => t)?.sampleRate ?? SAMPLE_RATE;
  }

  trackLength(trackNum: number): number {
    return this.track(trackNum)?.frames ?? 0;
  }

  get maxLength(): number {
    return Math.max(...[1, 2, 3, 4].map((n) => this.trackLength(n)));
  }
}

/** Planar audio: one Float32Array per channel */
export type Planar = Float32Array[];

/** Slice audio for a loop region. Returns track number -> planar slice, padded to the loop length. */
export function sliceLoop(audio: TapeAudio, loop: DetectedLoop, session: TapeSession): Map<number, Planar> {
  const activity = getTrackActivity(session, loop.startSample, loop.endSample);
  const slices = new Map<number, Planar>();

  for (let trackNum = 1; trackNum <= 4; trackNum++) {
    if (!activity[trackNum - 1]) continue;
    const track = audio.track(trackNum);
    if (!track) continue;

    const start = loop.startSample;
    const end = Math.min(loop.endSample, track.frames);
    if (start >= track.frames) continue;

    const targetLength = loop.endSample - loop.startSample;
    slices.set(trackNum, track.channels.map((ch) => {
      const out = new Float32Array(Math.max(0, targetLength)); // zero padding past the end
      out.set(ch.subarray(start, end));
      return out;
    }));
  }

  return slices;
}

function peakOf(channels: Planar): number {
  let peak = 0;
  for (const ch of channels) {
    for (let i = 0; i < ch.length; i++) {
      const a = Math.abs(ch[i]);
      if (a > peak) peak = a;
    }
  }
  return peak;
}

/** Scale in place by limit / peak when the peak exceeds limit (float32 math, like numpy) */
function limitPeak(channels: Planar, limit: number): void {
  const peak = peakOf(channels);
  const limit32 = f32(limit);
  if (peak > limit32) {
    const scale = f32(limit32 / peak);
    for (const ch of channels) {
      for (let i = 0; i < ch.length; i++) ch[i] = f32(ch[i] * scale);
    }
  }
}

/** Sum all track slices into a mix */
export function createMix(slices: Map<number, Planar>): Planar | null {
  if (slices.size === 0) return null;

  const all = [...slices.values()];
  const numChannels = Math.max(...all.map((s) => s.length));
  const length = all[0][0].length;
  const mix = Array.from({ length: numChannels }, () => new Float32Array(length));

  for (const audio of all) {
    const minLen = Math.min(length, audio[0].length);
    for (let c = 0; c < numChannels; c++) {
      // Mono sources broadcast into every mix channel
      const src = audio[audio.length === 1 ? 0 : c];
      if (!src) continue;
      const dst = mix[c];
      for (let i = 0; i < minLen; i++) dst[i] += src[i];
    }
  }

  limitPeak(mix, 0.99);
  return mix;
}

/** numpy.linspace(start, stop, num) */
function linspace(start: number, stop: number, num: number): Float64Array {
  const out = new Float64Array(num);
  if (num === 1) {
    out[0] = start;
    return out;
  }
  const step = (stop - start) / (num - 1);
  for (let i = 0; i < num; i++) out[i] = i * step + start;
  out[num - 1] = stop;
  return out;
}

/**
 * Apply short fade in/out to ensure audio starts and ends at zero.
 * fadeSamples defaults to 882 (~20ms at 44100Hz). Returns a faded copy.
 */
export function applyZeroFade(audio: Planar, fadeSamples = 882): Planar {
  const length = audio[0]?.length ?? 0;
  if (length < fadeSamples * 2) fadeSamples = Math.floor(length / 4);
  if (fadeSamples < 1) return audio;

  const fadeIn = linspace(0, 1, fadeSamples);
  const fadeOut = linspace(1, 0, fadeSamples);
  const tail = length - fadeSamples;

  return audio.map((src) => {
    const ch = new Float32Array(src);
    for (let i = 0; i < fadeSamples; i++) ch[i] = f32(ch[i] * fadeIn[i]);
    for (let i = 0; i < fadeSamples; i++) ch[tail + i] = f32(ch[tail + i] * fadeOut[i]);
    return ch;
  });
}

/** Encode planar audio as a 32-bit float WAV (soundfile subtype 'FLOAT') */
export function encodeWavFloat32(channels: Planar, sampleRate: number): ArrayBuffer {
  const numChannels = channels.length;
  const frames = channels[0]?.length ?? 0;
  const dataBytes = frames * numChannels * 4;
  // RIFF, fmt (16-byte body), fact, data header; 56 bytes keeps the samples 4-byte aligned
  const headerBytes = 12 + 24 + 12 + 8;
  const buffer = new ArrayBuffer(headerBytes + dataBytes);
  const view = new DataView(buffer);
  const writeStr = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i));
  };

  writeStr(0, 'RIFF');
  view.setUint32(4, buffer.byteLength - 8, true);
  writeStr(8, 'WAVE');

  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 3, true); // WAVE_FORMAT_IEEE_FLOAT
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * numChannels * 4, true);
  view.setUint16(32, numChannels * 4, true);
  view.setUint16(34, 32, true);

  writeStr(36, 'fact');
  view.setUint32(40, 4, true);
  view.setUint32(44, frames, true);

  writeStr(48, 'data');
  view.setUint32(52, dataBytes, true);

  const out = new Float32Array(buffer, headerBytes, frames * numChannels);
  if (numChannels === 1) {
    out.set(channels[0]);
  } else {
    for (let c = 0; c < numChannels; c++) {
      const ch = channels[c];
      for (let i = 0, j = c; i < frames; i++, j += numChannels) out[j] = ch[i];
    }
  }
  return buffer;
}
