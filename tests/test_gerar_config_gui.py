import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

from sentinel2_mt.config_builder import GeradorConfiguracao


class TestGerarConfigGui(unittest.TestCase):
    def setUp(self):
        self.gerador = GeradorConfiguracao()

    def test_gerar_config_estrutura_core(self):
        dados = {
            "bbox": [-61.65, -18.05, -50.20, -7.30],
            "nome_regiao": "Mato Grosso",
            "uf": "MT",
            "colecao": "S2-16D-2",
            "stac_url": "https://data.inpe.br/bdc/stac/v1/",
            "inicio": "2025-09-01",
            "fim": "2026-04-30",
            "nuvem_max_pct": 20,
            "pasta_download": "data/sentinel2",
            "catalogo": "catalogo/catalogo_imagens.csv",
            "tamanho_max_px": 1600,
            "qualidade_jpeg": 92,
            "timeout_segundos": 120,
            "chunk_mb": 1,
            "max_itens_teste": 5,
            "max_candidatos_teste": 40,
        }

        config = self.gerador.gerar(dados)

        self.assertEqual(config["stac"]["colecao"], "S2-16D-2")
        self.assertEqual(config["area"]["bbox"], [-61.65, -18.05, -50.20, -7.30])
        self.assertEqual(config["periodo"]["inicio"], "2025-09-01")
        self.assertEqual(config["qualidade"]["nuvem_max_pct"], 20.0)
        self.assertEqual(config["download"]["pasta"], "data/sentinel2")
        self.assertEqual(config["sincronizacao"]["oauth_json"], "${GOOGLE_OAUTH_JSON:-}")
        self.assertEqual(config["sincronizacao"]["token_json"], "${GOOGLE_TOKEN_JSON:-config/google-token.json}")
        self.assertEqual(config["sincronizacao"]["pasta_id"], "${GOOGLE_PASTA_ID:-root}")
        self.assertEqual(config["sincronizacao"]["extensoes"], [".tif", ".tiff", ".jpg", ".jpeg"])

    def test_salvar_config_cria_arquivo(self):
        dados = {"bbox": [-1, -2, 3, 4]}
        destino = self.gerador.salvar(ROOT / "tmp_config.yaml", dados)
        self.assertTrue(destino.exists())
        with destino.open("r", encoding="utf-8") as f:
            texto = f.read()
        self.assertIn("stac:", texto)
        destino.unlink()

    def test_rejeita_bbox_invertida(self):
        with self.assertRaisesRegex(ValueError, "oeste < leste"):
            self.gerador.gerar({"bbox": [3, -2, -1, 4]})


if __name__ == "__main__":
    unittest.main()
