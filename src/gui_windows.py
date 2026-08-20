from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtCore, QtWidgets

import gerar_config_gui as gui
from sentinel2_mt.gui_support import montar_argumentos_operacao


EMPACOTADO = bool(getattr(sys, "frozen", False))
ROOT = Path(sys.executable).resolve().parent if EMPACOTADO else Path(__file__).resolve().parents[1]
BACKEND_EXE = ROOT / "Sentinel2-MT-Core.exe"

# A GUI original foi escrita para execução pelo código-fonte. No pacote Windows,
# os arquivos graváveis e a configuração ficam ao lado do executável portátil.
gui.ROOT = ROOT
gui.DEFAULT_CONFIG = ROOT / "config" / "config.yaml"
gui.LOCAL_DB = ROOT / "config" / "configuracoes_local.db"
gui.SCRIPT_CLI = BACKEND_EXE if EMPACOTADO else ROOT / "src" / "baixar_inpe_mt.py"


class WindowsMainWindow(gui.MainWindow):
    def _executar(self) -> None:
        if not EMPACOTADO:
            super()._executar()
            return
        if self.processo.state() != QtCore.QProcess.ProcessState.NotRunning:
            return
        if not BACKEND_EXE.is_file():
            QtWidgets.QMessageBox.critical(
                self,
                "Motor não encontrado",
                f"O arquivo {BACKEND_EXE.name} precisa estar na mesma pasta da interface.",
            )
            return

        config = self._salvar_configuracao(avisar=False)
        if config is None:
            return
        try:
            argumentos = montar_argumentos_operacao(
                str(self.operacao.currentData()),
                config,
                inicio=self.inicio.date().toString(QtCore.Qt.DateFormat.ISODate),
                fim=self.fim.date().toString(QtCore.Qt.DateFormat.ISODate),
                max_itens=self.max_execucao.value(),
                oauth_json=self.oauth_json.text().strip(),
                tamanho_lote=self.tamanho_lote.value(),
            )
        except (FileNotFoundError, TypeError, ValueError) as erro:
            QtWidgets.QMessageBox.warning(self, "Não foi possível executar", str(erro))
            return

        ambiente = QtCore.QProcessEnvironment.systemEnvironment()
        ambiente.insert("PYTHONUNBUFFERED", "1")
        self.processo.setProcessEnvironment(ambiente)
        self.log.clear()
        comando = " ".join([f'"{BACKEND_EXE}"', *argumentos])
        self.log.appendPlainText(f"$ {comando}\n")
        self._definir_execucao(True, "Executando")
        self.processo.setWorkingDirectory(str(ROOT))
        self.processo.start(str(BACKEND_EXE), argumentos)


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Sentinel-2 MT")
    app.setOrganizationName("Sentinel2 MT")
    gui.configurar_aplicacao(app)
    janela = WindowsMainWindow()
    janela.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
