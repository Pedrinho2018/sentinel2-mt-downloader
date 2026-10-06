from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from sentinel2_mt.caminhos import caminho_contido, componente_seguro


class TestCaminhosSeguros(TestCase):
    def test_componentes_externos_nao_escapam_da_raiz_nem_colidem(self) -> None:
        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            primeiro = componente_seguro("../../cena")
            segundo = componente_seguro("..\\../cena")

            self.assertNotIn("/", primeiro)
            self.assertNotEqual(primeiro, segundo)
            self.assertEqual(caminho_contido(raiz, primeiro).parent, raiz.resolve())

    def test_caminho_contido_rejeita_escape_direto(self) -> None:
        with TemporaryDirectory() as temporario:
            with self.assertRaisesRegex(ValueError, "fora da raiz"):
                caminho_contido(Path(temporario), "..", "fora")
