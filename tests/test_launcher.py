from __future__ import annotations

from contextlib import redirect_stderr
from io import StringIO
from unittest import TestCase
from unittest.mock import patch

from sentinel2_mt import launcher


class TestLauncher(TestCase):
    def test_sem_argumentos_abre_gui(self) -> None:
        with patch.object(launcher, "_executar_gui", return_value=0) as executar:
            self.assertEqual(launcher.main([]), 0)

        executar.assert_called_once_with([])

    def test_gui_explica_encaminha_argumentos(self) -> None:
        with patch.object(launcher, "_executar_gui", return_value=0) as executar:
            self.assertEqual(launcher.main(["--gui", "--smoke-test"]), 0)

        executar.assert_called_once_with(["--smoke-test"])

    def test_tui_continua_disponivel(self) -> None:
        with patch.object(launcher, "_executar_tui", return_value=0) as executar:
            self.assertEqual(launcher.main(["--tui"]), 0)

        executar.assert_called_once_with()

    def test_cli_explica_encaminha_argumentos(self) -> None:
        with patch.object(launcher, "_executar_cli", return_value=0) as executar:
            self.assertEqual(launcher.main(["--cli", "--version"]), 0)

        executar.assert_called_once_with(["--version"])

    def test_opcoes_antigas_permanecem_compativeis(self) -> None:
        with patch.object(launcher, "_executar_cli", return_value=0) as executar:
            self.assertEqual(launcher.main(["--version"]), 0)

        executar.assert_called_once_with(["--version"])

    def test_tui_rejeita_argumentos_adicionais(self) -> None:
        saida = StringIO()
        with redirect_stderr(saida):
            self.assertEqual(launcher.main(["--tui", "--version"]), 2)

        self.assertIn("não aceita argumentos", saida.getvalue())
