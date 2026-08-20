from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import planetary_computer
import rasterio
import yaml
from PIL import Image
from pyproj import Transformer
from pystac_client import Client
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PADRAO = ROOT / "config" / "config.yaml"
BANDAS = ("B02", "B03", "B04", "B08")


def carregar_config(caminho: Path) -> dict:
    with caminho.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def meses(inicio: str, fim: str):
    a = datetime.strptime(inicio, "%Y-%m-%d").date().replace(day=1)
    b = datetime.strptime(fim, "%Y-%m-%d").date().replace(day=1)
    atual = a
    while atual <= b:
        ultimo = monthrange(atual.year, atual.month)[1]
        yield atual, date(atual.year, atual.month, ultimo)
        if atual.month == 12:
            atual = date(atual.year + 1, 1, 1)
        else:
            atual = date(atual.year, atual.month + 1, 1)


def dilatar(mask: np.ndarray, raio: int) -> np.ndarray:
    if raio <= 0:
        return mask.copy()
    h, w = mask.shape
    padded = np.pad(mask, raio, mode="constant", constant_values=True)
    out = np.zeros_like(mask, dtype=bool)
    for dy in range(-raio, raio + 1):
        for dx in range(-raio, raio + 1):
            y0 = raio + dy
            x0 = raio + dx
            out |= padded[y0 : y0 + h, x0 : x0 + w]
    return out


def read_patch(item, asset_key: str, bounds_m, crs_alvo: str, shape: tuple[int, int], resampling: Resampling):
    asset = item.assets.get(asset_key)
    if asset is None:
        return None

    href = asset.href
    try:
        with rasterio.Env(
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF",
            GDAL_HTTP_MULTIRANGE="YES",
            GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
        ):
            with rasterio.open(href) as src:
                with WarpedVRT(src, crs=crs_alvo, resampling=resampling) as vrt:
                    win = from_bounds(*bounds_m, transform=vrt.transform)
                    data = vrt.read(
                        1,
                        window=win,
                        out_shape=shape,
                        resampling=resampling,
                        boundless=True,
                        fill_value=0,
                    )
                    return data
    except Exception:
        return None


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


def salvar_preview(b02, b03, b04, valid, destino: Path, saida_px: int, pmin: float, pmax: float, qualidade: int):
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
    img.save(destino, "JPEG", quality=max(1, min(100, qualidade)), optimize=True)


def buscar_itens(cliente: Client, colecao: str, bbox, inicio: date, fim: date, cloud_max: float, limite: int):
    search = cliente.search(
        collections=[colecao],
        bbox=bbox,
        datetime=f"{inicio.isoformat()}/{fim.isoformat()}",
        query={"eo:cloud_cover": {"lte": cloud_max}},
        max_items=max(limite * 4, limite),
    )
    itens = list(search.items())

    def chave(item):
        cloud = float(item.properties.get("eo:cloud_cover", 100.0) or 100.0)
        dt = item.datetime or datetime.max.replace(tzinfo=timezone.utc)
        return (cloud, abs(dt.day - 15), dt)

    itens.sort(key=chave)
    return itens[:limite]


def grid_patches(bbox_wgs84, crs_metrico: str, tamanho_px: int, resolucao_m: float):
    fwd = Transformer.from_crs("EPSG:4326", crs_metrico, always_xy=True)
    inv = Transformer.from_crs(crs_metrico, "EPSG:4326", always_xy=True)

    minlon, minlat, maxlon, maxlat = bbox_wgs84
    xs, ys = fwd.transform([minlon, maxlon, minlon, maxlon], [minlat, minlat, maxlat, maxlat])
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


def main() -> int:
    p = argparse.ArgumentParser(description="Gera patches Sentinel-2 limpos para catalogação de soja sem baixar cenas inteiras.")
    p.add_argument("--config", type=Path, default=CONFIG_PADRAO)
    p.add_argument("--inicio")
    p.add_argument("--fim")
    p.add_argument("--mes", help="Processa apenas YYYY-MM")
    p.add_argument("--max-patches", type=int, help="Limite por mês. 0 = todos.")
    p.add_argument("--limpar", action="store_true")
    args = p.parse_args()

    cfg = carregar_config(args.config)
    fonte = cfg["fonte"]
    aoi = cfg["aoi"]
    busca_cfg = cfg["busca"]
    pcfg = cfg["patches"]
    saida_cfg = cfg["saida"]

    inicio = args.inicio or cfg["periodo"]["inicio"]
    fim = args.fim or cfg["periodo"]["fim"]
    bbox = list(aoi["bbox_wgs84"])
    crs_m = str(aoi["crs_metrico"])
    tamanho = int(pcfg["tamanho_px"])
    resolucao = float(pcfg["resolucao_m"])
    max_patches = int(pcfg.get("max_patches_por_mes", 20)) if args.max_patches is None else args.max_patches
    cloud_max = float(busca_cfg.get("nuvem_cena_max_pct", 45))
    max_cenas = int(busca_cfg.get("max_cenas_por_mes", 8))
    clean_min = float(pcfg.get("cobertura_limpa_min_pct", 99.5))
    classes_validas = set(int(x) for x in pcfg.get("scl_classes_validas", [4, 5, 6]))
    margem = int(pcfg.get("margem_nuvem_px", 4))
    saida_px = int(pcfg.get("preview_saida_px", 768))
    qualidade = int(pcfg.get("qualidade_jpeg", 95))
    pmin = float(pcfg.get("percentil_min", 2))
    pmax = float(pcfg.get("percentil_max", 98))
    salvar_npz = bool(pcfg.get("salvar_npz", False))

    pasta_saida = ROOT / saida_cfg["pasta_patches"]
    catalogo = ROOT / saida_cfg["catalogo"]
    resumo_path = ROOT / saida_cfg["resumo"]

    if args.limpar and pasta_saida.exists():
        shutil.rmtree(pasta_saida)

    cliente = Client.open(fonte["stac_url"], modifier=planetary_computer.sign_inplace)
    grid = list(grid_patches(bbox, crs_m, tamanho, resolucao))
    if not grid:
        print("[ERRO] AOI pequeno demais para gerar patches.")
        return 2

    print("=" * 82)
    print(" DATASET SOJA | Sentinel-2 L2A remoto, sem baixar cenas inteiras")
    print("=" * 82)
    print(f"Fonte: {fonte['nome']} | coleção: {fonte['colecao']}")
    print(f"AOI: {aoi['nome']} | patches possíveis: {len(grid)}")
    print(f"Patch: {tamanho}x{tamanho}px @ {resolucao:g} m = {tamanho * resolucao / 1000:.2f} km")
    print(f"Cobertura limpa mínima: {clean_min:.1f}%")
    print(f"Cenas remotas avaliadas por mês: até {max_cenas}")
    print("Nenhuma cena completa será salva no computador.\n")

    registros: list[dict] = []
    meses_ok = 0
    aprovados_total = 0
    descartados_total = 0

    for m_ini, m_fim in meses(inicio, fim):
        mes = m_ini.strftime("%Y-%m")
        if args.mes and mes != args.mes:
            continue

        print(f"\n[{mes}] Buscando cenas...")
        itens = buscar_itens(cliente, fonte["colecao"], bbox, m_ini, m_fim, cloud_max, max_cenas)
        if not itens:
            print("  [SEM DADOS] nenhuma cena adequada encontrada.")
            continue

        print("  Cenas candidatas:")
        for item in itens:
            cloud = float(item.properties.get("eo:cloud_cover", 100.0) or 100.0)
            dt = item.datetime.date().isoformat() if item.datetime else "sem_data"
            print(f"    {dt} | nuvem cena={cloud:.1f}% | {item.id}")

        aprovados_mes = 0
        for patch in grid:
            if max_patches > 0 and aprovados_mes >= max_patches:
                break

            shape = (tamanho, tamanho)
            comp = {b: np.zeros(shape, dtype=np.float32) for b in BANDAS}
            filled = np.zeros(shape, dtype=bool)
            obs_count = np.zeros(shape, dtype=np.uint8)
            fontes_usadas: list[str] = []

            for item in itens:
                scl = read_patch(item, "SCL", patch["bounds_m"], crs_m, shape, Resampling.nearest)
                if scl is None:
                    continue

                clean = np.isin(scl, list(classes_validas))
                ruim = ~clean
                if margem > 0:
                    clean &= ~dilatar(ruim, margem)
                obs_count = np.minimum(obs_count.astype(np.uint16) + clean.astype(np.uint16), 255).astype(np.uint8)

                contrib = clean & ~filled
                if not contrib.any():
                    continue

                dados = {}
                valido = contrib.copy()
                for banda in BANDAS:
                    arr = read_patch(item, banda, patch["bounds_m"], crs_m, shape, Resampling.bilinear)
                    if arr is None:
                        valido[:] = False
                        break
                    arr = arr.astype(np.float32)
                    dados[banda] = arr
                    valido &= np.isfinite(arr) & (arr > 0)

                if not valido.any():
                    continue

                for banda in BANDAS:
                    comp[banda][valido] = dados[banda][valido]
                filled[valido] = True
                fontes_usadas.append(item.id)

                if filled.mean() * 100.0 >= clean_min:
                    break

            valid_pct = float(filled.mean() * 100.0)
            if valid_pct < clean_min:
                descartados_total += 1
                continue

            obs2_pct = float(((obs_count >= 2) & filled).sum() * 100.0 / max(int(filled.sum()), 1))
            spatial_id = f"r{patch['row']:04d}_c{patch['col']:04d}"
            patch_id = f"{mes}_{spatial_id}"
            pasta_patch = pasta_saida / mes / patch_id
            preview = pasta_patch / "preview_rgb.jpg"

            salvar_preview(comp["B02"], comp["B03"], comp["B04"], filled, preview, saida_px, pmin, pmax, qualidade)

            ndvi = np.zeros(shape, dtype=np.float32)
            den = comp["B08"] + comp["B04"]
            ndvi_mask = filled & (den != 0)
            ndvi[ndvi_mask] = (comp["B08"][ndvi_mask] - comp["B04"][ndvi_mask]) / den[ndvi_mask]

            if salvar_npz:
                np.savez_compressed(
                    pasta_patch / "dados.npz",
                    B02=comp["B02"],
                    B03=comp["B03"],
                    B04=comp["B04"],
                    B08=comp["B08"],
                    NDVI=ndvi,
                    VALID_MASK=filled.astype(np.uint8),
                )

            b = patch["bbox_wgs84"]
            registros.append(
                {
                    "patch_id": patch_id,
                    "spatial_id": spatial_id,
                    "mes": mes,
                    "minlon": round(b[0], 7),
                    "minlat": round(b[1], 7),
                    "maxlon": round(b[2], 7),
                    "maxlat": round(b[3], 7),
                    "valid_data_pct": round(valid_pct, 4),
                    "obs_2plus_pct": round(obs2_pct, 4),
                    "fontes_usadas": json.dumps(fontes_usadas, ensure_ascii=False),
                    "preview": str(preview.relative_to(ROOT)),
                    "label": "",
                    "observacao": "",
                }
            )
            aprovados_mes += 1
            aprovados_total += 1
            print(f"  [OK] {patch_id} | limpo={valid_pct:.2f}% | 2+obs={obs2_pct:.1f}% | fontes={len(fontes_usadas)}")

        if aprovados_mes:
            meses_ok += 1
        print(f"  Resultado {mes}: {aprovados_mes} patches aprovados")

    catalogo.parent.mkdir(parents=True, exist_ok=True)
    campos = [
        "patch_id", "spatial_id", "mes", "minlon", "minlat", "maxlon", "maxlat",
        "valid_data_pct", "obs_2plus_pct", "fontes_usadas", "preview", "label", "observacao",
    ]
    with catalogo.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(registros)

    resumo = {
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(),
        "fonte": fonte["nome"],
        "colecao": fonte["colecao"],
        "aoi": aoi["nome"],
        "periodo": [inicio, fim],
        "meses_com_resultado": meses_ok,
        "patches_aprovados": aprovados_total,
        "patches_descartados_por_nuvem_ou_dados": descartados_total,
        "cenas_completas_salvas": 0,
        "salvar_npz": salvar_npz,
        "catalogo": str(catalogo.relative_to(ROOT)),
    }
    resumo_path.parent.mkdir(parents=True, exist_ok=True)
    resumo_path.write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== RESUMO ===")
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0 if aprovados_total > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
