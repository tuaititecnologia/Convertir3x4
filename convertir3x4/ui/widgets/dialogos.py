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


def crecer(dialogo):
    """Que la ventana crezca cuando aparece un texto (un resultado, un aviso)
    en lugar de aplastar los textos de varios renglones que ya tenía. Qt no lo
    hace solo con las etiquetas que cortan renglones (alto según el ancho)."""
    lay = dialogo.layout()
    if lay is None:
        return
    lay.activate()
    alto = lay.totalHeightForWidth(dialogo.width())
    if alto > dialogo.height():
        dialogo.resize(dialogo.width(), alto)
