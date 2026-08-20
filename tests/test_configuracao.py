from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from sentinel2_mt.configuracao import ConfiguracaoProjeto


CONFIG_MINIMA = """
stac:
  url: https://exemplo.test/stac
  colecao: S2
area:
  nome: Mato Grosso
  uf: MT
  bbox: [-61.0, -18.0, -50.0, -7.0]
periodo:
  inicio: '2025-01-01'
  fim: '2025-12-31'
bandas: [B02, B03, B04]
download:
  pasta: data/imagens
"""


class TestConfiguracaoProjeto(TestCase):
    def test_carrega_defaults_e_resolve_caminho(self) -> None:
        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            arquivo = raiz / "config.yaml"
            arquivo.write_text(CONFIG_MINIMA, encoding="utf-8")

            config = ConfiguracaoProjeto.carregar(arquivo, raiz=raiz)

            self.assertEqual(config.area.uf, "MT")
            self.assertEqual(config.download.timeout_segundos, 120)
            self.assertEqual(config.sincronizacao.tamanho_lote, 100)
            self.assertEqual(config.caminho(config.download.pasta), raiz / "data/imagens")

    def test_rejeita_bbox_incompleto(self) -> None:
        with TemporaryDirectory() as temporario:
            arquivo = Path(temporario) / "config.yaml"
            arquivo.write_text(CONFIG_MINIMA.replace("[-61.0, -18.0, -50.0, -7.0]", "[-61.0, -18.0]"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "quatro coordenadas"):
                ConfiguracaoProjeto.carregar(arquivo)

    def test_expande_variavel_do_dotenv(self) -> None:
        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            pasta_config = raiz / "config"
            pasta_config.mkdir()
            arquivo = pasta_config / "config.yaml"
            arquivo.write_text(
                CONFIG_MINIMA + "\nsincronizacao:\n  oauth_json: '${GOOGLE_OAUTH_JSON:-config/padrao.json}'\n",
                encoding="utf-8",
            )
            (pasta_config / ".env").write_text("GOOGLE_OAUTH_JSON=config/cliente.json\n", encoding="utf-8")

            config = ConfiguracaoProjeto.carregar(arquivo, raiz=raiz)

            self.assertEqual(config.sincronizacao.oauth_json, "config/cliente.json")
