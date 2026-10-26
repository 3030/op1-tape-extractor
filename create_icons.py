#!/usr/bin/env python3
"""Generate icon assets for the OP-1 Loop Extractor"""

from PySide6.QtGui import QImage, QPainter, QColor, QPen, QBrush, QPainterPath
from PySide6.QtCore import Qt, QPointF, QRectF
import os

def create_magnet_icon():
    """Create a magnet icon for snap to grid"""
    size = 32
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)

    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing)

    # Draw magnet shape (horseshoe)
    pen = QPen(QColor('#1b1a1d'))
    pen.setWidth(3)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)

    # Left pole
    painter.drawLine(10, 8, 10, 20)
    painter.drawLine(10, 20, 14, 24)

    # Right pole
    painter.drawLine(22, 8, 22, 20)
    painter.drawLine(22, 20, 18, 24)

    # Top bar connecting poles
    painter.drawLine(10, 8, 22, 8)

    # Draw attraction lines at bottom
    pen.setWidth(2)
    painter.setPen(pen)
    painter.drawLine(12, 26, 12, 28)
    painter.drawLine(16, 27, 16, 30)
    painter.drawLine(20, 26, 20, 28)

    painter.end()

    os.makedirs('assets/icons', exist_ok=True)
    img.save('assets/icons/magnet.png')
    print("Created magnet.png")

if __name__ == '__main__':
    create_magnet_icon()
