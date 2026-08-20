from __future__ import annotations

import argparse
import csv
import io
import json
import math
import shutil
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import requests
import yaml
from PIL import Image
from pyproj import Transformer
from pystac_client import Client
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PADRAO = ROOT / "config" / "config.yaml"
ASSETS = ("B02", "B03", "B04", "B08", "SCL")


class DataAPIError(RuntimeError):
    pass


def carregar_config(caminho: Path) -> dict:
    with caminho.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def iterar_meses(inicio: str, fim: str):
    atual = datetime.strptime(inicio, "%Y-%m-%d").date().replace(day=1)
    ultimo_mes = datetime.strptime(fim, "%Y-%m-%d").date().replace(day=1)
    while atual <= ultimo_mes:
        ultimo_dia = monthrange(atual.year, atual.month)[1]
        yield atual, date(atual.year, atual.month, ultimo_dia)
        if atual.month == 12:
            atual = date(atual.year + 1, 1, 1)
        else:
            atual = date(atual.year, atual.month + 1, 1)


def sessao_http(tentativas: int, backoff: float) -> requests.Session:
    retry = Retry(
        total=max(0, tentativas - 1),
        connect=max(0, tentativas - 1),
        read=max(0, tentativas - 1),
        status=max(0, tentativas - 1),
        backoff_factor=max(0.0, backoff),
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
    s = requests.Session()
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": "sentinel2-mt-downloader/soja-dataapi-2.0"})
    return s


def dilatar(mask: np.ndarray, raio: int) -> np.ndarray:
    if raio <= 0:
        return mask.copy()
    h, w = mask.shape
    padded = np.pad(mask, raio, mode="constant", constant_values=False)
    out = np.zeros_like(mask, dtype=bool)
    for dy in range(-raio, raio + 1):
        for dx in range(-raio, raio + 1):
            y0 = raio + dy
            x0 = raio + dx
            out |= padded[y0 : y0 + h, x0 : x0 + w]
    return out


def stretch(arr: np.ndarray, mask: np.ndarray, pmin: float, pmax: float) -> np.ndarray:
    out = np.zeros(arr.shape, dtype=np.uint8)
    vals = arr[mask & np.isfinite(arr)]
    if vals.size == 0:
        return out
    lo, hi = np.percentile(vals, [pmin, pmax])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = float(vals.min()), float(vals.max())
    if hi <= lo:
        return out
    norm = np.clip((arr - lo) / (hi - lo), 0, 1)
    out = (norm * 255).astype(np.uint8)
    out[~mask] = 0
    return out


def salvar_preview(
    b02: np.ndarray,
    b03: np.ndarray,
    b04: np.ndarray,
    valid: np.ndarray,
    destino: Path,
    saida_px: int,
    pmin: float,
    pmax: float,
    qualidade: int,
) -> None:
    rgb = np.dstack(
        (
            stretch(b04, valid, pmin, pmax),
            stretch(b03, valid, pmin, pmax),
            stretch(b02, valid, pmin, pmax),
        )
    )
    img = Image.fromarray(rgb, mode="RGB")
    if saida_px > 0 and img.size != (saida_px, saida_px):
        img = img.resize((saida_px, saida_px), resample=Image.Resampling.NEAREST)
    destino.parent.mkdir(parents=True, exist_ok=True)
    img.save(
        destino,
        "JPEG",
        quality=max(1, min(100, qualidade)),
        optimize=True,
    )


def grid_patches(bbox_wgs84, crs_metrico: str, tamanho_px: int, resolucao_m: float):
    fwd = Transformer.from_crs("EPSG:4326", crs_metrico, always_xy=True)
    inv = Transformer.from_crs(crs_metrico, "EPSG:4326", always_xy=True)

    minlon, minlat, maxlon, maxlat = bbox_wgs84
    xs, ys = fwd.transform(
        [minlon, maxlon, minlon, maxlon],
        [minlat, minlat, maxlat, maxlat],
    )
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)

    lado = tamanho_px * resolucao_m
    cols = max(0, math.floor((maxx - minx) / lado))
    rows = max(0, math.floor((maxy - miny) / lado))

    for r in range(rows):
        for c in range(cols):
            x0 = minx + c * lado
            y0 = miny + r * lado
            x1 = x0 + lado
            y1 = y0 + lado
            lon0, lat0 = inv.transform(x0, y0)
            lon1, lat1 = inv.transform(x1, y1)
            yield {
                "row": r,
                "col": c,
                "bounds_m": (x0, y0, x1, y1),
                "bbox_wgs84": (
                    min(lon0, lon1),
                    min(lat0, lat1),
                    max(lon0, lon1),
                    max(lat0, lat1),
                ),
            }


def bbox_intersecta(a, b) -> bool:
    if not a or not b or len(a) != 4 or len(b) != 4:
        return True
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


def buscar_itens(
    cliente: Client,
    colecao: str,
    bbox,
    inicio: date,
    fim: date,
    cloud_max: float,
    max_itens: int,
):
    search = cliente.search(
        collections=[colecao],
        bbox=bbox,
        datetime=f"{inicio.isoformat()}/{fim.isoformat()}",
        query={"eo:cloud_cover": {"lte": cloud_max}},
        max_items=max_itens,
    )
    itens = list(search.items())

    def chave(item):
        cloud = float(item.properties.get("eo:cloud_cover", 100.0) or 100.0)
        dt = item.datetime or datetime.max.replace(tzinfo=timezone.utc)
        return (cloud, abs(dt.day - 15), dt)

    itens.sort(key=chave)
    return itens


def ler_patch_data_api(
    sessao: requests.Session,
    data_api_url: str,
    colecao: str,
    item_id: str,
    bbox_wgs84,
    tamanho_px: int,
    crs_destino: str,
    timeout: int,
) -> dict[str, np.ndarray]:
    minlon, minlat, maxlon, maxlat = bbox_wgs84
    bbox_txt = ",".join(f"{v:.10f}" for v in (minlon, minlat, maxlon, maxlat))
    url = (
        f"{data_api_url.rstrip('/')}/item/bbox/"
        f"{bbox_txt}/{tamanho_px}x{tamanho_px}.npy"
    )

    params: list[tuple[str, str]] = [
        ("collection", colecao),
        ("item", item_id),
    ]
    params.extend(("assets", asset) for asset in ASSETS)
    params.extend(
        [
            ("asset_as_band", "true"),
            ("coord_crs", "EPSG:4326"),
            ("dst_crs", crs_destino),
            ("resampling", "nearest"),
            ("reproject", "nearest"),
            ("return_mask", "true"),
        ]
    )

    resposta = sessao.get(url, params=params, timeout=timeout)
    if resposta.status_code != 200:
        texto = resposta.text[:300].replace("\n", " ")
        raise DataAPIError(f"HTTP {resposta.status_code}: {texto}")

    try:
        arr = np.load(io.BytesIO(resposta.content), allow_pickle=False)
    except Exception as exc:
        tipo = resposta.headers.get("content-type", "desconhecido")
        raise DataAPIError(f"resposta NPY inválida ({tipo}): {exc}") from exc

    if arr.ndim != 3:
        raise DataAPIError(f"shape inesperado: {arr.shape}")

    # TiTiler NPY inclui a máscara como último plano quando return_mask=true.
    if arr.shape[0] < len(ASSETS) + 1:
        raise DataAPIError(
            f"esperado >= {len(ASSETS) + 1} planos (5 assets + mask), recebido {arr.shape}"
        )

    dados = arr[: len(ASSETS)]
    mask = arr[-1] > 0
    return {
        "B02": dados[0],
        "B03": dados[1],
        "B04": dados[2],
        "B08": dados[3],
        "SCL": dados[4],
        "MASK": mask,
    }


def compor_patch(
    sessao: requests.Session,
    itens,
    data_api_url: str,
    colecao: str,
    bbox_patch,
    tamanho: int,
    crs_destino: str,
    timeout: int,
    classes_validas: set[int],
    margem: int,
    max_cenas: int,
):
    candidatos = [i for i in itens if bbox_intersecta(i.bbox, bbox_patch)]
    candidatos = candidatos[:max_cenas]

    if not candidatos:
        return None, "sem_cenas_intersectando"

    saidas = {
        "B02": np.zeros((tamanho, tamanho), dtype=np.float32),
        "B03": np.zeros((tamanho, tamanho), dtype=np.float32),
        "B04": np.zeros((tamanho, tamanho), dtype=np.float32),
        "B08": np.zeros((tamanho, tamanho), dtype=np.float32),
    }
    preenchido = np.zeros((tamanho, tamanho), dtype=bool)
    obs_count = np.zeros((tamanho, tamanho), dtype=np.uint8)
    fontes_usadas: list[dict] = []
    erros: list[str] = []

    for item in candidatos:
        try:
            dados = ler_patch_data_api(
                sessao=sessao,
                data_api_url=data_api_url,
                colecao=colecao,
                item_id=item.id,
                bbox_wgs84=bbox_patch,
                tamanho_px=tamanho,
                crs_destino=crs_destino,
                timeout=timeout,
            )
        except Exception as exc:
            erros.append(f"{item.id}: {exc}")
            continue

        mask_servico = dados["MASK"]
        scl = np.rint(dados["SCL"]).astype(np.int16)
        superficie_ok = np.isin(scl, list(classes_validas))

        # Só dilatamos pixels contaminados dentro da área realmente coberta pela cena.
        contaminado = mask_servico & ~superficie_ok
        contaminado = dilatar(contaminado, margem)
        limpo = mask_servico & superficie_ok & ~contaminado

        obs_count = np.minimum(
            obs_count.astype(np.uint16) + limpo.astype(np.uint16),
            255,
        ).astype(np.uint8)

        novos = limpo & ~preenchido
        if novos.any():
            for banda in ("B02", "B03", "B04", "B08"):
                saidas[banda][novos] = dados[banda][novos]
            preenchido[novos] = True
            fontes_usadas.append(
                {
                    "item_id": item.id,
                    "data": item.datetime.date().isoformat() if item.datetime else "",
                    "cloud_scene_pct": float(item.properties.get("eo:cloud_cover", 100.0) or 100.0),
                    "pixels_adicionados": int(novos.sum()),
                }
            )

        if preenchido.all():
            break

    if not fontes_usadas:
        detalhe = erros[0] if erros else "nenhuma fonte forneceu pixels limpos"
        return None, detalhe

    coverage = float(preenchido.mean() * 100.0)
    obs2 = float(((obs_count >= 2) & preenchido).sum() * 100.0 / max(int(preenchido.sum()), 1))

    denom = saidas["B08"] + saidas["B04"]
    ndvi = np.full((tamanho, tamanho), np.nan, dtype=np.float32)
    ok_ndvi = preenchido & np.isfinite(denom) & (denom != 0)
    ndvi[ok_ndvi] = (
        (saidas["B08"][ok_ndvi] - saidas["B04"][ok_ndvi]) / denom[ok_ndvi]
    )

    return {
        **saidas,
        "NDVI": ndvi,
        "VALID": preenchido,
        "OBS_COUNT": obs_count,
        "coverage_pct": coverage,
        "obs2_pct": obs2,
        "fontes": fontes_usadas,
        "erros_fontes": erros,
    }, "ok"


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Gera patches Sentinel-2 limpos para soja usando Planetary Computer Data API NPY, "
            "sem Rasterio/GDAL e sem baixar cenas inteiras."
        )
    )
    p.add_argument("--config", type=Path, default=CONFIG_PADRAO)
    p.add_argument("--inicio")
    p.add_argument("--fim")
    p.add_argument("--mes", help="Processa apenas YYYY-MM")
    p.add_argument("--max-patches", type=int, help="Limite por mês. 0 = todos")
    p.add_argument("--limpar", action="store_true")
    args = p.parse_args()

    cfg = carregar_config(args.config)
    fonte = cfg["fonte"]
    aoi = cfg["aoi"]
    busca_cfg = cfg["busca"]
    http_cfg = cfg.get("http", {})
    pcfg = cfg["patches"]
    saida_cfg = cfg["saida"]

    inicio = args.inicio or cfg["periodo"]["inicio"]
    fim = args.fim or cfg["periodo"]["fim"]
    bbox = list(aoi["bbox_wgs84"])
    crs_m = str(aoi["crs_metrico"])
    tamanho = int(pcfg["tamanho_px"])
    resolucao = float(pcfg["resolucao_m"])
    max_patches = int(pcfg.get("max_patches_por_mes", 20)) if args.max_patches is None else args.max_patches
    cloud_max = float(busca_cfg.get("nuvem_cena_max_pct", 80))
    max_itens_mes = int(busca_cfg.get("max_itens_busca_mes", 120))
    max_cenas_patch = int(busca_cfg.get("max_cenas_por_patch", 10))
    clean_min = float(pcfg.get("cobertura_limpa_min_pct", 99.5))
    classes_validas = set(int(x) for x in pcfg.get("scl_classes_validas", [4, 5, 6]))
    margem = int(pcfg.get("margem_nuvem_px", 4))
    saida_px = int(pcfg.get("preview_saida_px", 768))
    qualidade = int(pcfg.get("qualidade_jpeg", 95))
    pmin = float(pcfg.get("percentil_min", 2))
    pmax = float(pcfg.get("percentil_max", 98))
    salvar_npz = bool(pcfg.get("salvar_npz", False))
    timeout = int(http_cfg.get("timeout_segundos", 90))
    tentativas = int(http_cfg.get("tentativas", 4))
    backoff = float(http_cfg.get("backoff_segundos", 1.0))

    pasta_saida = ROOT / saida_cfg["pasta_patches"]
    catalogo = ROOT / saida_cfg["catalogo"]
    resumo_path = ROOT / saida_cfg["resumo"]

    if args.limpar and pasta_saida.exists():
        shutil.rmtree(pasta_saida)

    cliente = Client.open(fonte["stac_url"])
    sessao = sessao_http(tentativas, backoff)
    grid = list(grid_patches(bbox, crs_m, tamanho, resolucao))
    if not grid:
        print("[ERRO] AOI pequeno demais para gerar patches.")
        return 2

    print("=" * 82)
    print(" DATASET SOJA | Sentinel-2 L2A via Data API NPY")
    print("=" * 82)
    print(f"Fonte: {fonte['nome']} | coleção: {fonte['colecao']}")
    print(f"AOI: {aoi['nome']} | patches possíveis: {len(grid)}")
    print(f"Patch: {tamanho}x{tamanho}px @ {resolucao:g} m = {tamanho * resolucao / 1000:.2f} km")
    print(f"Cobertura limpa mínima: {clean_min:.1f}%")
    print(f"Até {max_cenas_patch} cenas remotas por patch")
    print("Rasterio/GDAL: NÃO UTILIZADO")
    print("Cenas completas salvas no PC: NÃO\n")

    registros: list[dict] = []
    total_ok = total_descartados = total_erros_api = 0
    meses_processados = 0
    erros_amostra: list[str] = []

    for mes_inicio, mes_fim in iterar_meses(inicio, fim):
        mes_nome = mes_inicio.strftime("%Y-%m")
        if args.mes and mes_nome != args.mes:
            continue

        meses_processados += 1
        print(f"\n[{mes_nome}] Buscando cenas STAC...")
        itens = buscar_itens(
            cliente=cliente,
            colecao=fonte["colecao"],
            bbox=bbox,
            inicio=mes_inicio,
            fim=mes_fim,
            cloud_max=cloud_max,
            max_itens=max_itens_mes,
        )
        print(f"  Itens encontrados no AOI: {len(itens)}")
        if itens:
            print("  Melhores candidatos globais:")
            for item in itens[:8]:
                cloud = float(item.properties.get("eo:cloud_cover", 100.0) or 100.0)
                data_item = item.datetime.date().isoformat() if item.datetime else "sem-data"
                print(f"    {data_item} | nuvem cena={cloud:.1f}% | {item.id}")
        else:
            print("  [AVISO] Nenhuma cena encontrada.")
            continue

        aprovados_mes = 0
        avaliados_mes = 0

        for patch in grid:
            if max_patches > 0 and aprovados_mes >= max_patches:
                break

            avaliados_mes += 1
            resultado, status = compor_patch(
                sessao=sessao,
                itens=itens,
                data_api_url=fonte["data_api_url"],
                colecao=fonte["colecao"],
                bbox_patch=patch["bbox_wgs84"],
                tamanho=tamanho,
                crs_destino=crs_m,
                timeout=timeout,
                classes_validas=classes_validas,
                margem=margem,
                max_cenas=max_cenas_patch,
            )

            if resultado is None:
                total_descartados += 1
                if "HTTP" in status or "NPY" in status or "shape" in status:
                    total_erros_api += 1
                    if len(erros_amostra) < 5:
                        erros_amostra.append(status)
                continue

            coverage = float(resultado["coverage_pct"])
            if coverage < clean_min:
                total_descartados += 1
                continue

            r = patch["row"]
            c = patch["col"]
            spatial_id = f"r{r:04d}_c{c:04d}"
            patch_id = f"{mes_nome}_{spatial_id}"
            pasta_patch = pasta_saida / mes_nome / patch_id
            preview = pasta_patch / "preview_rgb.jpg"

            salvar_preview(
                resultado["B02"],
                resultado["B03"],
                resultado["B04"],
                resultado["VALID"],
                preview,
                saida_px,
                pmin,
                pmax,
                qualidade,
            )

            if salvar_npz:
                pasta_patch.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(
                    pasta_patch / "dados_cientificos.npz",
                    B02=resultado["B02"],
                    B03=resultado["B03"],
                    B04=resultado["B04"],
                    B08=resultado["B08"],
                    NDVI=resultado["NDVI"],
                    VALID=resultado["VALID"],
                    OBS_COUNT=resultado["OBS_COUNT"],
                )

            fontes = resultado["fontes"]
            bbox_patch = patch["bbox_wgs84"]
            ndvi_valid = resultado["NDVI"][resultado["VALID"]]
            ndvi_valid = ndvi_valid[np.isfinite(ndvi_valid)]
            ndvi_medio = float(ndvi_valid.mean()) if ndvi_valid.size else float("nan")

            registros.append(
                {
                    "patch_id": patch_id,
                    "spatial_id": spatial_id,
                    "mes": mes_nome,
                    "row": r,
                    "col": c,
                    "minlon": f"{bbox_patch[0]:.8f}",
                    "minlat": f"{bbox_patch[1]:.8f}",
                    "maxlon": f"{bbox_patch[2]:.8f}",
                    "maxlat": f"{bbox_patch[3]:.8f}",
                    "clean_coverage_pct": f"{coverage:.4f}",
                    "obs_2plus_pct": f"{resultado['obs2_pct']:.4f}",
                    "fontes_usadas": len(fontes),
                    "source_ids": ";".join(f["item_id"] for f in fontes),
                    "source_dates": ";".join(f["data"] for f in fontes),
                    "ndvi_medio": "" if not np.isfinite(ndvi_medio) else f"{ndvi_medio:.6f}",
                    "preview": str(preview.relative_to(ROOT)),
                    "label": "",
                    "observacao": "",
                }
            )
            aprovados_mes += 1
            total_ok += 1
            print(
                f"  [OK] {patch_id} | limpo={coverage:.2f}% | "
                f"2+obs={resultado['obs2_pct']:.1f}% | fontes={len(fontes)}"
            )

        print(
            f"  Resumo {mes_nome}: avaliados={avaliados_mes} | "
            f"aprovados={aprovados_mes}"
        )

    catalogo.parent.mkdir(parents=True, exist_ok=True)
    campos = [
        "patch_id",
        "spatial_id",
        "mes",
        "row",
        "col",
        "minlon",
        "minlat",
        "maxlon",
        "maxlat",
        "clean_coverage_pct",
        "obs_2plus_pct",
        "fontes_usadas",
        "source_ids",
        "source_dates",
        "ndvi_medio",
        "preview",
        "label",
        "observacao",
    ]
    with catalogo.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        writer.writerows(registros)

    resumo = {
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(),
        "pipeline": "planetary_computer_data_api_npy",
        "usa_rasterio_gdal": False,
        "salva_cena_completa": False,
        "aoi": aoi["nome"],
        "periodo_inicio": inicio,
        "periodo_fim": fim,
        "mes_filtro": args.mes or "todos",
        "patch_px": tamanho,
        "resolucao_m": resolucao,
        "cobertura_limpa_min_pct": clean_min,
        "meses_processados": meses_processados,
        "patches_aprovados": total_ok,
        "patches_descartados": total_descartados,
        "erros_api_estimados": total_erros_api,
        "erros_amostra": erros_amostra,
        "catalogo": str(catalogo.relative_to(ROOT)),
    }
    resumo_path.parent.mkdir(parents=True, exist_ok=True)
    resumo_path.write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 82)
    print(" RESUMO")
    print("=" * 82)
    print(json.dumps(resumo, ensure_ascii=False, indent=2))

    if total_ok == 0:
        print("\n[ERRO] Nenhum patch aprovado. Não foram geradas imagens para catalogação.")
        if erros_amostra:
            print("Primeiros erros da Data API:")
            for erro in erros_amostra:
                print(f"  - {erro}")
        return 1

    print(f"\n[OK] Previews: {pasta_saida}")
    print(f"[OK] Catálogo: {catalogo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
