/**
 * Audio playback engine with looping and track mute support (port of AudioPlayer in core.py).
 *
 * Like the desktop app, the unmuted tracks of the current region are pre-mixed into one
 * stereo buffer (peak-limited to 0.95), which a looping AudioBufferSourceNode plays.
 */

import { TapeAudio } from '../core/audio';

const f32 = Math.fround;

type SinkContext = AudioContext & { setSinkId?: (id: string) => Promise<void> };

export class AudioPlayer {
  private ctx: SinkContext | null = null;
  private source: AudioBufferSourceNode | null = null;
  private buffer: AudioBuffer | null = null;

  private playing = false;
  private loopEnabled = true;
  private position = 0;
  private loopStart = 0;
  private loopEnd = 0;
  private muted: Record<number, boolean> = { 1: false, 2: false, 3: false, 4: false };
  private outputDeviceId = '';

  // Where the current source started: context time and tape sample position
  private anchorTime = 0;
  private anchorPos = 0;

  constructor(private readonly audio: TapeAudio) {}

  get sampleRate(): number {
    return this.audio.sampleRate;
  }

  private ensureContext(): SinkContext {
    if (!this.ctx) {
      try {
        // Match the tape rate so nothing is resampled before the output stage
        this.ctx = new AudioContext({ sampleRate: this.sampleRate, latencyHint: 'interactive' });
      } catch {
        this.ctx = new AudioContext({ latencyHint: 'interactive' });
      }
      if (this.outputDeviceId && this.ctx.setSinkId) {
        this.ctx.setSinkId(this.outputDeviceId).catch(() => undefined);
      }
    }
    return this.ctx;
  }

  /** Change the audio output device. Pass '' for the system default. */
  async setOutputDevice(deviceId: string): Promise<void> {
    this.outputDeviceId = deviceId;
    if (this.ctx?.setSinkId) await this.ctx.setSinkId(deviceId);
  }

  setRegion(startSample: number, endSample: number): void {
    this.loopStart = startSample;
    this.loopEnd = endSample;
    this.position = startSample;
    this.buffer = null;
    // Prebuild mix for faster first playback
    this.buildBuffer();
    if (this.playing) this.restartAt(startSample);
  }

  setMuted(trackNum: number, muted: boolean): void {
    if (!(trackNum in this.muted)) return;
    this.muted[trackNum] = muted;
    this.buffer = null;
    if (this.playing) this.restartAt(this.getPosition());
    else this.buildBuffer();
  }

  isMuted(trackNum: number): boolean {
    return this.muted[trackNum] ?? false;
  }

  setLoopEnabled(enabled: boolean): void {
    if (this.playing) {
      // Re-anchor so the position math stays right after the change
      this.anchorPos = this.getPosition();
      this.anchorTime = this.ctx!.currentTime;
    }
    this.loopEnabled = enabled;
    if (this.source) this.source.loop = enabled;
  }

  /** Mix the unmuted tracks of the region into a stereo float32 buffer */
  private buildMix(): [Float32Array<ArrayBuffer>, Float32Array<ArrayBuffer>] {
    const length = Math.max(0, this.loopEnd - this.loopStart);
    const left = new Float32Array(length);
    const right = new Float32Array(length);

    for (let trackNum = 1; trackNum <= 4; trackNum++) {
      if (this.muted[trackNum]) continue;
      const track = this.audio.track(trackNum);
      if (!track) continue;

      const start = this.loopStart;
      const end = Math.min(this.loopEnd, track.frames);
      if (start >= track.frames) continue;

      const chunkLen = Math.min(end - start, length);
      const l = track.channels[0];
      const r = track.channels[1] ?? l;
      for (let i = 0; i < chunkLen; i++) {
        left[i] += l[start + i];
        right[i] += r[start + i];
      }
    }

    let peak = 0;
    for (let i = 0; i < length; i++) {
      const a = Math.max(Math.abs(left[i]), Math.abs(right[i]));
      if (a > peak) peak = a;
    }
    if (peak > f32(0.95)) {
      const scale = f32(f32(0.95) / peak);
      for (let i = 0; i < length; i++) {
        left[i] = f32(left[i] * scale);
        right[i] = f32(right[i] * scale);
      }
    }
    return [left, right];
  }

  private buildBuffer(): AudioBuffer | null {
    if (this.buffer) return this.buffer;
    const length = this.loopEnd - this.loopStart;
    if (length <= 0) return null;
    const ctx = this.ensureContext();
    const [left, right] = this.buildMix();
    const buffer = ctx.createBuffer(2, length, this.sampleRate);
    buffer.copyToChannel(left, 0);
    buffer.copyToChannel(right, 1);
    this.buffer = buffer;
    return buffer;
  }

  private stopSource(): void {
    if (this.source) {
      this.source.onended = null;
      try {
        this.source.stop();
      } catch {
        // already stopped
      }
      this.source.disconnect();
      this.source = null;
    }
  }

  private restartAt(samplePosition: number): void {
    this.stopSource();
    const buffer = this.buildBuffer();
    if (!buffer) return;
    const ctx = this.ensureContext();
    if (ctx.state === 'suspended') ctx.resume();

    const pos = Math.max(this.loopStart, Math.min(samplePosition, this.loopEnd));
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.loop = this.loopEnabled;
    source.loopStart = 0;
    source.loopEnd = buffer.duration;
    source.connect(ctx.destination);
    source.onended = () => {
      if (this.source === source) {
        // Reached the end with looping off
        this.playing = false;
        this.position = this.loopEnd;
        this.source = null;
      }
    };
    source.start(0, (pos - this.loopStart) / this.sampleRate);

    this.source = source;
    this.anchorTime = ctx.currentTime;
    this.anchorPos = pos;
    this.position = pos;
  }

  play(): void {
    if (this.playing || this.loopEnd <= this.loopStart) return;
    // Finished a one-shot pass: start again from the top
    const start = this.position >= this.loopEnd ? this.loopStart : this.position;
    this.playing = true;
    this.restartAt(start);
  }

  pause(): void {
    if (this.playing) this.position = this.getPosition();
    this.playing = false;
    this.stopSource();
  }

  stop(): void {
    this.playing = false;
    this.position = this.loopStart;
    this.stopSource();
  }

  /** Seek to a specific sample position within the current loop region. */
  seek(samplePosition: number): void {
    this.position = Math.max(this.loopStart, Math.min(samplePosition, this.loopEnd));
    if (this.playing) this.restartAt(this.position);
  }

  isPlaying(): boolean {
    return this.playing;
  }

  getPosition(): number {
    if (!this.playing || !this.ctx) return this.position;
    const length = this.loopEnd - this.loopStart;
    const elapsed = (this.ctx.currentTime - this.anchorTime) * this.sampleRate;
    let rel = this.anchorPos - this.loopStart + elapsed;
    if (this.loopEnabled) rel %= length;
    else rel = Math.min(rel, length);
    this.position = this.loopStart + Math.floor(rel);
    return this.position;
  }

  cleanup(): void {
    this.stopSource();
    this.buffer = null;
    this.ctx?.close().catch(() => undefined);
    this.ctx = null;
  }
}
