"""Punto de entrada: `python -m convertir3x4` (o vía el shim Convertir3x4.py)."""

import sys

from PyQt6.QtWidgets import QApplication

from .app import MainWindow
from .ui.style import DARK_STYLE


def main():
    app = QApplication(sys.argv)
    # Define dónde caen los datos por usuario (catalog.db, thumbs/) y las QSettings:
    # AppDataLocation usa estos nombres → .../Application Support/CDLM/Convertir3x4/
    app.setOrganizationName("CDLM")
    app.setApplicationName("Convertir3x4")
    app.setStyleSheet(DARK_STYLE)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
