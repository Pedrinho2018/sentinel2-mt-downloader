from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
import yaml
from PIL import Image
from rasterio.windows import Window
from rasterio.warp import transform_bounds, transform


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PADRAO = ROOT / "config" / "config.yaml"
BANDAS_OBRIGATORIAS = ("B02", "B03", "B04", "B08", "NDVI")

# Sentinel-2 SCL:
# 3 = sombra de nuvem
# 7 = não classificado / baixa confiança
# 8 = nuvem média probabilidade
# 9 = nuvem alta probabilidade
# 10 = cirrus
# 11 = neve/gelo
CLASSES_NUVEM_SOMBRA = {3, 7, 8, 9, 10, 11}
CLASSES_INVALIDAS = {0, 1}


def carregar_config(caminho: Path) -> dict:
    with caminho.open("r", encoding="utf-8") as arquivo:
        return yaml.safe_load(arquivo)


def localizar_tif(pasta: Path, nome: str) -> Path | None:
    candidatos = sorted(pasta.glob(f"{nome}.*"))
    return candidatos[0] if candidatos else None


def esticar(dados: np.ndarray, mascara: np.ndarray, pmin: float, pmax: float) -> np.ndarray:
    saida = np.zeros(dados.shape, dtype=np.uint8)
    validos = dados[mascara & np.isfinite(dados)]
    if validos.size == 0:
        return saida

    minimo, maximo = np.percentile(validos, [pmin, pmax])
    if not np.isfinite(minimo) or not np.isfinite(maximo) or maximo <= minimo:
        minimo, maximo = float(validos.min()), float(validos.max())
    if maximo <= minimo:
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
    preview_saida_px: int,
) -> None:
    r = esticar(b04, mascara, pmin, pmax)
    g = esticar(b03, mascara, pmin, pmax)
    b = esticar(b02, mascara, pmin, pmax)
    rgb = np.dstack((r, g, b))

    imagem = Image.fromarray(rgb, mode="RGB")

    # Amplia somente a visualização. NEAREST preserva os pixels originais e evita
    # criar falsa sensação de resolução espacial adicional.
    if preview_saida_px > 0 and imagem.width != preview_saida_px:
        imagem = imagem.resize(
            (preview_saida_px, preview_saida_px),
            resample=Image.Resampling.NEAREST,
        )

    destino.parent.mkdir(parents=True, exist_ok=True)
    imagem.save(
        destino,
        "JPEG",
        quality=max(1, min(100, qualidade)),
        optimize=True,
    )


def calcular_qualidade_scl(scl: np.ndarray) -> tuple[float, float]:
    total = scl.size
    if total == 0:
        return 100.0, 0.0

    validos = ~np.isin(scl, list(CLASSES_INVALIDAS))
    valid_pct = float(validos.sum() / total * 100.0)

    universo = max(int(validos.sum()), 1)
    nuvens = np.isin(scl, list(CLASSES_NUVEM_SOMBRA)) & validos
    nuvem_pct = float(nuvens.sum() / universo * 100.0)
    return nuvem_pct, valid_pct


def bounds_wgs84(
    src: rasterio.DatasetReader,
    janela: Window,
) -> tuple[float, float, float, float, float, float]:
    left, bottom, right, top = rasterio.windows.bounds(janela, src.transform)
    if src.crs:
        minlon, minlat, maxlon, maxlat = transform_bounds(
            src.crs,
            "EPSG:4326",
            left,
            bottom,
            right,
            top,
        )
        cx = (left + right) / 2
        cy = (bottom + top) / 2
        lon, lat = transform(src.crs, "EPSG:4326", [cx], [cy])
        return minlon, minlat, maxlon, maxlat, float(lon[0]), float(lat[0])
    return left, bottom, right, top, (left + right) / 2, (bottom + top) / 2


def abrir_cena(pasta_cena: Path):
    arquivos: dict[str, Path] = {}
    for banda in BANDAS_OBRIGATORIAS:
        caminho = localizar_tif(pasta_cena, banda)
        if caminho is None:
            return None, f"faltando_{banda}"
        arquivos[banda] = caminho

    scl = localizar_tif(pasta_cena / "qualidade", "SCL")
    if scl is None:
        return None, "faltando_SCL"
    arquivos["SCL"] = scl
    return arquivos, "ok"


def exportar_tif_patch(src: rasterio.DatasetReader, janela: Window, destino: Path) -> None:
    dados = src.read(1, window=janela)
    perfil = src.profile.copy()
    perfil.update(
        width=int(janela.width),
        height=int(janela.height),
        transform=src.window_transform(janela),
        count=1,
        compress="deflate",
        tiled=True,
    )
    destino.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(destino, "w", **perfil) as dst:
        dst.write(dados, 1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera patches georreferenciados próximos para catalogação agrícola/soja."
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PADRAO)
    parser.add_argument("--max-patches", type=int, default=0, help="0 = sem limite")
    parser.add_argument("--scene", help="Processa apenas uma cena cujo ID contenha este texto")
    args = parser.parse_args()

    cfg = carregar_config(args.config)
    pcfg = cfg.get("patches", {})
    dcfg = cfg["download"]

    tamanho = int(pcfg.get("tamanho_px", 128))
    passo = int(pcfg.get("passo_px", tamanho))
    preview_saida_px = int(pcfg.get("preview_saida_px", 768))
    nuvem_max = float(pcfg.get("nuvem_max_pct", 8))
    valid_min = float(pcfg.get("dados_validos_min_pct", 90))
    exportar_tifs = bool(pcfg.get("exportar_tifs", False))
    pmin = float(cfg.get("preview", {}).get("percentil_min", 2))
    pmax = float(cfg.get("preview", {}).get("percentil_max", 98))
    qualidade_jpeg = int(cfg.get("preview", {}).get("qualidade_jpeg", 94))

    origem = ROOT / dcfg["pasta"]
    destino_base = ROOT / pcfg.get("pasta", "data/patches")
    catalogo = ROOT / pcfg.get("catalogo", "catalogo/catalogo_patches.csv")
    resumo_path = ROOT / pcfg.get("resumo", "catalogo/resumo_patches.json")

    cenas = sorted(p for p in origem.glob("*/*") if p.is_dir())
    if args.scene:
        cenas = [p for p in cenas if args.scene in p.name]

    print("=" * 72)
    print(" GERADOR DE PATCHES | catalogação agrícola")
    print("=" * 72)
    print(f"Patch científico: {tamanho}x{tamanho} px (~{tamanho * 10 / 1000:.2f} km por lado)")
    print(f"Preview visual:   {preview_saida_px}x{preview_saida_px} px")
    print(f"Nuvem/sombra máx: {nuvem_max:.1f}%")
    print(f"Dados válidos mín:{valid_min:.1f}%")
    print()

    registros: list[dict] = []
    aprovados = descartados_nuvem = descartados_validos = cenas_ok = 0

    for pasta_cena in cenas:
        arquivos, status = abrir_cena(pasta_cena)
        if arquivos is None:
            print(f"[PULA] {pasta_cena.name}: {status}")
            continue

        datasets = {k: rasterio.open(v) for k, v in arquivos.items()}
        try:
            ref = datasets["B04"]
            mesma_grade = all(
                ds.width == ref.width
                and ds.height == ref.height
                and ds.transform == ref.transform
                for ds in datasets.values()
            )
            if not mesma_grade:
                print(f"[PULA] {pasta_cena.name}: bandas em grades incompatíveis")
                continue

            cenas_ok += 1
            data = pasta_cena.parent.name
            scene_id = pasta_cena.name

            for y in range(0, ref.height - tamanho + 1, passo):
                for x in range(0, ref.width - tamanho + 1, passo):
                    janela = Window(x, y, tamanho, tamanho)
                    scl = datasets["SCL"].read(1, window=janela)
                    nuvem_pct, valid_pct = calcular_qualidade_scl(scl)

                    if valid_pct < valid_min:
                        descartados_validos += 1
                        continue
                    if nuvem_pct > nuvem_max:
                        descartados_nuvem += 1
                        continue

                    row = y // passo
                    col = x // passo
                    patch_id = f"{scene_id}_r{row:04d}_c{col:04d}"
                    pasta_patch = destino_base / data / scene_id / patch_id
                    preview = pasta_patch / "preview_rgb.jpg"

                    b02 = datasets["B02"].read(1, window=janela).astype(np.float32)
                    b03 = datasets["B03"].read(1, window=janela).astype(np.float32)
                    b04 = datasets["B04"].read(1, window=janela).astype(np.float32)
                    mascara = ~np.isin(
                        scl,
                        list(CLASSES_INVALIDAS | CLASSES_NUVEM_SOMBRA),
                    )

                    gerar_preview(
                        b04,
                        b03,
                        b02,
                        mascara,
                        preview,
                        pmin,
                        pmax,
                        qualidade_jpeg,
                        preview_saida_px,
                    )

                    if exportar_tifs:
                        for banda in BANDAS_OBRIGATORIAS:
                            exportar_tif_patch(
                                datasets[banda],
                                janela,
                                pasta_patch / f"{banda}.tif",
                            )
                        exportar_tif_patch(
                            datasets["SCL"],
                            janela,
                            pasta_patch / "SCL.tif",
                        )

                    minlon, minlat, maxlon, maxlat, lon, lat = bounds_wgs84(ref, janela)
                    registros.append(
                        {
                            "patch_id": patch_id,
                            "scene_id": scene_id,
                            "data": data,
                            "row": row,
                            "col": col,
                            "xoff": x,
                            "yoff": y,
                            "width": tamanho,
                            "height": tamanho,
                            "ground_side_km": round(tamanho * 10 / 1000, 3),
                            "cloud_shadow_pct": round(nuvem_pct, 3),
                            "valid_data_pct": round(valid_pct, 3),
                            "minlon": round(minlon, 7),
                            "minlat": round(minlat, 7),
                            "maxlon": round(maxlon, 7),
                            "maxlat": round(maxlat, 7),
                            "centroid_lon": round(lon, 7),
                            "centroid_lat": round(lat, 7),
                            "preview": str(preview.relative_to(ROOT)),
                            "label": "",
                            "observacao": "",
                        }
                    )
                    aprovados += 1
                    print(
                        f"[OK] {patch_id} | "
                        f"nuvem+sombra={nuvem_pct:.1f}% | "
                        f"válidos={valid_pct:.1f}%"
                    )

                    if args.max_patches > 0 and aprovados >= args.max_patches:
                        break
                if args.max_patches > 0 and aprovados >= args.max_patches:
                    break
            if args.max_patches > 0 and aprovados >= args.max_patches:
                break
        finally:
            for ds in datasets.values():
                ds.close()

    catalogo.parent.mkdir(parents=True, exist_ok=True)
    campos = [
        "patch_id",
        "scene_id",
        "data",
        "row",
        "col",
        "xoff",
        "yoff",
        "width",
        "height",
        "ground_side_km",
        "cloud_shadow_pct",
        "valid_data_pct",
        "minlon",
        "minlat",
        "maxlon",
        "maxlat",
        "centroid_lon",
        "centroid_lat",
        "preview",
        "label",
        "observacao",
    ]
    with catalogo.open("w", newline="", encoding="utf-8-sig") as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=campos)
        writer.writeheader()
        writer.writerows(registros)

    resumo = {
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(),
        "tamanho_patch_px": tamanho,
        "preview_saida_px": preview_saida_px,
        "passo_px": passo,
        "resolucao_m_aproximada": 10,
        "lado_patch_km_aproximado": round(tamanho * 10 / 1000, 3),
        "nuvem_sombra_max_pct": nuvem_max,
        "dados_validos_min_pct": valid_min,
        "cenas_processadas": cenas_ok,
        "patches_aprovados": aprovados,
        "patches_descartados_nuvem": descartados_nuvem,
        "patches_descartados_dados_invalidos": descartados_validos,
        "exportou_tifs": exportar_tifs,
        "catalogo": str(catalogo.relative_to(ROOT)),
    }
    resumo_path.parent.mkdir(parents=True, exist_ok=True)
    resumo_path.write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n=== RESUMO ===")
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
