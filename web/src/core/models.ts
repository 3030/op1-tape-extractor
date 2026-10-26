/**
 * OP-1 tape data structures (port of the dataclasses in core.py).
 */

export const SAMPLE_RATE = 44100;
export const TRACK_NAMES = ['track_1.wav', 'track_2.wav', 'track_3.wav', 'track_4.wav'];
export const AIFF_TRACK_NAMES = ['track_1.aif', 'track_2.aif', 'track_3.aif', 'track_4.aif'];

/** A single clip from tape.json */
export interface Clip {
  channel: number; // 0-3
  clipId: number;
  start: number; // sample position
  stop: number; // sample position
}

/** Parsed OP-1 tape session */
export interface TapeSession {
  clips: Clip[];
  bpm: number;
  loopIn: number;
  loopOut: number;
  tapeType: number;
  isAiffSource: boolean; // true for OG OP-1 (AIFF), false for Field (WAV)
}

export function samplesPerBeat(session: TapeSession): number {
  return (SAMPLE_RATE * 60.0) / session.bpm;
}

export function samplesPerBar(session: TapeSession): number {
  return samplesPerBeat(session) * 4;
}

/** A detected boundary where tracks align */
export interface LoopBoundary {
  position: number;
  tracksStarting: Set<number>;
  confidence: number;
  rawPositions: number[];
}

/** A loop region between two boundaries */
export class DetectedLoop {
  constructor(
    public index: number,
    public startSample: number,
    public endSample: number,
    public startBoundary: LoopBoundary,
    public endBoundary: LoopBoundary | null,
    public bpm: number,
    public customName: string | null = null,
  ) {}

  get lengthSamples(): number {
    return this.endSample - this.startSample;
  }

  get lengthSeconds(): number {
    return this.lengthSamples / SAMPLE_RATE;
  }

  get lengthBars(): number {
    const spb = (SAMPLE_RATE * 60.0) / this.bpm;
    return this.lengthSamples / (spb * 4);
  }

  get tracksActive(): Set<number> {
    return this.startBoundary.tracksStarting;
  }

  timeStr(): string {
    const secs = this.lengthSeconds;
    return `${Math.floor(secs / 60)}:${String(Math.floor(secs % 60)).padStart(2, '0')}`;
  }
}
