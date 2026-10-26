/**
 * WAV and AIFF decoding to planar float32, matching soundfile's float conversion.
 *
 * decodeAudioData is avoided on purpose: it resamples to the context rate and
 * cannot read AIFF in Chrome or Firefox, and tape positions must stay sample-exact.
 */

export interface AudioData {
  sampleRate: number;
  frames: number;
  channels: Float32Array[]; // planar, one array per channel
}

type SampleFormat = 'int' | 'uint8' | 'float';

interface PcmLayout {
  format: SampleFormat;
  bits: number;
  littleEndian: boolean;
  numChannels: number;
  sampleRate: number;
  dataOffset: number;
  dataBytes: number;
}

function fourCC(view: DataView, offset: number): string {
  return String.fromCharCode(
    view.getUint8(offset), view.getUint8(offset + 1), view.getUint8(offset + 2), view.getUint8(offset + 3),
  );
}

export function decodeAudio(buffer: ArrayBuffer): AudioData {
  const view = new DataView(buffer);
  if (buffer.byteLength < 12) throw new Error('File is too short to be audio');
  const magic = fourCC(view, 0);
  if (magic === 'RIFF' || magic === 'RF64') return decodePcm(buffer, parseWav(view));
  if (magic === 'FORM') return decodePcm(buffer, parseAiff(view));
  throw new Error('Unsupported audio file (expected WAV or AIFF)');
}

function parseWav(view: DataView): PcmLayout {
  if (fourCC(view, 8) !== 'WAVE') throw new Error('Not a WAVE file');

  let fmt: Omit<PcmLayout, 'dataOffset' | 'dataBytes'> | null = null;
  let offset = 12;
  while (offset + 8 <= view.byteLength) {
    const id = fourCC(view, offset);
    const size = view.getUint32(offset + 4, true);
    const body = offset + 8;

    if (id === 'fmt ') {
      let tag = view.getUint16(body, true);
      const numChannels = view.getUint16(body + 2, true);
      const sampleRate = view.getUint32(body + 4, true);
      const bits = view.getUint16(body + 14, true);
      if (tag === 0xfffe && size >= 26) tag = view.getUint16(body + 24, true); // WAVE_FORMAT_EXTENSIBLE sub-format
      let format: SampleFormat;
      if (tag === 1) format = bits === 8 ? 'uint8' : 'int';
      else if (tag === 3) format = 'float';
      else throw new Error(`Unsupported WAV encoding (format ${tag})`);
      fmt = { format, bits, littleEndian: true, numChannels, sampleRate };
    } else if (id === 'data') {
      if (!fmt) throw new Error('WAV data chunk before fmt chunk');
      // Recorders sometimes leave a placeholder size; clamp to the file
      const dataBytes = Math.min(size, view.byteLength - body);
      return { ...fmt, dataOffset: body, dataBytes };
    }
    offset = body + size + (size % 2);
  }
  throw new Error('WAV file has no audio data');
}

/** 80-bit IEEE 754 extended float (AIFF sample rate) */
function readExtended(view: DataView, offset: number): number {
  const expon = view.getUint16(offset, false);
  const hi = view.getUint32(offset + 2, false);
  const lo = view.getUint32(offset + 6, false);
  if (expon === 0 && hi === 0 && lo === 0) return 0;
  const sign = expon & 0x8000 ? -1 : 1;
  const exp = (expon & 0x7fff) - 16383;
  return sign * (hi * 2 ** (exp - 31) + lo * 2 ** (exp - 63));
}

function parseAiff(view: DataView): PcmLayout {
  const formType = fourCC(view, 8);
  if (formType !== 'AIFF' && formType !== 'AIFC') throw new Error('Not an AIFF file');

  let comm: Omit<PcmLayout, 'dataOffset' | 'dataBytes'> | null = null;
  let ssnd: { dataOffset: number; dataBytes: number } | null = null;
  let offset = 12;
  while (offset + 8 <= view.byteLength) {
    const id = fourCC(view, offset);
    const size = view.getUint32(offset + 4, false);
    const body = offset + 8;

    if (id === 'COMM') {
      const numChannels = view.getInt16(body, false);
      const bits = view.getInt16(body + 6, false);
      const sampleRate = readExtended(view, body + 8);
      let format: SampleFormat = 'int';
      let littleEndian = false;
      if (formType === 'AIFC' && size >= 22) {
        const compression = fourCC(view, body + 18);
        // Little-endian integer PCM ('sowt' 16-bit; libsndfile also writes '42n1'/'23ni')
        const littleEndianInt = ['sowt', '42ni', '42n1', '23ni'];
        // Big-endian integer PCM
        const bigEndianInt = ['NONE', 'twos', 'in24', 'in32'];
        if (littleEndianInt.includes(compression)) littleEndian = true;
        else if (['fl32', 'FL32', 'fl64', 'FL64'].includes(compression)) format = 'float';
        else if (!bigEndianInt.includes(compression)) {
          throw new Error(`Unsupported AIFF-C compression "${compression}"`);
        }
      }
      if (format === 'float' && bits !== 32 && bits !== 64) {
        // Some writers store 0 bits for float; infer from the compression type
        comm = { format, bits: 32, littleEndian, numChannels, sampleRate };
      } else {
        comm = { format, bits, littleEndian, numChannels, sampleRate };
      }
    } else if (id === 'SSND') {
      const dataOffset = view.getUint32(body, false);
      const start = body + 8 + dataOffset;
      ssnd = { dataOffset: start, dataBytes: Math.min(size - 8 - dataOffset, view.byteLength - start) };
    }
    offset = body + size + (size % 2);
  }
  if (!comm) throw new Error('AIFF file has no COMM chunk');
  if (!ssnd) throw new Error('AIFF file has no audio data');
  return { ...comm, ...ssnd };
}

function decodePcm(buffer: ArrayBuffer, layout: PcmLayout): AudioData {
  const { format, bits, littleEndian, numChannels, sampleRate, dataOffset } = layout;
  if (numChannels < 1) throw new Error('Audio file has no channels');
  const bytesPerSample = Math.ceil(bits / 8);
  const frameBytes = bytesPerSample * numChannels;
  const frames = Math.floor(layout.dataBytes / frameBytes);

  // Fast path: mono little-endian float32 can be viewed in place without copying
  if (format === 'float' && bits === 32 && littleEndian && numChannels === 1 && dataOffset % 4 === 0) {
    return { sampleRate, frames, channels: [new Float32Array(buffer, dataOffset, frames)] };
  }

  const view = new DataView(buffer);
  const channels = Array.from({ length: numChannels }, () => new Float32Array(frames));
  const read = sampleReader(view, format, bits, littleEndian);

  for (let ch = 0; ch < numChannels; ch++) {
    const out = channels[ch];
    let pos = dataOffset + ch * bytesPerSample;
    for (let i = 0; i < frames; i++, pos += frameBytes) out[i] = read(pos);
  }
  return { sampleRate, frames, channels };
}

function sampleReader(view: DataView, format: SampleFormat, bits: number, le: boolean): (pos: number) => number {
  if (format === 'float') {
    if (bits === 64) return (pos) => view.getFloat64(pos, le);
    return (pos) => view.getFloat32(pos, le);
  }
  if (format === 'uint8') return (pos) => (view.getUint8(pos) - 128) / 128;
  switch (bits) {
    case 8:
      return (pos) => view.getInt8(pos) / 128;
    case 16:
      return (pos) => view.getInt16(pos, le) / 32768;
    case 24:
      return le
        ? (pos) => ((view.getUint8(pos) | (view.getUint8(pos + 1) << 8) | (view.getInt8(pos + 2) << 16))) / 8388608
        : (pos) => ((view.getInt8(pos) << 16) | (view.getUint8(pos + 1) << 8) | view.getUint8(pos + 2)) / 8388608;
    case 32:
      return (pos) => view.getInt32(pos, le) / 2147483648;
    default:
      throw new Error(`Unsupported bit depth ${bits}`);
  }
}

/** Mono mix of all channels (mean), used for waveform display */
export function toMono(audio: AudioData): Float32Array {
  if (audio.channels.length === 1) return audio.channels[0];
  const mono = new Float32Array(audio.frames);
  const n = audio.channels.length;
  for (const ch of audio.channels) {
    for (let i = 0; i < audio.frames; i++) mono[i] += ch[i];
  }
  for (let i = 0; i < audio.frames; i++) mono[i] /= n;
  return mono;
}
