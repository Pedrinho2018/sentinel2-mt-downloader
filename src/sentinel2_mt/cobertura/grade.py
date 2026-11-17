"""Limites, grade de cobertura e seleção de amostras para todo o Mato Grosso.

Geometria em lon/lat (SIRGAS 2000 ≈ WGS 84) sem dependências extras: teste de
ponto em polígono por *ray casting* e distância geodésica aproximada (haversine).

Cobertura geográfica não é o mesmo que capacidade de generalização: a grade
garante que o dataset *tenha amostras* de todo o estado; se o modelo generaliza
para essas regiões só se mede depois, com teste independente por região/safra.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import random

PASTA_LIMITES = Path(__file__).resolve().parents[3] / "assets" / "limites"
RAIO_TERRA_KM = 6371.0088


@dataclass(frozen=True)
class Regiao:
    codigo: str
    nome: str
    area_km2: float
    poligonos: tuple  # tupla de polígonos; cada polígono = tupla de anéis [(lon, lat), ...]

    def contem(self, lon: float, lat: float) -> bool:
        return any(_dentro_poligono(lon, lat, aneis) for aneis in self.poligonos)


def _dentro_anel(lon: float, lat: float, anel) -> bool:
    dentro = False
    n = len(anel)
    j = n - 1
    for i in range(n):
        xi, yi = anel[i]
        xj, yj = anel[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            dentro = not dentro
        j = i
    return dentro


def _dentro_poligono(lon: float, lat: float, aneis) -> bool:
    # Primeiro anel = contorno externo; os demais são buracos.
    return _dentro_anel(lon, lat, aneis[0]) and not any(_dentro_anel(lon, lat, b) for b in aneis[1:])


def _poligonos(geometria: dict) -> tuple:
    if geometria["type"] == "Polygon":
        return (tuple(tuple(map(tuple, anel)) for anel in geometria["coordinates"]),)
    if geometria["type"] == "MultiPolygon":
        return tuple(tuple(tuple(map(tuple, anel)) for anel in poligono) for poligono in geometria["coordinates"])
    raise ValueError(f"Geometria não suportada: {geometria['type']}")


def carregar_regioes(caminho: Path | None = None) -> list[Regiao]:
    dados = json.loads(Path(caminho or PASTA_LIMITES / "mt_regioes_intermediarias.geojson").read_text(encoding="utf-8"))
    regioes = []
    for feicao in dados["features"]:
        p = feicao["properties"]
        regioes.append(Regiao(str(p["codigo_ibge"]), p["nome"], float(p["area_km2"]), _poligonos(feicao["geometry"])))
    return regioes


def carregar_estado(caminho: Path | None = None) -> Regiao:
    dados = json.loads(Path(caminho or PASTA_LIMITES / "mt_estado.geojson").read_text(encoding="utf-8"))
    feicao = dados["features"][0]
    p = feicao["properties"]
    return Regiao(str(p["codigo_ibge"]), p["nome"], float(p["area_km2"]), _poligonos(feicao["geometry"]))


def distancia_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    f1, f2 = math.radians(lat1), math.radians(lat2)
    df, dl = f2 - f1, math.radians(lon2 - lon1)
    a = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return 2 * RAIO_TERRA_KM * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------- grade


@dataclass(frozen=True)
class Celula:
    id: str
    lon: float  # centro
    lat: float
    tamanho_graus: float
    regiao_codigo: str
    regiao_nome: str

    def limites(self) -> tuple[float, float, float, float]:
        m = self.tamanho_graus / 2
        return (self.lon - m, self.lat - m, self.lon + m, self.lat + m)

    def para_dict(self) -> dict:
        return asdict(self)


def criar_grade(regioes: list[Regiao], estado: Regiao, tamanho_graus: float = 0.25) -> list[Celula]:
    """Células regulares cujo centro está dentro do estado; região = a que contém o centro."""
    lons = [x for poligono in estado.poligonos for x, _ in poligono[0]]
    lats = [y for poligono in estado.poligonos for _, y in poligono[0]]
    oeste = math.floor(min(lons) / tamanho_graus) * tamanho_graus
    sul = math.floor(min(lats) / tamanho_graus) * tamanho_graus
    celulas = []
    linha = 0
    lat = sul + tamanho_graus / 2
    while lat < max(lats):
        coluna = 0
        lon = oeste + tamanho_graus / 2
        while lon < max(lons):
            if estado.contem(lon, lat):
                regiao = next((r for r in regioes if r.contem(lon, lat)), None)
                if regiao is not None:
                    celulas.append(
                        Celula(f"r{linha:03d}c{coluna:03d}", round(lon, 6), round(lat, 6), tamanho_graus,
                               regiao.codigo, regiao.nome)
                    )
            lon += tamanho_graus
            coluna += 1
        lat += tamanho_graus
        linha += 1
    return celulas


def alocar_por_regiao(regioes: list[Regiao], total: int, minimo: int) -> dict[str, int]:
    """Mínimo igual por região + restante proporcional à área (maiores restos)."""
    if minimo * len(regioes) > total:
        raise ValueError("O total do lote não comporta o mínimo por região.")
    restante = total - minimo * len(regioes)
    area_total = sum(r.area_km2 for r in regioes)
    cotas = {r.codigo: restante * r.area_km2 / area_total for r in regioes}
    alocacao = {codigo: minimo + int(cota) for codigo, cota in cotas.items()}
    sobra = total - sum(alocacao.values())
    for codigo in sorted(cotas, key=lambda c: -(cotas[c] - int(cotas[c])))[:sobra]:
        alocacao[codigo] += 1
    return alocacao


def selecionar_celulas(
    celulas: list[Celula],
    quantidade_por_regiao: dict[str, int],
    *,
    distancia_minima_km: float = 20.0,
    semente: int = 2026,
    excluir: set[str] | None = None,
) -> list[Celula]:
    """Sorteio reproduzível por região, mantendo distância mínima entre amostras.

    A distância mínima evita amostras quase iguais (janelas de 5 km sobrepostas ou
    vizinhas), que inflariam o dataset sem acrescentar diversidade.
    """
    excluir = excluir or set()
    gerador = random.Random(semente)
    escolhidas: list[Celula] = []
    for codigo, quantidade in sorted(quantidade_por_regiao.items()):
        candidatas = [c for c in celulas if c.regiao_codigo == codigo and c.id not in excluir]
        gerador.shuffle(candidatas)
        da_regiao = 0
        for celula in candidatas:
            if da_regiao >= quantidade:
                break
            if all(distancia_km(celula.lon, celula.lat, e.lon, e.lat) >= distancia_minima_km for e in escolhidas):
                escolhidas.append(celula)
                da_regiao += 1
    return escolhidas


def ordenar_por_regiao_intercalada(celulas: list[Celula]) -> list[Celula]:
    """Ordem de coleta alternando regiões, para um lote parcial já ser representativo."""
    por_regiao: dict[str, list[Celula]] = {}
    for celula in celulas:
        por_regiao.setdefault(celula.regiao_codigo, []).append(celula)
    filas = [por_regiao[c] for c in sorted(por_regiao)]
    ordem = []
    while any(filas):
        for fila in filas:
            if fila:
                ordem.append(fila.pop(0))
    return ordem
