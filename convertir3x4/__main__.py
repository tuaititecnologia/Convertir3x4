"""Punto de entrada: `python -m convertir3x4` (o vía el shim Convertir3x4.py)."""

import sys

from PyQt6.QtWidgets import QApplication

from .app import MainWindow
from .ui.style import apply_theme


def main():
    app = QApplication(sys.argv)
    # Define dónde caen los datos por usuario (catalog.db, thumbs/) y las QSettings:
    # AppDataLocation usa estos nombres → .../Application Support/CDLM/Convertir3x4/
    app.setOrganizationName("CDLM")
    app.setApplicationName("Convertir3x4")
    apply_theme(app)
    window = MainWindow()
    window.show()
    # Avisar en la barra lateral cuando se enchufa un disco de la grabadora
    window.discos_tab.start_background()
    # Revisar (y si hace falta ofrecer preparar) el programa para bajar videos
    window.convert_tab.revisar_herramientas()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
