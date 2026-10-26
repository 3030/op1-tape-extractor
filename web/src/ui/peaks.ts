/**
 * Min/max peak pyramid for fast waveform drawing at any zoom level.
 */

const BASE_BLOCK = 256;

export class Peaks {
  // levels[k] holds interleaved [min, max] pairs for blocks of BASE_BLOCK * 2^k samples
  private levels: Float32Array[] = [];

  constructor(private readonly mono: Float32Array) {
    const blocks = Math.ceil(mono.length / BASE_BLOCK);
    const base = new Float32Array(blocks * 2);
    for (let b = 0; b < blocks; b++) {
      let min = Infinity;
      let max = -Infinity;
      const end = Math.min(mono.length, (b + 1) * BASE_BLOCK);
      for (let i = b * BASE_BLOCK; i < end; i++) {
        const v = mono[i];
        if (v < min) min = v;
        if (v > max) max = v;
      }
      base[b * 2] = min;
      base[b * 2 + 1] = max;
    }
    this.levels.push(base);

    let prev = base;
    while (prev.length > 2) {
      const n = Math.ceil(prev.length / 4);
      const next = new Float32Array(n * 2);
      for (let b = 0; b < n; b++) {
        const a = b * 4;
        const hasPair = a + 2 < prev.length;
        next[b * 2] = hasPair ? Math.min(prev[a], prev[a + 2]) : prev[a];
        next[b * 2 + 1] = hasPair ? Math.max(prev[a + 1], prev[a + 3]) : prev[a + 1];
      }
      this.levels.push(next);
      prev = next;
    }
  }

  get length(): number {
    return this.mono.length;
  }

  /** Min and max over samples [start, end) */
  range(start: number, end: number): [number, number] {
    start = Math.max(0, Math.floor(start));
    end = Math.min(this.mono.length, Math.ceil(end));
    if (end <= start) return [0, 0];

    let min = Infinity;
    let max = -Infinity;
    const span = end - start;

    if (span < BASE_BLOCK * 2) {
      for (let i = start; i < end; i++) {
        const v = this.mono[i];
        if (v < min) min = v;
        if (v > max) max = v;
      }
      return [min, max];
    }

    // Coarsest level whose blocks still fit the span a few times over
    let level = 0;
    while (level + 1 < this.levels.length && BASE_BLOCK * 2 ** (level + 1) * 8 <= span) level++;
    const blockSize = BASE_BLOCK * 2 ** level;
    const data = this.levels[level];
    const b0 = Math.floor(start / blockSize);
    const b1 = Math.min(data.length / 2, Math.ceil(end / blockSize));
    for (let b = b0; b < b1; b++) {
      if (data[b * 2] < min) min = data[b * 2];
      if (data[b * 2 + 1] > max) max = data[b * 2 + 1];
    }
    return [min, max];
  }
}
