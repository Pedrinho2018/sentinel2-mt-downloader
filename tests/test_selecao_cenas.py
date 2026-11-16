from __future__ import annotations

from datetime import datetime, timezone
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, mock

from sentinel2_mt.cli import AplicacaoCLI, emitir_progresso_json
from sentinel2_mt.configuracao import (
    ConfiguracaoArea,
    ConfiguracaoDownload,
    ConfiguracaoPeriodo,
    ConfiguracaoPreview,
    ConfiguracaoProjeto,
    ConfiguracaoQualidade,
    ConfiguracaoSincronizacao,
    ConfiguracaoStac,
)
from sentinel2_mt.http import ProgressoDownload
from sentinel2_mt.servico import OpcoesColeta, ServicoSentinel2


def item(identificador: str, *, nuvem=None, tamanho=None, miniatura: str = "") -> SimpleNamespace:
    extra = {"file:size": tamanho} if tamanho else {}
    assets = {
        "B02": SimpleNamespace(href=f"https://example.test/{identificador}/B02.tif", extra_fields=extra),
    }
    if miniatura:
        assets["thumbnail"] = SimpleNamespace(href=miniatura, extra_fields={})
    return SimpleNamespace(
        id=identificador,
        datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
        properties={"eo:cloud_cover": nuvem} if nuvem is not None else {},
        assets=assets,
    )


class BuscaFake:
    def __init__(self, itens):
        self.itens = itens

    def items(self):
        return iter(self.itens)


class ClienteFake:
    def __init__(self, itens):
        self.itens = itens

    def search(self, **_):
        return BuscaFake(self.itens)


class CatalogoFake:
    def salvar(self, caminho, registros) -> None:
        self.registros = registros


class DownloaderFake:
    def __init__(self, timeout, chunk, progresso=None) -> None:
        self.progresso = progresso
        self.urls: list[str] = []

    def baixar(self, url: str, destino: Path) -> str:
        self.urls.append(url)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"x")
        if self.progresso:
            self.progresso(ProgressoDownload(destino.name, 1, 1, 10.0, False, True))
        return "baixado"


class TestSelecaoCenas(TestCase):
    def setUp(self) -> None:
        self.temporario = TemporaryDirectory()
        self.raiz = Path(self.temporario.name)
        self.config = ConfiguracaoProjeto(
            raiz=self.raiz,
            stac=ConfiguracaoStac("https://example.invalid/stac", "S2-16D-2"),
            area=ConfiguracaoArea("Teste", "MT", (-60, -15, -55, -10)),
            periodo=ConfiguracaoPeriodo("2026-01-01", "2026-01-31"),
            bandas=("B02",),
            qualidade=ConfiguracaoQualidade(filtrar_nuvens=False, camadas_auxiliares=()),
            preview=ConfiguracaoPreview(gerar_rgb=False),
            download=ConfiguracaoDownload(pasta="data/sentinel2", catalogo="catalogo/c.csv"),
            sincronizacao=ConfiguracaoSincronizacao(),
        )
        self.eventos: list[dict] = []
        self.downloaders: list[DownloaderFake] = []

    def tearDown(self) -> None:
        self.temporario.cleanup()

    def _servico(self, itens) -> ServicoSentinel2:
        def fabrica(*args, **kwargs):
            downloader = DownloaderFake(*args, **kwargs)
            self.downloaders.append(downloader)
            return downloader

        return ServicoSentinel2(
            self.config,
            catalogo=CatalogoFake(),
            cliente_stac_factory=lambda _: ClienteFake(itens),
            saida=lambda _: None,
            eventos=self.eventos.append,
            downloader_factory=fabrica,
        )

    def test_catalogacao_emite_cenas_com_metadados_publicos(self) -> None:
        pasta = self.raiz / "data/sentinel2/2026-01-01/A"
        pasta.mkdir(parents=True)
        (pasta / "B02.tif").write_bytes(b"x")
        servico = self._servico(
            [
                item("A", nuvem=12.5, tamanho=1000, miniatura="https://example.test/a.png?token=s"),
                item("B"),
            ]
        )

        servico.executar(OpcoesColeta(max_itens=0))

        cenas = [evento for evento in self.eventos if evento["tipo"] == "cena_encontrada"]
        self.assertEqual([cena["cena"] for cena in cenas], ["A", "B"])
        self.assertEqual(cenas[0]["nuvem_metadados_pct"], 12.5)
        self.assertEqual(cenas[0]["tamanho_bytes"], 1000)
        self.assertEqual(cenas[0]["miniatura"], "https://example.test/a.png")
        self.assertTrue(cenas[0]["baixada"])
        self.assertIsNone(cenas[1]["nuvem_metadados_pct"])
        self.assertIsNone(cenas[1]["tamanho_bytes"])
        self.assertFalse(cenas[1]["baixada"])
        self.assertEqual(self.downloaders, [])

    def test_download_restrito_as_cenas_selecionadas(self) -> None:
        servico = self._servico([item("A"), item("B"), item("C")])

        resumo = servico.executar(
            OpcoesColeta(baixar_arquivos=True, max_itens=0, cenas=("C", "A"))
        )

        self.assertEqual(resumo.aprovadas, 2)
        self.assertEqual(resumo.erros, 0)
        urls = self.downloaders[0].urls
        self.assertEqual(urls, ["https://example.test/A/B02.tif", "https://example.test/C/B02.tif"])
        self.assertFalse((self.raiz / "data/sentinel2/2026-01-01/B").exists())
        etapas = [(e["cena"], e["etapa"]) for e in self.eventos if e["tipo"] == "cena"]
        self.assertIn(("A", "concluida"), etapas)
        self.assertIn(("C", "concluida"), etapas)
        arquivos = [e for e in self.eventos if e["tipo"] == "arquivo"]
        self.assertEqual({e["cena"] for e in arquivos}, {"A", "C"})
        self.assertTrue(all(e["total"] == 2 for e in self.eventos if e["tipo"] == "cena"))

    def test_cena_selecionada_ausente_conta_como_erro(self) -> None:
        servico = self._servico([item("A")])

        resumo = servico.executar(
            OpcoesColeta(baixar_arquivos=True, max_itens=0, cenas=("A", "Z"))
        )

        self.assertEqual(resumo.aprovadas, 1)
        self.assertEqual(resumo.erros, 1)

    def test_sem_eventos_nao_ha_callback_de_progresso(self) -> None:
        servico = self._servico([item("A")])
        servico.eventos = None

        servico.executar(OpcoesColeta(baixar_arquivos=True, max_itens=1))

        self.assertIsNone(self.downloaders[0].progresso)


class TestCliProgresso(TestCase):
    def test_emite_linha_json_marcada(self) -> None:
        saida = io.StringIO()
        with redirect_stdout(saida):
            emitir_progresso_json({"tipo": "cena", "cena": "Á"})

        linha = saida.getvalue().strip()
        self.assertTrue(linha.startswith("[PROGRESSO] "))
        self.assertEqual(json.loads(linha[len("[PROGRESSO] "):])["cena"], "Á")

    def test_cli_repassa_cenas_e_progresso_ao_servico(self) -> None:
        raiz = Path(__file__).resolve().parents[1]
        capturado = {}

        class ServicoFalso:
            def __init__(self, config, eventos=None):
                capturado["eventos"] = eventos

            def executar(self, opcoes):
                capturado["opcoes"] = opcoes
                return SimpleNamespace(erros=0)

        with mock.patch("sentinel2_mt.cli.ServicoSentinel2", ServicoFalso):
            codigo = AplicacaoCLI().executar(
                [
                    "--config", str(raiz / "config/config.yaml"),
                    "--baixar", "--max-itens", "0",
                    "--cenas", "A, B,,", "--progresso-json",
                ]
            )

        self.assertEqual(codigo, 0)
        self.assertEqual(capturado["opcoes"].cenas, ("A", "B"))
        self.assertIs(capturado["eventos"], emitir_progresso_json)


class TestRobustezStac(TestCase):
    def test_politica_repete_erros_transitorios_do_servidor(self) -> None:
        from sentinel2_mt.servico import politica_retentativa_stac

        politica = politica_retentativa_stac()
        self.assertEqual(politica.total, 5)
        self.assertTrue({502, 503, 504}.issubset(set(politica.status_forcelist)))
        self.assertIsNone(politica.allowed_methods)  # busca STAC pode usar POST
        self.assertFalse(politica.raise_on_status)

    def test_para_de_paginar_quando_todas_as_cenas_foram_encontradas(self) -> None:
        consumidos: list[str] = []

        class BuscaQueFalha:
            def items(self):
                for nome in ("A", "B"):
                    consumidos.append(nome)
                    yield item(nome)
                raise AssertionError("não deveria pedir a próxima página")

        caso = TestSelecaoCenas("test_download_restrito_as_cenas_selecionadas")
        caso.setUp()
        try:
            servico = caso._servico([])
            servico.cliente_stac_factory = lambda _: SimpleNamespace(search=lambda **_: BuscaQueFalha())
            resumo = servico.executar(OpcoesColeta(baixar_arquivos=True, max_itens=0, cenas=("A",)))
        finally:
            caso.tearDown()

        self.assertEqual(resumo.aprovadas, 1)
        self.assertEqual(consumidos, ["A", "B"])
