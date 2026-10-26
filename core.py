"""
OP-1 Loop Maker - Core Logic
Parses tape.json (OP-1 Field) and AIFF metadata (OG OP-1), detects loop boundaries, slices audio.
"""

import os
import json
import struct
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
import numpy as np
import soundfile as sf
from PySide6.QtCore import QIODevice
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices, QAudioDevice


def get_audio_output_devices() -> list[QAudioDevice]:
    """Get a list of available audio output devices."""
    return QMediaDevices.audioOutputs()


def get_default_audio_output_device() -> QAudioDevice:
    """Get the system default audio output device."""
    return QMediaDevices.defaultAudioOutput()

SAMPLE_RATE = 44100
TRACK_NAMES = ["track_1.wav", "track_2.wav", "track_3.wav", "track_4.wav"]
AIFF_TRACK_NAMES = ["track_1.aif", "track_2.aif", "track_3.aif", "track_4.aif"]


# =============================================================================
# AIFF Parsing for Original OP-1
# =============================================================================

@dataclass
class AiffRegion:
    """A single region from AIFF regn chunk"""
    start_sample: int
    end_sample: int


@dataclass
class AiffTapeMetadata:
    """Metadata extracted from an OP-1 AIFF file"""
    regions: List[AiffRegion]
    sample_rate: int = SAMPLE_RATE


def parse_aiff_regions(filepath: str) -> Optional[AiffTapeMetadata]:
    """
    Parse an OP-1 AIFF file to extract region markers from the 'regn' chunk.
    
    The OP-1 stores loop region data in a custom 'regn' chunk within AIFF files.
    Each region entry is 88 bytes with start sample at bytes 20-23 and end at 28-31.
    """
    try:
        with open(filepath, 'rb') as f:
            # Read and verify FORM header
            form_header = f.read(4)
            if form_header != b'FORM':
                return None
            
            # Skip file size (4 bytes)
            f.read(4)
            
            # Read and verify AIFF/AIFC format tag
            format_tag = f.read(4)
            if format_tag not in (b'AIFF', b'AIFC'):
                return None
            
            regn_data = None
            sample_rate = SAMPLE_RATE
            
            # Parse chunks
            while True:
                chunk_header = f.read(4)
                if len(chunk_header) < 4:
                    break  # EOF
                
                chunk_id = chunk_header.decode('ascii', errors='ignore')
                
                # Read chunk size (big-endian)
                size_bytes = f.read(4)
                if len(size_bytes) < 4:
                    break
                chunk_size = struct.unpack('>I', size_bytes)[0]
                
                if chunk_id == 'regn':
                    # Read region chunk data
                    regn_data = f.read(chunk_size)
                elif chunk_id == 'COMM':
                    # Common chunk - extract sample rate
                    comm_data = f.read(chunk_size)
                    if len(comm_data) >= 18:
                        # Sample rate is stored as 80-bit extended float at offset 8
                        # For simplicity, we'll use the standard 44100 for OP-1
                        # The extended float parsing is complex, OP-1 always uses 44100
                        pass
                else:
                    # Skip other chunks
                    f.seek(chunk_size, 1)
                
                # AIFF chunks are padded to even byte boundaries
                if chunk_size % 2 == 1:
                    f.read(1)
                
                # Stop if we found regn (regions is what we need)
                if regn_data is not None:
                    break
            
            if regn_data is None:
                return None
            
            # Parse region data
            regions = _parse_regn_chunk(regn_data)
            
            if not regions:
                return None
            
            return AiffTapeMetadata(regions=regions, sample_rate=sample_rate)
            
    except Exception as e:
        print(f"Error parsing AIFF file {filepath}: {e}")
        return None


def _parse_regn_chunk(data: bytes) -> List[AiffRegion]:
    """
    Parse the regn chunk data to extract regions.
    
    Format:
    - First 4 bytes: region count (big-endian int)
    - Then N regions, each 88 bytes:
      - Bytes 20-23: start sample (big-endian)
      - Bytes 28-31: end sample (big-endian)
    """
    if len(data) < 4:
        return []
    
    region_count = struct.unpack('>I', data[0:4])[0]
    regions = []
    
    offset = 4
    for i in range(region_count):
        if offset + 88 > len(data):
            break
        
        region_data = data[offset:offset + 88]
        
        # Start sample at bytes 20-23, end at bytes 28-31
        start_sample = struct.unpack('>I', region_data[20:24])[0]
        end_sample = struct.unpack('>I', region_data[28:32])[0]
        
        regions.append(AiffRegion(start_sample=start_sample, end_sample=end_sample))
        offset += 88
    
    # Sort by start position
    regions.sort(key=lambda r: r.start_sample)
    
    return regions


def detect_tape_format(folder: str) -> str:
    """
    Detect whether a folder contains OP-1 Field (tape.json + WAV) or OG OP-1 (AIFF) format.
    
    Returns:
        'field' - OP-1 Field format (tape.json + WAV files)
        'og' - Original OP-1 format (AIFF files with embedded metadata)
        'unknown' - Neither format detected
    """
    tape_json = os.path.join(folder, 'tape.json')
    
    # Check for OP-1 Field format first
    if os.path.exists(tape_json):
        # Verify at least one WAV file exists
        for name in TRACK_NAMES:
            if os.path.exists(os.path.join(folder, name)):
                return 'field'
    
    # Check for OG OP-1 format (AIFF files)
    for name in AIFF_TRACK_NAMES:
        if os.path.exists(os.path.join(folder, name)):
            return 'og'
    
    return 'unknown'


def parse_aiff_tape_session(folder: str) -> Optional['TapeSession']:
    """
    Parse OG OP-1 AIFF files to create a TapeSession compatible with the existing workflow.
    
    Extracts region markers from AIFF files and creates clips/session data.
    BPM detection is tricky for AIFF - we'll try to estimate from region lengths.
    """
    # Find all AIFF track files
    aiff_files = []
    for i, name in enumerate(AIFF_TRACK_NAMES):
        path = os.path.join(folder, name)
        if os.path.exists(path):
            aiff_files.append((i, path))
    
    if not aiff_files:
        return None
    
    # Parse regions from the first available AIFF file
    # (All tracks share the same tape regions on OP-1)
    all_regions = []
    for channel, path in aiff_files:
        metadata = parse_aiff_regions(path)
        if metadata and metadata.regions:
            all_regions = metadata.regions
            break
    
    if not all_regions:
        # No regions found - create a default full-tape region
        # Get audio length from first file
        for channel, path in aiff_files:
            try:
                info = sf.info(path)
                all_regions = [AiffRegion(start_sample=0, end_sample=int(info.frames))]
                break
            except:
                pass
    
    # Create clips from regions - each region becomes a clip on each track
    clips = []
    clip_id = 0
    for region in all_regions:
        for channel, path in aiff_files:
            clips.append(Clip(
                channel=channel,
                clip_id=clip_id,
                start=region.start_sample,
                stop=region.end_sample
            ))
            clip_id += 1
    
    # Estimate BPM from region lengths (default to 120 if we can't determine)
    bpm = _estimate_bpm_from_regions(all_regions)
    
    # Calculate loop_in/loop_out from first and last regions
    loop_in = all_regions[0].start_sample if all_regions else 0
    loop_out = all_regions[-1].end_sample if all_regions else 0
    
    return TapeSession(
        clips=clips,
        bpm=bpm,
        loop_in=loop_in,
        loop_out=loop_out,
        tape_type=1,  # OG OP-1 indicator
        is_aiff_source=True  # Flag to indicate AIFF source
    )


def _estimate_bpm_from_regions(regions: List[AiffRegion]) -> float:
    """
    Attempt to estimate BPM from region lengths.
    
    Common bar lengths at different BPMs (for 4 bars at 44100 Hz):
    - 120 BPM: 352800 samples (4 bars)
    - 90 BPM: 470400 samples
    - 140 BPM: 302400 samples
    
    Returns 120 BPM as default if estimation fails.
    """
    if not regions:
        return 120.0
    
    # Get most common region length
    lengths = [r.end_sample - r.start_sample for r in regions]
    
    if not lengths:
        return 120.0
    
    # Use median length for estimation
    median_length = sorted(lengths)[len(lengths) // 2]
    
    # Common bar counts to check
    for bars in [4, 8, 2, 1, 16]:
        # samples_per_bar = SAMPLE_RATE * 60 / BPM * 4
        # median_length = bars * samples_per_bar
        # BPM = (SAMPLE_RATE * 60 * 4 * bars) / median_length
        estimated_bpm = (SAMPLE_RATE * 60.0 * 4 * bars) / median_length
        
        # Check if this gives a reasonable BPM (60-200)
        if 60 <= estimated_bpm <= 200:
            # Round to common BPM values
            common_bpms = [80, 90, 100, 110, 120, 125, 130, 140, 150, 160, 170, 180]
            closest = min(common_bpms, key=lambda x: abs(x - estimated_bpm))
            if abs(closest - estimated_bpm) < 5:
                return float(closest)
            return round(estimated_bpm, 1)
    
    return 120.0


def detect_loops_from_aiff_regions(session: 'TapeSession') -> List['DetectedLoop']:
    """
    Create DetectedLoop objects directly from AIFF region data.
    
    For AIFF sessions, the regions are already defined in the file metadata,
    so we use them directly instead of inferring from clip boundaries.
    """
    if not session.is_aiff_source:
        return []
    
    # Group clips by their start/stop to find unique regions
    region_map = {}
    for clip in session.clips:
        key = (clip.start, clip.stop)
        if key not in region_map:
            region_map[key] = set()
        region_map[key].add(clip.channel)
    
    # Sort regions by start position
    sorted_regions = sorted(region_map.items(), key=lambda x: x[0][0])
    
    loops = []
    for i, ((start, stop), channels) in enumerate(sorted_regions):
        boundary = LoopBoundary(
            position=start,
            tracks_starting=channels,
            confidence=1.0,  # AIFF regions are exact
            raw_positions=[start]
        )
        
        loop = DetectedLoop(
            index=i + 1,
            start_sample=start,
            end_sample=stop,
            start_boundary=boundary,
            end_boundary=None,
            bpm=session.bpm
        )
        loops.append(loop)
    
    return loops


# =============================================================================
# Original OP-1 Field Data Structures
# =============================================================================


@dataclass
class Clip:
    """A single clip from tape.json"""
    channel: int  # 0-3
    clip_id: int
    start: int    # sample position
    stop: int     # sample position
    
    @property
    def length(self) -> int:
        return self.stop - self.start
    
    @property
    def track_num(self) -> int:
        return self.channel + 1


@dataclass
class TapeSession:
    """Parsed OP-1 tape session"""
    clips: list[Clip]
    bpm: float
    loop_in: int
    loop_out: int
    tape_type: int
    is_aiff_source: bool = False  # True for OG OP-1 (AIFF), False for Field (WAV)
    
    @property
    def samples_per_beat(self) -> float:
        return SAMPLE_RATE * 60.0 / self.bpm
    
    @property
    def samples_per_bar(self) -> float:
        return self.samples_per_beat * 4
    
    def position_to_bars(self, samples: int) -> float:
        return samples / self.samples_per_bar
    
    def bars_to_samples(self, bars: float) -> int:
        return int(bars * self.samples_per_bar)


@dataclass
class LoopBoundary:
    """A detected boundary where tracks align"""
    position: int
    tracks_starting: set
    confidence: float
    raw_positions: list
    
    @property
    def track_count(self) -> int:
        return len(self.tracks_starting)


@dataclass 
class DetectedLoop:
    """A loop region between two boundaries"""
    index: int
    start_sample: int
    end_sample: int
    start_boundary: LoopBoundary
    end_boundary: Optional[LoopBoundary]
    bpm: float
    custom_name: str = None
    
    @property
    def length_samples(self) -> int:
        return self.end_sample - self.start_sample
    
    @property
    def length_seconds(self) -> float:
        return self.length_samples / SAMPLE_RATE
    
    @property
    def length_bars(self) -> float:
        spb = SAMPLE_RATE * 60.0 / self.bpm
        return self.length_samples / (spb * 4)
    
    @property
    def tracks_active(self) -> set:
        return self.start_boundary.tracks_starting
    
    def time_str(self) -> str:
        secs = self.length_seconds
        return f"{int(secs // 60)}:{int(secs % 60):02d}"


def parse_tape_json(path: str) -> TapeSession:
    """Parse tape.json into structured data"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    clips = []
    for c in data.get("clips", []):
        clips.append(Clip(
            channel=c["ch"],
            clip_id=c["id"],
            start=c["start"],
            stop=c["stop"]
        ))
    
    bpm_raw = data.get("tempobpmdecimal", 1200)
    bpm = bpm_raw / 10.0
    
    return TapeSession(
        clips=clips,
        bpm=bpm,
        loop_in=data.get("loop_in", 0),
        loop_out=data.get("loop_out", 0),
        tape_type=data.get("tape_type", 0)
    )


def cluster_positions(positions: list[tuple[int, int]], tolerance: int = 4410) -> list[LoopBoundary]:
    """Cluster clip start positions within tolerance."""
    if not positions:
        return []
    
    sorted_pos = sorted(positions, key=lambda x: x[0])
    clusters = []
    current_cluster = [sorted_pos[0]]
    
    for pos, ch in sorted_pos[1:]:
        cluster_center = sum(p for p, _ in current_cluster) / len(current_cluster)
        if abs(pos - cluster_center) <= tolerance:
            current_cluster.append((pos, ch))
        else:
            clusters.append(current_cluster)
            current_cluster = [(pos, ch)]
    
    clusters.append(current_cluster)
    
    boundaries = []
    for cluster in clusters:
        positions_only = [p for p, _ in cluster]
        channels = {ch for _, ch in cluster}
        center = int(round(sum(positions_only) / len(positions_only)))
        spread = max(positions_only) - min(positions_only) if len(positions_only) > 1 else 0
        confidence = 1.0 - min(spread / tolerance, 1.0) if tolerance > 0 else 1.0
        
        boundaries.append(LoopBoundary(
            position=center,
            tracks_starting=channels,
            confidence=confidence,
            raw_positions=positions_only
        ))
    
    return sorted(boundaries, key=lambda b: b.position)


def detect_boundaries(session: TapeSession, min_tracks: int = 1, 
                      tolerance_ms: float = 100) -> list[LoopBoundary]:
    """Detect loop boundaries from clip start positions."""
    tolerance_samples = int(tolerance_ms * SAMPLE_RATE / 1000)
    positions = [(clip.start, clip.channel) for clip in session.clips]
    boundaries = cluster_positions(positions, tolerance_samples)
    return [b for b in boundaries if b.track_count >= min_tracks]


def detect_loops(session: TapeSession, boundaries: list[LoopBoundary],
                 min_bars: float = 1.0) -> list[DetectedLoop]:
    """Convert boundaries into loop regions."""
    if not boundaries:
        return []
    
    min_samples = int(min_bars * session.samples_per_bar)
    loops = []
    
    for i, boundary in enumerate(boundaries):
        if i + 1 < len(boundaries):
            end_sample = boundaries[i + 1].position
            end_boundary = boundaries[i + 1]
        else:
            max_end = max(c.stop for c in session.clips)
            end_sample = max_end
            end_boundary = None
        
        length = end_sample - boundary.position
        if length < min_samples:
            continue
        
        loops.append(DetectedLoop(
            index=len(loops) + 1,
            start_sample=boundary.position,
            end_sample=end_sample,
            start_boundary=boundary,
            end_boundary=end_boundary,
            bpm=session.bpm
        ))
    
    return loops


def split_loop_by_bars(loop: DetectedLoop, bar_count: int) -> list[DetectedLoop]:
    """Split a loop into smaller chunks of N bars each."""
    samples_per_bar = SAMPLE_RATE * 60.0 / loop.bpm * 4
    chunk_samples = int(bar_count * samples_per_bar)
    
    chunks = []
    pos = loop.start_sample
    idx = 1
    
    while pos < loop.end_sample:
        end = min(pos + chunk_samples, loop.end_sample)
        
        chunk_boundary = LoopBoundary(
            position=pos,
            tracks_starting=loop.tracks_active,
            confidence=1.0,
            raw_positions=[pos]
        )
        
        chunks.append(DetectedLoop(
            index=idx,
            start_sample=pos,
            end_sample=end,
            start_boundary=chunk_boundary,
            end_boundary=None,
            bpm=loop.bpm
        ))
        
        pos = end
        idx += 1
    
    return chunks


def get_track_activity(session: TapeSession, start: int, end: int) -> dict[int, bool]:
    """Check which tracks have audio content in the given range."""
    activity = {0: False, 1: False, 2: False, 3: False}
    for clip in session.clips:
        if clip.stop > start and clip.start < end:
            activity[clip.channel] = True
    return activity


class AudioLoader:
    """Lazy-loads and caches track audio from WAV or AIFF files"""
    
    def __init__(self, folder: str, is_aiff: bool = False):
        self.folder = folder
        self.is_aiff = is_aiff
        self._cache: dict[int, np.ndarray] = {}
        self._sample_rate: Optional[int] = None
    
    def _get_track_path(self, track_num: int) -> Optional[str]:
        """Get the path to a track file, checking both WAV and AIFF extensions"""
        if self.is_aiff:
            # Try AIFF first for OG OP-1
            aiff_path = os.path.join(self.folder, f"track_{track_num}.aif")
            if os.path.exists(aiff_path):
                return aiff_path
            # Also try .aiff extension
            aiff_path2 = os.path.join(self.folder, f"track_{track_num}.aiff")
            if os.path.exists(aiff_path2):
                return aiff_path2
        
        # Try WAV (for Field or as fallback)
        wav_path = os.path.join(self.folder, f"track_{track_num}.wav")
        if os.path.exists(wav_path):
            return wav_path
        
        return None
    
    def load_track(self, track_num: int) -> Optional[np.ndarray]:
        if track_num in self._cache:
            return self._cache[track_num]
        
        path = self._get_track_path(track_num)
        if path is None:
            return None
        
        audio, sr = sf.read(path, dtype='float32')
        self._sample_rate = sr
        
        if audio.ndim == 1:
            audio = audio.reshape(-1, 1)
        
        self._cache[track_num] = audio
        return audio
    
    def get_sample_rate(self) -> int:
        if self._sample_rate is None:
            for i in range(1, 5):
                if self.load_track(i) is not None:
                    break
        return self._sample_rate or SAMPLE_RATE
    
    def get_track_length(self, track_num: int) -> int:
        audio = self.load_track(track_num)
        return len(audio) if audio is not None else 0
    
    def get_max_length(self) -> int:
        return max(self.get_track_length(i) for i in range(1, 5))


def slice_loop(loader: AudioLoader, loop: DetectedLoop, 
               session: TapeSession) -> dict[int, np.ndarray]:
    """Slice audio for a loop region."""
    activity = get_track_activity(session, loop.start_sample, loop.end_sample)
    slices = {}
    
    for track_num in range(1, 5):
        channel = track_num - 1
        if not activity[channel]:
            continue
        
        audio = loader.load_track(track_num)
        if audio is None:
            continue
        
        start = loop.start_sample
        end = min(loop.end_sample, len(audio))
        
        if start >= len(audio):
            continue
        
        sliced = audio[start:end]
        target_length = loop.end_sample - loop.start_sample
        if len(sliced) < target_length:
            padding = np.zeros((target_length - len(sliced), sliced.shape[1]), dtype=sliced.dtype)
            sliced = np.vstack([sliced, padding])
        
        slices[track_num] = sliced
    
    return slices


def create_mix(slices: dict[int, np.ndarray]) -> Optional[np.ndarray]:
    """Sum all track slices into a mix"""
    if not slices:
        return None
    
    first = next(iter(slices.values()))
    mix = np.zeros_like(first)
    
    for audio in slices.values():
        min_len = min(len(mix), len(audio))
        mix[:min_len] += audio[:min_len]
    
    peak = np.max(np.abs(mix))
    if peak > 0.99:
        mix = mix * (0.99 / peak)
    
    return mix


def apply_zero_fade(audio: np.ndarray, fade_samples: int = 882) -> np.ndarray:
    """Apply short fade in/out to ensure audio starts and ends at zero.
    
    Args:
        audio: Audio array (samples x channels)
        fade_samples: Number of samples for fade (default 882 = ~20ms at 44100Hz)
    
    Returns:
        Audio with fade applied
    """
    if len(audio) < fade_samples * 2:
        fade_samples = len(audio) // 4
    
    if fade_samples < 1:
        return audio
    
    audio = audio.copy()
    
    # Create fade curves
    fade_in = np.linspace(0, 1, fade_samples).reshape(-1, 1)
    fade_out = np.linspace(1, 0, fade_samples).reshape(-1, 1)
    
    # Apply fades
    audio[:fade_samples] *= fade_in
    audio[-fade_samples:] *= fade_out
    
    return audio


def export_loop(loader: AudioLoader, loop: DetectedLoop, session: TapeSession,
                output_dir: str, include_mix: bool = True, 
                include_metadata: bool = True,
                custom_folder_name: Optional[str] = None,
                zero_fade: bool = False) -> str:
    """Export a single loop to a folder."""
    folder_name = custom_folder_name or getattr(loop, 'custom_name', None) or f"L{loop.index:03d}"
    loop_folder = os.path.join(output_dir, folder_name)
    os.makedirs(loop_folder, exist_ok=True)
    
    slices = slice_loop(loader, loop, session)
    sr = loader.get_sample_rate()
    
    # Calculate fade samples (~20ms)
    fade_samples = int(sr * 0.020) if zero_fade else 0
    
    for track_num, audio in slices.items():
        if zero_fade:
            audio = apply_zero_fade(audio, fade_samples)
        path = os.path.join(loop_folder, f"track_{track_num}.wav")
        sf.write(path, audio, sr, subtype='FLOAT')
    
    if include_mix and slices:
        mix = create_mix(slices)
        if mix is not None:
            if zero_fade:
                mix = apply_zero_fade(mix, fade_samples)
            mix_path = os.path.join(loop_folder, "mix.wav")
            sf.write(mix_path, mix, sr, subtype='FLOAT')
    
    if include_metadata:
        metadata = {
            "name": folder_name,
            "loop_index": loop.index,
            "bpm": loop.bpm,
            "start_sample": loop.start_sample,
            "end_sample": loop.end_sample,
            "length_samples": loop.length_samples,
            "length_seconds": round(loop.length_seconds, 3),
            "length_bars": round(loop.length_bars, 2),
            "tracks": list(slices.keys()),
            "confidence": round(loop.start_boundary.confidence, 3)
        }
        meta_path = os.path.join(loop_folder, "index.json")
        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2)
    
    return loop_folder


class _LoopingAudioDevice(QIODevice):
    """QIODevice that feeds PCM bytes and handles looping."""

    def __init__(self, bytes_per_frame: int, parent=None):
        super().__init__(parent)
        self.bytes_per_frame = bytes_per_frame
        self.buffer = b""
        self.byte_offset = 0
        self.loop_enabled = True
        self.playing = False
        self.loop_start = 0
        self.loop_end = 0
        self.current_position = 0

    def set_buffer(self, data: bytes, loop_start: int, loop_end: int):
        self.buffer = data or b""
        self.loop_start = loop_start
        self.loop_end = loop_end
        self.byte_offset = 0
        self.current_position = loop_start

    def start_playback(self, start_sample: int):
        self.playing = True
        self.seek_sample(start_sample)

    def stop_playback(self):
        self.playing = False
        self.byte_offset = 0
        self.current_position = self.loop_start

    def seek_sample(self, sample_position: int):
        clamped = max(self.loop_start, min(sample_position, self.loop_end))
        self.current_position = clamped
        rel_samples = clamped - self.loop_start
        self.byte_offset = max(0, min(len(self.buffer), rel_samples * self.bytes_per_frame))

    def readData(self, maxlen: int):  # noqa: N802 - Qt API
        if not self.playing or not self.buffer:
            return bytes(maxlen)

        remaining = maxlen
        out = bytearray()

        while remaining > 0:
            available = len(self.buffer) - self.byte_offset
            if available <= 0:
                if self.loop_enabled:
                    self.byte_offset = 0
                    self.current_position = self.loop_start
                    continue
                self.playing = False
                out.extend(bytes(remaining))
                break

            to_copy = min(available, remaining)
            out.extend(self.buffer[self.byte_offset:self.byte_offset + to_copy])
            self.byte_offset += to_copy
            advanced_frames = to_copy // self.bytes_per_frame
            self.current_position += advanced_frames
            remaining -= to_copy

            if self.current_position >= self.loop_end:
                if self.loop_enabled:
                    self.byte_offset = 0
                    self.current_position = self.loop_start
                else:
                    self.playing = False
                    if remaining:
                        out.extend(bytes(remaining))
                    break

        return bytes(out)

    def writeData(self, data):  # noqa: N802 - Qt API
        return -1

    def bytesAvailable(self):  # noqa: N802 - Qt API
        return (len(self.buffer) - self.byte_offset) + super().bytesAvailable()


class AudioPlayer:
    """Audio playback engine with looping and track mute support."""
    
    def __init__(self, loader: AudioLoader, output_device=None):
        self.loader = loader
        self.sample_rate = loader.get_sample_rate()
        
        self._playing = False
        self._loop_enabled = True
        self._position = 0
        self._loop_start = 0
        self._loop_end = 0
        
        self._muted = {1: False, 2: False, 3: False, 4: False}
        self._mix_cache: Optional[np.ndarray] = None
        self._cache_start = 0
        self._cache_end = 0
        self._mix_bytes: Optional[bytes] = None
        self._audio_output: Optional[QAudioSink] = None
        self._stream_device: Optional[_LoopingAudioDevice] = None
        self._output_device = output_device  # Specific audio output device (None = default)
        self.on_position_changed = None
    
    def set_output_device(self, device):
        """Change the audio output device. Pass None to use the system default."""
        was_playing = self._playing
        current_pos = self.get_position()
        
        # Stop current playback
        if self._audio_output:
            self._audio_output.stop()
            self._audio_output = None
        if self._stream_device:
            self._stream_device.close()
            self._stream_device = None
        
        self._output_device = device
        self._playing = False
        
        # Reinitialize with new device
        if self._loop_end > self._loop_start:
            self._ensure_output()
            self._refresh_stream(keep_position=False)
            if self._stream_device:
                self._stream_device.seek_sample(current_pos)
        
        # Resume playback if was playing
        if was_playing:
            self.play()
    
    def set_region(self, start_sample: int, end_sample: int):
        self._loop_start = start_sample
        self._loop_end = end_sample
        self._position = start_sample
        self._invalidate_cache()
        # Prebuild mix for faster first playback
        self._mix_to_bytes()
        self._ensure_output()
        self._refresh_stream(keep_position=False)
    
    def set_muted(self, track_num: int, muted: bool):
        if track_num in self._muted:
            self._muted[track_num] = muted
            self._invalidate_cache()
            self._refresh_stream()
    
    def is_muted(self, track_num: int) -> bool:
        return self._muted.get(track_num, False)
    
    def set_loop_enabled(self, enabled: bool):
        self._loop_enabled = enabled
        if self._stream_device:
            self._stream_device.loop_enabled = enabled
    
    def _invalidate_cache(self):
        self._mix_cache = None
        self._mix_bytes = None

    def _build_mix(self) -> np.ndarray:
        if (
            self._mix_cache is not None
            and self._cache_start == self._loop_start
            and self._cache_end == self._loop_end
        ):
            return self._mix_cache

        length = max(0, self._loop_end - self._loop_start)
        mix = np.zeros((length, 2), dtype=np.float32)

        for track_num in range(1, 5):
            if self._muted[track_num]:
                continue

            audio = self.loader.load_track(track_num)
            if audio is None:
                continue

            start = self._loop_start
            end = min(self._loop_end, len(audio))

            if start >= len(audio):
                continue

            chunk = audio[start:end].astype(np.float32)
            if chunk.ndim == 1:
                chunk = np.column_stack([chunk, chunk])

            chunk_len = min(len(chunk), length)
            mix[:chunk_len] += chunk[:chunk_len]

        peak = np.max(np.abs(mix)) if mix.size else 0
        if peak > 0.95:
            mix = mix * (0.95 / peak)

        self._mix_cache = mix
        self._cache_start = self._loop_start
        self._cache_end = self._loop_end
        self._mix_bytes = None

        return mix

    def _mix_to_bytes(self) -> bytes:
        if self._mix_bytes is not None:
            return self._mix_bytes
        mix = self._build_mix()
        if mix.size == 0:
            self._mix_bytes = b""
            return self._mix_bytes
        clipped = np.clip(mix, -1.0, 1.0)
        pcm = (clipped * 32767).astype("<i2")
        self._mix_bytes = pcm.tobytes()
        return self._mix_bytes

    def _ensure_output(self):
        if self._audio_output is not None and self._stream_device is not None:
            return

        # Use specified device or fall back to system default
        device = self._output_device if self._output_device is not None else QMediaDevices.defaultAudioOutput()
        fmt = device.preferredFormat()
        fmt.setSampleRate(int(self.sample_rate))
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.Int16)

        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()

        self._audio_output = QAudioSink(device, fmt)
        # Reduce buffer size to lower startup latency
        target_frames = 2048
        bytes_per_frame = fmt.bytesPerFrame() or (fmt.channelCount() * 2)
        self._audio_output.setBufferSize(target_frames * bytes_per_frame)
        self._stream_device = _LoopingAudioDevice(bytes_per_frame=bytes_per_frame)
        self._stream_device.open(QIODevice.ReadOnly)

    def _refresh_stream(self, keep_position: bool = True):
        if not self._audio_output or not self._stream_device:
            return

        target_pos = self.get_position() if keep_position else self._loop_start
        mix_bytes = self._mix_to_bytes()
        self._stream_device.set_buffer(mix_bytes, self._loop_start, self._loop_end)
        self._stream_device.loop_enabled = self._loop_enabled
        self._stream_device.seek_sample(target_pos)

        if self._playing:
            self._audio_output.stop()
            self._stream_device.start_playback(target_pos)
            self._audio_output.start(self._stream_device)
    
    def play(self):
        if self._playing or self._loop_end <= self._loop_start:
            return
        self._ensure_output()
        self._refresh_stream(keep_position=True)

        if self._stream_device:
            self._stream_device.start_playback(self._position)
        if self._audio_output and self._stream_device:
            self._audio_output.start(self._stream_device)

        self._playing = True
    
    def pause(self):
        self._playing = False
        if self._audio_output:
            self._audio_output.suspend()
        if self._stream_device:
            self._stream_device.playing = False
    
    def stop(self):
        self._playing = False
        self._position = self._loop_start
        if self._audio_output:
            self._audio_output.stop()
        if self._stream_device:
            self._stream_device.stop_playback()
    
    def seek(self, sample_position: int):
        """Seek to a specific sample position within the current loop region."""
        # Clamp to loop bounds
        self._position = max(self._loop_start, min(sample_position, self._loop_end))
        if self._stream_device:
            self._stream_device.seek_sample(self._position)
    
    def is_playing(self) -> bool:
        return self._playing
    
    def get_position(self) -> int:
        if self._stream_device:
            self._position = self._stream_device.current_position
        return self._position
    
    def cleanup(self):
        if self._audio_output:
            self._audio_output.stop()
            self._audio_output = None
        if self._stream_device:
            self._stream_device.close()
            self._stream_device = None
        self._mix_bytes = None
