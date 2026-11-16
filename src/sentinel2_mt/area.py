"""Definição da área de interesse: importação de limites e busca de municípios.

Todas as funções são locais e sem rede. O pipeline do projeto consulta o STAC por
bounding box em EPSG:4326; por isso, polígonos importados são reduzidos ao seu
retângulo envolvente, e o usuário é informado de que a busca usará esse retângulo.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import struct
import unicodedata

from .gui_support import normalizar_bbox


TAMANHO_MAXIMO_ARQUIVO = 64 * 1024 * 1024
CRS_GEOGRAFICOS_WGS84 = {
    "EPSG:4326",
    "OGC:CRS84",
    "URN:OGC:DEF:CRS:OGC:1.3:CRS84",
    "URN:OGC:DEF:CRS:EPSG::4326",
}
CAMPOS_NOME = ("nome", "name", "NM_MUN", "NOME", "NAME", "municipio", "NM_MUNICIP")


@dataclass(frozen=True)
class AreaImportada:
    nome: str
    bbox: tuple[float, float, float, float]
    origem: str
    crs_origem: str
    geometrias: int
    avisos: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Municipio:
    codigo_ibge: str
    nome: str
    uf: str
    bbox: tuple[float, float, float, float]


# --------------------------------------------------------------------------- GeoJSON


def importar_area(caminho: Path | str) -> AreaImportada:
    """Importa GeoJSON (`.geojson`/`.json`) ou Shapefile (`.shp` com `.prj`)."""
    caminho = Path(caminho).expanduser()
    sufixo = caminho.suffix.lower()
    if sufixo in {".geojson", ".json"}:
        return importar_geojson(caminho)
    if sufixo == ".shp":
        return importar_shapefile(caminho)
    raise ValueError("Formato não suportado. Use GeoJSON (.geojson, .json) ou Shapefile (.shp).")


def importar_geojson(caminho: Path | str) -> AreaImportada:
    caminho = _validar_arquivo(Path(caminho).expanduser())
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"GeoJSON inválido: {exc}") from exc
    if not isinstance(dados, dict):
        raise ValueError("GeoJSON inválido: o conteúdo deve ser um objeto.")

    crs = _crs_geojson(dados)
    bbox_origem, geometrias = bbox_de_geojson(dados)
    avisos: list[str] = []
    if crs in CRS_GEOGRAFICOS_WGS84:
        bbox = bbox_origem
    else:
        bbox = _reprojetar_bbox(bbox_origem, crs)
        avisos.append(
            f"Coordenadas convertidas de {crs} para EPSG:4326; o retângulo resultante "
            "envolve a área original."
        )
    if geometrias > 1 or _tem_poligono(dados):
        avisos.append(
            "A busca de imagens usará o retângulo envolvente do limite importado, "
            "não o contorno exato."
        )
    return AreaImportada(
        nome=_nome_geojson(dados) or caminho.stem,
        bbox=tuple(normalizar_bbox(list(bbox))),
        origem=caminho.name,
        crs_origem=crs,
        geometrias=geometrias,
        avisos=tuple(avisos),
    )


def bbox_de_geojson(dados: dict) -> tuple[tuple[float, float, float, float], int]:
    """Calcula o retângulo envolvente de qualquer objeto GeoJSON com coordenadas."""
    geometrias = list(_geometrias(dados))
    if not geometrias:
        raise ValueError("O GeoJSON não contém geometrias.")
    xs: list[float] = []
    ys: list[float] = []
    for geometria in geometrias:
        for x, y in _posicoes(geometria.get("coordinates")):
            xs.append(x)
            ys.append(y)
    if not xs:
        raise ValueError("O GeoJSON não contém coordenadas.")
    bbox = (min(xs), min(ys), max(xs), max(ys))
    if bbox[0] == bbox[2] or bbox[1] == bbox[3]:
        raise ValueError("A geometria não tem área (por exemplo, um único ponto ou linha reta).")
    return bbox, len(geometrias)


def _geometrias(objeto: object) -> Iterator[dict]:
    if not isinstance(objeto, dict):
        return
    tipo = objeto.get("type")
    if tipo == "FeatureCollection":
        for feicao in objeto.get("features") or []:
            yield from _geometrias(feicao)
    elif tipo == "Feature":
        yield from _geometrias(objeto.get("geometry"))
    elif tipo == "GeometryCollection":
        for geometria in objeto.get("geometries") or []:
            yield from _geometrias(geometria)
    elif tipo in {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}:
        yield objeto


def _tem_poligono(objeto: dict) -> bool:
    return any(g.get("type") in {"Polygon", "MultiPolygon"} for g in _geometrias(objeto))


def _posicoes(coordenadas: object) -> Iterator[tuple[float, float]]:
    if not isinstance(coordenadas, list) or not coordenadas:
        return
    if all(isinstance(valor, (int, float)) and not isinstance(valor, bool) for valor in coordenadas):
        if len(coordenadas) < 2:
            raise ValueError("Posição GeoJSON com menos de duas coordenadas.")
        x, y = float(coordenadas[0]), float(coordenadas[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("O GeoJSON contém coordenadas não finitas.")
        yield x, y
        return
    for item in coordenadas:
        yield from _posicoes(item)


def _crs_geojson(dados: dict) -> str:
    """Lê o membro `crs` legado; sem ele, o padrão RFC 7946 é WGS 84 (CRS84)."""
    crs = dados.get("crs")
    if not crs:
        return "OGC:CRS84"
    nome = ""
    if isinstance(crs, dict):
        propriedades = crs.get("properties") or {}
        if isinstance(propriedades, dict):
            nome = str(propriedades.get("name", ""))
    if not nome:
        raise ValueError("O membro 'crs' do GeoJSON não informa um nome de sistema de referência.")
    normalizado = nome.strip().upper()
    if normalizado.startswith("URN:OGC:DEF:CRS:EPSG:"):
        codigo = normalizado.rsplit(":", 1)[-1]
        candidato = f"EPSG:{codigo}"
        return "EPSG:4326" if codigo == "4326" else candidato
    return normalizado


def _nome_geojson(dados: dict) -> str:
    feicoes = dados.get("features") if dados.get("type") == "FeatureCollection" else [dados]
    if not isinstance(feicoes, list) or len(feicoes) != 1:
        return ""
    propriedades = feicoes[0].get("properties") if isinstance(feicoes[0], dict) else None
    if not isinstance(propriedades, dict):
        return ""
    for campo in CAMPOS_NOME:
        valor = propriedades.get(campo)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()[:120]
    return ""


# --------------------------------------------------------------------------- Shapefile


def importar_shapefile(caminho: Path | str) -> AreaImportada:
    """Lê o retângulo do cabeçalho do `.shp` e o CRS do `.prj` adjacente.

    O cabeçalho oficial (ESRI Shapefile Technical Description) guarda o retângulo
    de todas as feições; não é necessário ler as geometrias para obter a área de busca.
    """
    caminho = _validar_arquivo(Path(caminho).expanduser())
    with caminho.open("rb") as arquivo:
        cabecalho = arquivo.read(100)
    if len(cabecalho) < 100:
        raise ValueError("Shapefile inválido: cabeçalho incompleto.")
    codigo_arquivo = struct.unpack(">i", cabecalho[0:4])[0]
    if codigo_arquivo != 9994:
        raise ValueError("O arquivo não é um Shapefile (.shp) válido.")
    tipo = struct.unpack("<i", cabecalho[32:36])[0]
    if tipo == 0:
        raise ValueError("O Shapefile não contém geometrias.")
    bbox_origem = struct.unpack("<4d", cabecalho[36:68])
    if not all(math.isfinite(valor) for valor in bbox_origem):
        raise ValueError("O Shapefile tem um retângulo envolvente inválido.")

    prj = caminho.with_suffix(".prj")
    if not prj.is_file():
        prj = caminho.with_suffix(".PRJ")
    if not prj.is_file():
        raise ValueError(
            "O arquivo .prj não foi encontrado ao lado do .shp; sem ele não é possível "
            "saber o sistema de coordenadas com segurança."
        )
    wkt = prj.read_text(encoding="utf-8", errors="replace").strip()
    crs = _identificar_crs_wkt(wkt)
    avisos = [
        "A busca de imagens usará o retângulo envolvente do Shapefile, não o contorno exato."
    ]
    if crs == "EPSG:4326":
        bbox = bbox_origem
    else:
        bbox = _reprojetar_bbox(bbox_origem, wkt)
        avisos.insert(
            0,
            f"Coordenadas convertidas de {crs} para EPSG:4326; o retângulo resultante "
            "envolve a área original.",
        )
    return AreaImportada(
        nome=caminho.stem,
        bbox=tuple(normalizar_bbox(list(bbox))),
        origem=caminho.name,
        crs_origem=crs,
        geometrias=0,
        avisos=tuple(avisos),
    )


def _identificar_crs_wkt(wkt: str) -> str:
    if not wkt:
        raise ValueError("O arquivo .prj está vazio.")
    from rasterio.crs import CRS
    from rasterio.errors import CRSError

    try:
        crs = CRS.from_wkt(wkt)
    except CRSError as exc:
        raise ValueError(f"Sistema de coordenadas do .prj não reconhecido: {exc}") from exc
    epsg = crs.to_epsg()
    if epsg is not None:
        return f"EPSG:{epsg}"
    return crs.to_string() or "CRS desconhecido"


def _reprojetar_bbox(bbox: Iterable[float], crs_origem: str) -> tuple[float, float, float, float]:
    from rasterio.crs import CRS
    from rasterio.errors import CRSError
    from rasterio.warp import transform_bounds

    try:
        origem = CRS.from_user_input(crs_origem)
    except CRSError as exc:
        raise ValueError(f"Sistema de coordenadas não reconhecido: {crs_origem}") from exc
    # `densify_pts` amostra as bordas para que o retângulo final envolva a área curva.
    resultado = transform_bounds(origem, CRS.from_epsg(4326), *bbox, densify_pts=21)
    return tuple(float(valor) for valor in resultado)


def _validar_arquivo(caminho: Path) -> Path:
    if not caminho.is_file():
        raise ValueError(f"Arquivo não encontrado: {caminho.name}")
    if caminho.stat().st_size > TAMANHO_MAXIMO_ARQUIVO:
        raise ValueError("Arquivo muito grande para importação (limite de 64 MB).")
    return caminho


# --------------------------------------------------------------------------- Municípios


def normalizar_texto(texto: str) -> str:
    """Remove acentos e diferenças de caixa para comparar nomes de municípios."""
    decomposto = unicodedata.normalize("NFKD", texto)
    sem_acentos = "".join(c for c in decomposto if not unicodedata.combining(c))
    return " ".join(sem_acentos.casefold().split())


def carregar_municipios(caminho: Path | str) -> list[Municipio]:
    """Lê a lista local de municípios gerada por `tools/gerar_municipios_ibge.py`."""
    caminho = Path(caminho)
    if not caminho.is_file():
        return []
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    itens = dados.get("municipios", []) if isinstance(dados, dict) else []
    municipios: list[Municipio] = []
    for item in itens:
        try:
            bbox = tuple(normalizar_bbox(list(item["bbox"])))
            municipios.append(
                Municipio(
                    codigo_ibge=str(item["codigo_ibge"]),
                    nome=str(item["nome"]),
                    uf=str(item["uf"]).upper(),
                    bbox=bbox,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return municipios


def buscar_municipios(
    municipios: Iterable[Municipio], termo: str, limite: int = 10
) -> list[Municipio]:
    """Busca sem acentos: nomes que começam com o termo vêm antes dos que o contêm."""
    alvo = normalizar_texto(termo)
    if len(alvo) < 2:
        return []
    inicio: list[Municipio] = []
    contem: list[Municipio] = []
    for municipio in municipios:
        nome = normalizar_texto(municipio.nome)
        if nome.startswith(alvo):
            inicio.append(municipio)
        elif alvo in nome:
            contem.append(municipio)
    ordenar = lambda m: (normalizar_texto(m.nome), m.uf)  # noqa: E731
    return (sorted(inicio, key=ordenar) + sorted(contem, key=ordenar))[: max(0, limite)]
