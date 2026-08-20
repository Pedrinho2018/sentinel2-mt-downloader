from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
import yaml
from PIL import Image
from rasterio.windows import Window
from rasterio.warp import transform, transform_bounds

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PADRAO = ROOT / "config" / "config.yaml"
BANDAS_OBRIGATORIAS = ("B02", "B03", "B04", "B08", "NDVI")


def carregar_config(caminho: Path) -> dict:
    with caminho.open("r", encoding="utf-8") as arquivo:
        return yaml.safe_load(arquivo)


def localizar_raster(pasta: Path, nome: str) -> Path | None:
    candidatos = sorted(pasta.glob(f"{nome}.*"))
    return candidatos[0] if candidatos else None


def esticar(dados: np.ndarray, mascara: np.ndarray, pmin: float, pmax: float) -> np.ndarray:
    saida = np.zeros(dados.shape, dtype=np.uint8)
    valores = dados[mascara & np.isfinite(dados)]
    if valores.size == 0:
        return saida
    minimo, maximo = np.percentile(valores, [pmin, pmax])
    if not np.isfinite(minimo) or not np.isfinite(maximo) or maximo <= minimo:
        return saida
    normalizado = np.clip((dados - minimo) / (maximo - minimo), 0, 1)
    saida = (normalizado * 255).astype(np.uint8)
    saida[~mascara] = 0
    return saida


def gerar_preview(
    b04: np.ndarray,
    b03: np.ndarray,
    b02: np.ndarray,
    mascara: np.ndarray,
    destino: Path,
    pmin: float,
    pmax: float,
    qualidade: int,
    saida_px: int,
) -> None:
    rgb = np.dstack((
        esticar(b04, mascara, pmin, pmax),
        esticar(b03, mascara, pmin, pmax),
        esticar(b02, mascara, pmin, pmax),
    ))
    imagem = Image.fromarray(rgb, mode="RGB")
    if saida_px > 0 and imagem.size != (saida_px, saida_px):
        imagem = imagem.resize((saida_px, saida_px), resample=Image.Resampling.NEAREST)
    destino.parent.mkdir(parents=True, exist_ok=True)
    imagem.save(destino, "JPEG", quality=max(1, min(100, qualidade)), optimize=True)


def bounds_wgs84(src, janela: Window):
    left, bottom, right, top = rasterio.windows.bounds(janela, src.transform)
    if src.crs:
        minlon, minlat, maxlon, maxlat = transform_bounds(
            src.crs, "EPSG:4326", left, bottom, right, top
        )
        cx = (left + right) / 2
        cy = (bottom + top) / 2
        lon, lat = transform(src.crs, "EPSG:4326", [cx], [cy])
        return minlon, minlat, maxlon, maxlat, float(lon[0]), float(lat[0])
    return left, bottom, right, top, (left + right) / 2, (bottom + top) / 2


def exportar_tif_patch(src, janela: Window, destino: Path) -> None:
    dados = src.read(1, window=janela)
    perfil = src.profile.copy()
    perfil.update(
        width=int(janela.width),
        height=int(janela.height),
        transform=src.window_transform(janela),
        count=1,
        compress="deflate",
        tiled=False,
    )
    destino.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(destino, "w", **perfil) as dst:
        dst.write(dados, 1)


def abrir_mosaico(pasta: Path):
    caminhos = {}
    for banda in BANDAS_OBRIGATORIAS:
        caminho = localizar_raster(pasta, banda)
        if caminho is None:
            return None, f"faltando_{banda}"
        caminhos[banda] = caminho

    for nome in ("VALID_MASK", "OBS_COUNT", "SOURCE_INDEX"):
        caminho = localizar_raster(pasta, nome)
        if caminho is None:
            return None, f"faltando_{nome}"
        caminhos[nome] = caminho
    return caminhos, "ok"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera patches de catalogação somente a partir de mosaicos L2A aprovados."
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PADRAO)
    parser.add_argument("--max-patches", type=int, default=0, help="0 = sem limite")
    parser.add_argument("--tile")
    parser.add_argument("--mes", help="YYYY-MM")
    parser.add_argument("--mosaico")
    parser.add_argument("--limpar-saida", action="store_true")
    args = parser.parse_args()

    cfg = carregar_config(args.config)
    pcfg = cfg["patches"]
    mcfg = cfg["mosaico"]

    tamanho = int(pcfg.get("tamanho_px", 128))
    passo = int(pcfg.get("passo_px", tamanho))
    preview_saida_px = int(pcfg.get("preview_saida_px", 768))
    valid_min = float(pcfg.get("dados_validos_min_pct", 99.5))
    obs2_min = float(pcfg.get("obs_2plus_min_pct", 20))
    exportar_tifs = bool(pcfg.get("exportar_tifs", False))
    pmin = float(cfg.get("preview", {}).get("percentil_min", 2))
    pmax = float(cfg.get("preview", {}).get("percentil_max", 98))
    qualidade_jpeg = int(cfg.get("preview", {}).get("qualidade_jpeg", 95))

    catalogo_mosaicos = ROOT / mcfg["catalogo"]
    destino_base = ROOT / pcfg["pasta"]
    catalogo = ROOT / pcfg["catalogo"]
    resumo_path = ROOT / pcfg["resumo"]

    if not catalogo_mosaicos.exists():
        print(f"[ERRO] Catálogo de mosaicos não encontrado: {catalogo_mosaicos}")
        return 2
    if args.limpar_saida and destino_base.exists():
        shutil.rmtree(destino_base)

    with catalogo_mosaicos.open("r", encoding="utf-8-sig", newline="") as arquivo:
        todos = list(csv.DictReader(arquivo))

    mosaicos = [r for r in todos if r.get("status") == "aprovado"]
    rejeitados = len(todos) - len(mosaicos)
    if args.tile:
        mosaicos = [r for r in mosaicos if r.get("tile_id") == args.tile]
    if args.mes:
        mosaicos = [r for r in mosaicos if r.get("mes") == args.mes]
    if args.mosaico:
        mosaicos = [r for r in mosaicos if r.get("mosaic_id") == args.mosaico]

    print("=" * 80)
    print(" PATCHES PARA LABELIMAGE | somente mosaicos APROVADOS")
    print("=" * 80)
    print(f"Mosaicos aprovados disponíveis: {len(mosaicos)}")
    print(f"Mosaicos rejeitados/ignorados: {rejeitados}")
    print(f"Patch: {tamanho}x{tamanho}px (~{tamanho * 10 / 1000:.2f} km por lado)")
    print(f"Dados válidos mínimos no patch: {valid_min:.1f}%")
    print(f"Pixels com 2+ observações: mínimo {obs2_min:.1f}%\n")

    registros = []
    aprovados = descartados_validos = descartados_obs = mosaicos_ok = erros = 0

    for info in mosaicos:
        pasta_mosaico = ROOT / info["mosaic_dir"]
        caminhos, status = abrir_mosaico(pasta_mosaico)
        if caminhos is None:
            print(f"[PULA] {info['mosaic_id']}: {status}")
            erros += 1
            continue

        datasets = {k: rasterio.open(v) for k, v in caminhos.items()}
        try:
            ref = datasets["B04"]
            mesma_grade = all(
                ds.width == ref.width
                and ds.height == ref.height
                and ds.transform == ref.transform
                and ds.crs == ref.crs
                for ds in datasets.values()
            )
            if not mesma_grade:
                print(f"[PULA] {info['mosaic_id']}: grades incompatíveis")
                erros += 1
                continue

            mosaicos_ok += 1
            mosaic_id = info["mosaic_id"]
            tile_id = info["tile_id"]
            mes = info["mes"]

            for y in range(0, ref.height - tamanho + 1, passo):
                for x in range(0, ref.width - tamanho + 1, passo):
                    janela = Window(x, y, tamanho, tamanho)
                    valid_mask = datasets["VALID_MASK"].read(1, window=janela) > 0
                    valid_pct = float(valid_mask.mean() * 100.0)
                    if valid_pct < valid_min:
                        descartados_validos += 1
                        continue

                    obs_count = datasets["OBS_COUNT"].read(1, window=janela)
                    obs2_pct = float(
                        ((obs_count >= 2) & valid_mask).sum()
                        * 100.0
                        / max(int(valid_mask.sum()), 1)
                    )
                    if obs2_pct < obs2_min:
                        descartados_obs += 1
                        continue

                    row = y // passo
                    col = x // passo
                    patch_id = f"{mosaic_id}_p{tamanho}_r{row:04d}_c{col:04d}"
                    pasta_patch = destino_base / tile_id / mes / patch_id
                    preview = pasta_patch / "preview_rgb.jpg"

                    b02 = datasets["B02"].read(1, window=janela).astype(np.float32)
                    b03 = datasets["B03"].read(1, window=janela).astype(np.float32)
                    b04 = datasets["B04"].read(1, window=janela).astype(np.float32)
                    gerar_preview(
                        b04, b03, b02, valid_mask, preview,
                        pmin, pmax, qualidade_jpeg, preview_saida_px
                    )

                    if exportar_tifs:
                        for nome in [*BANDAS_OBRIGATORIAS, "VALID_MASK", "OBS_COUNT", "SOURCE_INDEX"]:
                            exportar_tif_patch(
                                datasets[nome], janela, pasta_patch / f"{nome}.tif"
                            )

                    minlon, minlat, maxlon, maxlat, lon, lat = bounds_wgs84(ref, janela)
                    registros.append({
                        "patch_id": patch_id,
                        "mosaic_id": mosaic_id,
                        "tile_id": tile_id,
                        "mes": mes,
                        "row": row,
                        "col": col,
                        "xoff": x,
                        "yoff": y,
                        "width": tamanho,
                        "height": tamanho,
                        "lado_km_aproximado": round(tamanho * 10 / 1000, 3),
                        "valid_data_pct": round(valid_pct, 4),
                        "obs_2plus_pct": round(obs2_pct, 4),
                        "minlon": round(minlon, 7),
                        "minlat": round(minlat, 7),
                        "maxlon": round(maxlon, 7),
                        "maxlat": round(maxlat, 7),
                        "centroid_lon": round(lon, 7),
                        "centroid_lat": round(lat, 7),
                        "preview": str(preview.relative_to(ROOT)),
                        "label": "",
                        "observacao": "",
                    })
                    aprovados += 1
                    print(f"[OK] {patch_id} | válidos={valid_pct:.1f}% | 2+obs={obs2_pct:.1f}%")

                    if args.max_patches > 0 and aprovados >= args.max_patches:
                        break
                if args.max_patches > 0 and aprovados >= args.max_patches:
                    break
            if args.max_patches > 0 and aprovados >= args.max_patches:
                break
        finally:
            for ds in datasets.values():
                ds.close()

    campos = [
        "patch_id", "mosaic_id", "tile_id", "mes", "row", "col", "xoff", "yoff",
        "width", "height", "lado_km_aproximado", "valid_data_pct", "obs_2plus_pct",
        "minlon", "minlat", "maxlon", "maxlat", "centroid_lon", "centroid_lat",
        "preview", "label", "observacao"
    ]
    catalogo.parent.mkdir(parents=True, exist_ok=True)
    with catalogo.open("w", newline="", encoding="utf-8-sig") as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=campos)
        writer.writeheader()
        writer.writerows(registros)

    resumo = {
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(),
        "mosaicos_aprovados_processados": mosaicos_ok,
        "patches_aprovados": aprovados,
        "patches_descartados_dados_invalidos": descartados_validos,
        "patches_descartados_obs_insuficientes": descartados_obs,
        "tamanho_patch_px": tamanho,
        "preview_saida_px": preview_saida_px,
        "dados_validos_min_pct": valid_min,
        "obs_2plus_min_pct": obs2_min,
        "erros": erros,
        "catalogo": str(catalogo.relative_to(ROOT)),
    }
    resumo_path.parent.mkdir(parents=True, exist_ok=True)
    resumo_path.write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== RESUMO ===")
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0 if erros == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
