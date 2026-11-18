"""Seleção de candidatas com potencial agrícola (priorização, não rotulagem).

1. `mapa_cultivo` calcula, numa grade regular (padrão 0,05° ≈ 5,5 km, do tamanho
   de um recorte 512×512 a 10 m), a fração de pixels "cultivo" e "árvore" do
   ESA WorldCover 2021, lendo uma visão geral (overview) dos COGs públicos.
2. `selecionar_candidatas` escolhe pontos com fração de cultivo alta: primeiro
   dentro dos municípios agrícolas prioritários, depois nas demais regiões
   intermediárias (diversidade geográfica), com distância mínima entre si e em
   relação às amostras já existentes (evita quase-duplicatas).

A decisão final sobre cada recorte continua sendo a inspeção visual; a camada
de referência (2021) só ordena onde procurar imagens de 2026.
"""

from __future__ import annotations

from collections.abc import Callable
import math
import random

import numpy as np

from .grade import Regiao, distancia_km
from .referencia import URL_BASE, tile_worldcover

MUNICIPIOS_PRIORITARIOS = (
    "Sorriso", "Sinop", "Lucas do Rio Verde", "Nova Mutum", "Campo Novo do Parecis",
    "Primavera do Leste", "Campo Verde", "Sapezal", "Diamantino", "Querência", "Canarana", "Alto Garças",
)


def mapa_cultivo(
    caixa: tuple[float, float, float, float],
    passo: float = 0.05,
    *,
    nivel_overview: int = 3,
    abrir: Callable | None = None,
    url_base: str = URL_BASE,
) -> list[dict]:
    """Frações de cultivo (classe 40) e árvore (classe 10) por célula de `passo` graus."""
    import rasterio

    abrir = abrir or rasterio.open
    oeste, sul, leste, norte = caixa
    por_tile = round(3 / passo)
    lado = por_tile * 20  # 20×20 amostras por célula
    saida = []
    tiles = sorted({tile_worldcover(lon + 0.01, lat + 0.01)
                    for lon in np.arange(math.floor(oeste / 3) * 3, leste, 3)
                    for lat in np.arange(math.floor(sul / 3) * 3, norte, 3)})
    for tile in tiles:
        caminho = url_base.format(tile=tile)
        if caminho.startswith("http"):
            caminho = "/vsicurl/" + caminho
        try:
            with abrir(caminho, overview_level=nivel_overview) as fonte:
                classes = fonte.read(1, out_shape=(lado, lado))
                x0, y1 = fonte.bounds.left, fonte.bounds.top
        except Exception:  # noqa: BLE001 - tile inexistente (oceano) ou falha remota
            continue
        blocos = classes.reshape(por_tile, 20, por_tile, 20)
        validos = (blocos > 0).sum(axis=(1, 3))
        cultivo = (blocos == 40).sum(axis=(1, 3))
        arvore = (blocos == 10).sum(axis=(1, 3))
        for i in range(por_tile):
            for j in range(por_tile):
                lon = x0 + (j + 0.5) * passo
                lat = y1 - (i + 0.5) * passo
                if not (oeste <= lon <= leste and sul <= lat <= norte) or not validos[i, j]:
                    continue
                saida.append({"lon": round(lon, 5), "lat": round(lat, 5),
                              "cultivo": round(float(cultivo[i, j] / validos[i, j]), 3),
                              "arvore": round(float(arvore[i, j] / validos[i, j]), 3)})
    return saida


def _livre(lon: float, lat: float, pontos: list[tuple[float, float]], minimo_km: float) -> bool:
    return all(distancia_km(lon, lat, x, y) >= minimo_km for x, y in pontos)


def selecionar_candidatas(
    celulas: list[dict],
    estado: Regiao,
    regioes: list[Regiao],
    municipios: dict[str, tuple[float, float, float, float]],
    existentes: list[tuple[float, float]],
    *,
    total: int = 170,
    por_municipio: int = 7,
    min_cultivo: float = 0.4,
    max_arvore: float = 0.5,
    distancia_km_min: float = 12.0,
    distancia_existentes_km: float = 8.0,
    semente: int = 2026,
) -> list[dict]:
    """Lista ordenada (intercalando grupos) de pontos com potencial agrícola."""
    aptas = [c for c in celulas if c["cultivo"] >= min_cultivo and c["arvore"] <= max_arvore
             and estado.contem(c["lon"], c["lat"])]
    for c in aptas:
        c["regiao"] = next((r.nome for r in regioes if r.contem(c["lon"], c["lat"])), "")
        c["regiao_codigo"] = next((r.codigo for r in regioes if r.contem(c["lon"], c["lat"])), "")
    escolhidas: list[tuple[float, float]] = []
    grupos: dict[str, list[dict]] = {}

    def tentar(c: dict, grupo: str) -> bool:
        if not _livre(c["lon"], c["lat"], existentes, distancia_existentes_km):
            return False
        if not _livre(c["lon"], c["lat"], escolhidas, distancia_km_min):
            return False
        escolhidas.append((c["lon"], c["lat"]))
        grupos.setdefault(grupo, []).append({**c, "grupo": grupo})
        return True

    # 1) Municípios prioritários: maiores frações de cultivo dentro do bbox do município.
    for nome in sorted(municipios):
        o, s, l, n = municipios[nome]
        dentro = sorted((c for c in aptas if o <= c["lon"] <= l and s <= c["lat"] <= n),
                        key=lambda c: -c["cultivo"])
        for c in dentro:
            if len(grupos.get(f"municipio:{nome}", [])) >= por_municipio:
                break
            tentar(c, f"municipio:{nome}")
    # 2) Diversidade: demais áreas agrícolas, alternando regiões intermediárias.
    gerador = random.Random(semente)
    restantes: dict[str, list[dict]] = {}
    for c in aptas:
        restantes.setdefault(c["regiao"], []).append(c)
    for lista in restantes.values():
        gerador.shuffle(lista)
        lista.sort(key=lambda c: -round(c["cultivo"], 1))  # faixas de 10%, aleatório dentro da faixa
    while sum(len(v) for v in grupos.values()) < total and any(restantes.values()):
        for regiao in sorted(restantes):
            fila = restantes[regiao]
            while fila:
                if tentar(fila.pop(0), f"regiao:{regiao}"):
                    break
            if sum(len(v) for v in grupos.values()) >= total:
                break
    # Ordem de amostragem: intercala os grupos para o lote ficar diverso em qualquer ponto de parada.
    ordem: list[dict] = []
    filas = [list(v) for _, v in sorted(grupos.items())]
    while any(filas):
        for fila in filas:
            if fila:
                ordem.append(fila.pop(0))
    return ordem[:total]
