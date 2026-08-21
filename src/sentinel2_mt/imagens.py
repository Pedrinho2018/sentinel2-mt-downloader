from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling

from .configuracao import ConfiguracaoPreview, ConfiguracaoRGBDataset


class ProcessadorImagem:
    CLASSES_RUINS_SCL = np.array([3, 7, 8, 9, 10, 11], dtype=np.uint8)

    def percentual_nuvem(self, caminho: Path, amostra_px: int = 1400) -> float:
        with rasterio.open(caminho) as origem:
            escala = min(1.0, amostra_px / max(origem.width, origem.height))
            largura = max(1, int(origem.width * escala))
            altura = max(1, int(origem.height * escala))
            scl = origem.read(
                1,
                out_shape=(altura, largura),
                resampling=Resampling.nearest,
                masked=True,
            )
            dados_scl = np.asarray(scl.data)
            mascara_valida = ~np.ma.getmaskarray(scl) & np.isfinite(dados_scl)
            if origem.nodata is not None:
                if np.isnan(origem.nodata):
                    mascara_valida &= ~np.isnan(dados_scl)
                else:
                    mascara_valida &= dados_scl != origem.nodata

        return self.percentual_nuvem_scl(
            np.asarray(scl.filled(0)),
            mascara_valida,
        )

    def percentual_nuvem_scl(self, scl: np.ndarray, mascara: np.ndarray | None = None) -> float:
        validos = (scl != 0) & (scl != 1)
        if mascara is not None:
            validos &= mascara
        total = int(validos.sum())
        if total == 0:
            return 100.0
        ruins = validos & np.isin(scl, self.CLASSES_RUINS_SCL)
        return float(ruins.sum() * 100.0 / total)

    def gerar_rgb(self, arquivos: dict[str, Path], destino: Path, config: ConfiguracaoPreview) -> str:
        if any(banda not in arquivos or not arquivos[banda].exists() for banda in ("B04", "B03", "B02")):
            return "bandas_rgb_incompletas"

        vermelho, mascara_r = self._ler_preview(arquivos["B04"], config.tamanho_max_px)
        verde, mascara_g = self._ler_preview(arquivos["B03"], config.tamanho_max_px)
        azul, mascara_b = self._ler_preview(arquivos["B02"], config.tamanho_max_px)
        if vermelho.shape != verde.shape or vermelho.shape != azul.shape:
            return "dimensoes_incompativeis"

        mascara = mascara_r & mascara_g & mascara_b
        rgb = self.gerar_rgb_array((vermelho, verde, azul), (mascara, mascara, mascara), config)
        destino.parent.mkdir(parents=True, exist_ok=True)
        qualidade = max(1, min(100, config.qualidade_jpeg))
        Image.fromarray(rgb, mode="RGB").save(destino, "JPEG", quality=qualidade, optimize=True)
        return "gerado"

    @staticmethod
    def _ler_preview(caminho: Path, max_px: int) -> tuple[np.ndarray, np.ndarray]:
        with rasterio.open(caminho) as origem:
            escala = min(1.0, max_px / max(origem.width, origem.height))
            largura = max(1, int(origem.width * escala))
            altura = max(1, int(origem.height * escala))
            dados = origem.read(1, out_shape=(altura, largura), resampling=Resampling.bilinear).astype(np.float32)
            mascara = np.isfinite(dados)
            mascara &= dados != (origem.nodata if origem.nodata is not None else 0)
        return dados, mascara

    def gerar_rgb_array(
        self,
        canais: tuple[np.ndarray, np.ndarray, np.ndarray],
        mascaras: tuple[np.ndarray, np.ndarray, np.ndarray],
        config: ConfiguracaoPreview | ConfiguracaoRGBDataset,
    ) -> np.ndarray:
        mascara_comum = mascaras[0] & mascaras[1] & mascaras[2]
        return np.dstack(
            tuple(self.aplicar_stretch(canal, mascara_comum, config) for canal in canais)
        )

    @classmethod
    def aplicar_stretch(
        cls,
        dados: np.ndarray,
        mascara: np.ndarray,
        config: ConfiguracaoPreview | ConfiguracaoRGBDataset,
    ) -> np.ndarray:
        if config.metodo == "fixed":
            return cls.stretch_fixed(dados, mascara, config.minimo, config.maximo)
        return cls.stretch_percentile(dados, mascara, config.percentil_min, config.percentil_max)

    @staticmethod
    def stretch_fixed(dados: np.ndarray, mascara: np.ndarray, minimo: float, maximo: float) -> np.ndarray:
        if maximo <= minimo:
            raise ValueError("O máximo do stretch fixed deve ser maior que o mínimo")
        normalizado = np.clip((dados.astype(np.float32) - minimo) / (maximo - minimo), 0, 1)
        saida = np.rint(normalizado * 255).astype(np.uint8)
        saida[~mascara] = 0
        return saida

    @staticmethod
    def stretch_percentile(dados: np.ndarray, mascara: np.ndarray, pmin: float, pmax: float) -> np.ndarray:
        saida = np.zeros(dados.shape, dtype=np.uint8)
        valores = dados[mascara]
        if valores.size == 0:
            return saida
        minimo, maximo = np.percentile(valores, [pmin, pmax])
        if maximo <= minimo:
            return saida
        normalizado = np.clip((dados - minimo) / (maximo - minimo), 0, 1)
        saida = (normalizado * 255).astype(np.uint8)
        saida[~mascara] = 0
        return saida

    @staticmethod
    def _stretch(dados: np.ndarray, mascara: np.ndarray, pmin: float, pmax: float) -> np.ndarray:
        """Compatibilidade com chamadas da implementação anterior."""
        return ProcessadorImagem.stretch_percentile(dados, mascara, pmin, pmax)
