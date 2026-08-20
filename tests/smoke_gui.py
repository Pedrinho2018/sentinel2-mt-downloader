from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from gerar_config_gui import MainWindow


CAPTURAS = [Path(f"/tmp/sentinel2-mt-gui-{indice}.png") for indice in range(5)]


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    janela = MainWindow()
    janela.show()

    indice = 0

    def capturar() -> None:
        nonlocal indice
        janela._navegar(indice)
        app.processEvents()
        if not janela.grab().save(str(CAPTURAS[indice])):
            app.exit(2)
            return
        indice += 1
        if indice < len(CAPTURAS):
            QtCore.QTimer.singleShot(500, capturar)
        else:
            janela.close()
            app.exit(0)

    QtCore.QTimer.singleShot(3500, capturar)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
