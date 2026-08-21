from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import numpy as np
import rasterio
from rasterio.transform import from_origin

from sentinel2_mt.http import ClienteDownloadHTTP
from tests.test_patches import criar_raster


class TestIntegridadeDownload(TestCase):
    def test_reutiliza_somente_geotiff_legivel(self) -> None:
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            valido = criar_raster(pasta / "valido.tif", np.ones((8, 8), dtype=np.uint16))
            corrompido = pasta / "corrompido.tif"
            corrompido.write_bytes(b"nao e um geotiff")

            self.assertTrue(ClienteDownloadHTTP._arquivo_integro(valido))
            self.assertFalse(ClienteDownloadHTTP._arquivo_integro(corrompido))

    def test_rejeita_geotiff_tiled_truncado_em_blocos_posteriores(self) -> None:
        with TemporaryDirectory() as temporario:
            caminho = Path(temporario) / "tiled.tif"
            dados = np.arange(1024 * 1024, dtype=np.uint16).reshape(1024, 1024)
            with rasterio.open(
                caminho,
                "w",
                driver="GTiff",
                width=1024,
                height=1024,
                count=1,
                dtype="uint16",
                crs="EPSG:31981",
                transform=from_origin(500000, 1000000, 10, 10),
                tiled=True,
                blockxsize=256,
                blockysize=256,
                compress="none",
            ) as raster:
                raster.write(dados, 1)
            conteudo = caminho.read_bytes()
            caminho.write_bytes(conteudo[: len(conteudo) // 2])

            self.assertFalse(ClienteDownloadHTTP._arquivo_integro(caminho))

    def test_rejeita_esquema_de_url_nao_http(self) -> None:
        with TemporaryDirectory() as temporario:
            cliente = ClienteDownloadHTTP(10, 1)
            with self.assertRaisesRegex(ValueError, "HTTP ou HTTPS"):
                cliente.baixar("file:///etc/passwd", Path(temporario) / "arquivo.tif")
