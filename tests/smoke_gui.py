from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from gerar_config_gui import MainWindow


CAPTURAS = [Path(f"/tmp/sentinel2-mt-gui-{indice}.png") for indice in range(5)]
CAPTURA_EXECUTANDO = Path("/tmp/sentinel2-mt-gui-executando.png")
CAPTURA_SINCRONIZACAO = Path("/tmp/sentinel2-mt-gui-sincronizacao.png")


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
            janela._navegar(0)
            janela._definir_execucao(True, "Executando")
            app.processEvents()
            if not janela.grab().save(str(CAPTURA_EXECUTANDO)):
                app.exit(2)
                return
            janela.operacao.setCurrentIndex(janela.operacao.findData("sincronizar"))
            app.processEvents()
            if not janela.grab().save(str(CAPTURA_SINCRONIZACAO)):
                app.exit(2)
                return
            janela.close()
            app.exit(0)

    QtCore.QTimer.singleShot(3500, capturar)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
