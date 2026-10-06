from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from unittest import TestCase

from sentinel2_mt.configuracao import ConfiguracaoProjeto
from sentinel2_mt.configuracao import (
    ConfiguracaoArea,
    ConfiguracaoDataset,
    ConfiguracaoDownload,
    ConfiguracaoPatches,
    ConfiguracaoPeriodo,
    ConfiguracaoPreview,
    ConfiguracaoQualidade,
    ConfiguracaoSincronizacao,
    ConfiguracaoStac,
)
from sentinel2_mt.modelos import ResumoExecucao
from sentinel2_mt.servico import OpcoesColeta, ServicoSentinel2
from tests.test_patches import cena_sintetica


class ItemFake:
    id = "S2_TESTE"
    datetime = datetime(2025, 10, 1, tzinfo=timezone.utc)
    properties = {}
    assets = {}


class BuscaFake:
    def items(self):
        return iter([ItemFake()])


class ClienteFake:
    def __init__(self) -> None:
        self.parametros = None

    def search(self, **parametros):
        self.parametros = parametros
        return BuscaFake()


class CatalogoFake:
    def __init__(self) -> None:
        self.caminho = None
        self.registros = []

    def salvar(self, caminho, registros) -> None:
        self.caminho = caminho
        self.registros = registros


class TestServicoSentinel2(TestCase):
    def test_catalogacao_sem_download(self) -> None:
        raiz = Path(__file__).resolve().parents[1]
        config = ConfiguracaoProjeto.carregar(raiz / "config/config.yaml", raiz=raiz)
        cliente = ClienteFake()
        catalogo = CatalogoFake()
        saida: list[str] = []
        servico = ServicoSentinel2(
            config,
            catalogo=catalogo,
            cliente_stac_factory=lambda _: cliente,
            saida=saida.append,
        )

        resumo = servico.executar(OpcoesColeta(max_itens=1))

        self.assertEqual(resumo.candidatos, 1)
        self.assertEqual(resumo.aprovadas, 1)
        self.assertEqual(catalogo.registros[0].status, "candidato_catalogado")
        self.assertEqual(cliente.parametros["collections"], [config.stac.colecao])

    def test_dataset_habilitado_no_yaml_nao_muda_catalogacao_para_modo_local(self) -> None:
        raiz = Path(__file__).resolve().parents[1]
        original = ConfiguracaoProjeto.carregar(raiz / "config/config.yaml", raiz=raiz)
        config = replace(original, dataset=replace(original.dataset, gerar=True))
        cliente = ClienteFake()
        catalogo = CatalogoFake()
        servico = ServicoSentinel2(
            config,
            catalogo=catalogo,
            cliente_stac_factory=lambda _: cliente,
            saida=lambda _: None,
        )

        resumo = servico.executar(OpcoesColeta(max_itens=1))

        self.assertEqual(resumo.candidatos, 1)
        self.assertEqual(catalogo.registros[0].status, "candidato_catalogado")
        self.assertIsNotNone(cliente.parametros)

    def test_scl_ausente_nao_descarta_cena_inteira(self) -> None:
        raiz = Path(__file__).resolve().parents[1]
        config = ConfiguracaoProjeto.carregar(raiz / "config/config.yaml", raiz=raiz)
        registros = []
        resumo = ResumoExecucao()
        servico = ServicoSentinel2(config, saida=lambda _: None)

        qualidade = servico._avaliar_qualidade(ItemFake(), "2025-10-01", {}, registros, resumo)

        self.assertEqual(qualidade, "scl_indisponivel")
        self.assertEqual(resumo.descartadas, 0)
        self.assertEqual(registros[0].status, "nao_avaliado_sem_scl")

    def test_gera_dataset_local_sem_consultar_stac(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            cena_sintetica(raiz, 256)
            config = ConfiguracaoProjeto(
                raiz=raiz,
                stac=ConfiguracaoStac("https://example.invalid/stac", "S2-16D-2"),
                area=ConfiguracaoArea("Teste", "MT", (-60, -15, -55, -10)),
                periodo=ConfiguracaoPeriodo("2025-01-01", "2025-12-31"),
                bandas=("B02", "B03", "B04", "B08", "EVI"),
                qualidade=ConfiguracaoQualidade(),
                preview=ConfiguracaoPreview(),
                download=ConfiguracaoDownload(pasta="data/sentinel2"),
                sincronizacao=ConfiguracaoSincronizacao(),
                dataset=ConfiguracaoDataset(
                    gerar=False,
                    pasta="data/dataset",
                    catalogo="catalogo/patches.csv",
                    patches=ConfiguracaoPatches(tamanho_px=256, stride_px=256),
                ),
            )
            servico = ServicoSentinel2(
                config,
                cliente_stac_factory=lambda _: self.fail("STAC não deveria ser consultado"),
                saida=lambda _: None,
            )

            resumo = servico.executar(
                OpcoesColeta(gerar_dataset=True, patch_size=256, max_itens=0)
            )

            self.assertEqual(resumo.aprovadas, 1)
            self.assertEqual(resumo.erros, 0)
            self.assertTrue((raiz / "catalogo/patches.csv").is_file())

    def test_remove_scl_apos_uso_quando_configurado(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            config = ConfiguracaoProjeto(
                raiz=raiz,
                stac=ConfiguracaoStac("https://example.invalid/stac", "S2-16D-2"),
                area=ConfiguracaoArea("Teste", "MT", (-60, -15, -55, -10)),
                periodo=ConfiguracaoPeriodo("2025-01-01", "2025-12-31"),
                bandas=("B02",),
                qualidade=ConfiguracaoQualidade(manter_scl=False),
                preview=ConfiguracaoPreview(),
                download=ConfiguracaoDownload(),
                sincronizacao=ConfiguracaoSincronizacao(),
            )
            scl = raiz / "qualidade/SCL.tif"
            scl.parent.mkdir()
            scl.touch()
            auxiliares = {"SCL": scl}

            ServicoSentinel2(config, saida=lambda _: None)._remover_scl_se_configurado(
                auxiliares
            )

            self.assertFalse(scl.exists())
            self.assertNotIn("SCL", auxiliares)

    def test_catalogo_remove_credenciais_e_query_de_urls(self) -> None:
        raiz = Path(__file__).resolve().parents[1]
        config = ConfiguracaoProjeto.carregar(raiz / "config/config.yaml", raiz=raiz)
        servico = ServicoSentinel2(config, saida=lambda _: None)

        registro = servico._registro(
            ItemFake(),
            "2025-10-01",
            "B02",
            0.0,
            "erro_download",
            "https://usuario:senha@example.test/B02.tif?token=segredo#fragmento",
            erro="falha em https://example.test/B02.tif?token=segredo",
        )

        self.assertEqual(registro.url, "https://example.test/B02.tif")
        self.assertNotIn("segredo", registro.erro)
