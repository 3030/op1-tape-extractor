/**
 * Canvas waveform editor (port of WaveformWidget / WaveformContainer in ui.py).
 *
 * View coordinates are seconds on x. Tracks are stacked like the desktop plot:
 * track 1 at the top, y range -0.5..4 with each track centered on 4 - track.
 */

import { TapeAudio } from '../core/audio';
import { toMono } from '../core/decode';
import { DetectedLoop, SAMPLE_RATE } from '../core/models';
import { pyInt, roundHalfEven } from '../core/pyround';
import { h, Signal } from './dom';
import { Peaks } from './peaks';
import {
  COLORS, formatTime, Tool, TOOL_ADD, TOOL_GLUE, TOOL_REMOVE, TOOL_SELECT, TOOL_SPLIT, TRACK_COLORS,
} from './theme';

const AXIS_HEIGHT = 22;
const Y_MIN = -0.5;
const Y_MAX = 4.2; // a little headroom above the top track for loop labels
const TRACK_SCALE = 0.8 * 0.4; // amplitude scale used by the desktop plot

interface EdgeHit {
  loopIndex: number;
  side: 'start' | 'end';
}

export class WaveformView {
  readonly el: HTMLDivElement;
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;

  // Signals (same as the desktop widget)
  readonly loopClicked = new Signal<[number]>();
  readonly loopRegionChanged = new Signal<[number, number, number]>(); // index, start, end
  readonly splitRequested = new Signal<[number, number]>(); // index, sample
  readonly glueRequested = new Signal<[number, number]>(); // first, second
  readonly removeRequested = new Signal<[number]>();
  readonly addRequested = new Signal<[number, number]>(); // start, end
  readonly seekRequested = new Signal<[number]>();
  readonly viewChanged = new Signal();

  private audio: TapeAudio | null = null;
  private peaks: (Peaks | null)[] = [];
  private loops: DetectedLoop[] = [];
  private mutedTracks = new Set<number>();
  private highlightedLoopIndex = -1;
  private maxDuration = 0;
  private bpm = 120;

  // View range in seconds
  private viewStart = 0;
  private viewEnd = 1;

  private currentTool: Tool = TOOL_SELECT;
  private snapToGrid = true;
  private highlightBars = 4;
  private isPlaying = false;
  private playheadSec: number | null = null;

  // Hover previews
  private splitCursorSec: number | null = null;
  private glueCursorSec: number | null = null;
  private scrubCursorSec: number | null = null;
  private addPreview: [number, number] | null = null;

  // Pointer state
  private isPanning = false;
  private didPan = false;
  private panStartX = 0;
  private panStartRange: [number, number] = [0, 1];
  private dragEdge: EdgeHit | null = null;
  private dragRegion: [number, number] | null = null;

  private frame = 0;
  private cssWidth = 0;
  private cssHeight = 0;

  constructor() {
    this.canvas = h('canvas', { class: 'waveform-canvas' });
    this.ctx = this.canvas.getContext('2d')!;
    this.el = h('div', { class: 'waveform-plot' }, this.canvas);

    new ResizeObserver(() => this.resize()).observe(this.el);
    this.canvas.addEventListener('pointerdown', (e) => this.onPointerDown(e));
    this.canvas.addEventListener('pointermove', (e) => this.onPointerMove(e));
    this.canvas.addEventListener('pointerup', (e) => this.onPointerUp(e));
    this.canvas.addEventListener('pointercancel', () => this.cancelPointer());
    this.canvas.addEventListener('pointerleave', () => {
      if (!this.isPanning && !this.dragEdge) this.clearPreviews();
    });
    this.canvas.addEventListener('wheel', (e) => this.onWheel(e), { passive: false });
    this.setCursor('grab');
  }

  // ---------------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------------

  loadSession(audio: TapeAudio, bpm: number): void {
    this.resize();
    this.audio = audio;
    this.bpm = bpm;
    this.peaks = audio.tracks.map((t) => (t ? new Peaks(toMono(t)) : null));
    this.maxDuration = audio.maxLength / SAMPLE_RATE;
    this.viewStart = 0;
    this.viewEnd = Math.max(this.maxDuration, 1);
    this.playheadSec = null;
    this.viewChanged.emit();
    this.requestDraw();
  }

  get duration(): number {
    return this.maxDuration;
  }

  get viewRange(): [number, number] {
    return [this.viewStart, this.viewEnd];
  }

  setLoops(loops: DetectedLoop[]): void {
    this.loops = loops;
    this.requestDraw();
  }

  highlightLoop(loopIndex: number): void {
    this.highlightedLoopIndex = loopIndex - 1; // 1-based like the desktop app
    this.requestDraw();
  }

  setTrackMuted(trackNum: number, muted: boolean): void {
    if (muted) this.mutedTracks.add(trackNum);
    else this.mutedTracks.delete(trackNum);
    this.requestDraw();
  }

  setPlayheadPosition(samplePosition: number): void {
    this.playheadSec = samplePosition / SAMPLE_RATE;
    this.requestDraw();
  }

  setPlaying(playing: boolean): void {
    this.isPlaying = playing;
  }

  setTool(tool: Tool): void {
    this.currentTool = tool;
    this.clearPreviews();
    const cursors: Record<Tool, string> = {
      [TOOL_SPLIT]: 'crosshair',
      [TOOL_GLUE]: 'grab',
      [TOOL_REMOVE]: 'not-allowed',
      [TOOL_ADD]: 'crosshair',
      [TOOL_SELECT]: 'grab',
    };
    this.setCursor(cursors[tool]);
  }

  setSnapToGrid(enabled: boolean): void {
    this.snapToGrid = enabled;
  }

  setHighlightBars(bars: number): void {
    this.highlightBars = bars;
    this.requestDraw();
  }

  /** Set the visible range in seconds, clamped to the tape */
  setXRange(start: number, end: number): void {
    const minWidth = Math.min(1, this.maxDuration || 1);
    let width = Math.max(end - start, minWidth);
    if (this.maxDuration > 0) width = Math.min(width, this.maxDuration);
    if (start < 0) start = 0;
    if (this.maxDuration > 0 && start + width > this.maxDuration) start = Math.max(0, this.maxDuration - width);
    this.viewStart = start;
    this.viewEnd = start + width;
    this.viewChanged.emit();
    this.requestDraw();
  }

  /** Scroll the view to show the specified loop */
  scrollToLoop(loopIndex: number): void {
    const loop = this.loops[loopIndex];
    if (!loop) return;
    const startSec = loop.startSample / SAMPLE_RATE;
    const endSec = loop.endSample / SAMPLE_RATE;
    const loopDuration = endSec - startSec;
    const viewWidth = this.viewEnd - this.viewStart;

    // Already visible
    if (startSec >= this.viewStart && endSec <= this.viewEnd) return;

    // Center the view on the loop, with some padding
    const padding = Math.max(loopDuration * 0.5, viewWidth * 0.1);
    let newStart = startSec - padding;
    let newEnd = newStart + viewWidth;
    if (newStart < 0) {
      newStart = 0;
      newEnd = viewWidth;
    }
    if (newEnd > this.maxDuration) {
      newEnd = this.maxDuration;
      newStart = Math.max(0, newEnd - viewWidth);
    }
    this.setXRange(newStart, newEnd);
  }

  // ---------------------------------------------------------------------------
  // Coordinates and grid
  // ---------------------------------------------------------------------------

  private get plotHeight(): number {
    return this.cssHeight - AXIS_HEIGHT;
  }

  private secToX(sec: number): number {
    return ((sec - this.viewStart) / (this.viewEnd - this.viewStart)) * this.cssWidth;
  }

  private xToSec(x: number): number {
    return this.viewStart + (x / this.cssWidth) * (this.viewEnd - this.viewStart);
  }

  private yToPx(y: number): number {
    return ((Y_MAX - y) / (Y_MAX - Y_MIN)) * this.plotHeight;
  }

  private get pixelsPerSec(): number {
    const width = this.viewEnd - this.viewStart;
    return width > 0 ? this.cssWidth / width : 100;
  }

  /** Edge grab tolerance: about 8 pixels, capped between 0.01s and 0.1s */
  private get edgeTolerance(): number {
    return Math.min(0.1, Math.max(0.01, 8 / this.pixelsPerSec));
  }

  private samplesPerBar(): number {
    return ((SAMPLE_RATE * 60.0) / this.bpm) * 4;
  }

  /** Grid spacing in samples based on current zoom level (finer when zoomed in) */
  private gridSpacingSamples(): number {
    const visible = this.viewEnd - this.viewStart;
    const spb = this.samplesPerBar();
    if (visible < 2) return pyInt(spb / 16);
    if (visible < 5) return pyInt(spb / 8);
    if (visible < 10) return pyInt(spb / 4);
    if (visible < 30) return pyInt(spb / 2);
    if (visible < 60) return pyInt(spb);
    if (visible < 120) return pyInt(spb * 2);
    return pyInt(spb * 4);
  }

  private snapSample(sample: number): number {
    const grid = this.gridSpacingSamples();
    return pyInt(roundHalfEven(sample / grid) * grid);
  }

  private snapTime(sec: number): number {
    return this.snapSample(pyInt(sec * SAMPLE_RATE)) / SAMPLE_RATE;
  }

  private loopAt(sec: number): number {
    return this.loops.findIndex((l) => l.startSample / SAMPLE_RATE <= sec && sec <= l.endSample / SAMPLE_RATE);
  }

  /** The loop edge under the pointer, preferring the selected loop when edges coincide */
  private edgeAt(sec: number): EdgeHit | null {
    const tol = this.edgeTolerance;
    let best: EdgeHit | null = null;
    let bestDist = Infinity;
    this.loops.forEach((loop, i) => {
      for (const side of ['start', 'end'] as const) {
        const edgeSec = (side === 'start' ? loop.startSample : loop.endSample) / SAMPLE_RATE;
        const dist = Math.abs(sec - edgeSec);
        if (dist >= tol) continue;
        const preferred = i === this.highlightedLoopIndex;
        if (dist < bestDist - 1e-9 || (Math.abs(dist - bestDist) <= 1e-9 && preferred)) {
          best = { loopIndex: i, side };
          bestDist = dist;
        }
      }
    });
    return best;
  }

  // ---------------------------------------------------------------------------
  // Pointer handling (mirrors the desktop eventFilter)
  // ---------------------------------------------------------------------------

  private eventSec(e: PointerEvent | WheelEvent): number {
    const rect = this.canvas.getBoundingClientRect();
    // Resize observers can lag (e.g. in a background tab); keep the mapping in sync
    if (Math.abs(rect.width - this.cssWidth) > 0.5) this.resize();
    return this.xToSec(e.clientX - rect.left);
  }

  private onPointerDown(e: PointerEvent): void {
    if (e.button !== 0 || !this.audio) return;
    const sec = this.eventSec(e);
    this.canvas.setPointerCapture(e.pointerId);

    // With the glue tool, a click on a shared boundary glues instead of grabbing the edge
    const glueHere = this.currentTool === TOOL_GLUE && this.glueBoundaryAt(sec) >= 0;
    const edge = glueHere ? null : this.edgeAt(sec);
    if (edge) {
      // Start resizing a loop region edge
      const loop = this.loops[edge.loopIndex];
      this.dragEdge = edge;
      this.dragRegion = [loop.startSample / SAMPLE_RATE, loop.endSample / SAMPLE_RATE];
      this.setCursor('ew-resize');
      return;
    }

    // Start potential pan (could be click or drag)
    this.isPanning = true;
    this.didPan = false;
    this.panStartX = e.clientX;
    this.panStartRange = [this.viewStart, this.viewEnd];
    this.setCursor('grabbing');
  }

  private onPointerMove(e: PointerEvent): void {
    if (!this.audio) return;
    const sec = this.eventSec(e);

    if (this.dragEdge && this.dragRegion) {
      let t = Math.max(0, Math.min(sec, this.maxDuration));
      if (this.snapToGrid) t = this.snapTime(t);
      if (this.dragEdge.side === 'start') this.dragRegion[0] = t;
      else this.dragRegion[1] = t;
      this.requestDraw();
      return;
    }

    if (this.isPanning) {
      const dx = e.clientX - this.panStartX;
      // Only pan if moved more than a few pixels (distinguish click from drag)
      if (Math.abs(dx) > 5) {
        this.didPan = true;
        const [start, end] = this.panStartRange;
        const width = end - start;
        const dataDx = (-dx * width) / this.cssWidth;
        this.setXRange(start + dataDx, end + dataDx);
      }
      return;
    }

    this.updateCursorAndPreview(sec);
  }

  private onPointerUp(e: PointerEvent): void {
    if (e.button !== 0) return;
    const sec = this.eventSec(e);

    if (this.dragEdge && this.dragRegion) {
      const { loopIndex } = this.dragEdge;
      // Like a sort-swapping region: the edges never cross
      const [a, b] = this.dragRegion;
      const newStart = pyInt(Math.min(a, b) * SAMPLE_RATE);
      const newEnd = pyInt(Math.max(a, b) * SAMPLE_RATE);
      this.dragEdge = null;
      this.dragRegion = null;
      const loop = this.loops[loopIndex];
      // A press on an edge without moving it changes nothing
      const moved = Math.abs(newStart - loop.startSample) > 1 || Math.abs(newEnd - loop.endSample) > 1;
      if (moved && newEnd > newStart) this.loopRegionChanged.emit(loopIndex, newStart, newEnd);
      this.updateCursorAndPreview(sec);
      this.requestDraw();
      return;
    }

    const wasPanning = this.isPanning;
    const didPan = this.didPan;
    this.isPanning = false;
    this.didPan = false;
    this.updateCursorAndPreview(sec);
    // A press without movement is a click: run the tool action
    if (wasPanning && !didPan) this.handleClick(sec);
  }

  private cancelPointer(): void {
    this.isPanning = false;
    this.didPan = false;
    this.dragEdge = null;
    this.dragRegion = null;
    this.requestDraw();
  }

  private onWheel(e: WheelEvent): void {
    if (!this.audio) return;
    e.preventDefault();
    // Horizontal trackpad swipes pan; vertical wheel zooms around the pointer
    if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
      const width = this.viewEnd - this.viewStart;
      const dataDx = (e.deltaX * width) / this.cssWidth;
      this.setXRange(this.viewStart + dataDx, this.viewEnd + dataDx);
      return;
    }
    if (e.deltaY === 0) return;
    const center = this.eventSec(e);
    const zoomFactor = e.deltaY < 0 ? 1.2 : 1 / 1.2;
    const width = this.viewEnd - this.viewStart;
    const newWidth = width / zoomFactor;
    const ratio = width > 0 ? (center - this.viewStart) / width : 0.5;
    this.setXRange(center - newWidth * ratio, center + newWidth * (1 - ratio));
  }

  /** Handle click actions for different tools */
  private handleClick(sec: number): void {
    let clickSample = pyInt(sec * SAMPLE_RATE);

    if (this.currentTool === TOOL_SPLIT) {
      const i = this.loopAt(sec);
      if (i < 0) return;
      const loop = this.loops[i];
      if (this.snapToGrid) clickSample = this.snapSample(clickSample);
      // Don't split too close to edges (at least 1/4 bar)
      const minSamples = pyInt(0.25 * this.samplesPerBar());
      if (clickSample - loop.startSample > minSamples && loop.endSample - clickSample > minSamples) {
        this.splitRequested.emit(i, clickSample);
      }
    } else if (this.currentTool === TOOL_GLUE) {
      const pair = this.glueBoundaryAt(sec);
      if (pair >= 0) this.glueRequested.emit(pair, pair + 1);
    } else if (this.currentTool === TOOL_REMOVE) {
      const i = this.loopAt(sec);
      if (i >= 0) this.removeRequested.emit(i);
    } else if (this.currentTool === TOOL_ADD) {
      const [start, end] = this.addRegionAt(sec);
      this.addRequested.emit(start, end);
    } else {
      const i = this.loopAt(sec);
      if (i < 0) return;
      // Clicking inside the playing loop seeks, away from its edges
      if (this.isPlaying && i === this.highlightedLoopIndex) {
        const loop = this.loops[i];
        const buffer = this.edgeTolerance;
        if (sec > loop.startSample / SAMPLE_RATE + buffer && sec < loop.endSample / SAMPLE_RATE - buffer) {
          this.seekRequested.emit(clickSample);
        }
      } else {
        this.loopClicked.emit(i);
      }
    }
  }

  /** Index of the first loop of an adjacent pair whose shared boundary is near sec, or -1 */
  private glueBoundaryAt(sec: number): number {
    // About 15 pixels, capped between 0.05s and 0.3s
    const tol = Math.min(0.3, Math.max(0.05, 15 / this.pixelsPerSec));
    for (let i = 0; i < this.loops.length - 1; i++) {
      const loop1 = this.loops[i];
      const loop2 = this.loops[i + 1];
      if (Math.abs(sec - loop1.endSample / SAMPLE_RATE) < tol && Math.abs(loop1.endSample - loop2.startSample) < SAMPLE_RATE) {
        return i;
      }
    }
    return -1;
  }

  /** Region the add tool would create at sec, sized by the grid-size setting */
  private addRegionAt(sec: number): [number, number] {
    const t = this.snapToGrid ? this.snapTime(sec) : sec;
    const clickSample = pyInt(t * SAMPLE_RATE);
    const bars = this.highlightBars > 0 ? this.highlightBars : 4;
    const regionSamples = pyInt(bars * this.samplesPerBar());
    let start = clickSample;
    let end = clickSample + regionSamples;
    const maxSamples = this.audio?.maxLength ?? 0;
    if (end > maxSamples) {
      end = maxSamples;
      start = Math.max(0, end - regionSamples);
    }
    return [start, end];
  }

  private clearPreviews(): void {
    this.splitCursorSec = null;
    this.glueCursorSec = null;
    this.scrubCursorSec = null;
    this.addPreview = null;
    this.requestDraw();
  }

  private setCursor(cursor: string): void {
    this.canvas.style.cursor = cursor;
  }

  /** Update cursor and tool preview based on pointer position */
  private updateCursorAndPreview(sec: number): void {
    this.splitCursorSec = null;
    this.glueCursorSec = null;
    this.scrubCursorSec = null;
    this.addPreview = null;
    this.requestDraw();

    const overLoop = this.loopAt(sec);
    const glueHere = this.currentTool === TOOL_GLUE && this.glueBoundaryAt(sec) >= 0;
    if (!glueHere && this.edgeAt(sec)) {
      this.setCursor('ew-resize');
      return;
    }

    switch (this.currentTool) {
      case TOOL_SELECT:
        if (overLoop >= 0) {
          const loop = this.loops[overLoop];
          const buffer = this.edgeTolerance;
          if (this.isPlaying && overLoop === this.highlightedLoopIndex
            && sec > loop.startSample / SAMPLE_RATE + buffer && sec < loop.endSample / SAMPLE_RATE - buffer) {
            // Text cursor indicates "click to position"
            this.scrubCursorSec = sec;
            this.setCursor('text');
          } else {
            this.setCursor('pointer');
          }
        } else {
          this.setCursor('grab');
        }
        break;
      case TOOL_SPLIT:
        if (overLoop >= 0) {
          this.splitCursorSec = this.snapToGrid ? this.snapTime(sec) : sec;
          this.setCursor('crosshair');
        } else {
          this.setCursor('grab');
        }
        break;
      case TOOL_GLUE: {
        const pair = this.glueBoundaryAt(sec);
        if (pair >= 0) {
          this.glueCursorSec = this.loops[pair].endSample / SAMPLE_RATE;
          this.setCursor('pointer');
        } else {
          this.setCursor('grab');
        }
        break;
      }
      case TOOL_ADD: {
        const [start, end] = this.addRegionAt(sec);
        this.addPreview = [start / SAMPLE_RATE, end / SAMPLE_RATE];
        this.setCursor('crosshair');
        break;
      }
      case TOOL_REMOVE:
        this.setCursor(overLoop >= 0 ? 'not-allowed' : 'grab');
        break;
    }
  }

  // ---------------------------------------------------------------------------
  // Drawing
  // ---------------------------------------------------------------------------

  private resize(): void {
    const rect = this.el.getBoundingClientRect();
    this.cssWidth = Math.max(1, rect.width);
    this.cssHeight = Math.max(AXIS_HEIGHT + 10, rect.height);
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(this.cssWidth * dpr);
    this.canvas.height = Math.round(this.cssHeight * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.draw();
  }

  private requestDraw(): void {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.draw();
    });
  }

  private vline(sec: number, color: string, width: number, dash: number[] = []): void {
    const x = Math.round(this.secToX(sec)) + (width % 2 ? 0.5 : 0);
    if (x < -width || x > this.cssWidth + width) return;
    const ctx = this.ctx;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, this.plotHeight);
    ctx.stroke();
    ctx.restore();
  }

  private band(startSec: number, endSec: number, color: string): void {
    const x0 = Math.max(0, this.secToX(startSec));
    const x1 = Math.min(this.cssWidth, this.secToX(endSec));
    if (x1 <= x0) return;
    this.ctx.fillStyle = color;
    this.ctx.fillRect(x0, 0, x1 - x0, this.plotHeight);
  }

  private draw(): void {
    const ctx = this.ctx;
    const w = this.cssWidth;
    const plotH = this.plotHeight;
    ctx.fillStyle = COLORS.bgWaveform;
    ctx.fillRect(0, 0, w, this.cssHeight);
    if (!this.audio) return;

    this.drawBarHighlights();
    this.drawTimeGrid();
    this.drawLoopRegions();
    this.drawWaves();
    this.drawLoopLines();

    if (this.addPreview) {
      const [a, b] = this.addPreview;
      this.band(a, b, `${COLORS.addPreview}40`);
      this.vline(a, COLORS.addPreview, 2, [6, 4]);
      this.vline(b, COLORS.addPreview, 2, [6, 4]);
    }
    if (this.splitCursorSec !== null) this.vline(this.splitCursorSec, COLORS.splitCursor, 2, [6, 4]);
    if (this.glueCursorSec !== null) this.vline(this.glueCursorSec, COLORS.glueCursor, 3);
    if (this.scrubCursorSec !== null) this.vline(this.scrubCursorSec, '#ffffff80', 1, [2, 3]);
    if (this.playheadSec !== null) this.vline(this.playheadSec, COLORS.playhead, 2);

    // Axis strip
    ctx.fillStyle = COLORS.bgWaveform;
    ctx.fillRect(0, plotH, w, AXIS_HEIGHT);
    this.drawTimeLabels();
  }

  private drawBarHighlights(): void {
    if (this.highlightBars <= 0 || !this.audio) return;
    const highlightSec = (this.samplesPerBar() * this.highlightBars) / SAMPLE_RATE;
    const maxSec = this.maxDuration;
    // Alternating regions, starting highlighted at zero
    let k = Math.max(0, Math.floor(this.viewStart / highlightSec));
    if (k % 2 === 1) k -= 1;
    for (let pos = k * highlightSec; pos < Math.min(this.viewEnd, maxSec); pos += highlightSec * 2) {
      this.band(pos, Math.min(pos + highlightSec, maxSec), COLORS.barHighlight);
    }
  }

  private tickStep(): number {
    // Aim for roughly one label per 110 px
    const target = (this.viewEnd - this.viewStart) / Math.max(1, this.cssWidth / 110);
    const steps = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
    return steps.find((s) => s >= target) ?? 1200;
  }

  private drawTimeGrid(): void {
    const step = this.tickStep();
    const ctx = this.ctx;
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,0.2)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let t = Math.ceil(this.viewStart / step) * step; t <= this.viewEnd; t += step) {
      const x = Math.round(this.secToX(t)) + 0.5;
      ctx.moveTo(x, 0);
      ctx.lineTo(x, this.plotHeight);
    }
    ctx.stroke();
    ctx.restore();
  }

  private drawTimeLabels(): void {
    const step = this.tickStep();
    const ctx = this.ctx;
    ctx.save();
    ctx.fillStyle = COLORS.textDim;
    ctx.font = '300 11px Inter, "Helvetica Neue", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.strokeStyle = COLORS.textDim;
    ctx.beginPath();
    ctx.moveTo(0, this.plotHeight + 0.5);
    ctx.lineTo(this.cssWidth, this.plotHeight + 0.5);
    for (let t = Math.ceil(this.viewStart / step) * step; t <= this.viewEnd; t += step) {
      const x = Math.round(this.secToX(t)) + 0.5;
      ctx.moveTo(x, this.plotHeight);
      ctx.lineTo(x, this.plotHeight + 4);
      ctx.fillText(formatTime(t + 1e-9), Math.min(Math.max(x, 28), this.cssWidth - 28), this.plotHeight + 13);
    }
    ctx.stroke();
    ctx.restore();
  }

  private regionFor(i: number): [number, number] {
    if (this.dragEdge?.loopIndex === i && this.dragRegion) {
      return [Math.min(...this.dragRegion), Math.max(...this.dragRegion)];
    }
    const loop = this.loops[i];
    return [loop.startSample / SAMPLE_RATE, loop.endSample / SAMPLE_RATE];
  }

  private drawLoopRegions(): void {
    this.loops.forEach((_, i) => {
      const [a, b] = this.regionFor(i);
      this.band(a, b, i === this.highlightedLoopIndex ? COLORS.selected : COLORS.loopRegion);
    });
  }

  private drawLoopLines(): void {
    const ctx = this.ctx;
    this.loops.forEach((loop, i) => {
      const [a, b] = this.regionFor(i);
      const dragging = this.dragEdge?.loopIndex === i;
      // Region edges, then the dashed boundary line at the start
      this.vline(a, COLORS.boundary, dragging && this.dragEdge?.side === 'start' ? 3 : 2);
      this.vline(b, COLORS.boundary, dragging && this.dragEdge?.side === 'end' ? 3 : 2);
      this.vline(a, COLORS.boundary, 2, [6, 4]);

      const x = this.secToX(a);
      if (x < -20 || x > this.cssWidth) return;
      // Rotated label hanging from the top, just right of the start line
      ctx.save();
      ctx.fillStyle = COLORS.accent;
      ctx.font = '700 11px Inter, "Helvetica Neue", sans-serif';
      ctx.translate(x + 6, 6);
      ctx.rotate(Math.PI / 2);
      ctx.textBaseline = 'bottom';
      ctx.fillText(loop.customName || `L${loop.index}`, 0, 0);
      ctx.restore();
    });
  }

  private drawWaves(): void {
    const ctx = this.ctx;
    const w = Math.ceil(this.cssWidth);
    const samplesPerPx = ((this.viewEnd - this.viewStart) * SAMPLE_RATE) / this.cssWidth;
    const unit = this.plotHeight / (Y_MAX - Y_MIN);

    for (let trackNum = 1; trackNum <= 4; trackNum++) {
      const peaks = this.peaks[trackNum - 1];
      if (!peaks) continue;
      const centerY = this.yToPx(4 - trackNum);
      const amp = TRACK_SCALE * unit;
      ctx.fillStyle = this.mutedTracks.has(trackNum) ? COLORS.muted : TRACK_COLORS[trackNum - 1];

      for (let x = 0; x < w; x++) {
        const s0 = (this.viewStart * SAMPLE_RATE) + x * samplesPerPx;
        const s1 = s0 + Math.max(1, samplesPerPx);
        if (s0 >= peaks.length) break;
        const [min, max] = peaks.range(s0, s1);
        const top = centerY - max * amp;
        const bottom = centerY - min * amp;
        ctx.fillRect(x, top, 1, Math.max(1, bottom - top));
      }
    }
  }
}
