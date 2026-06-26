"""Punto de entrada: `python -m convertir3x4` (o vía el shim Convertir3x4.py)."""

import sys

from PyQt6.QtWidgets import QApplication

from .app import MainWindow
from .ui.style import DARK_STYLE


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
