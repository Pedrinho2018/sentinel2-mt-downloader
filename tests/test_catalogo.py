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

    def test_regeneracao_substitui_apenas_registros_da_cena_reprocessada(self) -> None:
        with TemporaryDirectory() as temporario:
            caminho = Path(temporario) / "patches.csv"
            repositorio = RepositorioCatalogoPatchesCSV()
            obsoleto = registro_patch(patch_id="obsoleto")
            outra_cena = registro_patch(
                patch_id="outra_cena",
                scene_id="cena_2",
                source_scene="data/sentinel2/cena_2",
            )
            repositorio.salvar(caminho, [obsoleto, outra_cena])

            repositorio.salvar(caminho, [registro_patch(patch_id="atual")])

            with caminho.open("r", encoding="utf-8-sig", newline="") as arquivo:
                linhas = {linha["patch_id"]: linha for linha in csv.DictReader(arquivo)}
            self.assertEqual(set(linhas), {"atual", "outra_cena"})
            self.assertEqual(linhas["outra_cena"]["scene_id"], "cena_2")

    def test_nao_preserva_label_se_identidade_geoespacial_mudar(self) -> None:
        alteracoes = {
            "bbox": "[0, 0, 2, 2]",
            "crs": "EPSG:4326",
            "pixel_size": "[20, 20]",
            "bands": "B02;B03",
        }
        for campo, valor in alteracoes.items():
            with self.subTest(campo=campo), TemporaryDirectory() as temporario:
                caminho = Path(temporario) / "patches.csv"
                repositorio = RepositorioCatalogoPatchesCSV()
                repositorio.salvar(caminho, [registro_patch(label="soja")])

                repositorio.salvar(caminho, [registro_patch(**{campo: valor})])

                with caminho.open("r", encoding="utf-8-sig", newline="") as arquivo:
                    linha = next(csv.DictReader(arquivo))
                self.assertEqual(linha["label"], "")
                self.assertEqual(linha[campo], valor)
