# OP-1 Loop Extractor

A desktop app that automatically converts OP-1 tape sessions into usable loop folders.

**Supports both OP-1 Field and Original OP-1 tape formats!**

## What It Does

- Reads `tape.json` from **OP-1 Field** tape backups (WAV files)
- Parses **Original OP-1** AIFF files with embedded region markers
- Detects loop boundaries where tracks start together (grid-aligned)
- Slices audio sample-accurately
- Exports organized loop folders with individual track WAVs

## Features

- **Waveform visualization** with all 4 tracks overlaid
- **Loop detection** based on clip start positions from tape.json
- **Audio playback** with looping and per-track mute
- **Loop renaming** - custom names for each region
- **Sub-splitting** - divide long loops into smaller bar-based chunks
- **Batch export** - outputs `loops/L001/track_1.wav`, etc.
- **Optional mix.wav** - combined stereo mixdown
- **Optional index.json** - metadata for each loop

## Tech Stack

- **Python 3.11+**
- **PySide6** (Qt) - UI framework
- **PyQtGraph** - waveform visualization
- **soundfile + numpy** - audio I/O and processing
- **PySide6 QtMultimedia** - audio playback
- **PyInstaller** - builds standalone .app/.exe

## Project Structure

```
OP1_LoopMaker/
├── app.py              # Main application window
├── ui.py               # All UI widgets (waveform, panels, controls)
├── core.py             # Audio/tape parsing logic, loop detection
├── requirements.txt    # Python dependencies
├── mac-dev.spec        # PyInstaller mac dev build config
├── windows.spec        # PyInstaller Windows build config
├── build.sh            # Build script (mac-dev)
├── icon.icns           # App icon (generated from icon/icon.png)
├── icon/
│   └── icon.png        # Source icon (1024x1024)
├── font/
│   ├── Inter-Light.ttf # Inter by Rasmus Andersson (SIL OFL 1.1)
│   ├── Inter-Bold.ttf
│   └── OFL.txt         # Font license
├── input/              # Test input folder (tape.json + WAVs)
└── venv/               # Python virtual environment
```

## Key Files

### `core.py`
- `detect_tape_format(folder)` - Detects whether folder is Field or OG OP-1 format
- `parse_tape_json(path)` - Parses tape.json (Field format), returns `TapeSession`
- `parse_aiff_tape_session(folder)` - Parses AIFF files (OG format), returns `TapeSession`
- `parse_aiff_regions(filepath)` - Extracts region markers from AIFF `regn` chunk
- `detect_boundaries(session, min_tracks)` - Finds loop start points (Field)
- `detect_loops(session, boundaries)` - Creates `DetectedLoop` objects (Field)
- `detect_loops_from_aiff_regions(session)` - Creates loops from AIFF regions (OG)
- `split_loop_by_bars(loop, bar_count, ...)` - Sub-splits a loop
- `export_loop(loop, audio_loader, output_dir, ...)` - Writes WAV files
- `AudioLoader` - Lazy-loads and caches track audio (WAV or AIFF)
- `AudioPlayer` - Handles playback with looping and muting

### `ui.py`
- `WaveformWidget` - PyQtGraph-based waveform display
- `WaveformContainer` - Waveform + mute buttons
- `LoopListWidget` - List of detected loops
- `LoopInfoPanel` - Selected loop details, rename, sub-split
- `PlaybackControls` - Play/pause/stop/loop buttons
- `TapeInfoDisplay` - Shows folder name + BPM
- `DropZone` - Drag-and-drop folder input
- `StatusBar` - Progress and messages

### `app.py`
- `MainWindow` - Orchestrates all UI components
- Handles signals/slots between components
- Manages `AudioPlayer` instance
- Export logic

## Building

### Development
```bash
cd OP1_LoopMaker
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

### Build Standalone App (macOS)
```bash
source venv/bin/activate
pip install pyinstaller
./build.sh
# Output: dist/OP-1 Loop Extractor.app
```

### Build for Windows
PyInstaller only builds for the current OS. Options:
1. Build on a Windows machine
2. Use GitHub Actions with Windows runner
3. Use a Windows VM

## Usage

1. Launch the app
2. Drag an OP-1 tape folder onto the drop zone:
   - **OP-1 Field**: folder with `tape.json` + `track_N.wav` files
   - **Original OP-1**: folder with `track_N.aif` files (regions embedded in AIFF metadata)
3. Adjust detection settings (min tracks, min bars)
4. Click loops to preview, use mute buttons to isolate tracks
5. Rename loops as needed
6. Sub-split long loops if desired
7. Click "Export Loops" to save

### Supported Formats

| Device | Audio Files | Metadata | Auto-detected |
|--------|-------------|----------|---------------|
| OP-1 Field | `track_N.wav` | `tape.json` | ✓ |
| Original OP-1 | `track_N.aif` | Embedded in AIFF `regn` chunk | ✓ |

## Export Structure

```
output_folder/
└── loops/
    ├── L001/
    │   ├── track_1.wav
    │   ├── track_2.wav
    │   ├── track_4.wav    # (empty tracks omitted)
    │   ├── mix.wav        # (if enabled)
    │   └── index.json     # (if enabled)
    └── L002/
        └── ...
```

## Constants

- Sample rate: 44100 Hz (fixed, OP-1 standard)
- Bit depth: 32-bit float internally, 24-bit WAV export
- Beats per bar: 4 (standard time signature)

## Version

Current: **1.0.x** (auto-incremented from build number)

**Versioning scheme:** The version follows `MAJOR.MINOR.BUILD` format where:
- `MAJOR.MINOR` is manually set (currently `1.0`)
- `BUILD` is automatically set to the GitHub Actions run number

This means every CI build gets a unique version number automatically.

---

Built for the OP-1 workflow. No AI guessing, no onset detection - just reads the actual tape metadata.

### Changelog

- **1.0.x**: Automatic versioning based on build number
- **1.0.2**: Fixed Mac App Store sandbox crash (PyInstaller semaphore issue)
- **1.0.1**: Fixed Mac App Store code signing for TestFlight compatibility
- **1.0**: Added splash screen, optimized startup performance with lazy loading
- **0.8.0**: Added support for Original OP-1 AIFF tape format (embedded region markers)
- **0.7.0**: Initial release with OP-1 Field support

The Mac App Store build setup (specs, entitlements, provisioning profile, CI steps) is parked on the `app-store` branch.

## License

Copyright (C) 2025-2026 Nate Schmold

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. See [LICENSE](LICENSE).

Bundled third-party assets keep their own licenses: Inter font (SIL OFL 1.1, `font/OFL.txt`) and Phosphor icons (MIT, `assets/icons/phosphor/LICENSE.txt`).
