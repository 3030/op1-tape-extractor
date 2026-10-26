/**
 * UI widgets around the waveform (port of the widget classes in ui.py).
 * Labels are written in Title Case; the all-caps look comes from CSS.
 */

import { DetectedLoop, SAMPLE_RATE } from '../core/models';
import { defaultLoopName } from '../core/export';
import { OutputDevice } from '../audio/devices';
import { h, Signal } from './dom';
import { icon, IconName } from './icons';
import { formatTime, Tool, TOOL_ADD, TOOL_GLUE, TOOL_REMOVE, TOOL_SELECT, TOOL_SPLIT, TRACK_COLORS } from './theme';
import { WaveformView } from './waveform';

function barsText(bars: number): string {
  return bars % 1 ? bars.toFixed(1) : String(Math.trunc(bars));
}

function roundButton(name: IconName, tooltip: string, checkable = false): HTMLButtonElement {
  const btn = h('button', { class: 'round-btn', type: 'button', title: tooltip, 'aria-label': tooltip }, icon(name, 28));
  if (checkable) btn.setAttribute('aria-pressed', 'false');
  return btn;
}

function setChecked(btn: HTMLButtonElement, checked: boolean): void {
  btn.setAttribute('aria-pressed', String(checked));
  btn.classList.toggle('checked', checked);
}

// =============================================================================
// Drop zone
// =============================================================================

export class DropZone {
  readonly el: HTMLDivElement;
  readonly folderDropped = new Signal<[DataTransfer]>();
  readonly chooseRequested = new Signal();

  constructor() {
    this.el = h('div', {
      class: 'drop-zone', role: 'button', tabindex: 0,
      'aria-label': 'Choose a tape folder',
    });
    this.setEmpty();

    this.el.addEventListener('click', () => this.chooseRequested.emit());
    this.el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        this.chooseRequested.emit();
      }
    });
    this.el.addEventListener('dragover', (e) => {
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
      this.el.classList.add('drag-over');
    });
    this.el.addEventListener('dragleave', () => this.el.classList.remove('drag-over'));
    this.el.addEventListener('drop', (e) => {
      e.preventDefault();
      this.el.classList.remove('drag-over');
      if (e.dataTransfer) this.folderDropped.emit(e.dataTransfer);
    });
  }

  private setContent(state: string, ...children: (Node | string)[]): void {
    this.el.className = `drop-zone ${state}`;
    this.el.replaceChildren(...children);
  }

  setEmpty(): void {
    this.setContent('empty',
      h('span', { class: 'drop-title' }, 'Drop Tape Folder Here'),
      icon('folder', 22),
      h('span', { class: 'drop-hint' }, 'or click to choose one'));
  }

  setLoaded(folderName: string, bpm: number, clipCount: number): void {
    this.setContent('loaded',
      h('span', { class: 'drop-folder' }, icon('folder', 18), folderName),
      h('span', { class: 'drop-meta' }, `${bpm} BPM  •  ${clipCount} clips`));
  }

  setLoading(): void {
    this.setContent('loading', h('span', {}, 'Loading…'));
  }
}

// =============================================================================
// Grid size buttons (HighlightButtonGroup)
// =============================================================================

export class GridSizeButtons {
  readonly el: HTMLDivElement;
  readonly highlightChanged = new Signal<[number]>();
  private buttons = new Map<number, HTMLButtonElement>();
  private value = 4;

  constructor() {
    this.el = h('div', { class: 'grid-buttons', role: 'group', 'aria-label': 'Grid size' });
    for (const label of ['Off', '1', '2', '4', '8', '16']) {
      const v = label === 'Off' ? 0 : Number(label);
      const btn = h('button', { class: 'chip-btn', type: 'button', onclick: () => {
        this.setValue(v);
        this.highlightChanged.emit(v);
      } }, label);
      this.buttons.set(v, btn);
      this.el.append(btn);
    }
    this.setValue(4);
  }

  setValue(value: number): void {
    this.value = value;
    for (const [v, btn] of this.buttons) setChecked(btn, v === value);
  }

  getValue(): number {
    return this.value;
  }
}

// =============================================================================
// Waveform container: scrollbar, mute buttons, plot
// =============================================================================

export class WaveformContainer {
  readonly el: HTMLDivElement;
  readonly waveform = new WaveformView();
  readonly trackMuteChanged = new Signal<[number, boolean]>();
  private thumb: HTMLDivElement;
  private track: HTMLDivElement;
  private muteButtons: HTMLButtonElement[] = [];
  private muted = [false, false, false, false];

  constructor() {
    this.thumb = h('div', { class: 'scroll-thumb' });
    this.track = h('div', { class: 'scroll-track' }, this.thumb);
    const mutePanel = h('div', { class: 'mute-panel' });
    for (let i = 0; i < 4; i++) {
      const trackNum = i + 1;
      const btn = h('button', {
        class: 'mute-btn', type: 'button', title: `Mute track ${trackNum}`, 'aria-pressed': 'false',
        onclick: () => this.toggleMute(trackNum),
      }, String(trackNum));
      // Set through CSSOM rather than a style attribute so the CSP needs no 'unsafe-inline'
      btn.style.setProperty('--track-color', TRACK_COLORS[i]);
      this.muteButtons.push(btn);
      mutePanel.append(btn);
    }

    this.el = h('div', { class: 'waveform-container' },
      h('div', { class: 'scroll-row' }, this.track),
      h('div', { class: 'waveform-row' }, mutePanel, this.waveform.el),
    );

    this.waveform.viewChanged.connect(() => this.syncScrollbar());
    new ResizeObserver(() => this.syncScrollbar()).observe(this.track);
    this.initScrollbarDrag();
  }

  isMuted(trackNum: number): boolean {
    return this.muted[trackNum - 1];
  }

  private toggleMute(trackNum: number): void {
    const muted = !this.muted[trackNum - 1];
    this.muted[trackNum - 1] = muted;
    setChecked(this.muteButtons[trackNum - 1], muted);
    this.muteButtons[trackNum - 1].classList.toggle('muted', muted);
    this.waveform.setTrackMuted(trackNum, muted);
    this.trackMuteChanged.emit(trackNum, muted);
  }

  private syncScrollbar(): void {
    const duration = this.waveform.duration;
    const trackWidth = this.track.clientWidth;
    if (duration <= 0 || trackWidth <= 0) {
      this.thumb.style.left = '0px';
      this.thumb.style.width = `${trackWidth}px`;
      return;
    }
    const [start, end] = this.waveform.viewRange;
    const thumbWidth = Math.max(40, Math.min(1, (end - start) / duration) * trackWidth);
    const maxLeft = trackWidth - thumbWidth;
    const scrollable = duration - (end - start);
    const left = scrollable > 0 ? (start / scrollable) * maxLeft : 0;
    this.thumb.style.width = `${thumbWidth}px`;
    this.thumb.style.left = `${left}px`;
  }

  private scrollToFraction(fraction: number): void {
    const [start, end] = this.waveform.viewRange;
    const width = end - start;
    const scrollable = this.waveform.duration - width;
    if (scrollable <= 0) return;
    const pos = Math.max(0, Math.min(1, fraction)) * scrollable;
    this.waveform.setXRange(pos, pos + width);
  }

  private initScrollbarDrag(): void {
    let dragOffset = 0;
    let dragging = false;
    const fractionAt = (clientX: number) => {
      const rect = this.track.getBoundingClientRect();
      const thumbWidth = this.thumb.offsetWidth;
      const maxLeft = rect.width - thumbWidth;
      return maxLeft > 0 ? (clientX - rect.left - dragOffset) / maxLeft : 0;
    };
    this.thumb.addEventListener('pointerdown', (e) => {
      e.stopPropagation();
      dragging = true;
      dragOffset = e.clientX - this.thumb.getBoundingClientRect().left;
      this.thumb.setPointerCapture(e.pointerId);
    });
    this.thumb.addEventListener('pointermove', (e) => {
      if (dragging) this.scrollToFraction(fractionAt(e.clientX));
    });
    this.thumb.addEventListener('pointerup', () => {
      dragging = false;
    });
    // Clicking the track pages toward the pointer
    this.track.addEventListener('pointerdown', (e) => {
      if (e.target !== this.track) return;
      const [start, end] = this.waveform.viewRange;
      const width = end - start;
      const thumbLeft = this.thumb.getBoundingClientRect().left;
      const dir = e.clientX < thumbLeft ? -1 : 1;
      this.waveform.setXRange(start + dir * width, end + dir * width);
    });
  }
}

// =============================================================================
// Playback controls
// =============================================================================

export class PlaybackControls {
  readonly el: HTMLDivElement;
  readonly timeLabel: HTMLDivElement;
  readonly playClicked = new Signal();
  readonly pauseClicked = new Signal();
  readonly stopClicked = new Signal();
  readonly loopToggled = new Signal<[boolean]>();
  private playBtn = roundButton('play', 'Play / Pause', true);
  private stopBtn = roundButton('stop', 'Stop');
  private loopBtn = roundButton('loop', 'Loop playback', true);
  private playing = false;
  private looping = true;

  constructor() {
    this.timeLabel = h('div', { class: 'time-label', 'aria-live': 'off' }, '0:00:000');
    this.el = h('div', { class: 'playback-controls' }, this.playBtn, this.stopBtn, this.loopBtn);
    setChecked(this.loopBtn, true);

    this.playBtn.addEventListener('click', () => {
      this.setPlaying(!this.playing);
      if (this.playing) this.playClicked.emit();
      else this.pauseClicked.emit();
    });
    this.stopBtn.addEventListener('click', () => {
      this.setPlaying(false);
      this.stopClicked.emit();
    });
    this.loopBtn.addEventListener('click', () => {
      this.looping = !this.looping;
      setChecked(this.loopBtn, this.looping);
      this.loopToggled.emit(this.looping);
    });
  }

  setPlaying(playing: boolean): void {
    this.playing = playing;
    setChecked(this.playBtn, playing);
  }

  updateTime(samplePosition: number): void {
    this.timeLabel.textContent = formatTime(samplePosition / SAMPLE_RATE);
  }
}

// =============================================================================
// Edit toolbar
// =============================================================================

export class EditToolbar {
  readonly el: HTMLDivElement;
  readonly toolChanged = new Signal<[Tool]>();
  readonly snapToggled = new Signal<[boolean]>();
  private current: Tool = TOOL_SELECT;
  private toolButtons = new Map<Tool, HTMLButtonElement>();
  private snapBtn = roundButton('snap', 'Snap to grid (M)', true);
  private snap = true;

  constructor() {
    const tools: [Tool, IconName, string][] = [
      [TOOL_SELECT, 'select', 'Select tool (S)'],
      [TOOL_SPLIT, 'split', 'Split tool (C)'],
      [TOOL_GLUE, 'glue', 'Glue tool (G)'],
      [TOOL_ADD, 'add', 'Add new region at grid size (A)'],
      [TOOL_REMOVE, 'remove', 'Remove tool (R)'],
    ];
    this.el = h('div', { class: 'edit-toolbar', role: 'toolbar', 'aria-label': 'Edit tools' });
    for (const [tool, iconName, tip] of tools) {
      const btn = roundButton(iconName, tip, true);
      btn.addEventListener('click', () => this.setTool(tool));
      this.toolButtons.set(tool, btn);
      this.el.append(btn);
    }
    this.el.append(h('span', { class: 'toolbar-gap' }), this.snapBtn);
    this.snapBtn.addEventListener('click', () => this.setSnapEnabled(!this.snap));
    this.setTool(TOOL_SELECT, false);
    setChecked(this.snapBtn, true);
  }

  setTool(tool: Tool, emit = true): void {
    this.current = tool;
    for (const [t, btn] of this.toolButtons) setChecked(btn, t === tool);
    if (emit) this.toolChanged.emit(tool);
  }

  getCurrentTool(): Tool {
    return this.current;
  }

  isSnapEnabled(): boolean {
    return this.snap;
  }

  setSnapEnabled(enabled: boolean): void {
    this.snap = enabled;
    setChecked(this.snapBtn, enabled);
    this.snapToggled.emit(enabled);
  }
}

// =============================================================================
// Audio output selector
// =============================================================================

export class AudioDeviceSelector {
  readonly el: HTMLDivElement;
  readonly deviceChanged = new Signal<[string, string]>(); // id ('' = system default), label
  readonly refreshRequested = new Signal();
  readonly unlockRequested = new Signal();
  private select: HTMLSelectElement;
  private unlockBtn: HTMLButtonElement;
  private devices: OutputDevice[] = [];

  constructor() {
    this.select = h('select', { class: 'device-select', 'aria-label': 'Audio output' });
    this.select.addEventListener('change', () => {
      const id = this.select.value;
      const label = id ? this.devices.find((d) => d.id === id)?.label ?? id : 'System Default';
      this.deviceChanged.emit(id, label);
    });
    const refreshBtn = h('button', {
      class: 'icon-btn', type: 'button', title: 'Refresh audio devices', 'aria-label': 'Refresh audio devices',
      onclick: () => this.refreshRequested.emit(),
    }, icon('refresh', 14));
    this.unlockBtn = h('button', {
      class: 'link-btn', type: 'button', hidden: true,
      title: 'Your browser hides device names until the page has used the microphone once. Nothing is recorded.',
      onclick: () => this.unlockRequested.emit(),
    }, 'Show Device Names');

    this.el = h('div', { class: 'device-selector' },
      h('span', { class: 'caps-label' }, icon('audioOutput', 14), 'Output:'),
      this.select, refreshBtn, this.unlockBtn);
  }

  setDevices(defaultLabel: string, devices: OutputDevice[], labelsHidden: boolean): void {
    const current = this.select.value;
    this.devices = devices;
    this.select.replaceChildren(
      h('option', { value: '' }, `System Default (${defaultLabel})`),
      ...devices.map((d) => h('option', { value: d.id }, d.label)),
    );
    // Restore selection if possible
    if (devices.some((d) => d.id === current)) this.select.value = current;
    this.unlockBtn.hidden = !labelsHidden;
  }

  getSelectedDevice(): string {
    return this.select.value;
  }
}

// =============================================================================
// Loop list
// =============================================================================

export class LoopList {
  readonly el: HTMLDivElement;
  readonly currentRowChanged = new Signal<[number]>();
  private rows: HTMLDivElement[] = [];
  private current = -1;

  constructor() {
    this.el = h('div', { class: 'loop-list', role: 'listbox', tabindex: 0, 'aria-label': 'Extracted loops' });
    this.el.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const next = this.current + (e.key === 'ArrowDown' ? 1 : -1);
        if (next >= 0 && next < this.rows.length) this.setCurrentRow(next);
      }
    });
  }

  setLoops(loops: DetectedLoop[]): void {
    this.current = -1;
    this.rows = loops.map((loop, i) => {
      const tracks = [...loop.tracksActive].sort((a, b) => a - b).map((t) => t + 1).join(',');
      const row = h('div', { class: 'loop-row', role: 'option', 'aria-selected': 'false', onclick: () => this.setCurrentRow(i) },
        h('span', { class: 'loop-name' }, loop.customName || defaultLoopName(loop)),
        h('span', {}, loop.timeStr()),
        h('span', {}, `${barsText(loop.lengthBars)} bars`),
        h('span', {}, `T[${tracks}]`),
        h('span', {}, `${Math.trunc(loop.startBoundary.confidence * 100)}%`),
      );
      return row;
    });
    this.el.replaceChildren(...this.rows);
  }

  updateLoopName(index: number, name: string): void {
    const nameEl = this.rows[index]?.querySelector('.loop-name');
    if (nameEl) nameEl.textContent = name;
  }

  currentRow(): number {
    return this.current;
  }

  setCurrentRow(row: number): void {
    if (row === this.current) return;
    this.rows[this.current]?.classList.remove('selected');
    this.rows[this.current]?.setAttribute('aria-selected', 'false');
    this.current = row;
    const el = this.rows[row];
    if (el) {
      el.classList.add('selected');
      el.setAttribute('aria-selected', 'true');
      el.scrollIntoView({ block: 'nearest' });
    }
    this.currentRowChanged.emit(row);
  }
}

// =============================================================================
// Loop info panel
// =============================================================================

export class LoopInfoPanel {
  readonly el: HTMLDivElement;
  readonly nameChanged = new Signal<[number, string]>();
  readonly splitRequested = new Signal<[number, number]>();
  private nameInput: HTMLInputElement;
  private values: Record<'bars' | 'duration' | 'start' | 'end', HTMLSpanElement>;
  private splitSelect: HTMLSelectElement;
  private splitBtn: HTMLButtonElement;
  private loopIndex = -1;
  private loopBars = 0;

  constructor() {
    this.nameInput = h('input', { class: 'name-input', type: 'text', 'aria-label': 'Loop name', spellcheck: 'false' });
    // Commit on Enter or when focus leaves, like editingFinished
    this.nameInput.addEventListener('change', () => {
      if (this.loopIndex >= 0) this.nameChanged.emit(this.loopIndex, this.nameInput.value);
    });
    this.nameInput.addEventListener('keydown', (e) => {
      e.stopPropagation(); // typing a name must not trigger tool shortcuts
      if (e.key === 'Enter') this.nameInput.blur();
    });

    const value = () => h('span', { class: 'info-value' }, '-');
    this.values = { bars: value(), duration: value(), start: value(), end: value() };
    const row = (label: string, el: HTMLSpanElement) => h('div', { class: 'info-row' }, h('span', { class: 'caps-label' }, label), el);

    this.splitSelect = h('select', { class: 'split-select', 'aria-label': 'Bars per split' },
      ...['1', '2', '4', '8', '16', '32'].map((v) => h('option', { value: v }, v)));
    this.splitSelect.value = '4';
    this.splitBtn = h('button', { class: 'btn-accent small', type: 'button', disabled: true, onclick: () => {
      if (this.loopIndex < 0) return;
      const barCount = Number(this.splitSelect.value);
      if (barCount < this.loopBars) this.splitRequested.emit(this.loopIndex, barCount);
    } }, 'Split');

    this.el = h('div', { class: 'loop-info' },
      h('div', { class: 'caps-label' }, 'Loop Info'),
      h('label', { class: 'name-field' }, h('span', { class: 'caps-label' }, 'Name:'), this.nameInput),
      row('Bars:', this.values.bars),
      row('Duration:', this.values.duration),
      row('Start:', this.values.start),
      row('End:', this.values.end),
      h('div', { class: 'caps-label subsplit-label' }, 'Sub-Split'),
      h('div', { class: 'split-row' }, this.splitSelect, h('span', { class: 'caps-label' }, 'Bars'), this.splitBtn),
    );
  }

  setLoop(loop: DetectedLoop, index: number): void {
    this.loopIndex = index;
    this.loopBars = loop.lengthBars;
    this.nameInput.value = loop.customName || defaultLoopName(loop);
    const fmt = (samples: number) => formatTime(samples / SAMPLE_RATE);
    this.values.bars.textContent = barsText(loop.lengthBars);
    this.values.duration.textContent = fmt(loop.lengthSamples);
    this.values.start.textContent = fmt(loop.startSample);
    this.values.end.textContent = fmt(loop.endSample);
    this.splitBtn.disabled = !(loop.lengthBars > 1);
  }

  clear(): void {
    this.loopIndex = -1;
    this.nameInput.value = '';
    for (const v of Object.values(this.values)) v.textContent = '-';
    this.splitBtn.disabled = true;
  }
}

// =============================================================================
// Status bar
// =============================================================================

export class StatusBar {
  readonly el: HTMLDivElement;
  readonly helpClicked = new Signal();
  private label: HTMLSpanElement;
  private progress: HTMLProgressElement;

  constructor(version: string) {
    this.label = h('span', { class: 'status-label', role: 'status' }, 'Ready');
    this.progress = h('progress', { class: 'status-progress', hidden: true });
    const helpBtn = h('button', {
      class: 'help-btn', type: 'button', title: 'About', 'aria-label': 'About',
      onclick: () => this.helpClicked.emit(),
    }, icon('help', 14));
    this.el = h('div', { class: 'status-bar' },
      this.label,
      h('span', { class: 'spacer' }),
      this.progress,
      helpBtn,
      h('span', { class: 'version' }, `v${version} • `,
        h('a', { href: 'mailto:nate@3030.ca' }, 'Bugs? Requests? Feedback? Love? Email ', h('u', {}, 'nate@3030.ca'))),
    );
  }

  showProgress(message = 'Loading…'): void {
    this.label.textContent = message;
    this.progress.removeAttribute('value'); // indeterminate
    this.progress.hidden = false;
  }

  hideProgress(): void {
    this.progress.hidden = true;
  }

  setStatus(message: string): void {
    this.label.textContent = message;
  }

  setProgress(current: number, total: number): void {
    this.progress.hidden = !(total > 0);
    this.progress.max = Math.max(total, 1);
    this.progress.value = current;
  }
}
