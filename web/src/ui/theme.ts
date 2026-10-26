/**
 * Colors shared with the desktop app (COLORS in ui.py).
 */

export const COLORS = {
  bg: '#ececec', // Light grey background
  bgWaveform: '#2c2c2c', // Dark waveform background
  panel: '#ffffff', // White panels
  border: '#3a3a3a', // Grid borders (darker)
  text: '#1b1a1d', // Dark text
  textDim: '#888888', // Dimmed text
  accent: '#ff6b35', // Warm orange
  accentDim: '#ffb8a0', // Lighter orange for hover
  track1: '#1a223d', // Dark blue
  track2: '#9c704d', // Brown
  track3: '#7b7778', // Grey
  track4: '#fa451c', // Orange
  boundary: '#ff6b35', // Orange boundaries
  loopRegion: '#ff6b3515', // Subtle orange tint
  selected: '#ff6b3530', // Selected orange tint
  playhead: '#ffffff', // White playhead
  muted: '#d0d0d0', // Light grey for muted
  barHighlight: '#ffffff08', // Very subtle highlight
  splitCursor: '#ff4444', // Red split cursor
  glueCursor: '#4ecdc4', // Teal glue cursor
  addPreview: '#44aaff', // Blue add-tool preview
} as const;

export const TRACK_COLORS = [COLORS.track1, COLORS.track2, COLORS.track3, COLORS.track4];

// Tool modes for the waveform editor
export const TOOL_SELECT = 'select';
export const TOOL_SPLIT = 'split';
export const TOOL_GLUE = 'glue';
export const TOOL_REMOVE = 'remove';
export const TOOL_ADD = 'add';
export type Tool = typeof TOOL_SELECT | typeof TOOL_SPLIT | typeof TOOL_GLUE | typeof TOOL_REMOVE | typeof TOOL_ADD;

/** m:ss:mmm, the time format used throughout the app */
export function formatTime(seconds: number): string {
  const s = Math.max(0, seconds);
  const mins = Math.floor(s / 60);
  const secs = Math.floor(s % 60);
  const ms = Math.floor((s % 1) * 1000);
  return `${mins}:${String(secs).padStart(2, '0')}:${String(ms).padStart(3, '0')}`;
}
