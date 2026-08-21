from unittest import TestCase

import numpy as np

from sentinel2_mt.configuracao import ConfiguracaoRGBDataset
from sentinel2_mt.imagens import ProcessadorImagem


class TestStretchRGB(TestCase):
    def test_stretch_fixed_clipa_e_escala(self) -> None:
        dados = np.array([[-1, 0, 1000, 2000, 2001]], dtype=np.float32)
        mascara = np.ones(dados.shape, dtype=bool)

        resultado = ProcessadorImagem.stretch_fixed(dados, mascara, 0, 2000)

        np.testing.assert_array_equal(resultado, [[0, 0, 128, 255, 255]])
        self.assertEqual(resultado.dtype, np.uint8)

    def test_stretch_percentile_preserva_comportamento(self) -> None:
        dados = np.arange(100, dtype=np.float32).reshape(10, 10)
        mascara = np.ones(dados.shape, dtype=bool)

        resultado = ProcessadorImagem.stretch_percentile(dados, mascara, 2, 98)

        self.assertEqual(int(resultado.min()), 0)
        self.assertEqual(int(resultado.max()), 255)
        self.assertEqual(resultado.shape, dados.shape)

    def test_rgb_array_tem_tres_canais_uint8(self) -> None:
        canal = np.full((16, 16), 1000, dtype=np.uint16)
        mascara = np.ones(canal.shape, dtype=bool)

        rgb = ProcessadorImagem().gerar_rgb_array(
            (canal, canal, canal),
            (mascara, mascara, mascara),
            ConfiguracaoRGBDataset(),
        )

        self.assertEqual(rgb.shape, (16, 16, 3))
        self.assertEqual(rgb.dtype, np.uint8)

    def test_percentual_nuvem_scl_considera_classes_ruins(self) -> None:
        scl = np.array([[3, 7, 8, 9, 10, 11, 4, 5]], dtype=np.uint8)

        percentual = ProcessadorImagem().percentual_nuvem_scl(scl)

        self.assertAlmostEqual(percentual, 75.0)

