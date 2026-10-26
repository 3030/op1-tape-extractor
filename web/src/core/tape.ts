/**
 * Tape parsing: tape.json (OP-1 Field) and AIFF region metadata (OG OP-1).
 * Port of the parsing half of core.py.
 */

import {
  AIFF_TRACK_NAMES, Clip, DetectedLoop, LoopBoundary, SAMPLE_RATE, TRACK_NAMES, TapeSession,
} from './models';
import { pyRound } from './pyround';

// =============================================================================
// AIFF Parsing for Original OP-1
// =============================================================================

/** A single region from AIFF regn chunk */
export interface AiffRegion {
  startSample: number;
  endSample: number;
}

/** Metadata extracted from an OP-1 AIFF file */
export interface AiffTapeMetadata {
  regions: AiffRegion[];
  sampleRate: number;
}

function fourCC(view: DataView, offset: number): string {
  return String.fromCharCode(
    view.getUint8(offset), view.getUint8(offset + 1), view.getUint8(offset + 2), view.getUint8(offset + 3),
  );
}

/**
 * Parse an OP-1 AIFF file to extract region markers from the 'regn' chunk.
 *
 * The OP-1 stores loop region data in a custom 'regn' chunk within AIFF files.
 * Each region entry is 88 bytes with start sample at bytes 20-23 and end at 28-31.
 */
export function parseAiffRegions(data: ArrayBuffer): AiffTapeMetadata | null {
  const view = new DataView(data);
  if (data.byteLength < 12) return null;

  // Read and verify FORM header, skip file size, verify AIFF/AIFC format tag
  if (fourCC(view, 0) !== 'FORM') return null;
  const formatTag = fourCC(view, 8);
  if (formatTag !== 'AIFF' && formatTag !== 'AIFC') return null;

  let regnData: DataView | null = null;
  let offset = 12;

  // Parse chunks
  while (offset + 4 <= data.byteLength) {
    const chunkId = fourCC(view, offset);
    if (offset + 8 > data.byteLength) break;
    const chunkSize = view.getUint32(offset + 4, false);
    const bodyStart = offset + 8;

    if (chunkId === 'regn') {
      const available = Math.min(chunkSize, data.byteLength - bodyStart);
      regnData = new DataView(data, bodyStart, available);
    }
    // COMM holds the sample rate, but the OP-1 always records at 44100

    // AIFF chunks are padded to even byte boundaries
    offset = bodyStart + chunkSize + (chunkSize % 2);

    // Stop if we found regn (regions is what we need)
    if (regnData !== null) break;
  }

  if (regnData === null) return null;

  const regions = parseRegnChunk(regnData);
  if (regions.length === 0) return null;

  return { regions, sampleRate: SAMPLE_RATE };
}

/**
 * Parse the regn chunk data to extract regions.
 *
 * Format:
 * - First 4 bytes: region count (big-endian int)
 * - Then N regions, each 88 bytes:
 *   - Bytes 20-23: start sample (big-endian)
 *   - Bytes 28-31: end sample (big-endian)
 */
export function parseRegnChunk(data: DataView): AiffRegion[] {
  if (data.byteLength < 4) return [];

  const regionCount = data.getUint32(0, false);
  const regions: AiffRegion[] = [];

  let offset = 4;
  for (let i = 0; i < regionCount; i++) {
    if (offset + 88 > data.byteLength) break;
    regions.push({
      startSample: data.getUint32(offset + 20, false),
      endSample: data.getUint32(offset + 28, false),
    });
    offset += 88;
  }

  // Sort by start position
  regions.sort((a, b) => a.startSample - b.startSample);
  return regions;
}

export type TapeFormat = 'field' | 'og' | 'unknown';

/**
 * Detect whether a folder contains OP-1 Field (tape.json + WAV) or OG OP-1 (AIFF) format.
 * Takes the lowercase file names in the folder.
 */
export function detectTapeFormat(fileNames: Set<string>): TapeFormat {
  // Check for OP-1 Field format first
  if (fileNames.has('tape.json') && TRACK_NAMES.some((name) => fileNames.has(name))) {
    return 'field';
  }

  // Check for OG OP-1 format (AIFF files)
  for (const name of AIFF_TRACK_NAMES) {
    if (fileNames.has(name) || fileNames.has(name.replace('.aif', '.aiff'))) return 'og';
  }

  return 'unknown';
}

/** One OG OP-1 AIFF track: its channel (0-3), raw bytes, and frame count */
export interface AiffTrackFile {
  channel: number;
  data: ArrayBuffer;
  frames: () => number;
}

/**
 * Parse OG OP-1 AIFF files to create a TapeSession compatible with the existing workflow.
 *
 * Extracts region markers from AIFF files and creates clips/session data.
 * BPM detection is tricky for AIFF - we try to estimate from region lengths.
 */
export function parseAiffTapeSession(aiffFiles: AiffTrackFile[]): TapeSession | null {
  if (aiffFiles.length === 0) return null;

  // Parse regions from the first available AIFF file
  // (All tracks share the same tape regions on OP-1)
  let allRegions: AiffRegion[] = [];
  for (const file of aiffFiles) {
    const metadata = parseAiffRegions(file.data);
    if (metadata && metadata.regions.length) {
      allRegions = metadata.regions;
      break;
    }
  }

  if (allRegions.length === 0) {
    // No regions found - create a default full-tape region from the first readable file
    for (const file of aiffFiles) {
      try {
        allRegions = [{ startSample: 0, endSample: file.frames() }];
        break;
      } catch {
        // try the next track
      }
    }
  }

  // Create clips from regions - each region becomes a clip on each track
  const clips: Clip[] = [];
  let clipId = 0;
  for (const region of allRegions) {
    for (const file of aiffFiles) {
      clips.push({ channel: file.channel, clipId, start: region.startSample, stop: region.endSample });
      clipId += 1;
    }
  }

  return {
    clips,
    bpm: estimateBpmFromRegions(allRegions),
    loopIn: allRegions.length ? allRegions[0].startSample : 0,
    loopOut: allRegions.length ? allRegions[allRegions.length - 1].endSample : 0,
    tapeType: 1, // OG OP-1 indicator
    isAiffSource: true,
  };
}

/**
 * Attempt to estimate BPM from region lengths.
 * Returns 120 BPM as default if estimation fails.
 */
export function estimateBpmFromRegions(regions: AiffRegion[]): number {
  if (regions.length === 0) return 120.0;

  const lengths = regions.map((r) => r.endSample - r.startSample);
  // Use median length for estimation
  const sorted = [...lengths].sort((a, b) => a - b);
  const medianLength = sorted[Math.floor(sorted.length / 2)];

  // Common bar counts to check
  for (const bars of [4, 8, 2, 1, 16]) {
    const estimatedBpm = (SAMPLE_RATE * 60.0 * 4 * bars) / medianLength;

    // Check if this gives a reasonable BPM (60-200)
    if (estimatedBpm >= 60 && estimatedBpm <= 200) {
      // Round to common BPM values
      const commonBpms = [80, 90, 100, 110, 120, 125, 130, 140, 150, 160, 170, 180];
      let closest = commonBpms[0];
      for (const bpm of commonBpms) {
        if (Math.abs(bpm - estimatedBpm) < Math.abs(closest - estimatedBpm)) closest = bpm;
      }
      if (Math.abs(closest - estimatedBpm) < 5) return closest;
      return pyRound(estimatedBpm, 1);
    }
  }

  return 120.0;
}

/**
 * Create DetectedLoop objects directly from AIFF region data.
 *
 * For AIFF sessions, the regions are already defined in the file metadata,
 * so we use them directly instead of inferring from clip boundaries.
 */
export function detectLoopsFromAiffRegions(session: TapeSession): DetectedLoop[] {
  if (!session.isAiffSource) return [];

  // Group clips by their start/stop to find unique regions (keeping first-seen order)
  const regionMap = new Map<string, { start: number; stop: number; channels: Set<number> }>();
  for (const clip of session.clips) {
    const key = `${clip.start}:${clip.stop}`;
    let region = regionMap.get(key);
    if (!region) {
      region = { start: clip.start, stop: clip.stop, channels: new Set() };
      regionMap.set(key, region);
    }
    region.channels.add(clip.channel);
  }

  // Sort regions by start position
  const sortedRegions = [...regionMap.values()].sort((a, b) => a.start - b.start);

  return sortedRegions.map((region, i) => {
    const boundary: LoopBoundary = {
      position: region.start,
      tracksStarting: region.channels,
      confidence: 1.0, // AIFF regions are exact
      rawPositions: [region.start],
    };
    return new DetectedLoop(i + 1, region.start, region.stop, boundary, null, session.bpm);
  });
}

// =============================================================================
// OP-1 Field
// =============================================================================

/** Parse tape.json into structured data */
export function parseTapeJson(text: string): TapeSession {
  const data = JSON.parse(text);

  const clips: Clip[] = (data.clips ?? []).map((c: { ch: number; id: number; start: number; stop: number }) => ({
    channel: c.ch,
    clipId: c.id,
    start: c.start,
    stop: c.stop,
  }));

  const bpmRaw = data.tempobpmdecimal ?? 1200;

  return {
    clips,
    bpm: bpmRaw / 10.0,
    loopIn: data.loop_in ?? 0,
    loopOut: data.loop_out ?? 0,
    tapeType: data.tape_type ?? 0,
    isAiffSource: false,
  };
}
