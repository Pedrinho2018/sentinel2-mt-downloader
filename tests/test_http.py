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


import requests
from requests.structures import CaseInsensitiveDict

from sentinel2_mt.http import (
    caminho_parcial,
    listar_parciais_legados,
    remover_parciais_legados,
)


class RespostaFake:
    def __init__(self, status: int, corpo: bytes, cabecalhos: dict | None = None, falhar_apos: int | None = None):
        self.status_code = status
        self.corpo = corpo
        self.headers = CaseInsensitiveDict(cabecalhos or {})
        self.falhar_apos = falhar_apos

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} erro")

    def iter_content(self, chunk_size: int):
        enviados = 0
        for posicao in range(0, len(self.corpo), 4):
            if self.falhar_apos is not None and enviados >= self.falhar_apos:
                raise requests.ConnectionError("conexão interrompida")
            parte = self.corpo[posicao : posicao + 4]
            enviados += len(parte)
            yield parte


class ServidorFake:
    """Simula um servidor HTTP com suporte opcional a Range e If-Range."""

    def __init__(self, conteudo: bytes, etag: str = '"v1"', aceita_range: bool = True) -> None:
        self.conteudo = conteudo
        self.etag = etag
        self.aceita_range = aceita_range
        self.falhar_apos: int | None = None
        self.pedidos: list[dict] = []

    def get(self, url, stream=True, timeout=None, headers=None):
        headers = dict(headers or {})
        self.pedidos.append(headers)
        falhar, self.falhar_apos = self.falhar_apos, None
        faixa = headers.get("Range")
        if_range = headers.get("If-Range")
        if faixa and self.aceita_range and (if_range is None or if_range == self.etag):
            inicio = int(faixa.split("=")[1].rstrip("-"))
            if inicio >= len(self.conteudo):
                return RespostaFake(416, b"")
            corpo = self.conteudo[inicio:]
            return RespostaFake(
                206,
                corpo,
                {
                    "Content-Range": f"bytes {inicio}-{len(self.conteudo) - 1}/{len(self.conteudo)}",
                    "Content-Length": str(len(corpo)),
                    "ETag": self.etag,
                },
                falhar,
            )
        return RespostaFake(
            200,
            self.conteudo,
            {"Content-Length": str(len(self.conteudo)), "ETag": self.etag},
            falhar,
        )


class TestRetomadaDownload(TestCase):
    URL = "https://example.test/dados/B04.bin?token=abc"

    def setUp(self) -> None:
        self.temporario = TemporaryDirectory()
        self.pasta = Path(self.temporario.name)
        self.destino = self.pasta / "cena" / "B04.bin"
        self.eventos = []
        self.cliente = ClienteDownloadHTTP(10, 1, progresso=self.eventos.append)

    def tearDown(self) -> None:
        self.temporario.cleanup()

    def _servidor(self, **kwargs) -> ServidorFake:
        servidor = ServidorFake(b"0123456789abcdefghij", **kwargs)
        self.cliente.sessao = servidor
        return servidor

    def test_interrupcao_mantem_parcial_e_nova_execucao_retoma(self) -> None:
        servidor = self._servidor()
        servidor.falhar_apos = 8

        with self.assertRaises(requests.ConnectionError):
            self.cliente.baixar(self.URL, self.destino)

        parcial = caminho_parcial(self.destino)
        self.assertEqual(parcial.read_bytes(), b"01234567")
        self.assertFalse(self.destino.exists())

        # Token diferente na query não impede a retomada do mesmo arquivo.
        status = self.cliente.baixar("https://example.test/dados/B04.bin?token=novo", self.destino)

        self.assertEqual(status, "retomado")
        self.assertEqual(self.destino.read_bytes(), b"0123456789abcdefghij")
        self.assertEqual(servidor.pedidos[-1]["Range"], "bytes=8-")
        self.assertEqual(servidor.pedidos[-1]["If-Range"], '"v1"')
        self.assertFalse(parcial.exists())
        self.assertFalse(parcial.with_name(parcial.name + ".json").exists())

    def test_arquivo_remoto_alterado_recomeca_do_zero(self) -> None:
        servidor = self._servidor()
        servidor.falhar_apos = 8
        with self.assertRaises(requests.ConnectionError):
            self.cliente.baixar(self.URL, self.destino)
        servidor.etag = '"v2"'
        servidor.conteudo = b"ABCDEFGHIJKLMNOPQRST"

        status = self.cliente.baixar(self.URL, self.destino)

        self.assertEqual(status, "baixado")
        self.assertEqual(self.destino.read_bytes(), b"ABCDEFGHIJKLMNOPQRST")

    def test_servidor_sem_range_reinicia_sem_misturar_bytes(self) -> None:
        servidor = self._servidor(aceita_range=False)
        servidor.falhar_apos = 8
        with self.assertRaises(requests.ConnectionError):
            self.cliente.baixar(self.URL, self.destino)

        status = self.cliente.baixar(self.URL, self.destino)

        self.assertEqual(status, "baixado")
        self.assertEqual(self.destino.read_bytes(), b"0123456789abcdefghij")

    def test_faixa_invalida_416_descarta_parcial_e_baixa_completo(self) -> None:
        servidor = self._servidor()
        parcial = caminho_parcial(self.destino)
        parcial.parent.mkdir(parents=True)
        parcial.write_bytes(b"x" * 30)
        identidade = ClienteDownloadHTTP._identidade_url(self.URL)
        parcial.with_name(parcial.name + ".json").write_text(
            '{"url": "%s", "validador": "\\"v1\\""}' % identidade, encoding="utf-8"
        )

        status = self.cliente.baixar(self.URL, self.destino)

        self.assertEqual(status, "baixado")
        self.assertEqual(self.destino.read_bytes(), servidor.conteudo)

    def test_parcial_de_outra_url_nao_e_reaproveitado(self) -> None:
        servidor = self._servidor()
        parcial = caminho_parcial(self.destino)
        parcial.parent.mkdir(parents=True)
        parcial.write_bytes(b"lixo")
        parcial.with_name(parcial.name + ".json").write_text('{"url": "outra"}', encoding="utf-8")

        self.cliente.baixar(self.URL, self.destino)

        self.assertNotIn("Range", servidor.pedidos[-1])
        self.assertEqual(self.destino.read_bytes(), servidor.conteudo)

    def test_erro_http_descarta_parcial(self) -> None:
        servidor = self._servidor()
        servidor.get = lambda *a, **k: RespostaFake(404, b"")

        with self.assertRaises(requests.HTTPError):
            self.cliente.baixar(self.URL, self.destino)

        self.assertFalse(caminho_parcial(self.destino).exists())

    def test_progresso_informa_bytes_total_e_conclusao(self) -> None:
        self._servidor()

        self.cliente.baixar(self.URL, self.destino)

        final = self.eventos[-1]
        self.assertTrue(final.concluido)
        self.assertEqual(final.recebidos, 20)
        self.assertEqual(final.total, 20)
        self.assertEqual(final.arquivo, "B04.bin")
        self.assertEqual(final.para_dict()["tipo"], "arquivo")

    def test_geotiff_retomado_passa_na_validacao(self) -> None:
        raster = criar_raster(self.pasta / "origem.tif", np.ones((32, 32), dtype=np.uint16))
        servidor = ServidorFake(raster.read_bytes())
        self.cliente.sessao = servidor
        destino = self.pasta / "cena" / "B02.tif"
        servidor.falhar_apos = 100
        with self.assertRaises(requests.ConnectionError):
            self.cliente.baixar("https://example.test/B02.tif", destino)

        status = self.cliente.baixar("https://example.test/B02.tif", destino)

        self.assertEqual(status, "retomado")
        self.assertTrue(ClienteDownloadHTTP._arquivo_integro(destino))

    def test_lista_e_remove_somente_parciais_legados(self) -> None:
        cena = self.pasta / "2026-04-23" / "CENA"
        cena.mkdir(parents=True)
        legado = cena / ".B03.tif.iy2xq9ea.part.tif"
        legado.write_bytes(b"123")
        retomavel = cena / ".B04.tif.part"
        retomavel.write_bytes(b"45")
        final = cena / "B02.tif"
        final.write_bytes(b"6")

        self.assertEqual(listar_parciais_legados(self.pasta), [legado])
        self.assertEqual(remover_parciais_legados(self.pasta), (1, 3))

        self.assertFalse(legado.exists())
        self.assertTrue(retomavel.exists())
        self.assertTrue(final.exists())
