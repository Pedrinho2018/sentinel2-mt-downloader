from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from sentinel2_mt.google_drive import AutenticadorGoogleDrive, SincronizadorGoogleDrive, dividir_em_lotes


class TestLotesGoogleDrive(TestCase):
    def test_usa_escopo_de_menor_privilegio(self) -> None:
        self.assertEqual(AutenticadorGoogleDrive.ESCOPO, ("https://www.googleapis.com/auth/drive.file",))

    def test_divide_sem_perder_ordem(self) -> None:
        lotes = [list(lote) for lote in dividir_em_lotes([1, 2, 3, 4, 5], 2)]
        self.assertEqual(lotes, [[1, 2], [3, 4], [5]])

    def test_rejeita_lote_invalido(self) -> None:
        with self.assertRaisesRegex(ValueError, "maior que zero"):
            list(dividir_em_lotes([1], 0))

    def test_seleciona_extensoes_e_ordena(self) -> None:
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            (pasta / "b.JPG").write_bytes(b"imagem")
            (pasta / "a.tif").write_bytes(b"imagem")
            (pasta / "ignorar.txt").write_text("texto", encoding="utf-8")

            arquivos = SincronizadorGoogleDrive.arquivos_para_sincronizar(pasta, ("tif", ".jpg"))

            self.assertEqual([arquivo.name for arquivo in arquivos], ["a.tif", "b.JPG"])
