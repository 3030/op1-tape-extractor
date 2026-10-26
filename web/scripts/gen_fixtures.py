#!/usr/bin/env python3
"""
Deterministic fixture generator for the TypeScript parity tests.

Writes small SYNTHETIC audio/tape fixtures into web/test/fixtures/ and runs the
original Python logic in core.py over them, recording the results in
web/test/fixtures/expected.json. The vitest suites in web/test/ load the same
fixtures, run the TS port, and compare.

Run from anywhere:
    QT_QPA_PLATFORM=offscreen ../.venv/bin/python scripts/gen_fixtures.py
"""

import hashlib
import json
import math
import os
import shutil
import struct
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.dirname(HERE)
ROOT = os.path.dirname(WEB)
FIX = os.path.join(WEB, "test", "fixtures")
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

import core  # noqa: E402
from core import (  # noqa: E402
    AiffRegion, AudioLoader, DetectedLoop, LoopBoundary,
    apply_zero_fade, cluster_positions, create_mix, detect_boundaries, detect_loops,
    detect_loops_from_aiff_regions, detect_tape_format, export_loop, get_track_activity,
    parse_aiff_regions, parse_aiff_tape_session, parse_tape_json, slice_loop, split_loop_by_bars,
    _estimate_bpm_from_regions, _parse_regn_chunk,
)


# =============================================================================
# Serialization helpers
# =============================================================================

def ser_boundary(b):
    if b is None:
        return None
    return {
        "position": b.position,
        "tracks": sorted(b.tracks_starting),
        "confidence": b.confidence,
        "raw_positions": list(b.raw_positions),
    }


def ser_loop(lp):
    return {
        "index": lp.index,
        "start_sample": lp.start_sample,
        "end_sample": lp.end_sample,
        "bpm": lp.bpm,
        "length_samples": lp.length_samples,
        "length_seconds": lp.length_seconds,
        "length_bars": lp.length_bars,
        "time_str": lp.time_str(),
        "tracks_active": sorted(lp.tracks_active),
        "start_boundary": ser_boundary(lp.start_boundary),
        "end_boundary": ser_boundary(lp.end_boundary),
    }


def ser_session(s):
    return {
        "clips": [{"channel": c.channel, "clip_id": c.clip_id, "start": c.start, "stop": c.stop} for c in s.clips],
        "bpm": s.bpm,
        "loop_in": s.loop_in,
        "loop_out": s.loop_out,
        "tape_type": s.tape_type,
        "is_aiff_source": s.is_aiff_source,
    }


def loop_def(lp, custom_name=None):
    """Enough to rebuild an identical DetectedLoop in TS without re-running detection."""
    return {
        "index": lp.index,
        "start_sample": lp.start_sample,
        "end_sample": lp.end_sample,
        "bpm": lp.bpm,
        "confidence": lp.start_boundary.confidence,
        "tracks": sorted(lp.start_boundary.tracks_starting),
        "custom_name": custom_name,
    }


def summarize(arr, sample_rate=None):
    """Summary of a (frames, channels) float32 array: strided values, sum, abs-max, sha256 per channel."""
    arr = np.asarray(arr)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    assert arr.dtype == np.float32, arr.dtype
    frames, nch = arr.shape
    stride = max(1, frames // 48)
    chans = []
    for c in range(nch):
        col = np.ascontiguousarray(arr[:, c]).astype("<f4")
        chans.append({
            "strided": [float(v) for v in col[::stride]],
            "last": float(col[-1]) if frames else None,
            "sum": math.fsum(float(v) for v in col),
            "absmax": float(np.max(np.abs(col))) if frames else 0.0,
            "sha256": hashlib.sha256(col.tobytes()).hexdigest(),
        })
    out = {"frames": int(frames), "channels": int(nch), "stride": stride, "per_channel": chans}
    if sample_rate is not None:
        out["sample_rate"] = int(sample_rate)
    return out


def summarize_file(path):
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    s = summarize(data, sr)
    s["subtype"] = sf.info(path).subtype
    return s


# =============================================================================
# Byte-level chunk helpers (AIFF / WAV)
# =============================================================================

def read_chunks(raw, big_endian):
    fmt = ">I" if big_endian else "<I"
    off = 12
    chunks = []
    while off + 8 <= len(raw):
        cid = raw[off:off + 4]
        size = struct.unpack(fmt, raw[off + 4:off + 8])[0]
        body = raw[off + 8:off + 8 + size]
        chunks.append((cid, size, body))
        off += 8 + size + (size % 2)
    return chunks


def build_container(magic, form_type, chunks, big_endian):
    """chunks: list of (id, declared_size, body). Body may be shorter than size only for the last chunk."""
    fmt = ">I" if big_endian else "<I"
    out = bytearray()
    for cid, size, body in chunks:
        out += cid + struct.pack(fmt, size) + body
        if size % 2 == 1 and len(body) == size:
            out += b"\0"
    total = 4 + len(out)
    return magic + struct.pack(fmt, total) + form_type + bytes(out)


def regn_body(regions, rng, declared_count=None, trailing=b""):
    """OP-1 'regn' chunk: big-endian count + N x 88-byte entries (start at 20-23, end at 28-31).
    Other entry bytes are filled with noise so only the documented offsets can matter."""
    count = len(regions) if declared_count is None else declared_count
    body = bytearray(struct.pack(">I", count))
    for start, end in regions:
        entry = bytearray(rng.integers(0, 256, 88, dtype=np.uint8).tobytes())
        entry[20:24] = struct.pack(">I", start)
        entry[28:32] = struct.pack(">I", end)
        body += entry
    return bytes(body) + trailing


def write_aiff_with_chunks(path, audio, sr, subtype, extra_before_ssnd=(), extra_after_ssnd=()):
    tmp = path + ".tmp"
    sf.write(tmp, audio, sr, format="AIFF", subtype=subtype)
    with open(tmp, "rb") as f:
        raw = f.read()
    os.remove(tmp)
    chunks = read_chunks(raw, big_endian=True)
    out = []
    for cid, size, body in chunks:
        if cid == b"SSND":
            out.extend(extra_before_ssnd)
            out.append((cid, size, body))
            out.extend(extra_after_ssnd)
        else:
            out.append((cid, size, body))
    with open(path, "wb") as f:
        f.write(build_container(b"FORM", raw[8:12], out, big_endian=True))


# =============================================================================
# Fixture builders
# =============================================================================

def rel(path):
    return os.path.relpath(path, FIX).replace(os.sep, "/")


def gen_field_tape():
    """OP-1 Field tape.json at 123.5 BPM, detection only (no audio)."""
    clips = [
        # boundary at 0/1 -> mean 0.5 -> half-even 0
        {"ch": 0, "id": 10, "start": 0, "stop": 171401},
        {"ch": 1, "id": 11, "start": 1, "stop": 171403},
        # boundary 171401..171404 -> mean 171402.5 -> half-even 171402 (4 tracks)
        {"ch": 0, "id": 12, "start": 171401, "stop": 342801},
        {"ch": 2, "id": 13, "start": 171402, "stop": 300000},
        {"ch": 1, "id": 14, "start": 171403, "stop": 346801},
        {"ch": 3, "id": 15, "start": 171404, "stop": 260000},
        # 342801 and 346801 (4000 apart, within 4410) cluster -> 344801
        {"ch": 0, "id": 16, "start": 342801, "stop": 514203},
        {"ch": 1, "id": 17, "start": 346801, "stop": 514204},
        # 350801 is 6000 from the running center -> new cluster; short region (< 1 bar) before it
        {"ch": 2, "id": 18, "start": 350801, "stop": 420000},
        # exactly 4410 from 350801 -> inclusive, joins (center 353006)
        {"ch": 3, "id": 19, "start": 355211, "stop": 430000},
        # 514203 / 514204 -> mean 514203.5 -> half-even 514204
        {"ch": 0, "id": 20, "start": 514203, "stop": 600000},
        {"ch": 1, "id": 21, "start": 514204, "stop": 640000},
        # same channel twice in one cluster: one track only (filtered at min_tracks=2)
        {"ch": 0, "id": 22, "start": 600000, "stop": 600100},
        {"ch": 0, "id": 23, "start": 600100, "stop": 700000},
        # two-track boundary that leaves a 0.5-1 bar region before the tape end
        {"ch": 2, "id": 24, "start": 640000, "stop": 690000},
        {"ch": 3, "id": 25, "start": 640005, "stop": 702345},
    ]
    tape = {
        "clips": clips,
        "tempobpmdecimal": 1235,
        "loop_in": 0,
        "loop_out": 702345,
        "tape_type": 2,
        "unused_key": {"x": 1},
    }
    path = os.path.join(FIX, "field", "tape.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tape, f, indent=2)
    return path


def gen_export_tape():
    """Small Field-style tape at 600 BPM (bar = 17640 samples) with real track audio."""
    clips = [
        # loop 1: 0 .. 16317 (all four tracks; starts 0,0,1,1 -> mean 0.5 -> half-even 0).
        # 16317 samples = 0.925 bars at 600 BPM, a value whose round(…, 2) is a near-tie.
        {"ch": 0, "id": 1, "start": 0, "stop": 16316},
        {"ch": 1, "id": 2, "start": 0, "stop": 16318},
        {"ch": 2, "id": 3, "start": 1, "stop": 15000},
        {"ch": 3, "id": 4, "start": 1, "stop": 10000},
        # loop 2: 16317 .. 37317 (tracks 1-3; track 3 audio ends at 20000 -> zero padding)
        {"ch": 0, "id": 5, "start": 16316, "stop": 37317},
        {"ch": 1, "id": 6, "start": 16318, "stop": 37317},
        {"ch": 2, "id": 7, "start": 16317, "stop": 42100},
        # 37317 .. 42000 is shorter than half a bar and is dropped at min_bars=0.5
        {"ch": 1, "id": 8, "start": 37317, "stop": 60000},
        # loop 3: 42000 .. 60000 (tracks 2 and 4; track 2 audio ends at 56000 -> padding;
        # track 3 is active via the clip above but its audio ends before 42000 -> skipped)
        {"ch": 3, "id": 9, "start": 42000, "stop": 45000},
    ]
    tape = {"clips": clips, "tempobpmdecimal": 6000, "loop_in": 0, "loop_out": 60000, "tape_type": 2}
    folder = os.path.join(FIX, "export")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "tape.json"), "w", encoding="utf-8") as f:
        json.dump(tape, f, indent=2)

    rng = np.random.default_rng(1234)
    # track 1: stereo 16-bit, 50000 frames, amp ~0.6
    t1 = (rng.uniform(-0.6, 0.6, (50000, 2)) * 32767).astype(np.int16)
    t1[100] = [19660, -19660]
    sf.write(os.path.join(folder, "track_1.wav"), t1, 44100, subtype="PCM_16")
    # track 2: mono 24-bit, 56000 frames, amp ~0.4
    t2 = (rng.uniform(-0.4, 0.4, 56000) * 8388607).astype(np.int32) << 8
    sf.write(os.path.join(folder, "track_2.wav"), t2, 44100, subtype="PCM_24")
    # track 3: mono 16-bit, 20000 frames (shorter than loop 2), amp ~0.45
    t3 = (rng.uniform(-0.45, 0.45, 20000) * 32767).astype(np.int16)
    sf.write(os.path.join(folder, "track_3.wav"), t3, 44100, subtype="PCM_16")
    # track 4: mono float, 45000 frames, amp ~0.4
    t4 = rng.uniform(-0.4, 0.4, 45000).astype(np.float32)
    sf.write(os.path.join(folder, "track_4.wav"), t4, 44100, subtype="FLOAT")
    return folder


def gen_decode_fixtures():
    folder = os.path.join(FIX, "decode")
    os.makedirs(folder, exist_ok=True)
    rng = np.random.default_rng(42)
    results = []
    skipped = []

    def i16(n, ch):
        a = rng.integers(-32768, 32768, (n, ch), dtype=np.int64).astype(np.int16)
        a[0] = -32768
        a[1] = 32767
        a[2] = 0
        a[3] = -1
        return a

    def i24(n, ch):
        a = rng.integers(-(1 << 23), 1 << 23, (n, ch), dtype=np.int64)
        a[0] = -(1 << 23)
        a[1] = (1 << 23) - 1
        a[2] = 0
        a[3] = -1
        return (a << 8).astype(np.int32)

    def i32(n, ch):
        a = rng.integers(-(1 << 31), 1 << 31, (n, ch), dtype=np.int64)
        a[0] = -(1 << 31)
        a[1] = (1 << 31) - 1
        a[2] = 0
        a[3] = -1
        a[4] = (1 << 24) + 1  # not representable in float32 after scaling
        return a.astype(np.int32)

    def f32(n, ch):
        a = rng.uniform(-1, 1, (n, ch)).astype(np.float32)
        a[0] = 1.5
        a[1] = -2.0
        a[2] = 0.0
        a[3] = np.float32(1e-40)  # subnormal
        a[4] = -0.0
        return a

    def f64(n, ch):
        a = rng.uniform(-1, 1, (n, ch)).astype(np.float64)
        a[0] = 1.5
        a[1] = 1.0 + 2 ** -30  # rounds when narrowed to float32
        a[2] = 1e-300  # underflows to 0 in float32
        a[3] = 0.1
        return a

    specs = [
        # name, format, subtype, data, sr, endian
        ("pcm16_mono.wav", "WAV", "PCM_16", i16(1500, 1), 44100, None),
        ("pcm24_stereo.wav", "WAV", "PCM_24", i24(1200, 2), 48000, None),
        ("pcm24_mono_odd.wav", "WAV", "PCM_24", i24(1501, 1), 44100, None),
        ("float_mono.wav", "WAV", "FLOAT", f32(1500, 1), 44100, None),
        ("float_stereo.wav", "WAV", "FLOAT", f32(900, 2), 22050, None),
        ("u8_mono.wav", "WAV", "PCM_U8", i16(1501, 1), 44100, None),
        ("pcm32_stereo.wav", "WAV", "PCM_32", i32(800, 2), 44100, None),
        ("double_mono.wav", "WAV", "DOUBLE", f64(700, 1), 44100, None),
        ("wavex24_stereo.wav", "WAVEX", "PCM_24", i24(700, 2), 44100, None),
        ("aiff16_stereo.aif", "AIFF", "PCM_16", i16(1200, 2), 44100, None),
        ("aiff24_mono_odd.aif", "AIFF", "PCM_24", i24(1501, 1), 48000, None),
        ("aiff8_mono.aif", "AIFF", "PCM_S8", i16(1501, 1), 44100, None),
        ("aiff32_mono.aif", "AIFF", "PCM_32", i32(800, 1), 44100, None),
        ("aifc_sowt_stereo.aif", "AIFF", "PCM_16", i16(1000, 2), 44100, "LITTLE"),
        ("aifc_sowt24_mono.aif", "AIFF", "PCM_24", i24(800, 1), 44100, "LITTLE"),  # libsndfile: '42n1'
        ("aifc_le32_mono.aif", "AIFF", "PCM_32", i32(800, 1), 44100, "LITTLE"),     # libsndfile: '23ni'
        ("aifc_fl32_mono.aif", "AIFF", "FLOAT", f32(1000, 1), 44100, None),
        ("aifc_fl64_mono.aif", "AIFF", "DOUBLE", f64(600, 1), 44100, None),
    ]
    for name, fmt, subtype, data, sr, endian in specs:
        path = os.path.join(folder, name)
        try:
            kw = {"format": fmt, "subtype": subtype}
            if endian:
                kw["endian"] = endian
            sf.write(path, data, sr, **kw)
        except Exception as e:  # pragma: no cover - depends on libsndfile build
            skipped.append({"file": name, "reason": f"soundfile could not write: {e}"})
            continue
        results.append({"file": rel(path), "summary": summarize_file(path)})

    # libsndfile writes the AIFF-C float compression code as uppercase 'FL32'; patch a
    # lowercase 'fl32' copy (Apple's spelling) and check soundfile still reads it.
    src = os.path.join(folder, "aifc_fl32_mono.aif")
    with open(src, "rb") as f:
        raw = bytearray(f.read())
    idx = raw.find(b"FL32")
    raw[idx:idx + 4] = b"fl32"
    lower = os.path.join(folder, "aifc_fl32_lowercase_mono.aif")
    with open(lower, "wb") as f:
        f.write(raw)
    try:
        results.append({"file": rel(lower), "summary": summarize_file(lower)})
    except Exception as e:
        skipped.append({"file": "aifc_fl32_lowercase_mono.aif", "reason": f"soundfile could not read: {e}"})
        os.remove(lower)

    # WAV with an odd-sized LIST chunk (pad byte) before 'data'
    src = os.path.join(folder, "pcm16_mono.wav")
    with open(src, "rb") as f:
        raw = f.read()
    chunks = read_chunks(raw, big_endian=False)
    out = []
    for cid, size, body in chunks:
        if cid == b"data":
            out.append((b"LIST", 7, b"INFOabc"))
        out.append((cid, size, body))
    odd = os.path.join(folder, "pcm16_oddchunk.wav")
    with open(odd, "wb") as f:
        f.write(build_container(b"RIFF", b"WAVE", out, big_endian=False))
    results.append({"file": rel(odd), "summary": summarize_file(odd)})

    return results, skipped


def gen_og(expected):
    """OG OP-1 AIFF sets with custom 'regn' chunks."""
    rng = np.random.default_rng(7)
    base = os.path.join(FIX, "og")
    os.makedirs(base, exist_ok=True)

    def audio(n):
        return (rng.uniform(-0.5, 0.5, n) * 32767).astype(np.int16)

    regions_a1 = [(352800, 705600), (0, 352800), (705600, 1058400), (1 << 31, (1 << 31) + 352800)]
    regions_a2 = [(0, 100), (100, 200)]
    sets = {}

    # og_a: track_1 has an odd-sized APPL chunk then regn before SSND; track_2 has regn after SSND
    d = os.path.join(base, "og_a")
    os.makedirs(d, exist_ok=True)
    write_aiff_with_chunks(
        os.path.join(d, "track_1.aif"), audio(4000), 44100, "PCM_16",
        extra_before_ssnd=[(b"APPL", 5, b"op-1\x01"), (b"regn", 4 + 88 * 4, regn_body(regions_a1, rng))],
    )
    write_aiff_with_chunks(
        os.path.join(d, "track_2.aif"), audio(3000), 44100, "PCM_24",
        extra_after_ssnd=[(b"regn", 4 + 88 * 2, regn_body(regions_a2, rng))],
    )
    sets["og_a"] = d

    # og_b: track_1 regn declares 0 regions (-> None), track_2 regn has an odd size and
    # declares more regions than it holds; track_3 has no regn.
    d = os.path.join(base, "og_b")
    os.makedirs(d, exist_ok=True)
    write_aiff_with_chunks(
        os.path.join(d, "track_1.aif"), audio(2000), 44100, "PCM_16",
        extra_before_ssnd=[(b"regn", 4, regn_body([], rng))],
    )
    regions_b = [(585000, 1170000), (0, 585000), (1170000, 1755000)]
    body_b = regn_body(regions_b, rng, declared_count=5, trailing=b"\x7f")
    write_aiff_with_chunks(
        os.path.join(d, "track_2.aif"), audio(2500), 44100, "PCM_16",
        extra_before_ssnd=[(b"regn", len(body_b), body_b)],
    )
    write_aiff_with_chunks(os.path.join(d, "track_4.aif"), audio(1000), 44100, "PCM_16")
    sets["og_b"] = d

    # og_c: no regn anywhere -> single region spanning the first file's frames
    d = os.path.join(base, "og_c")
    os.makedirs(d, exist_ok=True)
    write_aiff_with_chunks(os.path.join(d, "track_2.aif"), audio(3333), 44100, "PCM_16")
    write_aiff_with_chunks(os.path.join(d, "track_3.aif"), audio(1111), 44100, "PCM_16")
    sets["og_c"] = d

    # Malformed single files for parse_aiff_regions
    misc = os.path.join(base, "misc")
    os.makedirs(misc, exist_ok=True)
    with open(os.path.join(misc, "not_form.aif"), "wb") as f:
        f.write(b"RIFF\0\0\0\0WAVEjunkjunk")
    with open(os.path.join(misc, "wrong_type.aif"), "wb") as f:
        f.write(b"FORM\0\0\0\x10WAVEjunk\0\0\0\0")
    with open(os.path.join(misc, "tiny.aif"), "wb") as f:
        f.write(b"FORM\0\0")
    # truncated regn: declares 3 regions / 268 bytes, file ends after ~1.5 entries
    body = regn_body([(10, 20), (5, 6), (30, 40)], rng)
    trunc = build_container(b"FORM", b"AIFF", [(b"COMM", 18, b"\0" * 18)], big_endian=True)
    trunc += b"regn" + struct.pack(">I", len(body)) + body[: 4 + 88 + 40]
    with open(os.path.join(misc, "truncated_regn.aif"), "wb") as f:
        f.write(trunc)
    # AIFC with regn
    write_aiff_with_chunks(
        os.path.join(misc, "aifc_regn.aif"), audio(500).astype(np.float32) / 32768, 44100, "FLOAT",
        extra_before_ssnd=[(b"regn", 4 + 88, regn_body([(123, 456)], rng))],
    )

    # parse_aiff_regions on every file
    region_results = []
    for root, _, files in sorted(os.walk(base)):
        for name in sorted(files):
            path = os.path.join(root, name)
            md = parse_aiff_regions(path)
            region_results.append({
                "file": rel(path),
                "result": None if md is None else {
                    "regions": [[r.start_sample, r.end_sample] for r in md.regions],
                    "sample_rate": md.sample_rate,
                },
            })
    expected["aiff_regions"] = region_results

    # parse_regn_chunk directly on raw bytes
    regn_cases = []
    for label, raw in [
        ("empty", b""),
        ("short", b"\0\0\0"),
        ("zero_count", struct.pack(">I", 0) + b"\0" * 88),
        ("unsorted", regn_body([(500, 600), (100, 200), (300, 400), (100, 150)], rng)),
        ("overclaim", regn_body([(1, 2)], rng, declared_count=9)),
        ("big", regn_body([(0xFFFFFFFF, 0x80000000)], rng)),
    ]:
        regn_cases.append({
            "label": label,
            "hex": raw.hex(),
            "result": [[r.start_sample, r.end_sample] for r in _parse_regn_chunk(raw)],
        })
    expected["regn_chunks"] = regn_cases

    # parse_aiff_tape_session + detect_loops_from_aiff_regions per set
    sessions = {}
    for key, folder in sets.items():
        s = parse_aiff_tape_session(folder)
        loops = detect_loops_from_aiff_regions(s)
        sessions[key] = {
            "folder": rel(folder),
            "files": sorted(os.listdir(folder)),
            "session": ser_session(s),
            "loops": [ser_loop(lp) for lp in loops],
        }
    expected["og_sessions"] = sessions


def gen_bpm_estimates():
    cases = [
        [],
        [(0, 352800)],                                   # 4 bars @ 120
        [(0, 585000)],                                   # 72.369 -> round(…, 1) = 72.4
        [(0, 124518)],                                   # 2-bar path -> 170
        [(0, 2400000)],                                  # 16-bar path -> 70.56 -> 70.6
        [(0, 200000)],                                   # 2-bar path, 105.84 -> 110
        [(0, 403200)],                                   # exactly 105.0: 100/110 tie -> min picks 100, |5| not < 5 -> 105.0
        [(0, 313600)],                                   # exactly 135.0 -> round path
        [(0, 220000)],                                   # 192.436 -> 192.4
        [(0, 10000)],                                    # nothing in 60..200 -> 120
        [(0, 5000000)],                                  # nothing in 60..200 -> 120
        [(10, 5)],                                       # negative length -> 120
        [(0, 400000), (0, 300000), (0, 585000)],         # odd count: median = sorted[1]
        [(0, 352800), (0, 585000), (0, 100), (0, 220000)],  # even count: sorted[2]
        [(0, 0)],                                        # zero length: Python raises
        [(0, 529200)],                                   # 80.0 exact
        [(0, 498071)],                                   # 84.99… (closest 80? no: 85 not common) -> 85.0
        [(0, 581539)],                                   # 72.80 region
    ]
    out = []
    for regs in cases:
        rs = [AiffRegion(start_sample=a, end_sample=b) for a, b in regs]
        try:
            out.append({"regions": [list(r) for r in regs], "result": _estimate_bpm_from_regions(rs)})
        except Exception as e:
            out.append({"regions": [list(r) for r in regs], "error": f"{type(e).__name__}: {e}"})
    return out


def gen_clusters():
    cases = [
        ([], 4410),
        ([(100, 2)], 4410),
        ([(5, 0), (5, 1), (5, 2), (5, 3)], 4410),
        ([(1000, 0), (1001, 1)], 4410),               # 1000.5 -> 1000
        ([(1001, 0), (1002, 1)], 4410),               # 1001.5 -> 1002
        ([(0, 0), (4410, 1)], 4410),                  # exactly at tolerance -> joins
        ([(0, 0), (4411, 1)], 4410),                  # just past -> splits
        ([(0, 0), (4400, 1), (8800, 2), (13200, 3)], 4410),  # running-center drift
        ([(0, 0), (3000, 1), (6000, 2), (9000, 3)], 4410),   # center creeps: 0,1500,3000 -> 9000 splits?
        ([(900, 3), (100, 0), (500, 2), (100, 1)], 4410),    # unsorted, tie order preserved
        ([(0, 0), (1, 0), (2, 0)], 4410),             # duplicate channel
        ([(10, 0), (10, 1), (11, 2)], 0),             # zero tolerance
        ([(10, 0), (12, 1)], -5),                     # negative tolerance
        ([(0, 0), (100, 1), (200, 2)], 2205),         # spread/confidence
        ([(2147483000, 0), (2147483001, 1), (2147483647, 2)], 4410),
        ([(7, 1), (8, 2), (8, 3), (9, 0), (10, 0), (11, 1)], 4410),  # mean 8.833 -> 9
        ([(1, 0), (2, 1), (2, 2), (3, 3)], 4410),     # mean exactly 2.0
        ([(0, 0), (1, 1), (2, 2), (3, 3)], 4410),     # mean 1.5 -> 2
        ([(0, 0), (1, 1), (1, 2), (3, 3)], 4410),     # mean 1.25
    ]
    out = []
    for positions, tol in cases:
        res = cluster_positions(list(positions), tol)
        out.append({
            "positions": [list(p) for p in positions],
            "tolerance": tol,
            "result": [ser_boundary(b) for b in res],
        })
    return out


def gen_tape_detection(path):
    s = parse_tape_json(path)
    out = {"tape_file": rel(path), "session": ser_session(s), "boundaries": {}, "loops": {}}
    for min_tracks in (1, 2):
        for tol in (100, 50, 0.5):
            key = f"min{min_tracks}_tol{tol}"
            out["boundaries"][key] = [ser_boundary(b) for b in detect_boundaries(s, min_tracks, tol)]
    for min_tracks in (1, 2):
        bounds = detect_boundaries(s, min_tracks)
        for min_bars in (1.0, 0.5, 0.0, 2.5):
            key = f"min{min_tracks}_bars{min_bars}"
            out["loops"][key] = [ser_loop(lp) for lp in detect_loops(s, bounds, min_bars)]
    # splits of the loops from min_tracks=1, min_bars=0.5
    bounds = detect_boundaries(s, 1)
    loops = detect_loops(s, bounds, 0.5)
    splits = []
    for lp in loops:
        for bars in (1, 2, 4):
            splits.append({
                "loop_index": lp.index,
                "bars": bars,
                "result": [ser_loop(c) for c in split_loop_by_bars(lp, bars)],
            })
    out["splits"] = splits
    out["splits_from"] = "min1_bars0.5"
    # track activity over a spread of ranges
    ends = sorted({c.start for c in s.clips} | {c.stop for c in s.clips})
    ranges = [(0, 1), (0, 0), (ends[-1], ends[-1] + 10), (-100, 0)]
    for a, b in zip(ends, ends[1:]):
        ranges.append((a, b))
        ranges.append((a + 1, b - 1))
    act = []
    for a, b in ranges:
        r = get_track_activity(s, a, b)
        act.append({"start": a, "end": b, "result": [r[0], r[1], r[2], r[3]]})
    out["activity"] = act
    return s, out


def gen_audio_and_export(folder, session):
    loader = AudioLoader(folder)
    bounds = detect_boundaries(session, 1)
    loops = detect_loops(session, bounds, 0.5)
    result = {"folder": rel(folder), "loops": [loop_def(lp) for lp in loops], "slices": [], "fades": [], "exports": []}

    # slice_loop + create_mix + apply_zero_fade per detected loop
    for lp in loops:
        slices = slice_loop(loader, lp, session)
        entry = {"loop": loop_def(lp), "tracks": {}, "mix": None, "mix_normalized": None}
        for tn, arr in slices.items():
            entry["tracks"][str(tn)] = {"slice": summarize(arr), "fade": summarize(apply_zero_fade(arr))}
        mix = create_mix(slices)
        if mix is not None:
            # Did normalization run? Re-sum without it.
            first = next(iter(slices.values()))
            raw = np.zeros_like(first)
            for a in slices.values():
                n = min(len(raw), len(a))
                raw[:n] += a[:n]
            entry["mix_normalized"] = bool(np.max(np.abs(raw)) > 0.99)
            entry["mix_raw_peak"] = float(np.max(np.abs(raw)))
            entry["mix"] = summarize(mix)
            entry["mix_fade"] = summarize(apply_zero_fade(mix))
        result["slices"].append(entry)

    # apply_zero_fade short-length cases on prefixes of loop 1's track 1 (stereo) and track 2 (mono)
    slices1 = slice_loop(loader, loops[0], session)
    for tn in (1, 2):
        for n, fade in [(1000, 882), (5, 882), (3, 882), (8, 2), (1764, 882), (1763, 882), (100, 1), (4, 882), (0, 882)]:
            src = slices1[tn][:n]
            out = apply_zero_fade(src, fade)
            result["fades"].append({
                "track": tn, "length": n, "fade_samples": fade,
                "result": summarize(out) if n else {"frames": 0, "channels": int(src.shape[1])},
            })

    # create_mix with a mono slice first and a stereo slice second: numpy can't broadcast
    # (L,2) into the (L,1) accumulator, so Python raises.
    try:
        create_mix({2: slices1[2], 1: slices1[1]})
        result["mix_mono_first"] = {"error": None}
    except Exception as e:
        result["mix_mono_first"] = {"error": f"{type(e).__name__}: {e}"}

    # export_loop
    tmp = tempfile.mkdtemp(prefix="op1_parity_")
    try:
        cases = []
        for lp in loops:
            cases.append((lp, dict(include_mix=True, include_metadata=True, zero_fade=True), None))
        cases.append((loops[1], dict(include_mix=False, include_metadata=True, zero_fade=False), None))
        cases.append((loops[2], dict(include_mix=True, include_metadata=False, zero_fade=False), "plain_l3"))
        cases.append((loops[1], dict(include_mix=True, include_metadata=True, zero_fade=True, custom_folder_name="Café Loop"), None))
        # custom loops: short (fade shrinks to len // 4), past the end of every track (no slices)
        b = LoopBoundary(position=30000, tracks_starting={0, 1}, confidence=0.4567891, raw_positions=[30000])
        cases.append((DetectedLoop(7, 30000, 31000, b, None, session.bpm), dict(zero_fade=True), None))
        b = LoopBoundary(position=59000, tracks_starting={1}, confidence=0.0005, raw_positions=[59000])
        cases.append((DetectedLoop(8, 59000, 60000, b, None, session.bpm), dict(zero_fade=True), None))
        # a split chunk with a custom_name attribute
        chunk = split_loop_by_bars(loops[1], 1)[1]
        chunk.custom_name = "chunk two"
        cases.append((chunk, dict(zero_fade=True), None))

        for i, (lp, opts, custom) in enumerate(cases):
            outdir = os.path.join(tmp, f"case{i}")
            os.makedirs(outdir)
            if custom:
                opts = dict(opts, custom_folder_name=custom)
            folder_path = export_loop(loader, lp, session, outdir, **opts)
            files = []
            for name in sorted(os.listdir(folder_path)):
                p = os.path.join(folder_path, name)
                if name.endswith(".wav"):
                    files.append({"name": name, "summary": summarize_file(p)})
                else:
                    with open(p, "r", encoding="utf-8") as f:
                        files.append({"name": name, "text": f.read()})
            cases_opts = {k: v for k, v in opts.items()}
            result["exports"].append({
                "loop": loop_def(lp, getattr(lp, "custom_name", None)),
                "options": cases_opts,
                "folder_name": os.path.basename(folder_path),
                "files": files,
            })
    finally:
        shutil.rmtree(tmp)
    return result


def gen_detect_format():
    cases = [
        ["tape.json", "track_1.wav"],
        ["tape.json"],
        ["track_2.aif"],
        ["tape.json", "track_3.aif"],
        ["track_1.wav"],
        ["track_1.aiff"],
        [],
        ["tape.json", "track_4.wav", "track_1.aif"],
        ["tape.json", "track_5.wav"],
    ]
    out = []
    for names in cases:
        d = tempfile.mkdtemp(prefix="op1_fmt_")
        try:
            for n in names:
                open(os.path.join(d, n), "w").close()
            out.append({"files": names, "result": detect_tape_format(d)})
        finally:
            shutil.rmtree(d)
    return out


def gen_pyround():
    """Python's round() on values the port must reproduce (export metadata uses 1-3 digits)."""
    xs = [0.35, 2.675, 1.005, 0.125, 0.375, 0.5, 1.5, 2.5, 0.045, 5.395, 3.705, 0.925, 0.525,
          123.456, 72.369, 0.0005, 0.9995, 1.0, 0.0, 16318 / 17640, 0.4567891, 85.05, 85.15]
    out = []
    for x in xs:
        for n in (1, 2, 3):
            out.append({"x": x, "n": n, "result": round(x, n)})
    halves = []
    for x in [0.5, 1.5, 2.5, 3.5, -0.5, -1.5, -2.5, 2.4999999999999996, 1000.5, 171402.5, 0.49999999999999994]:
        halves.append({"x": x, "result": round(x)})
    return out, halves


def main():
    if os.path.isdir(FIX):
        shutil.rmtree(FIX)
    os.makedirs(FIX)
    expected = {
        "generator": "web/scripts/gen_fixtures.py",
        "numpy": np.__version__,
        "soundfile": sf.__version__,
        "libsndfile": sf.__libsndfile_version__,
        "sample_rate": core.SAMPLE_RATE,
    }

    field_path = gen_field_tape()
    _, expected["field"] = gen_tape_detection(field_path)

    export_folder = gen_export_tape()
    export_session, expected["export_tape"] = gen_tape_detection(os.path.join(export_folder, "tape.json"))
    expected["audio"] = gen_audio_and_export(export_folder, export_session)

    expected["cluster"] = gen_clusters()
    expected["decode"], expected["decode_skipped"] = gen_decode_fixtures()
    gen_og(expected)
    expected["bpm_estimates"] = gen_bpm_estimates()
    expected["detect_format"] = gen_detect_format()
    expected["pyround"], expected["round_half_even"] = gen_pyround()

    with open(os.path.join(FIX, "expected.json"), "w", encoding="utf-8") as f:
        json.dump(expected, f, indent=1)
        f.write("\n")

    total = 0
    for root, _, files in os.walk(FIX):
        for n in files:
            total += os.path.getsize(os.path.join(root, n))
    print(f"fixtures written to {FIX} ({total / 1024:.0f} KiB)")
    for s in expected["decode_skipped"]:
        print("skipped:", s)


if __name__ == "__main__":
    main()
