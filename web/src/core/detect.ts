/**
 * Loop detection from clip boundaries (port of core.py).
 */

import { DetectedLoop, LoopBoundary, SAMPLE_RATE, TapeSession, samplesPerBar } from './models';
import { pyInt, roundHalfEven } from './pyround';

/** Cluster clip start positions within tolerance. Positions are [sample, channel] pairs. */
export function clusterPositions(positions: [number, number][], tolerance = 4410): LoopBoundary[] {
  if (positions.length === 0) return [];

  const sortedPos = [...positions].sort((a, b) => a[0] - b[0]);
  const clusters: [number, number][][] = [];
  let currentCluster: [number, number][] = [sortedPos[0]];

  for (const [pos, ch] of sortedPos.slice(1)) {
    const clusterCenter = currentCluster.reduce((sum, [p]) => sum + p, 0) / currentCluster.length;
    if (Math.abs(pos - clusterCenter) <= tolerance) {
      currentCluster.push([pos, ch]);
    } else {
      clusters.push(currentCluster);
      currentCluster = [[pos, ch]];
    }
  }
  clusters.push(currentCluster);

  const boundaries = clusters.map((cluster): LoopBoundary => {
    const positionsOnly = cluster.map(([p]) => p);
    const channels = new Set(cluster.map(([, ch]) => ch));
    const center = pyInt(roundHalfEven(positionsOnly.reduce((a, b) => a + b, 0) / positionsOnly.length));
    const spread = positionsOnly.length > 1 ? Math.max(...positionsOnly) - Math.min(...positionsOnly) : 0;
    const confidence = tolerance > 0 ? 1.0 - Math.min(spread / tolerance, 1.0) : 1.0;
    return { position: center, tracksStarting: channels, confidence, rawPositions: positionsOnly };
  });

  return boundaries.sort((a, b) => a.position - b.position);
}

/** Detect loop boundaries from clip start positions. */
export function detectBoundaries(session: TapeSession, minTracks = 1, toleranceMs = 100): LoopBoundary[] {
  const toleranceSamples = pyInt((toleranceMs * SAMPLE_RATE) / 1000);
  const positions = session.clips.map((clip): [number, number] => [clip.start, clip.channel]);
  const boundaries = clusterPositions(positions, toleranceSamples);
  return boundaries.filter((b) => b.tracksStarting.size >= minTracks);
}

/** Convert boundaries into loop regions. */
export function detectLoops(session: TapeSession, boundaries: LoopBoundary[], minBars = 1.0): DetectedLoop[] {
  if (boundaries.length === 0) return [];

  const minSamples = pyInt(minBars * samplesPerBar(session));
  const loops: DetectedLoop[] = [];

  boundaries.forEach((boundary, i) => {
    let endSample: number;
    let endBoundary: LoopBoundary | null;
    if (i + 1 < boundaries.length) {
      endSample = boundaries[i + 1].position;
      endBoundary = boundaries[i + 1];
    } else {
      endSample = Math.max(...session.clips.map((c) => c.stop));
      endBoundary = null;
    }

    const length = endSample - boundary.position;
    if (length < minSamples) return;

    loops.push(new DetectedLoop(loops.length + 1, boundary.position, endSample, boundary, endBoundary, session.bpm));
  });

  return loops;
}

/** Split a loop into smaller chunks of N bars each. */
export function splitLoopByBars(loop: DetectedLoop, barCount: number): DetectedLoop[] {
  const spb = ((SAMPLE_RATE * 60.0) / loop.bpm) * 4;
  const chunkSamples = pyInt(barCount * spb);

  const chunks: DetectedLoop[] = [];
  let pos = loop.startSample;
  let idx = 1;

  while (pos < loop.endSample) {
    const end = Math.min(pos + chunkSamples, loop.endSample);
    const chunkBoundary: LoopBoundary = {
      position: pos,
      tracksStarting: loop.tracksActive,
      confidence: 1.0,
      rawPositions: [pos],
    };
    chunks.push(new DetectedLoop(idx, pos, end, chunkBoundary, null, loop.bpm));
    pos = end;
    idx += 1;
  }

  return chunks;
}

/** Check which tracks (channels 0-3) have audio content in the given range. */
export function getTrackActivity(session: TapeSession, start: number, end: number): boolean[] {
  const activity = [false, false, false, false];
  for (const clip of session.clips) {
    if (clip.stop > start && clip.start < end) activity[clip.channel] = true;
  }
  return activity;
}
