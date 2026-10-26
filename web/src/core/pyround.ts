/**
 * Python-compatible number helpers, so the port matches core.py exactly.
 */

/** Python's round(x): round half to even. */
export function roundHalfEven(x: number): number {
  const floor = Math.floor(x);
  const diff = x - floor;
  if (diff > 0.5) return floor + 1;
  if (diff < 0.5) return floor;
  return floor % 2 === 0 ? floor : floor + 1;
}

/** Python's round(x, ndigits) for small ndigits. */
export function pyRound(x: number, ndigits: number): number {
  const scale = 10 ** ndigits;
  const scaled = x * scale;
  // Only exact binary ties (e.g. 0.25 to 1 digit) round half to even, like Python.
  // x * scale is itself rounded, so confirm the tie with an exact power-of-two scale.
  if (Number.isInteger(scaled * 2) && !Number.isInteger(scaled) && Number.isInteger(x * 2 ** (ndigits + 1))) {
    return roundHalfEven(scaled) / scale;
  }
  // toFixed rounds the exact binary value, which matches Python for everything else
  return Number(x.toFixed(ndigits));
}

/** Python's int(x): truncate toward zero. */
export function pyInt(x: number): number {
  return Math.trunc(x);
}

/** Python's float repr for JSON output (always keeps a decimal point). */
export function pyFloatRepr(x: number): string {
  if (Number.isInteger(x)) return `${x}.0`;
  return String(x);
}

/** Zero-padded integer, like Python's f"{n:03d}". */
export function zeroPad(n: number, width: number): string {
  return String(n).padStart(width, '0');
}
