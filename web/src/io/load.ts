/**
 * Load a tape folder into a session plus decoded track audio
 * (the web side of MainWindow._on_folder_dropped and AudioLoader).
 */

import { TapeAudio } from '../core/audio';
import { AudioData, decodeAudio } from '../core/decode';
import { TapeSession } from '../core/models';
import { detectTapeFormat, parseAiffTapeSession, parseTapeJson } from '../core/tape';
import { TapeFolder } from './folder';

export interface LoadedTape {
  session: TapeSession;
  audio: TapeAudio;
  formatLabel: 'Field' | 'OG';
}

export class TapeLoadError extends Error {}

function trackFile(folder: TapeFolder, trackNum: number, isAiff: boolean): File | undefined {
  if (isAiff) {
    const aif = folder.files.get(`track_${trackNum}.aif`) ?? folder.files.get(`track_${trackNum}.aiff`);
    if (aif) return aif;
  }
  return folder.files.get(`track_${trackNum}.wav`);
}

/** Let the browser paint between heavy steps */
const nextFrame = () => new Promise((resolve) => setTimeout(resolve, 0));

export async function loadTape(folder: TapeFolder): Promise<LoadedTape> {
  const format = detectTapeFormat(new Set(folder.files.keys()));
  if (format === 'unknown') {
    throw new TapeLoadError('No OP-1 tape files found (need tape.json+WAV or AIFF files)');
  }
  const isAiff = format === 'og';

  const buffers: (ArrayBuffer | null)[] = [];
  for (let n = 1; n <= 4; n++) {
    const file = trackFile(folder, n, isAiff);
    buffers.push(file ? await file.arrayBuffer() : null);
  }

  const tracks: (AudioData | null)[] = [];
  for (const buffer of buffers) {
    await nextFrame();
    tracks.push(buffer ? decodeAudio(buffer) : null);
  }
  const audio = new TapeAudio(tracks);

  let session: TapeSession | null;
  if (isAiff) {
    // Only true AIFF tracks carry regn region chunks
    const aiffFiles = buffers.flatMap((data, i) => {
      const name = trackFile(folder, i + 1, true)?.name.toLowerCase() ?? '';
      if (!data || !/\.aiff?$/.test(name)) return [];
      return [{ channel: i, data, frames: () => tracks[i]?.frames ?? 0 }];
    });
    session = parseAiffTapeSession(aiffFiles);
    if (!session) throw new TapeLoadError('Could not parse AIFF tape files');
  } else {
    session = parseTapeJson(await folder.files.get('tape.json')!.text());
  }

  return { session, audio, formatLabel: isAiff ? 'OG' : 'Field' };
}
