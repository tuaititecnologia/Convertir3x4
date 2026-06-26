"""Grilla de miniaturas con drag & drop manual para reordenar.

`ReorderListWidget` hace reorden INTERNO. El drag-OUT hacia ProPresenter
(validado en el spike de la Fase 0) se agrega aquí en la Fase 3.
"""

from PyQt6.QtCore import Qt, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QPen, QColor
from PyQt6.QtWidgets import QApplication, QListWidget, QListWidgetItem

# Rol custom para guardar el path completo y la fecha de modificación
PATH_ROLE = Qt.ItemDataRole.UserRole
MTIME_ROLE = Qt.ItemDataRole.UserRole + 1


class ReorderListWidget(QListWidget):
    """QListWidget con drag & drop manual para reordenar."""
    orderChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self._drag_start_pos = None
        self._drag_row: int | None = None
        self._dragging = False
        self._drop_indicator_row: int | None = None

    def _drop_row_from_pos(self, pos) -> int | None:
        """Calcula en qué posición (entre items) caería el drop."""
        item = self.itemAt(pos)
        if not item:
            return None
        rect = self.visualItemRect(item)
        row = self.row(item)
        # Si el cursor está en la mitad derecha del item, insertar después
        if pos.x() > rect.center().x():
            return row + 1
        return row

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            item = self.itemAt(event.pos())
            self._drag_row = self.row(item) if item else None
            self._dragging = False

    def mouseMoveEvent(self, event):
        if (self._drag_row is not None
                and self._drag_start_pos is not None
                and not self._dragging):
            dist = (event.pos() - self._drag_start_pos).manhattanLength()
            if dist > QApplication.startDragDistance():
                self._dragging = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self._dragging:
            self._drop_indicator_row = self._drop_row_from_pos(event.pos())
            self.viewport().update()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging and self._drag_row is not None:
            drop_row = self._drop_row_from_pos(event.pos())

            if drop_row is not None and drop_row != self._drag_row and drop_row != self._drag_row + 1:
                src_item = self.item(self._drag_row)
                path = src_item.data(PATH_ROLE)
                mtime = src_item.data(MTIME_ROLE)
                icon = src_item.icon()
                text = src_item.text()

                self.takeItem(self._drag_row)
                # Ajustar índice después del takeItem
                insert_row = drop_row if drop_row < self._drag_row else drop_row - 1
                new_item = QListWidgetItem()
                new_item.setIcon(icon)
                new_item.setText(text)
                new_item.setData(PATH_ROLE, path)
                new_item.setData(MTIME_ROLE, mtime)
                self.insertItem(insert_row, new_item)
                self.clearSelection()
                new_item.setSelected(True)
                self.orderChanged.emit()

            self.unsetCursor()

        self._drag_row = None
        self._drag_start_pos = None
        self._dragging = False
        self._drop_indicator_row = None
        self.viewport().update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._dragging or self._drop_indicator_row is None:
            return

        row = self._drop_indicator_row
        # Calcular posición de la línea indicadora
        if row < self.count():
            rect = self.visualItemRect(self.item(row))
            x = rect.left() - self.spacing()
        else:
            # Después del último item
            rect = self.visualItemRect(self.item(self.count() - 1))
            x = rect.right() + self.spacing()

        painter = QPainter(self.viewport())
        pen = QPen(QColor("#0078d4"), 2)
        painter.setPen(pen)
        y_top = rect.top()
        y_bottom = rect.bottom()
        painter.drawLine(x, y_top, x, y_bottom)
        painter.setBrush(QColor("#0078d4"))
        painter.drawEllipse(QPoint(x, y_top), 3, 3)
        painter.drawEllipse(QPoint(x, y_bottom), 3, 3)
        painter.end()
