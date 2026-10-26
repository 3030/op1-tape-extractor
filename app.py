"""
OP-1 Loop Extractor
"""

import sys
import os
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QFontDatabase, QPixmap, QPainter, QFont, QPen, QColor

from core import (
    parse_tape_json, detect_boundaries, detect_loops, split_loop_by_bars,
    AudioLoader, AudioPlayer, TapeSession, DetectedLoop, SAMPLE_RATE, export_loop,
    detect_tape_format, parse_aiff_tape_session, LoopBoundary, detect_loops_from_aiff_regions
)
from ui import (
    WaveformContainer, LoopListWidget, SettingsPanel, DropZone, StatusBar,
    PlaybackControls, TapeInfoDisplay, LoopInfoPanel, EditToolbar, COLORS,
    TOOL_SELECT, TOOL_SPLIT, TOOL_GLUE, TOOL_REMOVE, TOOL_ADD, SAMPLE_RATE,
    HighlightButtonGroup, AudioDeviceSelector
)

# Read version
try:
    from version import __version__
except ImportError:
    __version__ = "1.0.dev"


class AboutDialog(QtWidgets.QDialog):
    """Help/credits dialog"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About OP-1 Loop Extractor")
        self.setFixedSize(600, 400)
        self.setStyleSheet(f"background-color: {COLORS['bg']};")

        # Main layout
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create canvas widget
        canvas = QtWidgets.QWidget()
        canvas.setFixedSize(600, 400)
        layout.addWidget(canvas)

        # Paint the content
        pixmap = QPixmap(600, 400)
        pixmap.fill(QColor(COLORS['bg']))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # Load and draw 3030 logo in top right
        logo_path = os.path.join(os.path.dirname(__file__), 'assets', '3030logo.png')
        if os.path.exists(logo_path):
            logo = QPixmap(logo_path)
            # Scale logo to reasonable size (max 60px height)
            if logo.height() > 60:
                logo = logo.scaledToHeight(60, Qt.SmoothTransformation)
            # Position in top right with 20px margin
            logo_x = 600 - logo.width() - 20
            logo_y = 20
            painter.drawPixmap(logo_x, logo_y, logo)

        # Draw title (left aligned)
        title_font = QFont("Inter", 48, QFont.Bold)
        painter.setFont(title_font)
        painter.setPen(QPen(QColor(COLORS['text'])))
        painter.drawText(pixmap.rect().adjusted(30, 80, 0, 0), Qt.AlignLeft | Qt.AlignTop, "OP-1")

        # Draw subtitle (left aligned)
        subtitle_font = QFont("Inter", 32, QFont.Light)
        painter.setFont(subtitle_font)
        painter.drawText(pixmap.rect().adjusted(30, 140, 0, 0), Qt.AlignLeft | Qt.AlignTop, "Loop Extractor")

        # Draw accent line (left aligned, shorter)
        painter.setPen(QPen(QColor(COLORS['accent']), 4))
        painter.drawLine(30, 200, 330, 200)

        # Draw version (left aligned)
        version_font = QFont("Inter", 14, QFont.Light)
        painter.setFont(version_font)
        painter.setPen(QPen(QColor(COLORS['text_dim'])))
        painter.drawText(pixmap.rect().adjusted(30, 0, 0, -60), Qt.AlignLeft | Qt.AlignBottom, f"v{__version__}")

        # Draw info text (right aligned at bottom)
        info_font = QFont("Inter", 10, QFont.Light)
        painter.setFont(info_font)
        painter.setPen(QPen(QColor(COLORS['text_dim'])))
        info_text = "This tool was lovingly crafted by\nNate Schmold @ www.3030.ca"
        painter.drawText(pixmap.rect().adjusted(0, 0, -20, -20), Qt.AlignRight | Qt.AlignBottom, info_text)

        painter.end()

        # Set as background
        canvas_label = QtWidgets.QLabel(canvas)
        canvas_label.setPixmap(pixmap)
        canvas_label.setGeometry(0, 0, 600, 400)


class ExportWorker(QThread):
    progress = Signal(int, int, str)
    finished = Signal(list)
    error = Signal(str)
    
    def __init__(self, folder, session, output_dir, loops, settings, is_aiff=False):
        super().__init__()
        self.folder = folder
        self.session = session
        self.output_dir = output_dir
        self.loops = loops
        self.settings = settings
        self.is_aiff = is_aiff
    
    def run(self):
        try:
            loader = AudioLoader(self.folder, is_aiff=self.is_aiff)
            os.makedirs(self.output_dir, exist_ok=True)
            
            exported = []
            for i, loop in enumerate(self.loops):
                self.progress.emit(i, len(self.loops), f"Exporting {getattr(loop, 'custom_name', None) or f'loop {loop.index}'}")
                path = export_loop(loader, loop, self.session, self.output_dir,
                                  include_mix=self.settings.get('include_mix', True),
                                  include_metadata=self.settings.get('include_metadata', False),
                                  custom_folder_name=getattr(loop, 'custom_name', None),
                                  zero_fade=self.settings.get('zero_fade', False))
                exported.append(path)
            
            self.progress.emit(len(self.loops), len(self.loops), "Done")
            self.finished.emit(exported)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        
        self.folder = None
        self.tape_json_path = None
        self.session = None
        self.loops = []
        self.export_worker = None
        
        self.audio_loader = None
        self.audio_player = None
        self.selected_loop_index = -1
        
        self.playhead_timer = QTimer()
        self.playhead_timer.setInterval(33)
        self.playhead_timer.timeout.connect(self._update_playhead)
        
        self._setup_ui()
        self._connect_signals()
    
    def _setup_ui(self):
        self.setWindowTitle("OP-1 Loop Extractor")
        self.setMinimumSize(1100, 870)
        self.setMaximumSize(2400, 1600)
        self.resize(1100, 870)
        # Set background image
        self.setStyleSheet(f"""
            QMainWindow {{
                background-image: url(assets/background.png);
                background-position: center;
                background-repeat: no-repeat;
            }}
        """)
        
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setContentsMargins(20, 16, 16, 16)  # 2x left margin
        main_layout.setSpacing(8)

        top_section = QtWidgets.QHBoxLayout()
        top_section.setSpacing(12)

        # Drop zone
        self.drop_zone = DropZone()
        self.drop_zone.setMinimumWidth(280)
        self.drop_zone.setMaximumWidth(320)
        top_section.addWidget(self.drop_zone)

        # Settings panel (contains auto_scan_btn)
        self.settings_panel = SettingsPanel()
        top_section.addWidget(self.settings_panel)

        # Add stretch before right side content
        top_section.addStretch()

        # Create container for title and grid size, aligned to top
        right_side_container = QtWidgets.QVBoxLayout()
        right_side_container.setSpacing(4)
        right_side_container.setAlignment(QtCore.Qt.AlignTop)

        # Title at top (tape info hidden, kept for internal use)
        header = QtWidgets.QLabel("OP-1 LOOP EXTRACTOR")
        header.setStyleSheet(f"font-size: 24px; font-weight: 600; color: {COLORS['text_dim']};")
        header.setAlignment(QtCore.Qt.AlignRight)
        self.tape_info = TapeInfoDisplay()
        self.tape_info.hide()  # Hidden but still exists for internal tracking
        right_side_container.addWidget(header)

        # Add more space between title and grid size
        right_side_container.addSpacing(20)

        # Grid size label
        grid_size_label = QtWidgets.QLabel("GRID SIZE")
        grid_size_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px; font-weight: 600;")
        grid_size_label.setAlignment(QtCore.Qt.AlignRight)
        right_side_container.addWidget(grid_size_label)

        # Grid size buttons
        self.highlight_buttons = HighlightButtonGroup()
        self.highlight_buttons.highlight_changed.connect(self._on_highlight_grid_changed)
        right_side_container.addWidget(self.highlight_buttons)

        top_section.addLayout(right_side_container)

        main_layout.addLayout(top_section)
        
        # Waveform (no header)
        self.waveform_container = WaveformContainer()
        self.waveform_container.setMinimumHeight(220)
        main_layout.addWidget(self.waveform_container, stretch=2)

        # Controls row: Play/Stop/Loop on left, Time in center, Tools on right, Highlight buttons far right
        controls_row = QtWidgets.QHBoxLayout()
        controls_row.setSpacing(12)

        self.playback_controls = PlaybackControls()
        controls_row.addWidget(self.playback_controls)

        # Add centered time display between playback and tools
        controls_row.addStretch(1)
        controls_row.addWidget(self.playback_controls.time_label)
        controls_row.addStretch(1)

        self.edit_toolbar = EditToolbar()
        controls_row.addWidget(self.edit_toolbar)
        
        # Audio device selector (right side)
        controls_row.addSpacing(16)
        self.audio_device_selector = AudioDeviceSelector()
        controls_row.addWidget(self.audio_device_selector)

        main_layout.addLayout(controls_row)
        
        bottom_section = QtWidgets.QHBoxLayout()
        list_container = QtWidgets.QVBoxLayout()
        detected_loops_label = QtWidgets.QLabel("EXTRACTED LOOPS")
        detected_loops_label.setStyleSheet(f"color: {COLORS['text']};")
        list_container.addWidget(detected_loops_label)
        self.loop_list = LoopListWidget()
        list_container.addWidget(self.loop_list)
        bottom_section.addLayout(list_container, stretch=1)
        
        self.loop_info_panel = LoopInfoPanel()
        bottom_section.addWidget(self.loop_info_panel)
        main_layout.addLayout(bottom_section, stretch=1)
        
        # Export options and button
        export_section = QtWidgets.QHBoxLayout()
        export_section.addStretch()
        
        # Export options
        export_options = QtWidgets.QVBoxLayout()
        export_options.setSpacing(4)
        
        # Use PNG icon for checkbox
        import os
        icon_path = os.path.join(os.path.dirname(__file__), 'assets', 'icons', 'checkbox-checked.png')
        checkbox_style = f"""
            QCheckBox {{
                color: {COLORS['text']};
                font-size: 11px;
                font-weight: 600;
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border-radius: 9px;
                border: 2px solid {COLORS['border']};
                background-color: {COLORS['bg_waveform']};
            }}
            QCheckBox::indicator:checked {{
                background-color: {COLORS['bg_waveform']};
                border: 2px solid {COLORS['border']};
                image: url({icon_path});
            }}
            QCheckBox::indicator:hover {{
                border-color: {COLORS['text']};
            }}
        """

        self.export_mix_check = QtWidgets.QCheckBox("EXPORT MIXDOWN")
        self.export_mix_check.setChecked(True)
        self.export_mix_check.setStyleSheet(checkbox_style)

        self.zero_fade_check = QtWidgets.QCheckBox("ZERO-FADE ENDPOINTS")
        self.zero_fade_check.setChecked(True)
        self.zero_fade_check.setToolTip("Apply 20ms fade in/out to ensure loops start and end at zero")
        self.zero_fade_check.setStyleSheet(checkbox_style)
        
        export_options.addWidget(self.export_mix_check)
        export_options.addWidget(self.zero_fade_check)
        export_section.addLayout(export_options)
        export_section.addSpacing(16)
        
        self.export_btn = QtWidgets.QPushButton("EXPORT LOOPS")
        self.export_btn.setEnabled(False)
        self.export_btn.setMinimumSize(150, 40)
        self.export_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['accent']};
                border: none;
                border-radius: 10px;
                color: white;
                font-size: 14px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent_dim']};
            }}
            QPushButton:disabled {{
                background-color: {COLORS['border']};
                color: {COLORS['text_dim']};
            }}
        """)
        export_section.addWidget(self.export_btn)
        main_layout.addLayout(export_section)
        
        self.status_bar = StatusBar()
        main_layout.addWidget(self.status_bar)
    
    def _connect_signals(self):
        self.drop_zone.folder_dropped.connect(self._on_folder_dropped)
        self.settings_panel.settings_changed.connect(self._redetect_loops)
        self.settings_panel.auto_scan_requested.connect(self._on_auto_scan_requested)
        self.loop_list.currentRowChanged.connect(self._on_loop_selected)
        self.export_btn.clicked.connect(self._export)
        self.edit_toolbar.tool_changed.connect(self._on_tool_changed)
        self.edit_toolbar.snap_toggled.connect(self._on_snap_to_grid_changed)
        self.waveform_container.loop_clicked.connect(self._on_waveform_loop_clicked)
        self.waveform_container.track_mute_changed.connect(self._on_track_mute)
        self.waveform_container.loop_region_changed.connect(self._on_loop_region_changed)
        self.waveform_container.split_requested.connect(self._on_split_at_position)
        self.waveform_container.glue_requested.connect(self._on_glue_loops)
        self.waveform_container.remove_requested.connect(self._on_remove_loop)
        self.waveform_container.add_requested.connect(self._on_add_region)
        self.waveform_container.seek_requested.connect(self._on_seek)
        self.playback_controls.play_clicked.connect(self._on_play)
        self.playback_controls.pause_clicked.connect(self._on_pause)
        self.playback_controls.stop_clicked.connect(self._on_stop)
        self.playback_controls.loop_toggled.connect(self._on_loop_toggle)
        self.loop_info_panel.name_changed.connect(self._on_loop_name_changed)
        self.loop_info_panel.split_requested.connect(self._on_loop_split_requested)
        self.status_bar.help_clicked.connect(self._show_about)
        self.audio_device_selector.device_changed.connect(self._on_audio_device_changed)
    
    def keyPressEvent(self, event):
        # Tool shortcuts
        if event.key() == Qt.Key_S and not event.isAutoRepeat():
            self.edit_toolbar.set_tool(TOOL_SELECT)
            event.accept()
        elif event.key() == Qt.Key_C and not event.isAutoRepeat():
            self.edit_toolbar.set_tool(TOOL_SPLIT)
            event.accept()
        elif event.key() == Qt.Key_G and not event.isAutoRepeat():
            self.edit_toolbar.set_tool(TOOL_GLUE)
            event.accept()
        elif event.key() == Qt.Key_A and not event.isAutoRepeat():
            self.edit_toolbar.set_tool(TOOL_ADD)
            event.accept()
        elif event.key() == Qt.Key_R and not event.isAutoRepeat():
            self.edit_toolbar.set_tool(TOOL_REMOVE)
            event.accept()
        elif event.key() == Qt.Key_M and not event.isAutoRepeat():
            # Toggle snap to grid
            self.edit_toolbar.set_snap_enabled(not self.edit_toolbar.is_snap_enabled())
            event.accept()
        elif event.key() == Qt.Key_Escape and not event.isAutoRepeat():
            self.edit_toolbar.set_tool(TOOL_SELECT)
            event.accept()
        else:
            super().keyPressEvent(event)
    
    def _on_folder_dropped(self, path):
        # Show loading state
        self.status_bar.show_progress("Loading…")
        self.drop_zone.set_loading()
        if self.audio_player:
            self.audio_player.stop()
            self.audio_player.cleanup()
            self.audio_player = None
        
        # Handle file or folder drop
        if os.path.isfile(path):
            if path.endswith('tape.json'):
                self.tape_json_path = path
                self.folder = os.path.dirname(path)
            elif path.endswith('.aif') or path.endswith('.aiff'):
                self.folder = os.path.dirname(path)
                self.tape_json_path = None
            else:
                return
        elif os.path.isdir(path):
            self.folder = path
            self.tape_json_path = os.path.join(path, 'tape.json')
        else:
            return
        
        try:
            # Detect tape format (OP-1 Field vs OG OP-1)
            tape_format = detect_tape_format(self.folder)
            
            if tape_format == 'field':
                # OP-1 Field format: tape.json + WAV files
                self.session = parse_tape_json(self.tape_json_path)
                format_label = "Field"
                is_aiff = False
            elif tape_format == 'og':
                # Original OP-1 format: AIFF files with embedded metadata
                self.session = parse_aiff_tape_session(self.folder)
                if self.session is None:
                    self.status_bar.set_status("Could not parse AIFF tape files")
                    return
                format_label = "OG"
                is_aiff = True
                self.tape_json_path = None  # No tape.json for AIFF format
            else:
                self.status_bar.set_status("No OP-1 tape files found (need tape.json+WAV or AIFF files)")
                return
            
            folder_name = os.path.basename(self.folder.rstrip('/'))
            self.drop_zone.set_loaded(folder_name, self.session.bpm, len(self.session.clips))
            self.tape_info.set_info(folder_name, self.session.bpm)
            
            self.audio_loader = self.waveform_container.load_session(
                self.folder, 
                tape_json_path=self.tape_json_path,
                is_aiff=is_aiff,
                session=self.session
            )
            # Create audio player with currently selected output device
            selected_device = self.audio_device_selector.get_selected_device()
            self.audio_player = AudioPlayer(self.audio_loader, output_device=selected_device)
            # Pre-mix first loop to reduce first-play delay
            if self.loops:
                first_loop = self.loops[0]
                self.audio_player.set_region(first_loop.start_sample, first_loop.end_sample)
            
            # Apply initial settings
            self.waveform_container.set_snap_to_grid(self.edit_toolbar.is_snap_enabled())
            self.waveform_container.set_highlight_bars(self.highlight_buttons.get_value())
            
            self._redetect_loops()
            self.settings_panel.set_scan_enabled(True)
            self.status_bar.set_status(f"Loaded {format_label} tape at {self.session.bpm} BPM")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.status_bar.set_status(f"Error loading: {e}")
        finally:
            self.status_bar.hide_progress()
            self.status_bar.set_status(f"Error: {e}")
    
    def _redetect_loops(self):
        if not self.session:
            return
        
        settings = self.settings_panel.get_settings()
        
        # For AIFF sessions, use the embedded region data directly
        if getattr(self.session, 'is_aiff_source', False):
            self.loops = detect_loops_from_aiff_regions(self.session)
            # Apply minimum bars filter
            min_samples = int(settings['min_bars'] * self.session.samples_per_bar)
            self.loops = [l for l in self.loops if l.length_samples >= min_samples]
        else:
            # For Field sessions, use boundary detection
            boundaries = detect_boundaries(self.session, min_tracks=settings['min_tracks'])
            self.loops = detect_loops(self.session, boundaries, min_bars=settings['min_bars'])
        
        self._refresh_loops_ui()
    
    def _refresh_loops_ui(self):
        self.waveform_container.set_loops(self.loops)
        self.loop_list.set_loops(self.loops)
        self.export_btn.setEnabled(len(self.loops) > 0)
        self.selected_loop_index = -1
        self.loop_info_panel.clear()
        if self.loops:
            self.loop_list.setCurrentRow(0)
        self.status_bar.set_status(f"Found {len(self.loops)} loops")
    
    def _on_loop_selected(self, row):
        if 0 <= row < len(self.loops):
            self._select_loop(row)
    
    def _on_waveform_loop_clicked(self, index):
        if 0 <= index < len(self.loops):
            was_playing = self.audio_player and self.audio_player.is_playing()
            self.loop_list.setCurrentRow(index)
            if was_playing:
                self.audio_player.play()
                self.playhead_timer.start()
                self.playback_controls.set_playing(True)
                self.waveform_container.set_playing(True)
    
    def _select_loop(self, index):
        was_playing = self.audio_player and self.audio_player.is_playing()
        self.selected_loop_index = index
        loop = self.loops[index]
        
        # Highlight the loop in the waveform and scroll to it if needed
        self.waveform_container.highlight_loop(index + 1)
        self.waveform_container.scroll_to_loop(index)
        self.loop_info_panel.set_loop(loop, index)
        
        if self.audio_player:
            self.audio_player.set_region(loop.start_sample, loop.end_sample)
            if not was_playing:
                self.waveform_container.set_playhead_position(loop.start_sample)
                self.playback_controls.update_time(loop.start_sample)
    
    def _on_loop_name_changed(self, index, name):
        if 0 <= index < len(self.loops):
            self.loops[index].custom_name = name
            self.loop_list.update_loop_name(index, name)
            self.waveform_container.set_loops(self.loops)
            if self.selected_loop_index >= 0:
                self.waveform_container.highlight_loop(self.selected_loop_index + 1)
    
    def _on_loop_split_requested(self, loop_index, bar_count):
        if loop_index < 0 or loop_index >= len(self.loops):
            return
        
        loop = self.loops[loop_index]
        if loop.length_bars <= bar_count:
            return
        
        if self.audio_player:
            self.audio_player.stop()
            self.playback_controls.set_playing(False)
        
        new_loops = split_loop_by_bars(loop, bar_count)
        self.loops = self.loops[:loop_index] + new_loops + self.loops[loop_index + 1:]
        
        for i, l in enumerate(self.loops):
            l.index = i + 1
        
        self._refresh_loops_ui()
        self.loop_list.setCurrentRow(loop_index)
        self.status_bar.set_status(f"Split into {len(new_loops)} × {bar_count}-bar loops")
    
    def _on_auto_scan_requested(self):
        """Handle Auto-Scan button click with confirmation dialog"""
        if not self.session:
            return
        
        reply = QtWidgets.QMessageBox.question(
            self,
            "Auto-Scan Loops",
            "This will overwrite all existing loop regions.\n\nProceed?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No
        )
        
        if reply == QtWidgets.QMessageBox.Yes:
            if self.audio_player:
                self.audio_player.stop()
                self.playback_controls.set_playing(False)
            self._redetect_loops()
            self.status_bar.set_status(f"Re-scanned: found {len(self.loops)} loops")
    
    def _on_loop_region_changed(self, loop_index, new_start, new_end):
        """Handle when user drags loop region edges"""
        if loop_index < 0 or loop_index >= len(self.loops):
            return
        
        loop = self.loops[loop_index]
        loop.start_sample = new_start
        loop.end_sample = new_end
        
        # Update the loop list display
        self.loop_list.set_loops(self.loops)
        self.loop_list.setCurrentRow(loop_index)
        
        # Update loop info panel if this loop is selected
        if self.selected_loop_index == loop_index:
            self.loop_info_panel.set_loop(loop, loop_index)
            # Update audio player region
            if self.audio_player:
                self.audio_player.set_region(new_start, new_end)
        
        self.status_bar.set_status(f"Loop {loop_index + 1} adjusted: {loop.length_bars:.1f} bars")
    
    def _on_tool_changed(self, tool: str):
        """Handle tool selection change"""
        self.waveform_container.set_tool(tool)
        tool_names = {TOOL_SELECT: "Select", TOOL_SPLIT: "Split", TOOL_GLUE: "Glue"}
        self.status_bar.set_status(f"{tool_names.get(tool, tool)} tool active")
    
    def _on_snap_to_grid_changed(self, enabled: bool):
        """Handle snap to grid toggle"""
        self.waveform_container.set_snap_to_grid(enabled)
        self.status_bar.set_status(f"Snap to grid {'enabled' if enabled else 'disabled'}")
    
    def _on_highlight_grid_changed(self, bars: int):
        """Handle highlight grid setting change"""
        self.waveform_container.set_highlight_bars(bars)
        if bars > 0:
            self.status_bar.set_status(f"Highlighting every {bars} bars")
        else:
            self.status_bar.set_status("Bar highlights disabled")
    
    def _on_split_at_position(self, loop_index: int, split_sample: int):
        """Handle split tool click - split a loop at the clicked position"""
        if loop_index < 0 or loop_index >= len(self.loops):
            return
        
        loop = self.loops[loop_index]
        
        if self.audio_player:
            self.audio_player.stop()
            self.playback_controls.set_playing(False)
        
        # Create two new loops from the split
        loop1_boundary = LoopBoundary(
            position=loop.start_sample,
            tracks_starting=loop.tracks_active,
            confidence=loop.start_boundary.confidence,
            raw_positions=[loop.start_sample]
        )
        
        loop2_boundary = LoopBoundary(
            position=split_sample,
            tracks_starting=loop.tracks_active,
            confidence=1.0,
            raw_positions=[split_sample]
        )
        
        loop1 = DetectedLoop(
            index=1,
            start_sample=loop.start_sample,
            end_sample=split_sample,
            start_boundary=loop1_boundary,
            end_boundary=loop2_boundary,
            bpm=loop.bpm,
            custom_name=loop.custom_name
        )
        
        loop2 = DetectedLoop(
            index=2,
            start_sample=split_sample,
            end_sample=loop.end_sample,
            start_boundary=loop2_boundary,
            end_boundary=loop.end_boundary,
            bpm=loop.bpm
        )
        
        # Replace the original loop with the two new ones
        self.loops = self.loops[:loop_index] + [loop1, loop2] + self.loops[loop_index + 1:]
        
        # Re-index all loops
        for i, l in enumerate(self.loops):
            l.index = i + 1
        
        self._refresh_loops_ui()
        self.loop_list.setCurrentRow(loop_index)
        self.status_bar.set_status(f"Split loop into 2 regions")
    
    def _on_glue_loops(self, first_index: int, second_index: int):
        """Handle glue tool click - join two adjacent loops"""
        if first_index < 0 or second_index >= len(self.loops):
            return
        
        if self.audio_player:
            self.audio_player.stop()
            self.playback_controls.set_playing(False)
        
        loop1 = self.loops[first_index]
        loop2 = self.loops[second_index]
        
        # Create a new combined loop
        combined_tracks = loop1.tracks_active.union(loop2.tracks_active)
        
        combined_boundary = LoopBoundary(
            position=loop1.start_sample,
            tracks_starting=combined_tracks,
            confidence=loop1.start_boundary.confidence,
            raw_positions=loop1.start_boundary.raw_positions
        )
        
        combined_loop = DetectedLoop(
            index=1,
            start_sample=loop1.start_sample,
            end_sample=loop2.end_sample,
            start_boundary=combined_boundary,
            end_boundary=loop2.end_boundary,
            bpm=loop1.bpm,
            custom_name=loop1.custom_name or loop2.custom_name
        )
        
        # Replace the two loops with the combined one
        self.loops = self.loops[:first_index] + [combined_loop] + self.loops[second_index + 1:]
        
        # Re-index all loops
        for i, l in enumerate(self.loops):
            l.index = i + 1
        
        self._refresh_loops_ui()
        self.loop_list.setCurrentRow(first_index)
        self.status_bar.set_status(f"Glued 2 loops into 1 region ({combined_loop.length_bars:.1f} bars)")
    
    def _on_remove_loop(self, loop_index: int):
        """Handle remove tool click - confirm and remove a loop"""
        if loop_index < 0 or loop_index >= len(self.loops):
            return
        
        loop = self.loops[loop_index]
        loop_name = getattr(loop, 'custom_name', None) or f"L{loop.index:03d}"
        
        reply = QtWidgets.QMessageBox.question(
            self,
            "Remove Loop",
            f"Are you sure you want to remove loop region '{loop_name}'?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No
        )
        
        if reply == QtWidgets.QMessageBox.Yes:
            if self.audio_player:
                self.audio_player.stop()
                self.playback_controls.set_playing(False)
            
            # Remove the loop
            self.loops = self.loops[:loop_index] + self.loops[loop_index + 1:]
            
            # Re-index all loops
            for i, l in enumerate(self.loops):
                l.index = i + 1
            
            self._refresh_loops_ui()
            
            # Select previous loop if available
            if self.loops:
                new_index = min(loop_index, len(self.loops) - 1)
                self.loop_list.setCurrentRow(new_index)
            
            self.status_bar.set_status(f"Removed loop '{loop_name}'")
    
    def _on_add_region(self, start_sample: int, end_sample: int):
        """Handle add tool click - create a new loop region"""
        if not self.session:
            return
        
        # Create a new loop boundary
        new_boundary = LoopBoundary(
            position=start_sample,
            tracks_starting=set(),  # No specific tracks
            confidence=1.0,
            raw_positions=[start_sample]
        )
        
        # Create the new loop
        new_loop = DetectedLoop(
            index=len(self.loops) + 1,
            start_sample=start_sample,
            end_sample=end_sample,
            start_boundary=new_boundary,
            end_boundary=None,
            bpm=self.session.bpm
        )
        
        # Add to loops list and sort by start position
        self.loops.append(new_loop)
        self.loops.sort(key=lambda l: l.start_sample)
        
        # Re-index all loops
        for i, l in enumerate(self.loops):
            l.index = i + 1
        
        self._refresh_loops_ui()
        
        # Select the new loop
        new_index = next((i for i, l in enumerate(self.loops) if l.start_sample == start_sample), 0)
        self.loop_list.setCurrentRow(new_index)
        
        bars = (end_sample - start_sample) / (SAMPLE_RATE * 60.0 / self.session.bpm * 4)
        self.status_bar.set_status(f"Added new {bars:.0f}-bar region")
    
    def _on_play(self):
        if self.audio_player and self.selected_loop_index >= 0:
            self.audio_player.play()
            self.playhead_timer.start()
            self.waveform_container.set_playing(True)
    
    def _on_pause(self):
        if self.audio_player:
            self.audio_player.pause()
            self.playhead_timer.stop()
            self.waveform_container.set_playing(False)
    
    def _on_stop(self):
        if self.audio_player:
            self.audio_player.stop()
            self.playhead_timer.stop()
            self.waveform_container.set_playing(False)
            if self.selected_loop_index >= 0:
                loop = self.loops[self.selected_loop_index]
                self.waveform_container.set_playhead_position(loop.start_sample)
                self.playback_controls.update_time(loop.start_sample)
    
    def _on_seek(self, sample_position):
        """Handle seeking within the current loop region"""
        if self.audio_player and self.selected_loop_index >= 0:
            loop = self.loops[self.selected_loop_index]
            # Clamp to loop bounds
            sample_position = max(loop.start_sample, min(sample_position, loop.end_sample))
            self.audio_player.seek(sample_position)
            self.waveform_container.set_playhead_position(sample_position)
            self.playback_controls.update_time(sample_position)
    
    def _on_track_mute(self, track_num, muted):
        if self.audio_player:
            self.audio_player.set_muted(track_num, muted)
    
    def _on_loop_toggle(self, enabled):
        if self.audio_player:
            self.audio_player.set_loop_enabled(enabled)
    
    def _on_audio_device_changed(self, device):
        """Handle audio output device change"""
        if self.audio_player:
            self.audio_player.set_output_device(device)
            device_name = device.description() if device else "System Default"
            self.status_bar.set_status(f"Audio output: {device_name}")
    
    def _update_playhead(self):
        if self.audio_player:
            pos = self.audio_player.get_position()
            self.waveform_container.set_playhead_position(pos)
            self.playback_controls.update_time(pos)
            if not self.audio_player.is_playing():
                self.playhead_timer.stop()
                self.playback_controls.set_playing(False)
                self.waveform_container.set_playing(False)
    
    def _export(self):
        if not self.folder or not self.loops:
            return
        
        if self.audio_player:
            self.audio_player.stop()
            self.playback_controls.set_playing(False)
        
        output_dir = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Output Directory", self.folder)
        if not output_dir:
            return
        
        self.export_btn.setEnabled(False)
        
        export_settings = {
            'include_mix': self.export_mix_check.isChecked(),
            'include_metadata': False,
            'zero_fade': self.zero_fade_check.isChecked(),
        }
        
        is_aiff = getattr(self.session, 'is_aiff_source', False)
        self.export_worker = ExportWorker(
            self.folder, self.session, output_dir, self.loops, export_settings, is_aiff=is_aiff
        )
        self.export_worker.progress.connect(lambda c, t, m: (self.status_bar.set_status(m), self.status_bar.set_progress(c, t)))
        self.export_worker.finished.connect(self._on_export_finished)
        self.export_worker.error.connect(lambda e: (self.export_btn.setEnabled(True), self.status_bar.set_status(f"Error: {e}")))
        self.export_worker.start()
    
    def _on_export_finished(self, exported):
        self.export_btn.setEnabled(True)
        self.status_bar.set_progress(0, 0)
        self.status_bar.set_status(f"Exported {len(exported)} loops ✓")
        QtWidgets.QMessageBox.information(self, "Done", f"Exported {len(exported)} loops!")

    def _show_about(self):
        """Show the about/help dialog"""
        dialog = AboutDialog(self)
        dialog.exec()

    def closeEvent(self, event):
        if self.audio_player:
            self.audio_player.cleanup()
        event.accept()


def load_fonts():
    """Load bundled Inter fonts (SIL OFL)"""
    # Try multiple paths for development vs packaged app
    font_paths = [
        os.path.join(os.path.dirname(__file__), 'font'),
        os.path.join(sys._MEIPASS, 'font') if hasattr(sys, '_MEIPASS') else None,
        'font'
    ]
    
    for base_path in font_paths:
        if base_path and os.path.exists(base_path):
            light = os.path.join(base_path, 'Inter-Light.ttf')
            bold = os.path.join(base_path, 'Inter-Bold.ttf')
            if os.path.exists(light):
                QFontDatabase.addApplicationFont(light)
            if os.path.exists(bold):
                QFontDatabase.addApplicationFont(bold)
            break


def main():
    app = QtWidgets.QApplication(sys.argv)
    if sys.platform == 'darwin':
        app.setStyle('Fusion')

    # Load fonts FIRST before creating any UI
    load_fonts()

    # Set default font to Inter Light
    font = QtGui.QFont("Inter", 12)
    font.setWeight(QtGui.QFont.Light)
    app.setFont(font)

    # Create and show main window immediately
    window = MainWindow()
    window.show()
    window.raise_()
    window.activateWindow()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
