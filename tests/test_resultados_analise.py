from __future__ import annotations

import csv
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import numpy as np
from PIL import Image
import rasterio
from rasterio.transform import from_origin

from sentinel2_mt.analise.historico import RegistroHistoricoAnalise
from sentinel2_mt.analise.resultados import (
    AVISO_CULTURA,
    carregar_resultado,
    exportar_csv,
    exportar_json,
    filtrar_historico,
    indicadores_vegetacao,
    modelo_classificacao_aprovado,
)

PATCH_ID = "CENA_X_20260322_abc_x000000_y000000_4"


def criar_artefatos(raiz: Path, colecao: str = "S2-16D-2") -> Path:
    """Cria um patch sintético, overlay e resultado.json no layout do pipeline."""
    pasta_patch = raiz / "data/dataset/2026-03-22/CENA_X" / PATCH_ID
    pasta_patch.mkdir(parents=True)
    rgb = pasta_patch / "11111111-2222-3333-4444-555555555555.png"
    Image.new("RGB", (4, 4), (10, 120, 10)).save(rgb)
    ndvi = np.array([[8000, 6000, -9999, 20000]] * 4, dtype=np.int16)
    evi = np.full((4, 4), 5000, dtype=np.int16)
    b04 = np.full((4, 4), 500, dtype=np.int16)
    with rasterio.open(
        pasta_patch / "multiband.tif", "w", driver="GTiff", width=4, height=4, count=3,
        dtype="int16", crs="EPSG:10857", transform=from_origin(0, 0, 10, 10),
    ) as saida:
        for indice, (nome, dados) in enumerate((("B04", b04), ("NDVI", ndvi), ("EVI", evi)), 1):
            saida.write(dados, indice)
            saida.set_band_description(indice, nome)
        mascara = np.full((4, 4), 255, dtype=np.uint8)
        mascara[3, :] = 0  # última linha fora da cena
        saida.write_mask(mascara)
    (pasta_patch / "metadata.json").write_text(
        json.dumps(
            {"patch_id": PATCH_ID, "scene_id": "CENA_X", "date": "2026-03-22", "collection": colecao}
        ),
        encoding="utf-8",
    )
    pasta_analise = raiz / "data/analises/analise-1"
    pasta_analise.mkdir(parents=True)
    Image.new("RGB", (4, 4), (200, 0, 0)).save(pasta_analise / f"{PATCH_ID}-deteccoes.png")
    resultado = pasta_analise / "resultado.json"
    resultado.write_text(
        json.dumps(
            {
                "analysis_id": "analise-1",
                "criado_em": "2026-11-16T12:00:00Z",
                "regiao": "Sorriso",
                "periodo_inicio": "2026-03-01",
                "periodo_fim": "2026-03-31",
                "scene_ids": ["CENA_X"],
                "modelo_versao": "2.1.0-beta.1",
                "modelo_hash": "a" * 64,
                "dispositivo": "cpu",
                "estatisticas": {"total_deteccoes": 2, "por_classe": {"talhao": 2}},
                "caminho_relatorio": "",
                "resultados": [
                    {
                        "entrada": str(rgb.relative_to(raiz)),
                        "nuvens_pct": 3.5,
                        "modelo": {"nome": "Detector", "classes": {"0": "talhao"}},
                        "deteccoes": [
                            {"classe": "talhao", "confianca": 0.4, "caixa": [0, 0, 2, 2]},
                            {"classe": "talhao", "confianca": 0.9, "caixa": [1, 1, 3, 3]},
                        ],
                    },
                    {"entrada": "../../etc/passwd", "deteccoes": []},
                ],
            }
        ),
        encoding="utf-8",
    )
    return resultado


class TestResultados(TestCase):
    def setUp(self) -> None:
        self.temporario = TemporaryDirectory()
        self.raiz = Path(self.temporario.name)
        self.raiz_dataset = self.raiz / "data/dataset"
        self.raiz_analises = self.raiz / "data/analises"

    def tearDown(self) -> None:
        self.temporario.cleanup()

    def _carregar(self, caminho: Path):
        return carregar_resultado(
            caminho,
            raiz_projeto=self.raiz,
            raiz_dataset=self.raiz_dataset,
            raiz_analises=self.raiz_analises,
        )

    def test_carrega_itens_com_data_cena_overlay_e_deteccoes_ordenadas(self) -> None:
        resultado = self._carregar(criar_artefatos(self.raiz))

        self.assertEqual(resultado.tarefa, "deteccao")
        self.assertEqual(resultado.modelo_classes, ("talhao",))
        item = resultado.itens[0]
        self.assertEqual(item.patch_id, PATCH_ID)
        self.assertEqual(item.data, "2026-03-22")
        self.assertEqual(item.cena, "CENA_X")
        self.assertIsNotNone(item.imagem_analisada)
        self.assertEqual([d.confianca for d in item.deteccoes], [0.9, 0.4])

    def test_caminho_fora_da_raiz_nao_e_resolvido(self) -> None:
        resultado = self._carregar(criar_artefatos(self.raiz))

        externo = resultado.itens[1]
        self.assertIsNone(externo.imagem_original)
        self.assertIsNone(externo.multibanda)

    def test_rejeita_json_fora_da_pasta_de_analises(self) -> None:
        fora = self.raiz / "resultado.json"
        fora.write_text("{}", encoding="utf-8")
        self.raiz_analises.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "fora da pasta"):
            self._carregar(fora)

    def test_indicadores_usam_escala_documentada_mascara_e_nodata(self) -> None:
        item = self._carregar(criar_artefatos(self.raiz)).itens[0]

        indicadores = {ind.indice: ind for ind in indicadores_vegetacao(item)}

        self.assertEqual(set(indicadores), {"NDVI", "EVI"})
        ndvi = indicadores["NDVI"]
        # 3 linhas válidas pela máscara; em cada uma, -9999 e 20000 são descartados.
        self.assertAlmostEqual(ndvi.media, 0.7, places=6)
        self.assertAlmostEqual(ndvi.minimo, 0.6, places=6)
        self.assertAlmostEqual(ndvi.maximo, 0.8, places=6)
        self.assertAlmostEqual(ndvi.pixels_validos_pct, 6 * 100 / 16)
        self.assertAlmostEqual(indicadores["EVI"].media, 0.5, places=6)

    def test_colecao_sem_escala_documentada_nao_exibe_indicador(self) -> None:
        item = self._carregar(criar_artefatos(self.raiz, colecao="S2_L2A-1")).itens[0]

        self.assertEqual(indicadores_vegetacao(item), [])

    def test_exporta_csv_em_pixels_sem_area(self) -> None:
        resultado = self._carregar(criar_artefatos(self.raiz))
        destino = self.raiz / "saida" / "deteccoes.csv"

        linhas = exportar_csv(resultado, destino)

        self.assertEqual(linhas, 2)
        with destino.open(encoding="utf-8") as arquivo:
            registros = list(csv.DictReader(arquivo))
        self.assertEqual(registros[0]["tarefa"], "deteccao_de_talhoes")
        self.assertEqual(registros[0]["data_cena"], "2026-03-22")
        self.assertIn("x1_px", registros[0])
        cabecalho = ",".join(registros[0]).casefold()
        self.assertNotIn("hectare", cabecalho)
        self.assertNotIn("area", cabecalho)

    def test_exporta_json_com_observacoes(self) -> None:
        resultado = self._carregar(criar_artefatos(self.raiz))
        destino = self.raiz / "saida.json"

        exportar_json(resultado, destino)

        dados = json.loads(destino.read_text(encoding="utf-8"))
        self.assertEqual(dados["analysis_id"], "analise-1")
        self.assertEqual(dados["observacoes"]["cultura"], AVISO_CULTURA)

    def test_sem_modelo_de_classificacao_aprovado(self) -> None:
        self.assertIsNone(modelo_classificacao_aprovado())
        self.assertIn("não determinada", AVISO_CULTURA)


class TestFiltroHistorico(TestCase):
    REGISTROS = [
        RegistroHistoricoAnalise("a1", "concluida", "2026-11-10T10:00:00Z", regiao="Sorriso", scene_id="S2_A"),
        RegistroHistoricoAnalise("a2", "concluida", "2026-01-05T10:00:00Z", regiao="Cuiabá", scene_id="S2_B"),
        RegistroHistoricoAnalise("a3", "concluida", "data inválida", regiao="Sorriso"),
    ]

    def test_filtra_por_texto_em_varios_campos(self) -> None:
        self.assertEqual([r.analysis_id for r in filtrar_historico(self.REGISTROS, "sorr")], ["a1", "a3"])
        self.assertEqual([r.analysis_id for r in filtrar_historico(self.REGISTROS, "s2_b")], ["a2"])

    def test_filtra_por_idade(self) -> None:
        recentes = filtrar_historico(self.REGISTROS, ultimos_dias=30, hoje=date(2026, 11, 16))
        self.assertEqual([r.analysis_id for r in recentes], ["a1"])
        self.assertEqual(len(filtrar_historico(self.REGISTROS, ultimos_dias=0)), 3)
