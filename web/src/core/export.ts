/**
 * Loop export (port of export_loop in core.py). Produces files in memory;
 * the io layer writes them to a folder or a ZIP.
 */

import { applyZeroFade, createMix, encodeWavFloat32, sliceLoop, TapeAudio } from './audio';
import { DetectedLoop, TapeSession } from './models';
import { pyFloatRepr, pyInt, pyRound, zeroPad } from './pyround';

export interface ExportOptions {
  includeMix?: boolean;
  includeMetadata?: boolean;
  customFolderName?: string | null;
  zeroFade?: boolean;
}

export interface ExportedFile {
  name: string;
  data: ArrayBuffer | string;
}

export interface ExportedLoop {
  folderName: string;
  files: ExportedFile[];
}

/** JSON string like Python's json.dump (ensure_ascii): everything outside space..~ is \u-escaped */
function pyJsonString(s: string): string {
  return JSON.stringify(s).replace(/[\u007f-￿]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`);
}

/** Default loop name, e.g. L001 */
export function defaultLoopName(loop: DetectedLoop): string {
  return `L${zeroPad(loop.index, 3)}`;
}

/** Export a single loop to an in-memory folder. */
export function exportLoop(
  audio: TapeAudio,
  loop: DetectedLoop,
  session: TapeSession,
  { includeMix = true, includeMetadata = true, customFolderName = null, zeroFade = false }: ExportOptions = {},
): ExportedLoop {
  const folderName = customFolderName || loop.customName || defaultLoopName(loop);
  const files: ExportedFile[] = [];

  const slices = sliceLoop(audio, loop, session);
  const sr = audio.sampleRate;

  // Calculate fade samples (~20ms)
  const fadeSamples = zeroFade ? pyInt(sr * 0.02) : 0;

  for (const [trackNum, slice] of slices) {
    const out = zeroFade ? applyZeroFade(slice, fadeSamples) : slice;
    files.push({ name: `track_${trackNum}.wav`, data: encodeWavFloat32(out, sr) });
  }

  if (includeMix && slices.size) {
    let mix = createMix(slices);
    if (mix) {
      if (zeroFade) mix = applyZeroFade(mix, fadeSamples);
      files.push({ name: 'mix.wav', data: encodeWavFloat32(mix, sr) });
    }
  }

  if (includeMetadata) {
    // Written by hand so floats keep Python's json formatting (120.0, not 120)
    const tracks = [...slices.keys()].map((n) => `    ${n}`).join(',\n');
    const metadata = [
      `  "name": ${pyJsonString(folderName)}`,
      `  "loop_index": ${loop.index}`,
      `  "bpm": ${pyFloatRepr(loop.bpm)}`,
      `  "start_sample": ${loop.startSample}`,
      `  "end_sample": ${loop.endSample}`,
      `  "length_samples": ${loop.lengthSamples}`,
      `  "length_seconds": ${pyFloatRepr(pyRound(loop.lengthSeconds, 3))}`,
      `  "length_bars": ${pyFloatRepr(pyRound(loop.lengthBars, 2))}`,
      slices.size ? `  "tracks": [\n${tracks}\n  ]` : '  "tracks": []',
      `  "confidence": ${pyFloatRepr(pyRound(loop.startBoundary.confidence, 3))}`,
    ];
    files.push({ name: 'index.json', data: `{\n${metadata.join(',\n')}\n}` });
  }

  return { folderName, files };
}
