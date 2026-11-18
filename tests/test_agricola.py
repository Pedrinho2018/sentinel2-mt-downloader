"""Seleção agrícola e camada de referência (WorldCover sintético local, sem rede)."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import numpy as np
import rasterio
from rasterio.transform import from_origin

from sentinel2_mt.cobertura.agricola import mapa_cultivo, selecionar_candidatas
from sentinel2_mt.cobertura.grade import Regiao, distancia_km
from sentinel2_mt.cobertura.referencia import ReferenciaUsoSolo, tile_worldcover, tiles_da_caixa

QUADRADO = [[(-57.0, -15.0), (-54.0, -15.0), (-54.0, -12.0), (-57.0, -12.0), (-57.0, -15.0)]]


class TestReferencia(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.pasta = Path(self.tmp.name)
        # Tile S15W057 sintético (resolução grossa para o teste): metade oeste cultivo, leste árvore.
        lado = 600
        classes = np.full((lado, lado), 10, dtype=np.uint8)
        classes[:, : lado // 2] = 40
        with rasterio.open(self.pasta / "wc_S15W057.tif", "w", driver="GTiff", width=lado, height=lado, count=1,
                           dtype="uint8", crs="EPSG:4326", transform=from_origin(-57, -12, 3 / lado, 3 / lado),
                           nodata=0) as saida:
            saida.write(classes, 1)
        self.url = str(self.pasta / "wc_{tile}.tif")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_nome_dos_tiles(self) -> None:
        self.assertEqual(tile_worldcover(-55.5, -13.5), "S15W057")
        self.assertEqual(tile_worldcover(-54.0, -12.0), "S12W054")
        self.assertEqual(tiles_da_caixa(-54.1, -12.1, -53.9, -11.9), ["S12W054", "S12W057", "S15W054", "S15W057"])

    def test_resumo_na_grade_utm_do_recorte(self) -> None:
        ref = ReferenciaUsoSolo(url_base=self.url)
        # Recorte UTM 21S (EPSG:32721) de 512×512 a 10 m todo dentro da metade "cultivo".
        from rasterio.warp import transform_bounds

        transform = from_origin(600000, 8450000, 10, 10)
        caixa = transform_bounds("EPSG:32721", "EPSG:4326", 600000, 8450000 - 5120, 605120, 8450000)
        self.assertEqual(tiles_da_caixa(*caixa), ["S15W057"])
        resumo = ref.resumo("EPSG:32721", transform, 512, 512, caixa)
        self.assertEqual(resumo["ano"], 2021)
        self.assertGreater(resumo["cultivo_pct"], 99)
        self.assertIn("não gera rótulos", resumo["uso"])

    def test_mapa_cultivo_fracoes(self) -> None:
        celulas = mapa_cultivo((-57, -15, -54, -12), passo=0.5, nivel_overview=None,
                               abrir=lambda caminho, overview_level=None: rasterio.open(caminho), url_base=self.url)
        oeste = [c for c in celulas if c["lon"] < -55.6]
        leste = [c for c in celulas if c["lon"] > -55.4]
        self.assertEqual(len(celulas), 36)
        self.assertTrue(all(c["cultivo"] == 1.0 for c in oeste))
        self.assertTrue(all(c["arvore"] == 1.0 for c in leste))


class TestSelecao(unittest.TestCase):
    def test_prioriza_municipios_respeita_distancias_e_exclui_floresta(self) -> None:
        estado = Regiao("51", "MT", 1.0, [QUADRADO])
        regioes = [Regiao("5101", "A", 1.0, [QUADRADO])]
        celulas = [{"lon": round(-57 + 0.025 + 0.05 * i, 4), "lat": round(-15 + 0.025 + 0.05 * j, 4),
                    "cultivo": 0.9 if i < 30 else 0.0, "arvore": 0.05 if i < 30 else 0.95}
                   for i in range(60) for j in range(60)]
        existentes = [(-56.5, -14.5)]
        candidatas = selecionar_candidatas(celulas, estado, regioes, {"Alfa": (-57, -15, -56.5, -14.5)},
                                           existentes, total=20, por_municipio=4)
        self.assertEqual(len(candidatas), 20)
        self.assertEqual(sum(c["grupo"] == "municipio:Alfa" for c in candidatas), 4)
        self.assertTrue(all(c["cultivo"] >= 0.4 for c in candidatas))  # nada da metade florestal
        for a in candidatas:
            self.assertGreaterEqual(distancia_km(a["lon"], a["lat"], *existentes[0]), 8)
            for b in candidatas:
                if a is not b:
                    self.assertGreaterEqual(distancia_km(a["lon"], a["lat"], b["lon"], b["lat"]), 12)
        self.assertEqual(candidatas[0]["grupo"], "municipio:Alfa")  # grupos intercalados


if __name__ == "__main__":
    unittest.main()
