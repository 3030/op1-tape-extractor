"""
OP-1 Loop Extractor - Icon mapping

Semantic icon names -> Phosphor icons (bold weight, MIT license).
Only the SVGs listed here ship, in assets/icons/phosphor/.
"""

import os
from PySide6 import QtCore, QtGui
from PySide6.QtCore import Qt
from PySide6.QtSvg import QSvgRenderer

ICON_DIR = os.path.join(os.path.dirname(__file__), 'assets', 'icons', 'phosphor')

ICONS = {
    'audio_output': 'speaker-high',
    'refresh': 'arrows-clockwise',
}


def icon(name, color='white', size=64):
    """Render a Phosphor SVG in the given color as a QIcon"""
    path = os.path.join(ICON_DIR, ICONS[name] + '.svg')
    with open(path, encoding='utf-8') as f:
        svg = f.read().replace('currentColor', color)

    renderer = QSvgRenderer(QtCore.QByteArray(svg.encode('utf-8')))
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QtGui.QIcon(pixmap)
