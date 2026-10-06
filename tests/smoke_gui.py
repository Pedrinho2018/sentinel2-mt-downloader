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

    def salvar_e_avancar() -> None:
        nonlocal indice
        if not janela.grab().save(str(CAPTURAS[indice])):
            app.exit(2)
            return
        if indice == 1:
            imagem = janela.mapa.grab().toImage()
            cores = {
                imagem.pixelColor(x, y).rgba()
                for x in range(0, imagem.width(), max(1, imagem.width() // 12))
                for y in range(0, imagem.height(), max(1, imagem.height() // 8))
            }
            if len(cores) < 5:
                app.exit(4)
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

    def aguardar_mapa(tentativa: int = 0) -> None:
        def verificar(pronto: bool) -> None:
            if pronto:
                QtCore.QTimer.singleShot(700, salvar_e_avancar)
            elif tentativa >= 40:
                app.exit(3)
            else:
                QtCore.QTimer.singleShot(250, lambda: aguardar_mapa(tentativa + 1))

        janela.mapa.page().runJavaScript("window.mapReady === true", verificar)

    def capturar() -> None:
        janela._navegar(indice)
        app.processEvents()
        if indice == 1:
            aguardar_mapa()
        else:
            salvar_e_avancar()

    QtCore.QTimer.singleShot(3500, capturar)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
