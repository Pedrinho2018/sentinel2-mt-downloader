import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from sentinel2_mt.catalogo import RepositorioCatalogoPatchesCSV
from sentinel2_mt.modelos import RegistroPatch


def registro_patch(**alteracoes) -> RegistroPatch:
    dados = {
        "patch_id": "cena_hash_x000000_y000000_256",
        "scene_id": "cena",
        "collection": "S2-16D-2",
        "date": "2025-10-01",
        "bbox": "[0, 0, 1, 1]",
        "crs": "EPSG:31981",
        "width": 256,
        "height": 256,
        "pixel_size": "[10, 10]",
        "cloud_pct": "1.00",
        "valid_pixel_pct": "100.00",
        "source_scene": "data/sentinel2/cena",
        "status": "APROVADO",
    }
    dados.update(alteracoes)
    return RegistroPatch(**dados)


class TestCatalogoPatches(TestCase):
    def test_regeneracao_preserva_labels_existentes(self) -> None:
        with TemporaryDirectory() as temporario:
            caminho = Path(temporario) / "patches.csv"
            repositorio = RepositorioCatalogoPatchesCSV()
            repositorio.salvar(
                caminho,
                [
                    registro_patch(
                        label="soja",
                        label_source="mapbiomas",
                        label_confidence="0.95",
                    )
                ],
            )

            repositorio.salvar(caminho, [registro_patch(cloud_pct="2.00")])

            with caminho.open("r", encoding="utf-8-sig", newline="") as arquivo:
                linha = next(csv.DictReader(arquivo))
            self.assertEqual(linha["cloud_pct"], "2.00")
            self.assertEqual(linha["label"], "soja")
            self.assertEqual(linha["label_source"], "mapbiomas")
            self.assertEqual(linha["label_confidence"], "0.95")
            self.assertFalse(list(caminho.parent.glob(".*.tmp")))
