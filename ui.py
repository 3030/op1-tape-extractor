"""
OP-1 Loop Extractor - UI Components
"""

import numpy as np
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtCore import Qt, Signal, QTimer

from core import (
    TapeSession, DetectedLoop, LoopBoundary, AudioLoader,
    parse_tape_json, detect_boundaries, detect_loops,
    split_loop_by_bars, SAMPLE_RATE,
    get_audio_output_devices, get_default_audio_output_device
)

# Read version
try:
    from version import __version__
except ImportError:
    __version__ = "1.0.dev"

# Lazy import for pyqtgraph - only load when needed
_pg = None
def _get_pg():
    global _pg
    if _pg is None:
        import pyqtgraph as pg
        _pg = pg
    return _pg

# Font family - will fall back to system fonts if Inter not loaded
FONT_LIGHT = "'Inter', 'Helvetica Neue', sans-serif"
FONT_BOLD = "'Inter', 'Helvetica Neue', sans-serif"

COLORS = {
    'bg': '#ececec',           # Light grey background
    'bg_waveform': '#2c2c2c',  # Dark waveform background
    'panel': '#ffffff',         # White panels
    'border': '#3a3a3a',       # Grid borders (darker)
    'text': '#1b1a1d',         # Dark text
    'text_dim': '#888888',     # Dimmed text
    'accent': '#ff6b35',       # Warm orange
    'accent_dim': '#ffb8a0',   # Lighter orange for hover
    'track1': '#1a223d',       # Dark blue
    'track2': '#9c704d',       # Brown
    'track3': '#7b7778',       # Grey
    'track4': '#fa451c',       # Orange
    'boundary': '#ff6b35',     # Orange boundaries
    'loop_region': '#ff6b3515', # Subtle orange tint
    'selected': '#ff6b3530',   # Selected orange tint
    'playhead': '#ffffff',     # White playhead
    'muted': '#d0d0d0',        # Light grey for muted
    'bar_highlight': '#ffffff08', # Very subtle highlight
    'split_cursor': '#ff4444', # Red split cursor
    'glue_cursor': '#4ecdc4',  # Teal glue cursor
}

# Tool modes for the waveform editor
TOOL_SELECT = 'select'
TOOL_SPLIT = 'split'
TOOL_GLUE = 'glue'
TOOL_REMOVE = 'remove'
TOOL_ADD = 'add'

TRACK_COLORS = [COLORS['track1'], COLORS['track2'], COLORS['track3'], COLORS['track4']]


class TimeAxisItem:
    """Wrapper for pyqtgraph AxisItem - created lazily"""
    def __new__(cls, *args, **kwargs):
        pg = _get_pg()

        class _TimeAxisItem(pg.AxisItem):
            def tickStrings(self, values, scale, spacing):
                strings = []
                for v in values:
                    secs = float(v)
                    mins = int(secs // 60)
                    secs_rem = secs % 60
                    ms = int((secs_rem % 1) * 1000)
                    secs_int = int(secs_rem)
                    strings.append(f"{mins}:{secs_int:02d}:{ms:03d}")
                return strings

        return _TimeAxisItem(*args, **kwargs)


class TrackMuteButton(QtWidgets.QPushButton):
    mute_toggled = Signal(int, bool)
    
    def __init__(self, track_num: int, color: str):
        super().__init__(f"{track_num}")
        self.track_num = track_num
        self.color = color
        self._muted = False
        self._update_style()
        self.clicked.connect(self._on_click)
        self.setFixedSize(26, 26)
        self.setCursor(Qt.PointingHandCursor)
    
    def _on_click(self):
        self._muted = not self._muted
        self._update_style()
        self.mute_toggled.emit(self.track_num, self._muted)
    
    def _update_style(self):
        if self._muted:
            self.setStyleSheet(f"QPushButton {{ background-color: {COLORS['muted']}; border: none; border-radius: 13px; color: {COLORS['text_dim']}; font-weight: 700; font-size: 11px; }}")
        else:
            self.setStyleSheet(f"QPushButton {{ background-color: {self.color}; border: none; border-radius: 13px; color: #ffffff; font-weight: 700; font-size: 11px; }}")


class WaveformWidget(QtWidgets.QWidget):
    loop_clicked = Signal(int)
    loop_region_changed = Signal(int, int, int)  # loop_index, new_start_sample, new_end_sample
    split_requested = Signal(int, int)  # loop_index, split_sample_position
    glue_requested = Signal(int, int)  # first_loop_index, second_loop_index
    remove_requested = Signal(int)  # loop_index to remove
    add_requested = Signal(int, int)  # start_sample, end_sample for new region
    seek_requested = Signal(int)  # sample_position to seek to within current region

    def __init__(self):
        super().__init__()

        # Lazy load pyqtgraph and create the plot widget
        pg = _get_pg()

        # Create layout
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create the actual plot widget
        self.plot_widget = pg.PlotWidget(axisItems={'bottom': TimeAxisItem(orientation='bottom')})
        layout.addWidget(self.plot_widget)

        # Forward essential methods to plot_widget
        self.plotItem = self.plot_widget.plotItem
        self.getPlotItem = self.plot_widget.getPlotItem
        self.setBackground = self.plot_widget.setBackground
        self.showGrid = self.plot_widget.showGrid
        self.getAxis = self.plot_widget.getAxis
        self.setMouseEnabled = self.plot_widget.setMouseEnabled
        self.setCursor = self.plot_widget.setCursor
        self.clear = self.plot_widget.clear
        self.addItem = self.plot_widget.addItem
        self.removeItem = self.plot_widget.removeItem
        self.plot = self.plot_widget.plot
        self.setYRange = self.plot_widget.setYRange
        self.setLimits = self.plot_widget.setLimits
        self.setXRange = self.plot_widget.setXRange
        self.enableAutoRange = self.plot_widget.enableAutoRange
        self.viewRange = self.plot_widget.viewRange
        self.viewport = self.plot_widget.viewport
        self.mapToScene = self.plot_widget.mapToScene
        self.scene = self.plot_widget.scene
        self.sigXRangeChanged = self.plot_widget.sigXRangeChanged

        self.setBackground(COLORS['bg_waveform'])
        self.showGrid(x=True, y=False, alpha=0.2)
        self.getAxis('left').hide()
        self.getAxis('bottom').setTextPen(COLORS['text_dim'])
        
        # Completely disable PyQtGraph's built-in mouse handling
        self.setMouseEnabled(x=False, y=False)
        vb = self.getPlotItem().getViewBox()
        vb.setMenuEnabled(False)
        
        # Default cursor
        self.setCursor(Qt.OpenHandCursor)
        
        self.waveform_items = {}
        self.boundary_lines = []
        self.loop_regions = []
        self.loop_labels = []
        self.bar_highlights = []
        self._muted_tracks = set()
        self._dragging_region = False
        self._highlighted_loop_index = -1
        
        # Pan state
        self._is_panning = False
        self._did_pan = False
        self._pan_start_x = 0
        self._pan_start_range = None
        
        # Tool and grid settings
        self._current_tool = TOOL_SELECT
        self._snap_to_grid = True
        self._highlight_bars = 4
        self._bpm = 120.0
        
        # Playback state for scrubbing
        self._is_playing = False
        
        # Cursor line for split tool (red dotted)
        self.split_cursor = pg.InfiniteLine(pos=0, angle=90, pen=pg.mkPen(COLORS['split_cursor'], width=2, style=Qt.DashLine), movable=False)
        self.split_cursor.setVisible(False)
        self.addItem(self.split_cursor)

        # Cursor line for glue tool (green solid)
        self.glue_cursor = pg.InfiniteLine(pos=0, angle=90, pen=pg.mkPen(COLORS['glue_cursor'], width=3), movable=False)
        self.glue_cursor.setVisible(False)
        self.addItem(self.glue_cursor)

        # Scrub cursor - ghost line shown when hovering over playing region
        self.scrub_cursor = pg.InfiniteLine(pos=0, angle=90, pen=pg.mkPen('#ffffff80', width=1, style=Qt.DotLine), movable=False)
        self.scrub_cursor.setVisible(False)
        self.addItem(self.scrub_cursor)

        # Preview region for add tool (cyan)
        self.add_preview = None  # Will be created when needed

        self.playhead = pg.InfiniteLine(pos=0, angle=90, pen=pg.mkPen(COLORS['playhead'], width=2), movable=False)
        self.playhead.setVisible(False)
        self.addItem(self.playhead)

        # Store pg reference for later use
        self._pg = pg
        
        self._loader = None
        self._session = None
        self._loops = []
        self._downsample_factor = 500
        self._audio_data = {}
        self._max_duration = 0
        
        # Install event filter on viewport for raw mouse events
        self.viewport().installEventFilter(self)
        self.viewport().setMouseTracking(True)
    
    def load_session(self, folder: str, tape_json_path: str = None, is_aiff: bool = False, session: 'TapeSession' = None):
        self._loader = AudioLoader(folder, is_aiff=is_aiff)
        if session is not None:
            self._session = session
        elif tape_json_path is not None:
            self._session = parse_tape_json(tape_json_path)
        else:
            return None
        self._bpm = self._session.bpm
        self._max_duration = self._loader.get_max_length() / SAMPLE_RATE
        self._update_waveforms()
        self._update_bar_highlights()
        # Disable auto-range after loading
        self.enableAutoRange(axis='x', enable=False)
        self.enableAutoRange(axis='y', enable=False)
        return self._loader
    
    def eventFilter(self, obj, event):
        """Handle mouse events from the viewport"""
        from PySide6.QtCore import QEvent
        
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            # Map widget position to scene, then to view coordinates
            scene_pos = self.mapToScene(event.position().toPoint())
            view_pos = self.plotItem.vb.mapSceneToView(scene_pos)
            click_time = view_pos.x()
            
            # Check if we're over a loop region edge (for resizing)
            over_edge = self._is_over_edge(click_time)
            
            if not over_edge:
                # Start potential pan (could be click or drag)
                self._is_panning = True
                self._did_pan = False  # Track if we actually moved
                self._pan_start_x = event.position().x()
                self._pan_start_range = list(self.viewRange()[0])
                self.setCursor(Qt.ClosedHandCursor)
                return True  # Consume the event
            
        elif event.type() == QEvent.MouseMove:
            scene_pos = self.mapToScene(event.position().toPoint())
            view_pos = self.plotItem.vb.mapSceneToView(scene_pos)
            click_time = view_pos.x()
            
            if self._is_panning and self._pan_start_range is not None:
                # Calculate pan delta
                dx = event.position().x() - self._pan_start_x
                
                # Only pan if moved more than a few pixels (distinguish click from drag)
                if abs(dx) > 5:
                    self._did_pan = True
                    view_range = self._pan_start_range
                    view_width = view_range[1] - view_range[0]
                    plot_width = self.viewport().width()
                    
                    if plot_width > 0:
                        data_dx = -dx * view_width / plot_width
                        new_start = view_range[0] + data_dx
                        new_end = view_range[1] + data_dx
                        
                        # Clamp to valid range
                        if new_start < 0:
                            new_end -= new_start
                            new_start = 0
                        if new_end > self._max_duration and self._max_duration > 0:
                            new_start -= (new_end - self._max_duration)
                            new_end = self._max_duration
                            new_start = max(0, new_start)
                        
                        self.setXRange(new_start, new_end, padding=0)
                return True
            else:
                # Update cursor and tool previews
                self._update_cursor_and_preview(click_time)
            
        elif event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            scene_pos = self.mapToScene(event.position().toPoint())
            view_pos = self.plotItem.vb.mapSceneToView(scene_pos)
            click_time = view_pos.x()
            
            was_panning = self._is_panning
            did_pan = getattr(self, '_did_pan', False)
            self._is_panning = False
            self._did_pan = False
            self._pan_start_range = None
            
            if was_panning and did_pan:
                # Actually dragged - just update cursor, don't trigger click
                self._update_cursor_and_preview(click_time)
                return True
            elif was_panning and not did_pan:
                # Was a click (no drag movement) - handle tool action
                self._update_cursor_and_preview(click_time)
                self._handle_click(click_time)
                return True
            else:
                # Check if over edge - let edge resizing work
                if not self._is_over_edge(click_time):
                    self._handle_click(click_time)
                    return True
        
        elif event.type() == QEvent.Wheel:
            # Handle mouse wheel for zooming
            scene_pos = self.mapToScene(event.position().toPoint())
            view_pos = self.plotItem.vb.mapSceneToView(scene_pos)
            center_time = view_pos.x()
            
            delta = event.angleDelta().y()
            zoom_factor = 1.2 if delta > 0 else 1/1.2
            
            view_range = self.viewRange()[0]
            view_width = view_range[1] - view_range[0]
            new_width = view_width / zoom_factor
            
            # Zoom centered on mouse position
            ratio = (center_time - view_range[0]) / view_width if view_width > 0 else 0.5
            new_start = center_time - new_width * ratio
            new_end = center_time + new_width * (1 - ratio)
            
            # Clamp
            if new_start < 0:
                new_end -= new_start
                new_start = 0
            if new_end > self._max_duration and self._max_duration > 0:
                new_start -= (new_end - self._max_duration)
                new_end = self._max_duration
                new_start = max(0, new_start)
            
            self.setXRange(new_start, new_end, padding=0)
            return True
        
        return super().eventFilter(obj, event)
    
    def _is_over_edge(self, click_time: float) -> bool:
        """Check if mouse is over a region edge (handles are always draggable)"""
        # Calculate tolerance based on current zoom (about 8 pixels worth)
        # Cap to a reasonable maximum of 0.1 seconds
        view_range = self.viewRange()[0]
        view_width = view_range[1] - view_range[0]
        pixels_per_sec = self.viewport().width() / view_width if view_width > 0 else 100
        tolerance_sec = min(0.1, max(0.01, 8 / pixels_per_sec))  # 8 pixels, capped at 0.1s
        
        for region in self.loop_regions:
            for line in region.lines:
                line_pos = line.value()
                if abs(click_time - line_pos) < tolerance_sec:
                    return True
        return False
    
    def _handle_click(self, click_time: float):
        """Handle click actions for different tools"""
        click_sample = int(click_time * SAMPLE_RATE)
        
        if self._current_tool == TOOL_SPLIT:
            self._handle_split_click(click_time, click_sample)
        elif self._current_tool == TOOL_GLUE:
            self._handle_glue_click(click_time)
        elif self._current_tool == TOOL_REMOVE:
            self._handle_remove_click(click_time)
        elif self._current_tool == TOOL_ADD:
            self._handle_add_click(click_time)
        else:  # TOOL_SELECT
            for i, loop in enumerate(self._loops):
                if loop.start_sample / SAMPLE_RATE <= click_time <= loop.end_sample / SAMPLE_RATE:
                    # If clicking on the currently selected loop while playing, seek to position
                    # But only if not near the edges (those are for handle dragging)
                    if self._is_playing and i == self._highlighted_loop_index:
                        loop_start = loop.start_sample / SAMPLE_RATE
                        loop_end = loop.end_sample / SAMPLE_RATE
                        
                        # Calculate edge buffer (match _is_over_edge)
                        view_range = self.viewRange()[0]
                        view_width = view_range[1] - view_range[0]
                        pixels_per_sec = self.viewport().width() / view_width if view_width > 0 else 100
                        edge_buffer = min(0.1, max(0.01, 8 / pixels_per_sec))
                        
                        # Only seek if not near edges
                        if click_time > loop_start + edge_buffer and click_time < loop_end - edge_buffer:
                            self.seek_requested.emit(click_sample)
                        # If near edge, don't do anything - let edge drag handle it
                    else:
                        # Select a different loop (or first click on this loop)
                        self.loop_clicked.emit(i)
                    break
    
    def _update_cursor_and_preview(self, click_time: float):
        """Update cursor and tool preview based on mouse position"""
        # Hide all cursors first
        self.split_cursor.setVisible(False)
        self.glue_cursor.setVisible(False)
        self.scrub_cursor.setVisible(False)
        self._hide_add_preview()
        
        # Check if hovering over a loop region and which one
        over_loop = False
        over_loop_index = -1
        for i, loop in enumerate(self._loops):
            if loop.start_sample / SAMPLE_RATE <= click_time <= loop.end_sample / SAMPLE_RATE:
                over_loop = True
                over_loop_index = i
                break
        
        # Check if over region edge
        if self._is_over_edge(click_time):
            self.setCursor(Qt.SizeHorCursor)
            return
        
        if self._current_tool == TOOL_SELECT:
            if over_loop:
                # If playing and hovering over the currently selected loop, show scrub cursor
                # But only if we're not too close to the edges (leave room for handle dragging)
                if self._is_playing and over_loop_index == self._highlighted_loop_index:
                    loop = self._loops[over_loop_index]
                    loop_start = loop.start_sample / SAMPLE_RATE
                    loop_end = loop.end_sample / SAMPLE_RATE
                    
                    # Calculate edge buffer (same as edge tolerance)
                    view_range = self.viewRange()[0]
                    view_width = view_range[1] - view_range[0]
                    pixels_per_sec = self.viewport().width() / view_width if view_width > 0 else 100
                    edge_buffer = min(0.1, max(0.01, 8 / pixels_per_sec))  # Match _is_over_edge
                    
                    # Only show scrub cursor if not near the edges
                    if click_time > loop_start + edge_buffer and click_time < loop_end - edge_buffer:
                        self.scrub_cursor.setPos(click_time)
                        self.scrub_cursor.setVisible(True)
                        self.setCursor(Qt.IBeamCursor)  # Text cursor indicates "click to position"
                    else:
                        # Near edge - show resize cursor handled by _is_over_edge check above
                        self.setCursor(Qt.PointingHandCursor)
                else:
                    self.setCursor(Qt.PointingHandCursor)
            else:
                self.setCursor(Qt.OpenHandCursor)
                
        elif self._current_tool == TOOL_SPLIT:
            if over_loop:
                snapped_time = self._snap_time_to_grid(click_time) if self._snap_to_grid else click_time
                self.split_cursor.setPos(snapped_time)
                self.split_cursor.setVisible(True)
                self.setCursor(Qt.CrossCursor)
            else:
                self.setCursor(Qt.OpenHandCursor)
                
        elif self._current_tool == TOOL_GLUE:
            # Calculate tolerance based on current zoom (about 15 pixels worth)
            # Cap to a reasonable maximum of 0.3 seconds
            view_range = self.viewRange()[0]
            view_width = view_range[1] - view_range[0]
            pixels_per_sec = self.viewport().width() / view_width if view_width > 0 else 100
            tolerance_sec = min(0.3, max(0.05, 15 / pixels_per_sec))  # 15 pixels, capped at 0.3s
            found_boundary = False
            
            for i in range(len(self._loops) - 1):
                loop1 = self._loops[i]
                loop2 = self._loops[i + 1]
                # Check both the end of loop1 and start of loop2 for adjacent loops
                boundary_time = loop1.end_sample / SAMPLE_RATE
                
                if abs(click_time - boundary_time) < tolerance_sec:
                    if abs(loop1.end_sample - loop2.start_sample) < SAMPLE_RATE:
                        self.glue_cursor.setPos(boundary_time)
                        self.glue_cursor.setVisible(True)
                        found_boundary = True
                        break
            
            if found_boundary:
                self.setCursor(Qt.PointingHandCursor)
            else:
                self.setCursor(Qt.OpenHandCursor)
                        
        elif self._current_tool == TOOL_ADD:
            snapped_time = self._snap_time_to_grid(click_time) if self._snap_to_grid else click_time
            bars = self._highlight_bars if self._highlight_bars > 0 else 4
            samples_per_bar = self._samples_per_bar()
            region_duration = bars * samples_per_bar / SAMPLE_RATE
            self._show_add_preview(snapped_time, snapped_time + region_duration)
            self.setCursor(Qt.CrossCursor)
            
        elif self._current_tool == TOOL_REMOVE:
            if over_loop:
                self.setCursor(Qt.ForbiddenCursor)
            else:
                self.setCursor(Qt.OpenHandCursor)
    
    def set_playing(self, playing: bool):
        """Set whether playback is active (for scrub cursor display)"""
        self._is_playing = playing
    
    def _update_waveforms(self):
        self.clear()
        self.waveform_items = {}
        self.loop_labels = []
        self.boundary_lines = []
        self.loop_regions = []
        self._audio_data = {}
        self.addItem(self.playhead)
        self.addItem(self.split_cursor)
        self.addItem(self.glue_cursor)
        self.addItem(self.scrub_cursor)
        
        if not self._loader:
            return
        
        track_height = 0.8
        spacing = 1.0
        
        for track_num in range(1, 5):
            audio = self._loader.load_track(track_num)
            if audio is None:
                continue
            
            mono = audio.mean(axis=1) if audio.ndim > 1 else audio
            downsampled = mono[::self._downsample_factor]
            time = np.arange(len(downsampled)) * self._downsample_factor / SAMPLE_RATE
            y_offset = (4 - track_num) * spacing
            normalized = downsampled * track_height * 0.4
            
            self._audio_data[track_num] = (time, normalized, y_offset)
            
            is_muted = track_num in self._muted_tracks
            color = COLORS['muted'] if is_muted else TRACK_COLORS[track_num - 1]
            pen = self._pg.mkPen(color=color, width=1)
            item = self.plot(time, normalized + y_offset, pen=pen)
            self.waveform_items[track_num] = item
        
        self.setYRange(-0.5, 4 * spacing)
        
        max_duration = self._loader.get_max_length() / SAMPLE_RATE if self._loader else 0
        if max_duration > 0:
            self.setLimits(xMin=0, xMax=max_duration, minXRange=1, maxXRange=max_duration)
            self.setXRange(0, max_duration)
    
    def set_track_muted(self, track_num: int, muted: bool):
        if muted:
            self._muted_tracks.add(track_num)
        else:
            self._muted_tracks.discard(track_num)
        
        if track_num in self.waveform_items and track_num in self._audio_data:
            time, normalized, y_offset = self._audio_data[track_num]
            color = COLORS['muted'] if muted else TRACK_COLORS[track_num - 1]
            pen = self._pg.mkPen(color=color, width=1)
            self.waveform_items[track_num].setData(time, normalized + y_offset, pen=pen)
    
    def set_loops(self, loops):
        self._loops = loops
        
        for line in self.boundary_lines:
            self.removeItem(line)
        for region in self.loop_regions:
            try:
                region.sigRegionChangeFinished.disconnect()
            except:
                pass
            self.removeItem(region)
        for label in self.loop_labels:
            self.removeItem(label)
        
        self.boundary_lines = []
        self.loop_regions = []
        self.loop_labels = []
        
        for i, loop in enumerate(loops):
            start_sec = loop.start_sample / SAMPLE_RATE
            end_sec = loop.end_sample / SAMPLE_RATE

            line = self._pg.InfiniteLine(pos=start_sec, angle=90, pen=self._pg.mkPen(COLORS['boundary'], width=2, style=Qt.DashLine), movable=False)
            self.addItem(line)
            self.boundary_lines.append(line)

            # Create region with draggable edges only (not the whole region)
            region = self._pg.LinearRegionItem(
                values=(start_sec, end_sec),
                brush=self._pg.mkBrush(COLORS['loop_region']),
                pen=self._pg.mkPen(COLORS['boundary'], width=2),
                movable=False,  # Region itself not movable, only edges
                swapMode='sort'
            )
            region.setZValue(-10)
            region.loop_index = i  # Store loop index for callback

            # Make only the edge lines movable with resize cursor
            for line in region.lines:
                line.setMovable(True)
                line.setCursor(Qt.SizeHorCursor)
                line.setHoverPen(self._pg.mkPen(COLORS['accent'], width=3))
            
            # Connect signal for when region drag finishes
            region.sigRegionChangeFinished.connect(lambda reg=region: self._on_region_changed(reg))
            region.sigRegionChanged.connect(lambda reg=region: self._on_region_changing(reg))
            
            self.addItem(region)
            self.loop_regions.append(region)
            
            label_text = getattr(loop, 'custom_name', None) or f"L{loop.index}"
            label = self._pg.TextItem(label_text, color=COLORS['accent'], anchor=(0, 1), angle=-90)
            label.setPos(start_sec + 0.8, 4.2)
            self.addItem(label)
            self.loop_labels.append(label)
    
    def _on_region_changing(self, region):
        """Called while dragging - update the boundary line position"""
        self._dragging_region = True
        idx = region.loop_index
        if 0 <= idx < len(self.boundary_lines):
            start_sec, end_sec = region.getRegion()
            
            # Snap to grid if enabled
            if self._snap_to_grid:
                start_sec = self._snap_time_to_grid(start_sec)
                end_sec = self._snap_time_to_grid(end_sec)
                # Update region position to snapped values
                region.blockSignals(True)
                region.setRegion((start_sec, end_sec))
                region.blockSignals(False)
            
            self.boundary_lines[idx].setPos(start_sec)
            # Update label position too
            if 0 <= idx < len(self.loop_labels):
                self.loop_labels[idx].setPos(start_sec + 0.8, 4.2)
    
    def _on_region_changed(self, region):
        """Called when region drag finishes - emit signal to update loop data"""
        self._dragging_region = False
        idx = region.loop_index
        start_sec, end_sec = region.getRegion()
        
        # Snap to grid if enabled
        if self._snap_to_grid:
            start_sec = self._snap_time_to_grid(start_sec)
            end_sec = self._snap_time_to_grid(end_sec)
            # Update region position to snapped values
            region.blockSignals(True)
            region.setRegion((start_sec, end_sec))
            region.blockSignals(False)
        
        new_start = int(start_sec * SAMPLE_RATE)
        new_end = int(end_sec * SAMPLE_RATE)
        self.loop_region_changed.emit(idx, new_start, new_end)
    
    def highlight_loop(self, loop_index: int):
        """Highlight the specified loop and unhighlight all others"""
        self._highlighted_loop_index = loop_index - 1  # Store 0-based index
        
        for i, region in enumerate(self.loop_regions):
            if i == loop_index - 1:
                brush = self._pg.mkBrush(COLORS['selected'])
            else:
                brush = self._pg.mkBrush(COLORS['loop_region'])
            region.setBrush(brush)
        
        # Force scene to repaint
        if self.scene():
            self.scene().update()
    
    def set_playhead_position(self, sample_position: int):
        self.playhead.setPos(sample_position / SAMPLE_RATE)
        self.playhead.setVisible(True)
    
    def scroll_to_loop(self, loop_index: int):
        """Scroll the view to show the specified loop"""
        if loop_index < 0 or loop_index >= len(self._loops):
            return
        
        loop = self._loops[loop_index]
        start_sec = loop.start_sample / SAMPLE_RATE
        end_sec = loop.end_sample / SAMPLE_RATE
        loop_duration = end_sec - start_sec
        
        # Get current view range
        view_range = self.viewRange()[0]
        view_width = view_range[1] - view_range[0]
        
        # Check if loop is already visible
        if start_sec >= view_range[0] and end_sec <= view_range[1]:
            return  # Already visible
        
        # Center the view on the loop, with some padding
        padding = max(loop_duration * 0.5, view_width * 0.1)
        new_start = start_sec - padding
        new_end = new_start + view_width
        
        # Clamp to valid range
        max_duration = self._loader.get_max_length() / SAMPLE_RATE if self._loader else 0
        if new_start < 0:
            new_start = 0
            new_end = view_width
        if new_end > max_duration:
            new_end = max_duration
            new_start = max(0, new_end - view_width)
        
        self.setXRange(new_start, new_end, padding=0)
    
    
    def _show_add_preview(self, start_sec: float, end_sec: float):
        """Show a preview region for the add tool"""
        if self.add_preview is not None:
            self.removeItem(self.add_preview)

        self.add_preview = self._pg.LinearRegionItem(
            values=(start_sec, end_sec),
            brush=self._pg.mkBrush('#44aaff40'),
            pen=self._pg.mkPen('#44aaff', width=2, style=Qt.DashLine),
            movable=False
        )
        self.add_preview.setZValue(-5)
        # Make non-interactive so it doesn't block mouse events
        self.add_preview.setAcceptedMouseButtons(Qt.NoButton)
        for line in self.add_preview.lines:
            line.setMovable(False)
            line.setAcceptedMouseButtons(Qt.NoButton)
        self.addItem(self.add_preview)
    
    def _hide_add_preview(self):
        """Hide the add tool preview region"""
        if self.add_preview is not None:
            self.removeItem(self.add_preview)
            self.add_preview = None
    
    def _handle_split_click(self, click_time: float, click_sample: int):
        """Handle click with split tool - split a loop at click position"""
        for i, loop in enumerate(self._loops):
            if loop.start_sample / SAMPLE_RATE <= click_time <= loop.end_sample / SAMPLE_RATE:
                # Snap to grid if enabled
                if self._snap_to_grid:
                    click_sample = self._snap_sample_to_grid(click_sample)
                
                # Don't split too close to edges
                min_samples = int(0.25 * self._samples_per_bar())  # At least 1/4 bar
                if (click_sample - loop.start_sample > min_samples and 
                    loop.end_sample - click_sample > min_samples):
                    self.split_requested.emit(i, click_sample)
                break
    
    def _handle_glue_click(self, click_time: float):
        """Handle click with glue tool - find two adjacent loops to join"""
        # Calculate tolerance based on current zoom (about 15 pixels worth)
        # Cap to a reasonable maximum of 0.3 seconds
        view_range = self.viewRange()[0]
        view_width = view_range[1] - view_range[0]
        pixels_per_sec = self.viewport().width() / view_width if view_width > 0 else 100
        tolerance_sec = min(0.3, max(0.05, 15 / pixels_per_sec))  # 15 pixels, capped at 0.3s
        
        for i in range(len(self._loops) - 1):
            loop1 = self._loops[i]
            loop2 = self._loops[i + 1]
            
            # Check if loops are adjacent (end of one = start of next)
            boundary_time = loop1.end_sample / SAMPLE_RATE
            
            if abs(click_time - boundary_time) < tolerance_sec:
                # Check if they're truly adjacent
                if abs(loop1.end_sample - loop2.start_sample) < SAMPLE_RATE:  # Within 1 second
                    self.glue_requested.emit(i, i + 1)
                    break
    
    def _handle_remove_click(self, click_time: float):
        """Handle click with remove tool - request removal of clicked loop"""
        for i, loop in enumerate(self._loops):
            if loop.start_sample / SAMPLE_RATE <= click_time <= loop.end_sample / SAMPLE_RATE:
                self.remove_requested.emit(i)
                break
    
    def _handle_add_click(self, click_time: float):
        """Handle click with add tool - add a new region at the highlight grid size"""
        # Snap to grid
        if self._snap_to_grid:
            click_time = self._snap_time_to_grid(click_time)
        
        click_sample = int(click_time * SAMPLE_RATE)
        
        # Use the highlight bars setting for the region size
        bars = self._highlight_bars if self._highlight_bars > 0 else 4
        samples_per_bar = self._samples_per_bar()
        region_samples = int(bars * samples_per_bar)
        
        start_sample = click_sample
        end_sample = click_sample + region_samples
        
        # Make sure we don't go past the end of the audio
        if self._loader:
            max_samples = self._loader.get_max_length()
            if end_sample > max_samples:
                end_sample = max_samples
                start_sample = max(0, end_sample - region_samples)
        
        self.add_requested.emit(start_sample, end_sample)
    
    def set_tool(self, tool: str):
        """Set the current editing tool"""
        self._current_tool = tool
        self.split_cursor.setVisible(False)
        self.glue_cursor.setVisible(False)
        self._hide_add_preview()
        
        # Update cursor based on tool - default is open hand for panning
        # Cursor will be updated dynamically in _on_mouse_moved based on hover
        if tool == TOOL_SPLIT:
            self.setCursor(Qt.CrossCursor)
        elif tool == TOOL_GLUE:
            self.setCursor(Qt.OpenHandCursor)
        elif tool == TOOL_REMOVE:
            self.setCursor(Qt.ForbiddenCursor)
        elif tool == TOOL_ADD:
            self.setCursor(Qt.CrossCursor)
        else:
            # SELECT mode - default to open hand, pointer on hover over regions
            self.setCursor(Qt.OpenHandCursor)
    
    def set_snap_to_grid(self, enabled: bool):
        """Enable or disable snap to grid"""
        self._snap_to_grid = enabled
    
    def set_bpm(self, bpm: float):
        """Set BPM for grid calculations"""
        self._bpm = bpm
        self._update_bar_highlights()
    
    def set_highlight_bars(self, bars: int):
        """Set the bar interval for highlights (0 = off)"""
        self._highlight_bars = bars
        self._update_bar_highlights()
    
    def _samples_per_bar(self) -> float:
        """Calculate samples per bar based on BPM"""
        return SAMPLE_RATE * 60.0 / self._bpm * 4
    
    def _snap_sample_to_grid(self, sample: int) -> int:
        """Snap a sample position to the nearest grid line based on current zoom"""
        grid_samples = self._get_grid_spacing_samples()
        return int(round(sample / grid_samples) * grid_samples)
    
    def _snap_time_to_grid(self, time_sec: float) -> float:
        """Snap a time position to the nearest grid line"""
        sample = int(time_sec * SAMPLE_RATE)
        snapped = self._snap_sample_to_grid(sample)
        return snapped / SAMPLE_RATE
    
    def _get_grid_spacing_samples(self) -> int:
        """Get grid spacing in samples based on current zoom level"""
        # Get current view range
        view_range = self.viewRange()[0]
        visible_duration = view_range[1] - view_range[0]
        
        samples_per_bar = self._samples_per_bar()
        
        # Choose grid resolution based on visible duration
        # More zoomed in = finer grid
        if visible_duration < 2:  # Very zoomed in: 1/16 bar
            return int(samples_per_bar / 16)
        elif visible_duration < 5:  # 1/8 bar
            return int(samples_per_bar / 8)
        elif visible_duration < 10:  # 1/4 bar
            return int(samples_per_bar / 4)
        elif visible_duration < 30:  # 1/2 bar
            return int(samples_per_bar / 2)
        elif visible_duration < 60:  # 1 bar
            return int(samples_per_bar)
        elif visible_duration < 120:  # 2 bars
            return int(samples_per_bar * 2)
        else:  # 4 bars
            return int(samples_per_bar * 4)
    
    def _update_bar_highlights(self):
        """Update the bar highlight regions"""
        # Remove existing highlights
        for highlight in self.bar_highlights:
            self.removeItem(highlight)
        self.bar_highlights = []
        
        if self._highlight_bars <= 0 or not self._loader:
            return
        
        samples_per_bar = self._samples_per_bar()
        highlight_samples = samples_per_bar * self._highlight_bars
        max_samples = self._loader.get_max_length()
        
        # Draw alternating highlight regions
        pos = 0
        is_highlighted = True
        while pos < max_samples:
            if is_highlighted:
                start_sec = pos / SAMPLE_RATE
                end_sec = min(pos + highlight_samples, max_samples) / SAMPLE_RATE

                region = self._pg.LinearRegionItem(
                    values=(start_sec, end_sec),
                    brush=self._pg.mkBrush(COLORS['bar_highlight']),
                    pen=self._pg.mkPen(None),
                    movable=False
                )
                region.setZValue(-20)  # Behind loop regions
                # Make completely non-interactive so it doesn't block panning
                region.setAcceptedMouseButtons(Qt.NoButton)
                for line in region.lines:
                    line.setMovable(False)
                    line.setAcceptedMouseButtons(Qt.NoButton)
                self.addItem(region)
                self.bar_highlights.append(region)
            
            pos += highlight_samples
            is_highlighted = not is_highlighted


class WaveformContainer(QtWidgets.QWidget):
    loop_clicked = Signal(int)
    track_mute_changed = Signal(int, bool)
    loop_region_changed = Signal(int, int, int)  # loop_index, new_start_sample, new_end_sample
    split_requested = Signal(int, int)  # loop_index, split_sample_position
    glue_requested = Signal(int, int)  # first_loop_index, second_loop_index
    remove_requested = Signal(int)  # loop_index to remove
    add_requested = Signal(int, int)  # start_sample, end_sample for new region
    seek_requested = Signal(int)  # sample_position to seek to within current region
    
    def __init__(self):
        super().__init__()
        # Add border to waveform container for OP-1 grid style
        self.setStyleSheet(f"""
            WaveformContainer {{
                background-color: {COLORS['bg_waveform']};
                border: 2px solid {COLORS['border']};
                border-radius: 3px;
            }}
        """)
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 8, 8, 8)  # No left margin - buttons sit there
        main_layout.setSpacing(4)

        # Scrollbar at top for quick panning
        scroll_container = QtWidgets.QHBoxLayout()
        scroll_container.setContentsMargins(32, 0, 0, 0)  # Offset to align with waveform (button width)
        self.scroll_bar = QtWidgets.QScrollBar(Qt.Horizontal)
        self.scroll_bar.setStyleSheet(f"""
            QScrollBar:horizontal {{
                background: {COLORS['bg']};
                height: 12px;
                border-radius: 6px;
                border: 1px solid {COLORS['border']};
            }}
            QScrollBar::handle:horizontal {{
                background: {COLORS['text_dim']};
                border-radius: 5px;
                min-width: 40px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {COLORS['accent']};
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0px;
            }}
        """)
        self.scroll_bar.setMinimum(0)
        self.scroll_bar.setMaximum(10000)  # Use finer granularity
        self.scroll_bar.setSingleStep(100)
        self.scroll_bar.setPageStep(1000)  # Will be updated dynamically
        self.scroll_bar.valueChanged.connect(self._on_scroll)
        scroll_container.addWidget(self.scroll_bar)
        main_layout.addLayout(scroll_container)
        
        # Waveform area with mute buttons
        waveform_layout = QtWidgets.QHBoxLayout()
        waveform_layout.setContentsMargins(0, 0, 0, 0)
        waveform_layout.setSpacing(0)  # No spacing so buttons can sit in margin

        mute_panel = QtWidgets.QWidget()
        mute_panel.setFixedWidth(32)  # Fit inside the left margin
        mute_layout = QtWidgets.QVBoxLayout(mute_panel)
        mute_layout.setContentsMargins(0, 30, 15, 30)  # Added 8px right margin
        mute_layout.setSpacing(0)
        
        self.mute_buttons = []
        for i in range(4):
            btn = TrackMuteButton(i + 1, TRACK_COLORS[i])
            btn.mute_toggled.connect(self._on_mute_toggled)
            self.mute_buttons.append(btn)
        
        for btn in self.mute_buttons:
            mute_layout.addWidget(btn, alignment=Qt.AlignCenter)
            mute_layout.addStretch()
        mute_layout.takeAt(mute_layout.count() - 1)
        
        self.waveform = WaveformWidget()
        self.waveform.loop_clicked.connect(self.loop_clicked)
        self.waveform.loop_region_changed.connect(self.loop_region_changed)
        self.waveform.split_requested.connect(self.split_requested)
        self.waveform.glue_requested.connect(self.glue_requested)
        self.waveform.remove_requested.connect(self.remove_requested)
        self.waveform.add_requested.connect(self.add_requested)
        self.waveform.seek_requested.connect(self.seek_requested)
        
        # Connect waveform view changes to update scrollbar
        self.waveform.sigXRangeChanged.connect(self._on_view_changed)
        
        waveform_layout.addWidget(mute_panel)
        waveform_layout.addWidget(self.waveform, stretch=1)
        main_layout.addLayout(waveform_layout, stretch=1)
        
        self._updating_scroll = False
    
    def _on_mute_toggled(self, track_num, muted):
        self.waveform.set_track_muted(track_num, muted)
        self.track_mute_changed.emit(track_num, muted)
    
    def _on_scroll(self, value):
        """Handle scrollbar value change - pan the waveform"""
        if self._updating_scroll:
            return
        
        max_duration = self.waveform._max_duration
        if max_duration <= 0:
            return
        
        view_range = self.waveform.viewRange()[0]
        view_width = view_range[1] - view_range[0]
        
        if view_width >= max_duration:
            return  # Can't scroll if view shows everything
        
        # Convert scroll value to position (value is in units of 1/10000 of total duration)
        position = (value / 10000.0) * max_duration
        position = max(0, min(position, max_duration - view_width))
        
        self._updating_scroll = True
        try:
            self.waveform.setXRange(position, position + view_width, padding=0)
        finally:
            self._updating_scroll = False
    
    def _on_view_changed(self, *args):
        """Handle waveform view change - update scrollbar position and size"""
        if self._updating_scroll:
            return
        
        max_duration = self.waveform._max_duration
        if max_duration <= 0:
            return
        
        view_range = self.waveform.viewRange()[0]
        view_start = max(0, view_range[0])
        view_width = view_range[1] - view_range[0]
        
        self._updating_scroll = True
        try:
            # Calculate page step (determines scrollbar handle size)
            # Page step represents the visible portion relative to total
            visible_ratio = min(1.0, view_width / max_duration)
            page_step = int(visible_ratio * 10000)
            page_step = max(100, page_step)  # Minimum handle size
            self.scroll_bar.setPageStep(page_step)
            
            if view_width >= max_duration:
                # View shows everything, scrollbar should fill the track
                self.scroll_bar.setValue(0)
            else:
                # Calculate scroll position
                scroll_value = int((view_start / max_duration) * 10000)
                scroll_value = max(0, min(10000 - page_step, scroll_value))
                self.scroll_bar.setValue(scroll_value)
        finally:
            self._updating_scroll = False
    
    def load_session(self, folder, tape_json_path=None, is_aiff=False, session=None):
        loader = self.waveform.load_session(folder, tape_json_path, is_aiff=is_aiff, session=session)
        if loader:
            # Initialize scrollbar after session loads
            self._on_view_changed()
        return loader
    
    def set_loops(self, loops):
        self.waveform.set_loops(loops)
    
    def highlight_loop(self, loop_index):
        self.waveform.highlight_loop(loop_index)
    
    def set_playhead_position(self, sample_position):
        self.waveform.set_playhead_position(sample_position)
    
    def set_tool(self, tool: str):
        self.waveform.set_tool(tool)
    
    def set_snap_to_grid(self, enabled: bool):
        self.waveform.set_snap_to_grid(enabled)
    
    def set_highlight_bars(self, bars: int):
        self.waveform.set_highlight_bars(bars)
    
    def scroll_to_loop(self, loop_index: int):
        self.waveform.scroll_to_loop(loop_index)
    
    def set_playing(self, playing: bool):
        """Set whether playback is active (for scrub cursor display)"""
        self.waveform.set_playing(playing)


class ComboBoxDelegate(QtWidgets.QStyledItemDelegate):
    """Custom delegate for QComboBox dropdown items with orange hover/selection"""

    def paint(self, painter, option, index):
        # Check if item is selected or hovered
        is_selected = option.state & QtWidgets.QStyle.State_Selected
        is_hovered = option.state & QtWidgets.QStyle.State_MouseOver

        # Set background color
        if is_selected or is_hovered:
            painter.fillRect(option.rect, QtGui.QColor(COLORS['accent']))
        else:
            painter.fillRect(option.rect, QtGui.QColor(COLORS['border']))

        # Draw text
        painter.setPen(QtGui.QColor('white'))
        text = index.data(Qt.DisplayRole)
        painter.drawText(option.rect, Qt.AlignLeft | Qt.AlignVCenter, f"  {text}")


class LoopInfoPanel(QtWidgets.QWidget):
    name_changed = Signal(int, str)
    split_requested = Signal(int, int)

    def __init__(self):
        super().__init__()
        self.setMinimumWidth(220)
        self.setMaximumWidth(280)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QtWidgets.QLabel("LOOP INFO")
        title.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px; font-weight: 600;")
        layout.addWidget(title)

        # Name field - make it taller
        name_layout = QtWidgets.QVBoxLayout()
        name_label = QtWidgets.QLabel("NAME:")
        name_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px; font-weight: 600;")
        name_layout.addWidget(name_label)
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setMinimumHeight(32)
        self.name_edit.editingFinished.connect(self._on_name_changed)
        name_layout.addWidget(self.name_edit)
        layout.addLayout(name_layout)

        layout.addSpacing(4)

        # Only keep essential info fields: bars, duration, start, end
        self.info_labels = {}
        for key, label_text in [('bars', 'BARS'), ('duration', 'DURATION'), ('start', 'START'), ('end', 'END')]:
            row = QtWidgets.QHBoxLayout()
            info_label = QtWidgets.QLabel(f"{label_text}:")
            info_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px; font-weight: 600;")
            row.addWidget(info_label)
            value = QtWidgets.QLabel("-")
            row.addWidget(value, stretch=1)
            layout.addLayout(row)
            self.info_labels[key] = value

        layout.addSpacing(8)
        subsplit_label = QtWidgets.QLabel("SUB-SPLIT")
        subsplit_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px; font-weight: 600;")
        layout.addWidget(subsplit_label)

        split_layout = QtWidgets.QHBoxLayout()
        self.split_combo = QtWidgets.QComboBox()
        self.split_combo.addItems(["1", "2", "4", "8", "16", "32"])
        self.split_combo.setCurrentText("4")

        # Set custom delegate for dropdown items
        self.split_combo.setItemDelegate(ComboBoxDelegate())

        self.split_btn = QtWidgets.QPushButton("SPLIT")
        self.split_btn.clicked.connect(self._on_split_clicked)
        self.split_btn.setEnabled(False)
        split_layout.addWidget(self.split_combo)
        bars_label = QtWidgets.QLabel("BARS")
        bars_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px; font-weight: 600;")
        split_layout.addWidget(bars_label)
        split_layout.addWidget(self.split_btn)
        split_layout.addStretch()
        layout.addLayout(split_layout)
        
        layout.addStretch()
        
        self.setStyleSheet(f"""
            LoopInfoPanel {{
                background-color: transparent;
                border: 2px solid {COLORS['border']};
                border-radius: 8px;
            }}
            QLabel {{
                color: {COLORS['text']};
                border: none;
                background-color: transparent;
            }}
            QLineEdit {{
                background-color: {COLORS['bg']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                padding: 8px;
                color: {COLORS['text']};
                font-size: 14px;
            }}
            QComboBox {{
                background-color: {COLORS['border']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                padding: 6px 8px;
                color: white;
            }}
            QComboBox QAbstractItemView {{
                background-color: {COLORS['border']};
                border: 1px solid {COLORS['border']};
                outline: none;
            }}
            QComboBox QAbstractItemView::item {{
                padding: 4px 8px;
                color: white;
                background-color: {COLORS['border']};
            }}
            QComboBox QAbstractItemView::item:hover {{
                background-color: {COLORS['accent']};
                color: white;
            }}
            QComboBox QAbstractItemView::item:selected {{
                background-color: {COLORS['accent']};
                color: white;
            }}
            QComboBox QAbstractItemView::item:selected:hover {{
                background-color: {COLORS['accent']};
                color: white;
            }}
            QPushButton {{
                background-color: {COLORS['accent']};
                border: none;
                border-radius: 6px;
                color: white;
                padding: 8px 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent_dim']};
            }}
            QPushButton:disabled {{
                background-color: {COLORS['border']};
                color: {COLORS['text_dim']};
            }}
        """)
        
        self._current_loop_index = -1
        self._current_loop_bars = 0
    
    def set_loop(self, loop, index):
        self._current_loop_index = index
        self._current_loop_bars = loop.length_bars
        
        self.name_edit.setText(getattr(loop, 'custom_name', None) or f"L{loop.index:03d}")
        
        def fmt(samples):
            s = samples / SAMPLE_RATE
            return f"{int(s // 60)}:{int(s % 60):02d}:{int((s % 1) * 1000):03d}"
        
        bars = loop.length_bars
        self.info_labels['bars'].setText(f"{bars:.1f}" if bars % 1 else f"{int(bars)}")
        self.info_labels['duration'].setText(fmt(loop.length_samples))
        self.info_labels['start'].setText(fmt(loop.start_sample))
        self.info_labels['end'].setText(fmt(loop.end_sample))
        self.split_btn.setEnabled(bars > 1)
    
    def clear(self):
        self._current_loop_index = -1
        self.name_edit.clear()
        for label in self.info_labels.values():
            label.setText("-")
        self.split_btn.setEnabled(False)
    
    def _on_name_changed(self):
        if self._current_loop_index >= 0:
            self.name_changed.emit(self._current_loop_index, self.name_edit.text())
    
    def _on_split_clicked(self):
        if self._current_loop_index >= 0:
            bar_count = int(self.split_combo.currentText())
            if bar_count < self._current_loop_bars:
                self.split_requested.emit(self._current_loop_index, bar_count)


class PlaybackControls(QtWidgets.QWidget):
    play_clicked = Signal()
    pause_clicked = Signal()
    stop_clicked = Signal()
    loop_toggled = Signal(bool)
    
    def __init__(self):
        super().__init__()
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        btn_style = f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #ffffff, stop:1 {COLORS['panel']});
                border: 2px solid {COLORS['border']};
                border-radius: 24px;
                color: {COLORS['text']};
                font-size: 20px;
                padding: 0px;
                min-width: 48px;
                min-height: 48px;
                max-width: 48px;
                max-height: 48px;
            }}
            QPushButton:checked {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {COLORS['accent']}, stop:1 #cc5528);
                color: white;
                border-color: {COLORS['accent']};
            }}
            QPushButton:hover:!checked {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #ffffff, stop:1 {COLORS['bg']});
                border-color: {COLORS['text']};
            }}
            QPushButton:pressed {{
                background: qlineargradient(x1:1, y1:1, x2:0, y2:0,
                    stop:0 #ffffff, stop:1 {COLORS['text_dim']});
            }}
        """

        # Play/Pause button with icon
        self.play_btn = QtWidgets.QPushButton()
        self.play_btn.setIcon(QtGui.QIcon('assets/icons/play.png'))
        self.play_btn.setIconSize(QtCore.QSize(32, 32))
        self.play_btn.setCheckable(True)
        self.play_btn.setStyleSheet(btn_style)
        self.play_btn.clicked.connect(self._on_play_clicked)

        # Stop button with icon
        self.stop_btn = QtWidgets.QPushButton()
        self.stop_btn.setIcon(QtGui.QIcon('assets/icons/stop.png'))
        self.stop_btn.setIconSize(QtCore.QSize(32, 32))
        self.stop_btn.setStyleSheet(btn_style)
        self.stop_btn.clicked.connect(self._on_stop_clicked)

        # Loop button with icon
        self.loop_btn = QtWidgets.QPushButton()
        self.loop_btn.setIcon(QtGui.QIcon('assets/icons/loop.png'))
        self.loop_btn.setIconSize(QtCore.QSize(32, 32))
        self.loop_btn.setCheckable(True)
        self.loop_btn.setChecked(True)
        self.loop_btn.setStyleSheet(btn_style)
        self.loop_btn.toggled.connect(self.loop_toggled)
        
        # Time label moved to center between playback and tools - larger size
        self.time_label = QtWidgets.QLabel("0:00:000")
        self.time_label.setStyleSheet(f"""
            color: {COLORS['text']};
            font-family: {FONT_LIGHT};
            font-size: 32px;
            font-weight: 300;
            padding: 0 24px;
        """)
        self.time_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(self.play_btn)
        layout.addWidget(self.stop_btn)
        layout.addWidget(self.loop_btn)
    
    def _on_play_clicked(self, checked):
        if checked:
            self.play_clicked.emit()
        else:
            self.pause_clicked.emit()
    
    def _on_stop_clicked(self):
        self.play_btn.setChecked(False)
        self.stop_clicked.emit()
    
    def set_playing(self, playing):
        self.play_btn.setChecked(playing)
    
    def update_time(self, sample_position):
        secs = sample_position / SAMPLE_RATE
        self.time_label.setText(f"{int(secs // 60)}:{int(secs % 60):02d}:{int((secs % 1) * 1000):03d}")


class AudioDeviceSelector(QtWidgets.QWidget):
    """Dropdown selector for audio output device"""
    device_changed = Signal(object)  # Emits QAudioDevice or None for system default
    
    def __init__(self):
        super().__init__()
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        # Label
        label = QtWidgets.QLabel("OUTPUT:")
        label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px; font-weight: 600;")
        layout.addWidget(label)
        
        # Device dropdown
        self.combo = QtWidgets.QComboBox()
        self.combo.setMinimumWidth(180)
        self.combo.setMaximumWidth(280)
        self.combo.currentIndexChanged.connect(self._on_selection_changed)
        
        # Set custom delegate for dropdown items
        self.combo.setItemDelegate(ComboBoxDelegate())
        
        self.combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {COLORS['border']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                padding: 6px 8px;
                color: white;
                font-size: 11px;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 6px solid white;
                margin-right: 6px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {COLORS['border']};
                border: 1px solid {COLORS['border']};
                outline: none;
            }}
            QComboBox QAbstractItemView::item {{
                padding: 4px 8px;
                color: white;
                background-color: {COLORS['border']};
            }}
            QComboBox QAbstractItemView::item:hover {{
                background-color: {COLORS['accent']};
                color: white;
            }}
            QComboBox QAbstractItemView::item:selected {{
                background-color: {COLORS['accent']};
                color: white;
            }}
        """)
        layout.addWidget(self.combo)
        
        # Refresh button
        self.refresh_btn = QtWidgets.QPushButton("↻")
        self.refresh_btn.setFixedSize(28, 28)
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.setToolTip("Refresh audio devices")
        self.refresh_btn.clicked.connect(self.refresh_devices)
        self.refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['border']};
                border: none;
                border-radius: 14px;
                color: white;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent']};
            }}
        """)
        layout.addWidget(self.refresh_btn)
        
        self._devices = []  # List of QAudioDevice
        self.refresh_devices()
    
    def refresh_devices(self):
        """Refresh the list of available audio devices"""
        current_id = None
        if self.combo.currentIndex() > 0:  # Not "System Default"
            idx = self.combo.currentIndex() - 1
            if idx < len(self._devices):
                current_id = self._devices[idx].id()
        
        self.combo.blockSignals(True)
        self.combo.clear()
        
        # First option is always system default
        default_device = get_default_audio_output_device()
        default_name = default_device.description() if default_device else "Default"
        self.combo.addItem(f"🔊 System Default ({default_name})")
        
        # Add all available devices
        self._devices = get_audio_output_devices()
        for device in self._devices:
            name = device.description() or device.id().data().decode('utf-8', errors='ignore')
            self.combo.addItem(f"  {name}")
        
        # Restore selection if possible
        if current_id:
            for i, device in enumerate(self._devices):
                if device.id() == current_id:
                    self.combo.setCurrentIndex(i + 1)
                    break
        
        self.combo.blockSignals(False)
    
    def _on_selection_changed(self, index):
        """Handle device selection change"""
        if index == 0:
            # System default
            self.device_changed.emit(None)
        elif index > 0 and index - 1 < len(self._devices):
            self.device_changed.emit(self._devices[index - 1])
    
    def get_selected_device(self):
        """Get the currently selected device (None for system default)"""
        index = self.combo.currentIndex()
        if index == 0 or index - 1 >= len(self._devices):
            return None
        return self._devices[index - 1]


class EditToolbar(QtWidgets.QWidget):
    """Toolbar for loop editing tools: Select, Split, Glue, Remove, Add, Snap"""
    tool_changed = Signal(str)  # Emits TOOL_SELECT, TOOL_SPLIT, TOOL_GLUE, TOOL_REMOVE, or TOOL_ADD
    snap_toggled = Signal(bool)  # Emits True when snap is enabled, False when disabled
    
    def __init__(self):
        super().__init__()
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        
        self._current_tool = TOOL_SELECT
        
        btn_style = f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #ffffff, stop:1 {COLORS['panel']});
                border: 2px solid {COLORS['border']};
                border-radius: 24px;
                color: {COLORS['text']};
                font-size: 20px;
                padding: 0px;
                min-width: 48px;
                min-height: 48px;
                max-width: 48px;
                max-height: 48px;
            }}
            QPushButton:checked {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {COLORS['accent']}, stop:1 #cc5528);
                color: white;
                border-color: {COLORS['accent']};
            }}
            QPushButton:hover:!checked {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #ffffff, stop:1 {COLORS['bg']});
                border-color: {COLORS['text']};
            }}
            QPushButton:pressed {{
                background: qlineargradient(x1:1, y1:1, x2:0, y2:0,
                    stop:0 #ffffff, stop:1 {COLORS['text_dim']});
            }}
        """

        # SELECT - Arrow/cursor icon
        self.select_btn = QtWidgets.QPushButton()
        self.select_btn.setIcon(QtGui.QIcon('assets/icons/select.png'))
        self.select_btn.setIconSize(QtCore.QSize(32, 32))
        self.select_btn.setCheckable(True)
        self.select_btn.setChecked(True)
        self.select_btn.setStyleSheet(btn_style)
        self.select_btn.setCursor(Qt.PointingHandCursor)
        self.select_btn.setToolTip("Select tool (S)")
        self.select_btn.clicked.connect(lambda: self._set_tool(TOOL_SELECT))

        # SPLIT - Scissors icon
        self.split_btn = QtWidgets.QPushButton()
        self.split_btn.setIcon(QtGui.QIcon('assets/icons/split.png'))
        self.split_btn.setIconSize(QtCore.QSize(32, 32))
        self.split_btn.setCheckable(True)
        self.split_btn.setStyleSheet(btn_style)
        self.split_btn.setCursor(Qt.PointingHandCursor)
        self.split_btn.setToolTip("Split tool (C)")
        self.split_btn.clicked.connect(lambda: self._set_tool(TOOL_SPLIT))

        # GLUE - Arrows pointing inward
        self.glue_btn = QtWidgets.QPushButton()
        self.glue_btn.setIcon(QtGui.QIcon('assets/icons/glue.png'))
        self.glue_btn.setIconSize(QtCore.QSize(32, 32))
        self.glue_btn.setCheckable(True)
        self.glue_btn.setStyleSheet(btn_style)
        self.glue_btn.setCursor(Qt.PointingHandCursor)
        self.glue_btn.setToolTip("Glue tool (G)")
        self.glue_btn.clicked.connect(lambda: self._set_tool(TOOL_GLUE))

        # ADD - Plus icon
        self.add_btn = QtWidgets.QPushButton()
        self.add_btn.setIcon(QtGui.QIcon('assets/icons/add.png'))
        self.add_btn.setIconSize(QtCore.QSize(32, 32))
        self.add_btn.setCheckable(True)
        self.add_btn.setStyleSheet(btn_style)
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.setToolTip("Add new region at grid size (A)")
        self.add_btn.clicked.connect(lambda: self._set_tool(TOOL_ADD))

        # REMOVE - Minus icon
        self.remove_btn = QtWidgets.QPushButton()
        self.remove_btn.setIcon(QtGui.QIcon('assets/icons/remove.png'))
        self.remove_btn.setIconSize(QtCore.QSize(32, 32))
        self.remove_btn.setCheckable(True)
        self.remove_btn.setStyleSheet(btn_style)
        self.remove_btn.setCursor(Qt.PointingHandCursor)
        self.remove_btn.setToolTip("Remove tool (R)")
        self.remove_btn.clicked.connect(lambda: self._set_tool(TOOL_REMOVE))

        # SNAP TO GRID - Magnet icon (independent toggle, not a tool)
        self.snap_btn = QtWidgets.QPushButton()
        self.snap_btn.setIcon(QtGui.QIcon('assets/icons/magnet.png'))
        self.snap_btn.setIconSize(QtCore.QSize(32, 32))
        self.snap_btn.setCheckable(True)
        self.snap_btn.setChecked(True)  # Default to enabled
        self.snap_btn.setStyleSheet(btn_style)
        self.snap_btn.setCursor(Qt.PointingHandCursor)
        self.snap_btn.setToolTip("Snap to grid (M)")
        self.snap_btn.toggled.connect(self.snap_toggled)

        layout.addWidget(self.select_btn)
        layout.addWidget(self.split_btn)
        layout.addWidget(self.glue_btn)
        layout.addWidget(self.add_btn)
        layout.addWidget(self.remove_btn)
        layout.addSpacing(12)  # Space before snap button
        layout.addWidget(self.snap_btn)
        layout.addStretch()
    
    def _set_tool(self, tool: str):
        self._current_tool = tool
        self.select_btn.setChecked(tool == TOOL_SELECT)
        self.split_btn.setChecked(tool == TOOL_SPLIT)
        self.glue_btn.setChecked(tool == TOOL_GLUE)
        self.add_btn.setChecked(tool == TOOL_ADD)
        self.remove_btn.setChecked(tool == TOOL_REMOVE)
        self.tool_changed.emit(tool)
    
    def get_current_tool(self) -> str:
        return self._current_tool

    def set_tool(self, tool: str):
        """Public method to set the tool (e.g., from keyboard shortcuts)"""
        self._set_tool(tool)

    def is_snap_enabled(self) -> bool:
        """Check if snap to grid is enabled"""
        return self.snap_btn.isChecked()

    def set_snap_enabled(self, enabled: bool):
        """Set snap to grid state"""
        self.snap_btn.setChecked(enabled)


class TapeInfoDisplay(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        self.folder_label = QtWidgets.QLabel("")
        self.folder_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 16px; font-weight: 600;")
        self.folder_label.setAlignment(Qt.AlignRight)
        
        self.bpm_label = QtWidgets.QLabel("")
        self.bpm_label.setStyleSheet(f"color: {COLORS['accent']}; font-size: 14px; font-weight: 500;")
        self.bpm_label.setAlignment(Qt.AlignRight)
        
        layout.addWidget(self.folder_label)
        layout.addWidget(self.bpm_label)
        self._bpm = 120.0
    
    def set_info(self, folder_name, bpm):
        self.folder_label.setText(folder_name)
        self.bpm_label.setText(f"{int(bpm) if bpm == int(bpm) else bpm:.1f} BPM")
        self._bpm = bpm
    
    def get_bpm(self):
        return self._bpm


class LoopListWidget(QtWidgets.QListWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"""
            QListWidget {{
                background-color: {COLORS['panel']};
                border: 2px solid {COLORS['border']};
                border-radius: 8px;
                color: {COLORS['text']};
                font-family: {FONT_LIGHT};
                font-size: 12px;
                outline: none;
            }}
            QListWidget::item {{
                padding: 10px 12px;
                border-bottom: 1px solid {COLORS['border']};
                cursor: pointer;
                outline: none;
            }}
            QListWidget::item:selected {{
                background-color: {COLORS['accent_dim']};
                color: {COLORS['text']};
                outline: none;
            }}
            QListWidget::item:hover {{
                background-color: {COLORS['bg']};
            }}
        """)
        self.setCursor(Qt.PointingHandCursor)
    
    def set_loops(self, loops):
        self.clear()
        for loop in loops:
            bars = loop.length_bars
            bars_str = f"{bars:.1f}" if bars % 1 else f"{int(bars)}"
            name = getattr(loop, 'custom_name', None) or f"L{loop.index:03d}"
            text = f"{name}  │  {loop.time_str()}  │  {bars_str} bars  │  T[{','.join(str(t+1) for t in sorted(loop.tracks_active))}]  │  {int(loop.start_boundary.confidence * 100)}%"
            item = QtWidgets.QListWidgetItem(text)
            item.setData(Qt.UserRole, loop.index)
            self.addItem(item)
    
    def update_loop_name(self, index, name):
        if 0 <= index < self.count():
            item = self.item(index)
            parts = item.text().split("  │  ")
            parts[0] = name
            item.setText("  │  ".join(parts))


class HighlightButtonGroup(QtWidgets.QWidget):
    """Button group for selecting highlight grid value"""
    highlight_changed = Signal(int)

    def __init__(self):
        super().__init__()
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._current_value = 4
        self.buttons = {}

        # Button style for rounded rectangles
        btn_style = f"""
            QPushButton {{
                background-color: {COLORS['border']};
                border: none;
                border-radius: 6px;
                color: {COLORS['text_dim']};
                font-size: 11px;
                font-weight: 600;
                padding: 4px 8px;
                min-width: 32px;
                min-height: 24px;
            }}
            QPushButton:checked {{
                background-color: {COLORS['accent']};
                color: white;
            }}
            QPushButton:hover:!checked {{
                background-color: {COLORS['text_dim']};
                color: white;
            }}
        """

        # Create buttons for each value
        values = ["OFF", "1", "2", "4", "8", "16"]
        for val in values:
            btn = QtWidgets.QPushButton(val)
            btn.setCheckable(True)
            btn.setStyleSheet(btn_style)
            btn.setCursor(Qt.PointingHandCursor)

            # Convert to int value (0 for OFF)
            int_val = 0 if val == "OFF" else int(val)
            btn.clicked.connect(lambda _, v=int_val: self._on_button_clicked(v))

            self.buttons[int_val] = btn
            layout.addWidget(btn)

        # Set default to 4
        self.set_value(4)

    def _on_button_clicked(self, value: int):
        self.set_value(value)
        self.highlight_changed.emit(value)

    def set_value(self, value: int):
        """Set the current highlight value and update button states"""
        self._current_value = value
        for val, btn in self.buttons.items():
            btn.setChecked(val == value)

    def get_value(self) -> int:
        """Get the current highlight value"""
        return self._current_value


class SettingsPanel(QtWidgets.QWidget):
    settings_changed = Signal()
    auto_scan_requested = Signal()
    snap_to_grid_changed = Signal(bool)
    highlight_grid_changed = Signal(int)

    def __init__(self):
        super().__init__()
        # Use HBoxLayout instead of FormLayout for horizontal arrangement
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Re-scan button (same height as drop zone)
        self.auto_scan_btn = QtWidgets.QPushButton("RE-SCAN LOOPS")
        self.auto_scan_btn.setCursor(Qt.PointingHandCursor)
        self.auto_scan_btn.clicked.connect(self.auto_scan_requested)
        self.auto_scan_btn.setEnabled(False)
        self.auto_scan_btn.setMinimumHeight(120)  # Match drop zone height
        self.auto_scan_btn.setMinimumWidth(120)
        layout.addWidget(self.auto_scan_btn)
        layout.addStretch()
        
        self.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text']};
            }}
            QSpinBox, QDoubleSpinBox, QComboBox {{
                background-color: {COLORS['bg']};
                border: 1px solid {COLORS['border']};
                border-radius: 8px;
                padding: 6px 8px;
                color: {COLORS['text']};
            }}
            QCheckBox {{
                color: {COLORS['text']};
                font-weight: 500;
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
                image: url(assets/icons/checkbox-checked.png);
            }}
            QCheckBox::indicator:hover {{
                border-color: {COLORS['text']};
            }}
            QPushButton {{
                background-color: {COLORS['accent']};
                border: none;
                border-radius: 8px;
                padding: 8px 14px;
                color: white;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent_dim']};
            }}
            QPushButton:disabled {{
                background-color: {COLORS['border']};
                color: {COLORS['text_dim']};
            }}
        """)
    
    def get_settings(self):
        return {
            'min_tracks': 2,  # Default value (widget commented out)
            'min_bars': 1.0,  # Default value (widget commented out)
            'snap_to_grid': True,  # Default value (moved to EditToolbar)
        }

    def set_scan_enabled(self, enabled: bool):
        self.auto_scan_btn.setEnabled(enabled)


class DropZone(QtWidgets.QLabel):
    folder_dropped = Signal(str)
    
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(50)
        self.setText("DROP TAPE FOLDER HERE\n📁")
        self.setStyleSheet(f"QLabel {{ background-color: {COLORS['panel']}; border: 2px dashed {COLORS['border']}; border-radius: 12px; color: {COLORS['text_dim']}; font-size: 14px; padding: 10px; }}")
    
    def set_loaded(self, folder_name, bpm, clip_count):
        self.setText(f"📁 {folder_name}\n{bpm} BPM  •  {clip_count} clips")
        self.setStyleSheet(f"QLabel {{ background-color: {COLORS['panel']}; border: 2px solid {COLORS['accent']}; border-radius: 12px; color: {COLORS['text']}; font-size: 16px; padding: 10px; }}")

    def set_loading(self):
        self.setText("Loading…")
        self.setStyleSheet(f"QLabel {{ background-color: {COLORS['panel']}; border: 2px dashed {COLORS['accent']}; border-radius: 12px; color: {COLORS['text']}; font-size: 14px; padding: 10px; }}")
    
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()
    
    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()
    
    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self.folder_dropped.emit(path)
                event.accept()
                return
        event.ignore()


class StatusBar(QtWidgets.QWidget):
    help_clicked = Signal()

    def __init__(self):
        super().__init__()
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)

        self.label = QtWidgets.QLabel("READY")
        self.label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px; font-weight: 600;")

        self.progress = QtWidgets.QProgressBar()
        self.progress.setMaximumWidth(200)
        self.progress.setVisible(False)

        # Help button
        self.help_btn = QtWidgets.QPushButton("?")
        self.help_btn.setFixedSize(24, 24)
        self.help_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {COLORS['text_dim']};
                border: 1px solid {COLORS['text_dim']};
                border-radius: 12px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {COLORS['panel']};
                color: {COLORS['accent']};
                border-color: {COLORS['accent']};
            }}
        """)
        self.help_btn.clicked.connect(self.help_clicked.emit)

        self.version_label = QtWidgets.QLabel(
            f'v{__version__} • <a href="mailto:nate@3030.ca" style="color: {COLORS["text_dim"]}; text-decoration: none;">'
            f'Bugs? Requests? Feedback? Love? Email <span style="text-decoration: underline;">nate@3030.ca</span></a>'
        )
        self.version_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11px;")
        self.version_label.setOpenExternalLinks(True)

        layout.addWidget(self.label)
        layout.addStretch()
        layout.addWidget(self.progress)
        layout.addWidget(self.help_btn)
        layout.addWidget(self.version_label)

    def show_progress(self, message: str = "Loading…"):
        self.label.setText(message)
        self.progress.setRange(0, 0)  # Indeterminate
        self.progress.setVisible(True)

    def hide_progress(self):
        self.progress.setVisible(False)

    def set_status(self, message):
        self.label.setText(message.upper())

    def set_progress(self, current, total):
        self.progress.setVisible(total > 0)
        self.progress.setMaximum(total)
        self.progress.setValue(current)
