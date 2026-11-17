"""Amostragem por janela em COGs Sentinel-2 L2A (coleção S2_L2A-1 do INPE/BDC).

Para cada ponto, lê **só a janela** de 512×512 px (10 m) das bandas B02/B03/B04 e
a janela correspondente da SCL (20 m), via HTTP Range — sem baixar a cena inteira.

Qualidade: a cobertura de nuvem da cena (`eo:cloud_cover`) só ordena candidatas;
a decisão usa a SCL **dentro da janela** (sombra 3, nuvens 8/9, cirros 10, neve 11
e não classificado 7, as mesmas classes do pipeline). Se a janela tiver nuvem ou
dados ausentes acima do limite, tenta-se a próxima data.

Preservação: o GeoTIFF guarda os valores originais (uint16, CRS e transform da
cena, nodata 0) e a SCL na resolução nativa de 20 m, sem reamostrar. Só o PNG
para rotulagem converte a radiometria: no L2A com baseline ≥ 04.00 a ESA soma
1000 aos números digitais (BOA_ADD_OFFSET = -1000); o PNG remove esse deslocamento
e usa o mesmo stretch fixo (0–2000) do dataset existente.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import uuid

import numpy as np

from .grade import Celula, _dentro_poligono, _poligonos
import math

COLECAO_PADRAO = "S2_L2A-1"
CLASSES_RUINS_SCL = (3, 7, 8, 9, 10, 11)
BASELINE_RE = re.compile(r"_N(\d{4})_")
NAMESPACE_PATCH = uuid.UUID("6f0e3b52-6a4b-4f3e-9b8e-6d0b0a0b5a21")
AMBIENTE_GDAL = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
    "GDAL_HTTP_MAX_RETRY": "4",
    "GDAL_HTTP_RETRY_DELAY": "3",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "VSI_CACHE": "TRUE",
}


@dataclass(frozen=True)
class OpcoesAmostragem:
    inicio: str
    fim: str
    colecao: str = COLECAO_PADRAO
    tamanho_px: int = 512
    max_nuvem_cena_pct: float = 40.0
    max_tentativas: int = 6
    max_nuvem_janela_pct: float = 5.0
    min_validos_janela_pct: float = 99.0
    rgb_minimo: float = 0.0
    rgb_maximo: float = 2000.0


@dataclass
class Tentativa:
    item_id: str
    data: str
    nuvem_cena_pct: float | None
    resultado: str  # "aceita" ou o motivo da recusa
    nuvem_janela_pct: float | None = None
    validos_janela_pct: float | None = None


@dataclass
class ResultadoAmostra:
    celula: Celula
    aceita: bool
    tentativas: list[Tentativa] = field(default_factory=list)
    patch_id: str = ""
    pasta: str = ""
    metadata: dict = field(default_factory=dict)

    def para_dict(self) -> dict:
        dados = asdict(self)
        dados["celula"] = self.celula.para_dict()
        return dados


def deslocamento_radiometrico(item_id: str) -> int:
    """1000 para baseline ≥ 04.00 (ESA, desde 25/01/2022); 0 para as anteriores."""
    correspondencia = BASELINE_RE.search(item_id)
    if not correspondencia:
        return 0
    return 1000 if int(correspondencia.group(1)) >= 400 else 0


def qualidade_scl(scl: np.ndarray) -> tuple[float, float]:
    """(percentual de nuvem/sombra entre válidos, percentual de pixels válidos). SCL 0 = sem dado."""
    total = scl.size
    validos = scl != 0
    n_validos = int(validos.sum())
    if not n_validos:
        return 100.0, 0.0
    ruins = int((np.isin(scl, CLASSES_RUINS_SCL) & validos).sum())
    return 100.0 * ruins / n_validos, 100.0 * n_validos / total


def janela_dentro_da_pegada(item, lon: float, lat: float, lado_m: float) -> bool:
    """A pegada (geometria do item STAC) contém os 4 cantos e o centro da janela?

    Sem geometria no item, não descarta (a SCL decide depois).
    """
    geometria = getattr(item, "geometry", None)
    if not geometria:
        return True
    try:
        poligonos = _poligonos(geometria)
    except (KeyError, ValueError):
        return True
    meio_lat = (lado_m / 2) / 111_320.0
    meio_lon = (lado_m / 2) / (111_320.0 * max(math.cos(math.radians(lat)), 1e-6))
    pontos = [(lon, lat)] + [(lon + dx, lat + dy) for dx in (-meio_lon, meio_lon) for dy in (-meio_lat, meio_lat)]
    return all(any(_dentro_poligono(x, y, aneis) for aneis in poligonos) for x, y in pontos)


def rgb_para_rotulagem(b04: np.ndarray, b03: np.ndarray, b02: np.ndarray, offset: int, minimo: float, maximo: float) -> np.ndarray:
    """PNG 8 bits (R=B04, G=B03, B=B02) com remoção do offset L2A e stretch fixo."""
    mascara = (b04 > 0) & (b03 > 0) & (b02 > 0)
    canais = []
    for banda in (b04, b03, b02):
        refletancia = banda.astype(np.float32) - offset
        normalizado = np.clip((refletancia - minimo) / (maximo - minimo), 0, 1)
        canal = np.rint(normalizado * 255).astype(np.uint8)
        canal[~mascara] = 0
        canais.append(canal)
    return np.dstack(canais)


def _url_publica(url: str) -> str:
    return url.split("?", 1)[0]


def _data_item(item) -> str:
    if getattr(item, "datetime", None):
        return item.datetime.strftime("%Y-%m-%d")
    return str(item.properties.get("datetime", ""))[:10]


class AmostradorJanelas:
    def __init__(
        self,
        pasta_saida: Path,
        raiz: Path,
        cliente_stac,
        abrir_raster: Callable | None = None,
    ) -> None:
        import rasterio

        self.pasta_saida = Path(pasta_saida)
        self.raiz = Path(raiz).resolve()
        self.cliente = cliente_stac
        self.abrir = abrir_raster or rasterio.open

    # ------------------------------------------------------------------ candidatas

    def candidatas(self, celula: Celula, opcoes: OpcoesAmostragem) -> list:
        margem = 0.0005
        busca = self.cliente.search(
            collections=[opcoes.colecao],
            bbox=[celula.lon - margem, celula.lat - margem, celula.lon + margem, celula.lat + margem],
            datetime=f"{opcoes.inicio}/{opcoes.fim}",
        )
        itens = []
        for item in busca.items():
            nuvem = item.properties.get("eo:cloud_cover")
            if nuvem is not None and float(nuvem) > opcoes.max_nuvem_cena_pct:
                continue
            if not {"B02", "B03", "B04", "SCL"} <= set(item.assets):
                continue
            if not janela_dentro_da_pegada(item, celula.lon, celula.lat, opcoes.tamanho_px * 10):
                continue  # borda da faixa imageada ou do tile: a janela teria dados ausentes
            itens.append(item)
        # Menos nuvem primeiro; em empate, a data mais recente.
        itens.sort(key=lambda it: (float(it.properties.get("eo:cloud_cover") or 100.0), -_ordinal(_data_item(it))))
        return itens[: opcoes.max_tentativas]

    # ------------------------------------------------------------------ amostra

    def amostrar(self, celula: Celula, opcoes: OpcoesAmostragem) -> ResultadoAmostra:
        from rasterio.warp import transform as transformar
        from rasterio.windows import Window

        resultado = ResultadoAmostra(celula, False)
        try:
            itens = self.candidatas(celula, opcoes)
        except Exception as erro:  # noqa: BLE001 - registrar e seguir para a próxima célula
            resultado.tentativas.append(Tentativa("", "", None, f"falha na busca STAC: {erro}"[:200]))
            return resultado
        if not itens:
            resultado.tentativas.append(Tentativa("", "", None, "nenhuma cena com nuvem aceitável no período"))
            return resultado

        tamanho = opcoes.tamanho_px
        for item in itens:
            tentativa = Tentativa(item.id, _data_item(item), _float(item.properties.get("eo:cloud_cover")), "")
            resultado.tentativas.append(tentativa)
            try:
                with self.abrir(item.assets["B04"].href) as b04:
                    xs, ys = transformar("EPSG:4326", b04.crs, [celula.lon], [celula.lat])
                    coluna, linha = ~b04.transform * (xs[0], ys[0])
                    # Canto par, para a janela de 20 m da SCL alinhar exatamente (2×2 px).
                    col0 = int(coluna - tamanho / 2) // 2 * 2
                    lin0 = int(linha - tamanho / 2) // 2 * 2
                    if col0 < 0 or lin0 < 0 or col0 + tamanho > b04.width or lin0 + tamanho > b04.height:
                        tentativa.resultado = "janela fora da cena (borda do tile)"
                        continue
                    janela = Window(col0, lin0, tamanho, tamanho)
                    perfil = {
                        "crs": b04.crs,
                        "transform": b04.window_transform(janela),
                        "dtype": b04.dtypes[0],
                    }
                    banda_r = b04.read(1, window=janela)
                with self.abrir(item.assets["SCL"].href) as scl_raster:
                    janela_scl = Window(col0 // 2, lin0 // 2, tamanho // 2, tamanho // 2)
                    scl = scl_raster.read(1, window=janela_scl)
                    perfil_scl = {"transform": scl_raster.window_transform(janela_scl), "crs": scl_raster.crs}
                nuvem, validos_scl = qualidade_scl(scl)
                tentativa.nuvem_janela_pct = round(nuvem, 2)
                tentativa.validos_janela_pct = round(validos_scl, 2)
                if validos_scl < opcoes.min_validos_janela_pct:
                    tentativa.resultado = f"dados válidos {validos_scl:.1f}% (< {opcoes.min_validos_janela_pct:g}%)"
                    continue
                if nuvem > opcoes.max_nuvem_janela_pct:
                    tentativa.resultado = f"nuvem/sombra na janela {nuvem:.1f}% (> {opcoes.max_nuvem_janela_pct:g}%)"
                    continue
                bandas = {"B04": banda_r}
                for nome in ("B03", "B02"):
                    with self.abrir(item.assets[nome].href) as raster:
                        if raster.crs != perfil["crs"] or raster.transform != b04.transform:
                            raise ValueError(f"{nome} não está na mesma grade da B04")
                        bandas[nome] = raster.read(1, window=janela)
                validos = (bandas["B02"] > 0) & (bandas["B03"] > 0) & (bandas["B04"] > 0)
                validos_pct = 100.0 * float(validos.mean())
                if validos_pct < opcoes.min_validos_janela_pct:
                    tentativa.resultado = f"bandas com dados ausentes ({validos_pct:.1f}% válidos)"
                    continue
                tentativa.resultado = "aceita"
                self._gravar(resultado, item, celula, opcoes, bandas, scl, perfil, perfil_scl, col0, lin0, nuvem, validos_pct)
                resultado.aceita = True
                return resultado
            except Exception as erro:  # noqa: BLE001 - leitura remota pode falhar; tenta a próxima data
                tentativa.resultado = f"erro de leitura: {type(erro).__name__}: {erro}"[:200]
        return resultado

    # ------------------------------------------------------------------ gravação

    def _gravar(self, resultado, item, celula, opcoes, bandas, scl, perfil, perfil_scl, col0, lin0, nuvem, validos_pct) -> None:
        import rasterio
        from PIL import Image
        from rasterio.warp import transform_bounds

        tamanho = opcoes.tamanho_px
        data = _data_item(item)
        assinatura = hashlib.sha256(f"{item.id}:{col0}:{lin0}:{tamanho}".encode()).hexdigest()[:12]
        patch_id = f"{item.id}_{assinatura}_x{col0:06d}_y{lin0:06d}_{tamanho}"
        pasta = self.pasta_saida / data / item.id / patch_id
        pasta.mkdir(parents=True, exist_ok=False)
        comum = {"driver": "GTiff", "width": tamanho, "height": tamanho, "crs": perfil["crs"],
                 "nodata": 0, "tiled": True, "blockxsize": 256, "blockysize": 256, "compress": "deflate"}
        with rasterio.open(pasta / "multiband.tif", "w", count=3, dtype=perfil["dtype"],
                           transform=perfil["transform"], **comum) as saida:
            for indice, nome in enumerate(("B02", "B03", "B04"), start=1):
                saida.write(bandas[nome], indice)
                saida.set_band_description(indice, nome)
                saida.update_tags(indice, SOURCE_BAND=nome, NATIVE_RESOLUTION_M="10")
            saida.update_tags(SOURCE_ITEM=item.id, BOA_ADD_OFFSET=str(-deslocamento_radiometrico(item.id)))
        with rasterio.open(pasta / "scl.tif", "w", count=1, dtype="uint8", transform=perfil_scl["transform"],
                           **{**comum, "width": tamanho // 2, "height": tamanho // 2, "blockxsize": 128, "blockysize": 128}) as saida:
            saida.write(scl, 1)
            saida.set_band_description(1, "SCL")
            saida.update_tags(1, NATIVE_RESOLUTION_M="20")
        offset = deslocamento_radiometrico(item.id)
        rgb = rgb_para_rotulagem(bandas["B04"], bandas["B03"], bandas["B02"], offset, opcoes.rgb_minimo, opcoes.rgb_maximo)
        nome_png = f"{uuid.uuid5(NAMESPACE_PATCH, patch_id)}.png"
        Image.fromarray(rgb, mode="RGB").save(pasta / nome_png, "PNG")
        t = perfil["transform"]
        bounds = (t.c, t.f + t.e * tamanho, t.c + t.a * tamanho, t.f)
        bounds_wgs84 = transform_bounds(perfil["crs"], "EPSG:4326", *bounds, densify_pts=21)
        classes, contagens = np.unique(scl, return_counts=True)
        metadata = {
            "patch_id": patch_id,
            "scene_id": item.id,
            "collection": opcoes.colecao,
            "date": data,
            "datetime": item.properties.get("datetime"),
            "bands": ["B02", "B03", "B04"],
            "missing_bands": [],
            "crs": perfil["crs"].to_string(),
            "bounds": [round(v, 3) for v in bounds],
            "bounds_wgs84": [round(v, 6) for v in bounds_wgs84],
            "width": tamanho,
            "height": tamanho,
            "pixel_size": [abs(t.a), abs(t.e)],
            "cloud_pct": round(nuvem, 2),
            "valid_pixel_pct": round(validos_pct, 2),
            "eo_cloud_cover_cena": _float(item.properties.get("eo:cloud_cover")),
            "processing_baseline": (BASELINE_RE.search(item.id).group(1) if BASELINE_RE.search(item.id) else ""),
            "boa_add_offset": -offset,
            "rgb_png": {"arquivo": nome_png, "bandas": ["B04", "B03", "B02"],
                        "stretch": f"fixo {opcoes.rgb_minimo:g}–{opcoes.rgb_maximo:g} após remover o offset"},
            "scl_classes": {str(int(c)): int(n) for c, n in zip(classes, contagens)},
            "scl_classes_ruins": list(CLASSES_RUINS_SCL),
            "source_assets": {nome: _url_publica(item.assets[nome].href) for nome in ("B02", "B03", "B04", "SCL")},
            "janela_pixels_10m": {"coluna": col0, "linha": lin0, "tamanho": tamanho},
            "amostragem": {
                "celula": celula.id,
                "centro_celula_lon_lat": [celula.lon, celula.lat],
                "regiao_intermediaria": {"codigo_ibge": celula.regiao_codigo, "nome": celula.regiao_nome},
                "tentativas": [asdict(t) for t in resultado.tentativas],
            },
            "resampling": "nenhum (janelas na grade nativa: 10 m nas bandas, 20 m na SCL)",
        }
        (pasta / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        resultado.patch_id = patch_id
        resultado.pasta = pasta.resolve().relative_to(self.raiz).as_posix()
        resultado.metadata = metadata


def _float(valor) -> float | None:
    try:
        return round(float(valor), 3)
    except (TypeError, ValueError):
        return None


def _ordinal(data: str) -> int:
    try:
        return datetime.fromisoformat(data).toordinal()
    except ValueError:
        return 0
