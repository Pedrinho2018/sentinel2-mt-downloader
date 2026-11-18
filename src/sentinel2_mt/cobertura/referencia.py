"""Camada de referência de uso do solo (ESA WorldCover 10 m) para PRIORIZAR recortes agrícolas.

A camada nunca gera rótulos: serve só para estimar, por recorte, a fração de pixels
mapeados como "cultivo" e evitar janelas só de floresta. O WorldCover v200 é de 2021;
o ano difere das imagens (2026) e isso fica registrado no metadado de cada amostra.

Fonte: ESA WorldCover 10 m 2021 v200 (CC BY 4.0), COGs públicos em
https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/
"""

from __future__ import annotations

from collections.abc import Callable
import math

import numpy as np

URL_BASE = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
REFERENCIA = {
    "nome": "ESA WorldCover 10 m",
    "versao": "v200",
    "ano": 2021,
    "licenca": "CC BY 4.0",
    "url": "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/",
    "uso": "somente priorização de candidatas; não gera rótulos",
}
CLASSES = {10: "arvore", 20: "arbusto", 30: "campo_pastagem", 40: "cultivo", 50: "urbano",
           60: "solo_exposto", 70: "neve", 80: "agua", 90: "area_umida", 95: "mangue", 100: "musgo"}


def tile_worldcover(lon: float, lat: float) -> str:
    """Nome do tile 3°×3° pelo canto inferior esquerdo (ex.: S15W057)."""
    lat0 = math.floor(lat / 3) * 3
    lon0 = math.floor(lon / 3) * 3
    return f"{'S' if lat0 < 0 else 'N'}{abs(lat0):02d}{'W' if lon0 < 0 else 'E'}{abs(lon0):03d}"


def tiles_da_caixa(oeste: float, sul: float, leste: float, norte: float) -> list[str]:
    nomes = {tile_worldcover(lon, lat) for lon in (oeste, leste) for lat in (sul, norte)}
    return sorted(nomes)


def fracoes(contagem: dict[int, int]) -> dict[str, float]:
    total = sum(n for c, n in contagem.items() if c != 0)
    if not total:
        return {}
    return {CLASSES.get(c, str(c)): round(100 * n / total, 2) for c, n in sorted(contagem.items()) if c != 0}


class ReferenciaUsoSolo:
    """Reprojeta o WorldCover para a grade exata do recorte (vizinho mais próximo)."""

    def __init__(self, abrir: Callable | None = None, url_base: str = URL_BASE) -> None:
        import rasterio

        self._abrir = abrir or rasterio.open
        self.url_base = url_base

    def classes_na_grade(self, crs, transform, largura: int, altura: int, caixa_wgs84) -> np.ndarray:
        from rasterio import band
        from rasterio.warp import Resampling, reproject

        destino = np.zeros((altura, largura), dtype=np.uint8)
        for tile in tiles_da_caixa(*caixa_wgs84):
            caminho = self.url_base.format(tile=tile)
            if caminho.startswith("http"):
                caminho = "/vsicurl/" + caminho
            parcial = np.zeros_like(destino)
            with self._abrir(caminho) as fonte:
                reproject(band(fonte, 1), parcial, dst_transform=transform, dst_crs=crs,
                          src_nodata=0, dst_nodata=0, resampling=Resampling.nearest)
            destino = np.where(destino == 0, parcial, destino)
        return destino

    def resumo(self, crs, transform, largura: int, altura: int, caixa_wgs84) -> dict:
        classes = self.classes_na_grade(crs, transform, largura, altura, caixa_wgs84)
        valores, contagens = np.unique(classes, return_counts=True)
        fr = fracoes({int(v): int(n) for v, n in zip(valores, contagens)})
        return {**REFERENCIA, "fracoes_pct": fr, "cultivo_pct": fr.get("cultivo", 0.0),
                "arvore_pct": fr.get("arvore", 0.0), "campo_pastagem_pct": fr.get("campo_pastagem", 0.0),
                "cobertura_pct": round(100 * float((classes > 0).mean()), 2)}
