/**
 * makeZip / crc32: header-level checks plus a cross-check with Python's zipfile.
 */

import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { crc32 as zlibCrc32 } from 'node:zlib';
import { describe, expect, it } from 'vitest';
import { crc32, makeZip, ZipEntry } from '../src/core/zip';
import { HAS_PYTHON, PYTHON } from './helpers';

const enc = new TextEncoder();

describe('crc32', () => {
  it('known check values', () => {
    expect(crc32(enc.encode('123456789'))).toBe(0xcbf43926);
    expect(crc32(new Uint8Array(0))).toBe(0);
    expect(crc32(enc.encode('The quick brown fox jumps over the lazy dog'))).toBe(0x414fa339);
  });

  it('matches zlib on pseudo-random data', () => {
    const data = new Uint8Array(100_003);
    let x = 12345;
    for (let i = 0; i < data.length; i++) {
      x = (Math.imul(x, 1103515245) + 12345) >>> 0;
      data[i] = x >>> 24;
    }
    expect(crc32(data)).toBe(zlibCrc32(data));
    expect(crc32(data.subarray(7, 999))).toBe(zlibCrc32(data.subarray(7, 999)));
  });
});

describe('makeZip', () => {
  const wav = new Uint8Array(1001).map((_, i) => (i * 37) & 0xff);
  const entries: ZipEntry[] = [
    { path: 'L001/index.json', data: '{\n  "name": "L001"\n}' },
    { path: 'L001/track_1.wav', data: wav.buffer },
    { path: 'Café Loop/mix.wav', data: new ArrayBuffer(0) },
    { path: 'Café Loop/notes ü.txt', data: 'héllo' },
  ];
  const when = new Date(2026, 9, 26, 13, 45, 31);
  const bytesOf = (e: ZipEntry) => (typeof e.data === 'string' ? enc.encode(e.data) : new Uint8Array(e.data));

  it('has consistent local headers, central directory and end record', async () => {
    const zip = new Uint8Array(await makeZip(entries, when).arrayBuffer());
    const v = new DataView(zip.buffer);
    const eocd = zip.length - 22;
    expect(v.getUint32(eocd, true)).toBe(0x06054b50);
    expect(v.getUint16(eocd + 8, true)).toBe(entries.length);
    expect(v.getUint16(eocd + 10, true)).toBe(entries.length);
    const cdSize = v.getUint32(eocd + 12, true);
    const cdOffset = v.getUint32(eocd + 16, true);
    expect(cdOffset + cdSize).toBe(eocd);
    expect(v.getUint16(eocd + 20, true)).toBe(0); // no comment

    const dosTime = (13 << 11) | (45 << 5) | 15;
    const dosDate = ((2026 - 1980) << 9) | (10 << 5) | 26;
    let p = cdOffset;
    let expectedLocal = 0;
    for (const entry of entries) {
      const name = enc.encode(entry.path);
      const data = bytesOf(entry);
      expect(v.getUint32(p, true)).toBe(0x02014b50);
      expect(v.getUint16(p + 8, true)).toBe(0x0800); // UTF-8 names
      expect(v.getUint16(p + 10, true)).toBe(0); // stored
      expect(v.getUint16(p + 12, true)).toBe(dosTime);
      expect(v.getUint16(p + 14, true)).toBe(dosDate);
      expect(v.getUint32(p + 16, true)).toBe(crc32(data));
      expect(v.getUint32(p + 20, true)).toBe(data.length);
      expect(v.getUint32(p + 24, true)).toBe(data.length);
      expect(v.getUint16(p + 28, true)).toBe(name.length);
      expect(v.getUint16(p + 30, true)).toBe(0);
      expect(v.getUint16(p + 32, true)).toBe(0);
      const local = v.getUint32(p + 42, true);
      expect(local).toBe(expectedLocal);
      expect(zip.subarray(p + 46, p + 46 + name.length)).toEqual(name);

      expect(v.getUint32(local, true)).toBe(0x04034b50);
      expect(v.getUint16(local + 6, true)).toBe(0x0800);
      expect(v.getUint32(local + 14, true)).toBe(crc32(data));
      expect(v.getUint32(local + 18, true)).toBe(data.length);
      expect(v.getUint16(local + 26, true)).toBe(name.length);
      expect(v.getUint16(local + 28, true)).toBe(0);
      expect(zip.subarray(local + 30, local + 30 + name.length)).toEqual(name);
      const body = zip.subarray(local + 30 + name.length, local + 30 + name.length + data.length);
      expect(body).toEqual(data);

      expectedLocal = local + 30 + name.length + data.length;
      p += 46 + name.length;
    }
    expect(p).toBe(eocd);
    expect(expectedLocal).toBe(cdOffset);
  });

  it.skipIf(!HAS_PYTHON)("Python's zipfile reads it back", async () => {
    const dir = mkdtempSync(join(tmpdir(), 'op1-zip-'));
    try {
      const path = join(dir, 'export.zip');
      writeFileSync(path, new Uint8Array(await makeZip(entries, when).arrayBuffer()));
      const script = [
        'import sys, json, zipfile',
        'z = zipfile.ZipFile(sys.argv[1])',
        "print(json.dumps({'bad': z.testzip(), 'infos': [{'name': i.filename, 'dt': list(i.date_time),",
        "  'size': i.file_size, 'method': i.compress_type, 'hex': z.read(i).hex()} for i in z.infolist()]}))",
      ].join('\n');
      const r = spawnSync(PYTHON, ['-c', script, path], { encoding: 'utf-8' });
      expect(r.status, r.stderr).toBe(0);
      const py = JSON.parse(r.stdout);
      expect(py.bad).toBeNull();
      expect(py.infos).toEqual(entries.map((e) => ({
        name: e.path,
        dt: [2026, 10, 26, 13, 45, 30],
        size: bytesOf(e).length,
        method: 0,
        hex: Buffer.from(bytesOf(e)).toString('hex'),
      })));
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
