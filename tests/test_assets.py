from unittest import TestCase

from sentinel2_mt.assets import ResolvedorAssets


class TestResolvedorAssets(TestCase):
    def test_localiza_alias_sem_depender_da_colecao(self) -> None:
        asset = object()

        encontrado = ResolvedorAssets.localizar({"blue": asset}, "B02")

        self.assertIs(encontrado, asset)

    def test_normaliza_mascaras_e_indices(self) -> None:
        self.assertEqual(ResolvedorAssets.normalizar("scene_classification_map"), "SCL")
        self.assertEqual(ResolvedorAssets.normalizar("ndvi"), "NDVI")

