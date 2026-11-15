"""Confirmaciones con el nombre de la acción en el botón.

Criterio (skill impeccable/clarify, 15/11/2026): un "Sí" obliga a releer la
pregunta para saber qué hace; "Reemplazar" o "Detener copia" no. El botón seguro
(Cancelar o equivalente) queda como predeterminado en las acciones que borran o
cortan algo, para que un Enter apurado no haga daño.
"""

from PyQt6.QtWidgets import QMessageBox


def confirmar(parent, titulo: str, pregunta: str, detalle: str, accion: str,
              cancelar: str = "Cancelar", peligro: bool = False) -> bool:
    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Icon.Warning if peligro else QMessageBox.Icon.Question)
    msg.setWindowTitle(titulo)
    msg.setText(pregunta)
    if detalle:
        msg.setInformativeText(detalle)
    rol = QMessageBox.ButtonRole.DestructiveRole if peligro else QMessageBox.ButtonRole.AcceptRole
    si = msg.addButton(accion, rol)
    no = msg.addButton(cancelar, QMessageBox.ButtonRole.RejectRole)
    msg.setDefaultButton(no if peligro else si)
    msg.setEscapeButton(no)
    msg.exec()
    return msg.clickedButton() is si
