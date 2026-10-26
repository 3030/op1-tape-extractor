/**
 * OP-1 Loop Extractor main window (port of MainWindow in app.py).
 */

import { TapeAudio } from '../core/audio';
import { detectBoundaries, detectLoops, splitLoopByBars } from '../core/detect';
import { defaultLoopName, exportLoop } from '../core/export';
import { DetectedLoop, LoopBoundary, SAMPLE_RATE, TapeSession, samplesPerBar } from '../core/models';
import { pyInt } from '../core/pyround';
import { detectLoopsFromAiffRegions } from '../core/tape';
import { listOutputDevices, outputSelectionSupported, unlockDeviceLabels } from '../audio/devices';
import { AudioPlayer } from '../audio/player';
import { chooseExportTarget, folderFromDrop, pickFolder, TapeFolder } from '../io/folder';
import { loadTape, TapeLoadError } from '../io/load';
import {
  AudioDeviceSelector, DropZone, EditToolbar, GridSizeButtons, LoopInfoPanel, LoopList, PlaybackControls,
  StatusBar, WaveformContainer,
} from './components';
import { h } from './dom';
import { icon } from './icons';
import { confirmModal, contentModal, infoModal, isModalOpen } from './modal';
import { Tool, TOOL_ADD, TOOL_GLUE, TOOL_REMOVE, TOOL_SELECT, TOOL_SPLIT } from './theme';
import logoUrl from '../../../assets/3030logo.png';

declare const __APP_VERSION__: string;
const VERSION = __APP_VERSION__;

// Detection settings (the desktop settings panel uses these fixed values)
const SETTINGS = { minTracks: 2, minBars: 1.0 };

export class App {
  readonly el: HTMLDivElement;

  private folderName = '';
  private session: TapeSession | null = null;
  private audio: TapeAudio | null = null;
  private loops: DetectedLoop[] = [];
  private player: AudioPlayer | null = null;
  private selectedLoopIndex = -1;
  private playheadTimer = 0;
  private exporting = false;

  private dropZone = new DropZone();
  private rescanBtn: HTMLButtonElement;
  private gridButtons = new GridSizeButtons();
  private waveformContainer = new WaveformContainer();
  private playback = new PlaybackControls();
  private editToolbar = new EditToolbar();
  private deviceSelector: AudioDeviceSelector | null = null;
  private loopList = new LoopList();
  private loopInfo = new LoopInfoPanel();
  private exportMixCheck: HTMLInputElement;
  private zeroFadeCheck: HTMLInputElement;
  private exportBtn: HTMLButtonElement;
  private statusBar = new StatusBar(VERSION);

  private get waveform() {
    return this.waveformContainer.waveform;
  }

  constructor() {
    this.rescanBtn = h('button', { class: 'rescan-btn', type: 'button', disabled: true }, 'Re-Scan Loops');

    const checkbox = (label: string, tooltip?: string) => {
      const input = h('input', { type: 'checkbox', checked: true });
      const wrap = h('label', { class: 'round-check', title: tooltip },
        input, h('span', { class: 'check-indicator' }, icon('checked', 12)), h('span', { class: 'caps-label' }, label));
      return { input, wrap };
    };
    const mix = checkbox('Export Mixdown');
    const fade = checkbox('Zero-Fade Endpoints', 'Apply 20ms fade in/out to ensure loops start and end at zero');
    this.exportMixCheck = mix.input;
    this.zeroFadeCheck = fade.input;
    this.exportBtn = h('button', { class: 'export-btn', type: 'button', disabled: true }, 'Export Loops');

    if (outputSelectionSupported()) this.deviceSelector = new AudioDeviceSelector();

    this.el = h('div', { class: 'app' },
      h('div', { class: 'top-section' },
        this.dropZone.el,
        this.rescanBtn,
        h('div', { class: 'stretch' }),
        h('div', { class: 'title-block' },
          h('h1', { class: 'app-title' }, 'OP-1 Loop Extractor'),
          h('div', { class: 'caps-label' }, 'Grid Size'),
          this.gridButtons.el,
        ),
      ),
      this.waveformContainer.el,
      h('div', { class: 'controls-row' },
        this.playback.el,
        h('div', { class: 'stretch' }),
        this.playback.timeLabel,
        h('div', { class: 'stretch' }),
        this.editToolbar.el,
        this.deviceSelector?.el ?? null,
      ),
      h('div', { class: 'bottom-section' },
        h('div', { class: 'list-container' }, h('div', { class: 'section-label' }, 'Extracted Loops'), this.loopList.el),
        this.loopInfo.el,
      ),
      h('div', { class: 'export-section' },
        h('div', { class: 'export-options' }, mix.wrap, fade.wrap),
        this.exportBtn,
      ),
      this.statusBar.el,
    );

    this.connectSignals();
    if (this.deviceSelector) this.refreshDevices();
  }

  private connectSignals(): void {
    this.dropZone.folderDropped.connect(async (dt) => {
      const folder = await folderFromDrop(dt);
      if (folder) this.onFolderDropped(folder);
    });
    this.dropZone.chooseRequested.connect(async () => {
      const folder = await pickFolder();
      if (folder) this.onFolderDropped(folder);
    });
    this.rescanBtn.addEventListener('click', () => this.onAutoScanRequested());
    this.gridButtons.highlightChanged.connect((bars) => this.onHighlightGridChanged(bars));
    this.loopList.currentRowChanged.connect((row) => this.onLoopSelected(row));
    this.exportBtn.addEventListener('click', () => this.export());
    this.editToolbar.toolChanged.connect((tool) => this.onToolChanged(tool));
    this.editToolbar.snapToggled.connect((enabled) => this.onSnapToGridChanged(enabled));

    const wf = this.waveform;
    wf.loopClicked.connect((i) => this.onWaveformLoopClicked(i));
    wf.loopRegionChanged.connect((i, s, e) => this.onLoopRegionChanged(i, s, e));
    wf.splitRequested.connect((i, s) => this.onSplitAtPosition(i, s));
    wf.glueRequested.connect((a, b) => this.onGlueLoops(a, b));
    wf.removeRequested.connect((i) => this.onRemoveLoop(i));
    wf.addRequested.connect((s, e) => this.onAddRegion(s, e));
    wf.seekRequested.connect((s) => this.onSeek(s));
    this.waveformContainer.trackMuteChanged.connect((n, m) => this.player?.setMuted(n, m));

    this.playback.playClicked.connect(() => this.onPlay());
    this.playback.pauseClicked.connect(() => this.onPause());
    this.playback.stopClicked.connect(() => this.onStop());
    this.playback.loopToggled.connect((enabled) => this.player?.setLoopEnabled(enabled));
    this.loopInfo.nameChanged.connect((i, name) => this.onLoopNameChanged(i, name));
    this.loopInfo.splitRequested.connect((i, bars) => this.onLoopSplitRequested(i, bars));
    this.statusBar.helpClicked.connect(() => this.showAbout());

    if (this.deviceSelector) {
      const selector = this.deviceSelector;
      selector.deviceChanged.connect((id, label) => this.onAudioDeviceChanged(id, label));
      selector.refreshRequested.connect(() => this.refreshDevices());
      selector.unlockRequested.connect(async () => {
        await unlockDeviceLabels();
        this.refreshDevices();
      });
      navigator.mediaDevices?.addEventListener?.('devicechange', () => this.refreshDevices());
    }

    window.addEventListener('keydown', (e) => this.onKeyDown(e));
    // Dropping a folder anywhere on the page loads it too
    window.addEventListener('dragover', (e) => e.preventDefault());
    window.addEventListener('drop', async (e) => {
      e.preventDefault();
      if (!e.dataTransfer || this.dropZone.el.contains(e.target as Node)) return;
      const folder = await folderFromDrop(e.dataTransfer);
      if (folder) this.onFolderDropped(folder);
    });
  }

  private onKeyDown(e: KeyboardEvent): void {
    if (isModalOpen() || e.repeat || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target instanceof Element && e.target.closest('input, select, textarea')) return;

    const tools: Record<string, Tool> = { s: TOOL_SELECT, c: TOOL_SPLIT, g: TOOL_GLUE, a: TOOL_ADD, r: TOOL_REMOVE };
    const key = e.key.toLowerCase();
    if (key in tools) {
      this.editToolbar.setTool(tools[key]);
    } else if (key === 'm') {
      // Toggle snap to grid
      this.editToolbar.setSnapEnabled(!this.editToolbar.isSnapEnabled());
    } else if (e.key === 'Escape') {
      this.editToolbar.setTool(TOOL_SELECT);
    } else {
      return;
    }
    e.preventDefault();
  }

  // ---------------------------------------------------------------------------
  // Loading and detection
  // ---------------------------------------------------------------------------

  private async onFolderDropped(folder: TapeFolder): Promise<void> {
    // Show loading state
    this.statusBar.showProgress('Loading…');
    this.dropZone.setLoading();
    this.stopTimer();
    this.player?.cleanup();
    this.player = null;

    try {
      const tape = await loadTape(folder);
      this.session = tape.session;
      this.audio = tape.audio;
      this.folderName = folder.name;

      this.dropZone.setLoaded(folder.name, this.session.bpm, this.session.clips.length);
      this.waveform.loadSession(tape.audio, this.session.bpm);
      this.player = new AudioPlayer(tape.audio);
      // Keep mutes that were already set
      for (let n = 1; n <= 4; n++) {
        if (this.waveformContainer.isMuted(n)) this.player.setMuted(n, true);
      }
      const deviceId = this.deviceSelector?.getSelectedDevice();
      if (deviceId) this.player.setOutputDevice(deviceId).catch(() => undefined);
      this.playback.setPlaying(false);
      this.waveform.setPlaying(false);

      // Apply initial settings
      this.waveform.setSnapToGrid(this.editToolbar.isSnapEnabled());
      this.waveform.setHighlightBars(this.gridButtons.getValue());

      this.redetectLoops();
      this.rescanBtn.disabled = false;
      this.statusBar.setStatus(`Loaded ${tape.formatLabel} tape at ${this.session.bpm} BPM`);
    } catch (err) {
      console.error(err);
      if (this.session) this.dropZone.setLoaded(this.folderName, this.session.bpm, this.session.clips.length);
      else this.dropZone.setEmpty();
      const message = err instanceof TapeLoadError ? err.message : `Error loading: ${(err as Error).message}`;
      this.statusBar.setStatus(message);
    } finally {
      this.statusBar.hideProgress();
    }
  }

  private redetectLoops(): void {
    if (!this.session) return;
    if (this.session.isAiffSource) {
      // For AIFF sessions, use the embedded region data directly, then the minimum bars filter
      const minSamples = pyInt(SETTINGS.minBars * samplesPerBar(this.session));
      this.loops = detectLoopsFromAiffRegions(this.session).filter((l) => l.lengthSamples >= minSamples);
    } else {
      // For Field sessions, use boundary detection
      const boundaries = detectBoundaries(this.session, SETTINGS.minTracks);
      this.loops = detectLoops(this.session, boundaries, SETTINGS.minBars);
    }
    this.refreshLoopsUi();
  }

  private refreshLoopsUi(): void {
    this.waveform.setLoops(this.loops);
    this.loopList.setLoops(this.loops);
    this.exportBtn.disabled = this.loops.length === 0;
    this.selectedLoopIndex = -1;
    this.loopInfo.clear();
    if (this.loops.length) this.loopList.setCurrentRow(0);
    this.statusBar.setStatus(`Found ${this.loops.length} loops`);
  }

  private reindex(): void {
    this.loops.forEach((l, i) => {
      l.index = i + 1;
    });
  }

  // ---------------------------------------------------------------------------
  // Selection
  // ---------------------------------------------------------------------------

  private onLoopSelected(row: number): void {
    if (row >= 0 && row < this.loops.length) this.selectLoop(row);
  }

  private onWaveformLoopClicked(index: number): void {
    if (index < 0 || index >= this.loops.length) return;
    const wasPlaying = this.player?.isPlaying() ?? false;
    this.loopList.setCurrentRow(index);
    if (wasPlaying) {
      this.player!.play();
      this.startTimer();
      this.playback.setPlaying(true);
      this.waveform.setPlaying(true);
    }
  }

  private selectLoop(index: number): void {
    const wasPlaying = this.player?.isPlaying() ?? false;
    this.selectedLoopIndex = index;
    const loop = this.loops[index];

    // Highlight the loop in the waveform and scroll to it if needed
    this.waveform.highlightLoop(index + 1);
    this.waveform.scrollToLoop(index);
    this.loopInfo.setLoop(loop, index);

    if (this.player) {
      this.player.setRegion(loop.startSample, loop.endSample);
      if (!wasPlaying) {
        this.waveform.setPlayheadPosition(loop.startSample);
        this.playback.updateTime(loop.startSample);
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Editing
  // ---------------------------------------------------------------------------

  private onLoopNameChanged(index: number, name: string): void {
    if (index < 0 || index >= this.loops.length) return;
    this.loops[index].customName = name;
    this.loopList.updateLoopName(index, name || defaultLoopName(this.loops[index]));
    this.waveform.setLoops(this.loops);
    if (this.selectedLoopIndex >= 0) this.waveform.highlightLoop(this.selectedLoopIndex + 1);
  }

  private stopPlayback(): void {
    if (!this.player) return;
    this.player.stop();
    this.stopTimer();
    this.playback.setPlaying(false);
    this.waveform.setPlaying(false);
  }

  private onLoopSplitRequested(loopIndex: number, barCount: number): void {
    if (loopIndex < 0 || loopIndex >= this.loops.length) return;
    const loop = this.loops[loopIndex];
    if (loop.lengthBars <= barCount) return;

    this.stopPlayback();
    const newLoops = splitLoopByBars(loop, barCount);
    this.loops = [...this.loops.slice(0, loopIndex), ...newLoops, ...this.loops.slice(loopIndex + 1)];
    this.reindex();
    this.refreshLoopsUi();
    this.loopList.setCurrentRow(loopIndex);
    this.statusBar.setStatus(`Split into ${newLoops.length} × ${barCount}-bar loops`);
  }

  private async onAutoScanRequested(): Promise<void> {
    if (!this.session) return;
    const proceed = await confirmModal(
      'Re-Scan Loops',
      'This will overwrite all existing loop regions. Proceed?',
      'Re-Scan',
    );
    if (!proceed) return;
    this.stopPlayback();
    this.redetectLoops();
    this.statusBar.setStatus(`Re-scanned: found ${this.loops.length} loops`);
  }

  private onLoopRegionChanged(loopIndex: number, newStart: number, newEnd: number): void {
    if (loopIndex < 0 || loopIndex >= this.loops.length) return;
    const loop = this.loops[loopIndex];
    loop.startSample = newStart;
    loop.endSample = newEnd;

    // Update the loop list display
    this.loopList.setLoops(this.loops);
    this.waveform.setLoops(this.loops);
    this.loopList.setCurrentRow(loopIndex);

    if (this.selectedLoopIndex === loopIndex) {
      this.loopInfo.setLoop(loop, loopIndex);
      this.player?.setRegion(newStart, newEnd);
    }
    this.statusBar.setStatus(`Loop ${loopIndex + 1} adjusted: ${loop.lengthBars.toFixed(1)} bars`);
  }

  private onToolChanged(tool: Tool): void {
    this.waveform.setTool(tool);
    const names: Partial<Record<Tool, string>> = { [TOOL_SELECT]: 'Select', [TOOL_SPLIT]: 'Split', [TOOL_GLUE]: 'Glue' };
    this.statusBar.setStatus(`${names[tool] ?? tool} tool active`);
  }

  private onSnapToGridChanged(enabled: boolean): void {
    this.waveform.setSnapToGrid(enabled);
    this.statusBar.setStatus(`Snap to grid ${enabled ? 'enabled' : 'disabled'}`);
  }

  private onHighlightGridChanged(bars: number): void {
    this.waveform.setHighlightBars(bars);
    this.statusBar.setStatus(bars > 0 ? `Highlighting every ${bars} bars` : 'Bar highlights disabled');
  }

  /** Split tool click: split a loop at the clicked position */
  private onSplitAtPosition(loopIndex: number, splitSample: number): void {
    if (loopIndex < 0 || loopIndex >= this.loops.length) return;
    const loop = this.loops[loopIndex];
    this.stopPlayback();

    const loop1Boundary: LoopBoundary = {
      position: loop.startSample, tracksStarting: loop.tracksActive,
      confidence: loop.startBoundary.confidence, rawPositions: [loop.startSample],
    };
    const loop2Boundary: LoopBoundary = {
      position: splitSample, tracksStarting: loop.tracksActive, confidence: 1.0, rawPositions: [splitSample],
    };
    const loop1 = new DetectedLoop(1, loop.startSample, splitSample, loop1Boundary, loop2Boundary, loop.bpm, loop.customName);
    const loop2 = new DetectedLoop(2, splitSample, loop.endSample, loop2Boundary, loop.endBoundary, loop.bpm);

    this.loops = [...this.loops.slice(0, loopIndex), loop1, loop2, ...this.loops.slice(loopIndex + 1)];
    this.reindex();
    this.refreshLoopsUi();
    this.loopList.setCurrentRow(loopIndex);
    this.statusBar.setStatus('Split loop into 2 regions');
  }

  /** Glue tool click: join two adjacent loops */
  private onGlueLoops(first: number, second: number): void {
    if (first < 0 || second >= this.loops.length) return;
    this.stopPlayback();
    const loop1 = this.loops[first];
    const loop2 = this.loops[second];

    const combinedBoundary: LoopBoundary = {
      position: loop1.startSample,
      tracksStarting: new Set([...loop1.tracksActive, ...loop2.tracksActive]),
      confidence: loop1.startBoundary.confidence,
      rawPositions: loop1.startBoundary.rawPositions,
    };
    const combined = new DetectedLoop(1, loop1.startSample, loop2.endSample, combinedBoundary, loop2.endBoundary,
      loop1.bpm, loop1.customName || loop2.customName);

    this.loops = [...this.loops.slice(0, first), combined, ...this.loops.slice(second + 1)];
    this.reindex();
    this.refreshLoopsUi();
    this.loopList.setCurrentRow(first);
    this.statusBar.setStatus(`Glued 2 loops into 1 region (${combined.lengthBars.toFixed(1)} bars)`);
  }

  /** Remove tool click: confirm and remove a loop */
  private async onRemoveLoop(loopIndex: number): Promise<void> {
    if (loopIndex < 0 || loopIndex >= this.loops.length) return;
    const loop = this.loops[loopIndex];
    const loopName = loop.customName || defaultLoopName(loop);

    const confirmed = await confirmModal(
      'Remove Loop',
      `Are you sure you want to remove loop region '${loopName}'?`,
      'Remove',
    );
    if (!confirmed) return;

    this.stopPlayback();
    this.loops = [...this.loops.slice(0, loopIndex), ...this.loops.slice(loopIndex + 1)];
    this.reindex();
    this.refreshLoopsUi();
    // Select the loop that took its place, or the new last one
    if (this.loops.length) this.loopList.setCurrentRow(Math.min(loopIndex, this.loops.length - 1));
    this.statusBar.setStatus(`Removed loop '${loopName}'`);
  }

  /** Add tool click: create a new loop region */
  private onAddRegion(startSample: number, endSample: number): void {
    if (!this.session) return;
    const boundary: LoopBoundary = { position: startSample, tracksStarting: new Set(), confidence: 1.0, rawPositions: [startSample] };
    const newLoop = new DetectedLoop(this.loops.length + 1, startSample, endSample, boundary, null, this.session.bpm);

    this.loops.push(newLoop);
    this.loops.sort((a, b) => a.startSample - b.startSample);
    this.reindex();
    this.refreshLoopsUi();

    const newIndex = Math.max(0, this.loops.findIndex((l) => l.startSample === startSample));
    this.loopList.setCurrentRow(newIndex);

    const bars = (endSample - startSample) / (((SAMPLE_RATE * 60.0) / this.session.bpm) * 4);
    this.statusBar.setStatus(`Added new ${bars.toFixed(0)}-bar region`);
  }

  // ---------------------------------------------------------------------------
  // Playback
  // ---------------------------------------------------------------------------

  private startTimer(): void {
    if (!this.playheadTimer) this.playheadTimer = window.setInterval(() => this.updatePlayhead(), 33);
  }

  private stopTimer(): void {
    clearInterval(this.playheadTimer);
    this.playheadTimer = 0;
  }

  private onPlay(): void {
    if (this.player && this.selectedLoopIndex >= 0) {
      this.player.play();
      this.startTimer();
      this.waveform.setPlaying(true);
    } else {
      this.playback.setPlaying(false);
    }
  }

  private onPause(): void {
    if (!this.player) return;
    this.player.pause();
    this.stopTimer();
    this.waveform.setPlaying(false);
  }

  private onStop(): void {
    if (!this.player) return;
    this.player.stop();
    this.stopTimer();
    this.waveform.setPlaying(false);
    if (this.selectedLoopIndex >= 0) {
      const loop = this.loops[this.selectedLoopIndex];
      this.waveform.setPlayheadPosition(loop.startSample);
      this.playback.updateTime(loop.startSample);
    }
  }

  /** Seek within the current loop region */
  private onSeek(samplePosition: number): void {
    if (!this.player || this.selectedLoopIndex < 0) return;
    const loop = this.loops[this.selectedLoopIndex];
    const pos = Math.max(loop.startSample, Math.min(samplePosition, loop.endSample));
    this.player.seek(pos);
    this.waveform.setPlayheadPosition(pos);
    this.playback.updateTime(pos);
  }

  private updatePlayhead(): void {
    if (!this.player) return;
    const pos = this.player.getPosition();
    this.waveform.setPlayheadPosition(pos);
    this.playback.updateTime(pos);
    if (!this.player.isPlaying()) {
      this.stopTimer();
      this.playback.setPlaying(false);
      this.waveform.setPlaying(false);
    }
  }

  private async refreshDevices(): Promise<void> {
    if (!this.deviceSelector) return;
    const { defaultLabel, devices, labelsHidden } = await listOutputDevices();
    this.deviceSelector.setDevices(defaultLabel, devices, labelsHidden);
  }

  private async onAudioDeviceChanged(deviceId: string, label: string): Promise<void> {
    try {
      await this.player?.setOutputDevice(deviceId);
      this.statusBar.setStatus(`Audio output: ${label}`);
    } catch (err) {
      this.statusBar.setStatus(`Could not switch audio output: ${(err as Error).message}`);
    }
  }

  // ---------------------------------------------------------------------------
  // Export
  // ---------------------------------------------------------------------------

  private async export(): Promise<void> {
    if (!this.session || !this.audio || !this.loops.length || this.exporting) return;
    this.stopPlayback();

    const target = await chooseExportTarget(`${this.folderName || 'Tape'} Loops`);
    if (!target) return;

    this.exporting = true;
    this.exportBtn.disabled = true;
    const options = {
      includeMix: this.exportMixCheck.checked,
      includeMetadata: false,
      zeroFade: this.zeroFadeCheck.checked,
    };

    try {
      const total = this.loops.length;
      for (let i = 0; i < total; i++) {
        const loop = this.loops[i];
        this.statusBar.setStatus(`Exporting ${loop.customName || `loop ${loop.index}`}`);
        this.statusBar.setProgress(i, total);
        await new Promise((resolve) => setTimeout(resolve, 0));
        const exported = exportLoop(this.audio, loop, this.session, { ...options, customFolderName: loop.customName });
        await target.writeLoop(exported);
      }
      this.statusBar.setProgress(total, total);
      await target.finish();
      this.statusBar.setProgress(0, 0);
      this.statusBar.setStatus(`Exported ${total} loops to ${target.label}`);
      await infoModal('Export Complete', `Exported ${total} loops to ${target.label}.`);
    } catch (err) {
      console.error(err);
      this.statusBar.setProgress(0, 0);
      this.statusBar.setStatus(`Error: ${(err as Error).message}`);
    } finally {
      this.exporting = false;
      this.exportBtn.disabled = this.loops.length === 0;
    }
  }

  private showAbout(): void {
    const body = h('div', { class: 'about' },
      h('img', { class: 'about-logo', src: logoUrl, alt: '3030' }),
      h('div', { class: 'about-title' }, 'OP-1'),
      h('div', { class: 'about-subtitle' }, 'Loop Extractor'),
      h('div', { class: 'about-rule' }),
      h('div', { class: 'about-version' }, `v${VERSION}`),
      h('p', { class: 'about-credit' }, 'This tool was lovingly crafted by', h('br'), 'Nate Schmold @ ',
        h('a', { href: 'https://www.3030.ca', target: '_blank', rel: 'noopener' }, 'www.3030.ca')),
      h('p', { class: 'about-note' }, 'Everything runs in your browser. Your tapes never leave your computer. ',
        h('a', { href: 'https://github.com/3030/op1-tape-extractor', target: '_blank', rel: 'noopener' }, 'Source on GitHub'),
        ' (GPL-3.0).'),
    );
    contentModal('About OP-1 Loop Extractor', body, 'about-modal');
  }
}
