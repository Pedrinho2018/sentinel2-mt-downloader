from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from sentinel2_mt.configuracao import ConfiguracaoProjeto
from sentinel2_mt.servico import OpcoesColeta, ServicoSentinel2


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
