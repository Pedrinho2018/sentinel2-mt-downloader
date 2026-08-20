from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling

from .configuracao import ConfiguracaoPreview


class ProcessadorImagem:
    CLASSES_RUINS_SCL = np.array([3, 7, 8, 9, 10, 11], dtype=np.uint8)

    def percentual_nuvem(self, caminho: Path, amostra_px: int = 1400) -> float:
        with rasterio.open(caminho) as origem:
            escala = min(1.0, amostra_px / max(origem.width, origem.height))
            largura = max(1, int(origem.width * escala))
            altura = max(1, int(origem.height * escala))
            scl = origem.read(1, out_shape=(altura, largura), resampling=Resampling.nearest)

        validos = (scl != 0) & (scl != 1)
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
        rgb = np.dstack(
            (
                self._stretch(vermelho, mascara, config.percentil_min, config.percentil_max),
                self._stretch(verde, mascara, config.percentil_min, config.percentil_max),
                self._stretch(azul, mascara, config.percentil_min, config.percentil_max),
            )
        )
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

    @staticmethod
    def _stretch(dados: np.ndarray, mascara: np.ndarray, pmin: float, pmax: float) -> np.ndarray:
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
