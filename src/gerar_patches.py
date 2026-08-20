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
CLASSES_NUVEM_SOMBRA = {3, 7, 8, 9, 10, 11}
CLASSES_INVALIDAS = {0, 1}


def carregar_config(caminho: Path) -> dict:
    with caminho.open("r", encoding="utf-8") as arquivo:
        return yaml.safe_load(arquivo)


def localizar_tif(pasta: Path, nome: str) -> Path | None:
    candidatos = sorted(pasta.glob(f"{nome}.*"))
    return candidatos[0] if candidatos else None


def dilatar_mascara(mascara: np.ndarray, raio: int) -> np.ndarray:
    if raio <= 0:
        return mascara.copy()
    h, w = mascara.shape
    padded = np.pad(mascara, raio, mode="constant", constant_values=False)
    saida = np.zeros_like(mascara, dtype=bool)
    for dy in range(-raio, raio + 1):
        for dx in range(-raio, raio + 1):
            y0 = raio + dy
            x0 = raio + dx
            saida |= padded[y0:y0 + h, x0:x0 + w]
    return saida


def calcular_qualidade_scl(scl: np.ndarray, margem_px: int) -> tuple[float, float, np.ndarray]:
    total = scl.size
    if total == 0:
        return 100.0, 0.0, np.ones_like(scl, dtype=bool)

    invalidos = np.isin(scl, list(CLASSES_INVALIDAS))
    validos = ~invalidos
    valid_pct = float(validos.sum() / total * 100.0)

    ruins = np.isin(scl, list(CLASSES_NUVEM_SOMBRA)) & validos
    ruins_com_margem = dilatar_mascara(ruins, margem_px) & validos
    universo = max(int(validos.sum()), 1)
    nuvem_pct = float(ruins_com_margem.sum() / universo * 100.0)
    return nuvem_pct, valid_pct, ruins_com_margem


def esticar(dados: np.ndarray, mascara: np.ndarray, pmin: float, pmax: float) -> np.ndarray:
    saida = np.zeros(dados.shape, dtype=np.uint8)
    validos = dados[mascara & np.isfinite(dados)]
    if validos.size == 0:
        return saida
    minimo, maximo = np.percentile(validos, [pmin, pmax])
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
    rgb = np.dstack(
        (
            esticar(b04, mascara, pmin, pmax),
            esticar(b03, mascara, pmin, pmax),
            esticar(b02, mascara, pmin, pmax),
        )
    )
    imagem = Image.fromarray(rgb, mode="RGB")
    if saida_px > 0 and (imagem.width != saida_px or imagem.height != saida_px):
        imagem = imagem.resize((saida_px, saida_px), resample=Image.Resampling.NEAREST)
    destino.parent.mkdir(parents=True, exist_ok=True)
    imagem.save(destino, "JPEG", quality=max(1, min(100, qualidade)), optimize=True)


def bounds_wgs84(src: rasterio.DatasetReader, janela: Window) -> tuple[float, float, float, float, float, float]:
    left, bottom, right, top = rasterio.windows.bounds(janela, src.transform)
    if src.crs:
        minlon, minlat, maxlon, maxlat = transform_bounds(src.crs, "EPSG:4326", left, bottom, right, top)
        cx = (left + right) / 2
        cy = (bottom + top) / 2
        lon, lat = transform(src.crs, "EPSG:4326", [cx], [cy])
        return minlon, minlat, maxlon, maxlat, float(lon[0]), float(lat[0])
    return left, bottom, right, top, (left + right) / 2, (bottom + top) / 2


def abrir_cena(pasta_cena: Path):
    marcador = pasta_cena / "qualidade" / "aprovada.json"
    if not marcador.exists():
        return None, "sem_marcador_de_aprovacao", None
    try:
        qualidade = json.loads(marcador.read_text(encoding="utf-8"))
    except Exception:
        return None, "marcador_invalido", None

    arquivos: dict[str, Path] = {}
    for banda in BANDAS_OBRIGATORIAS:
        caminho = localizar_tif(pasta_cena, banda)
        if caminho is None:
            return None, f"faltando_{banda}", qualidade
        arquivos[banda] = caminho

    scl = localizar_tif(pasta_cena / "qualidade", "SCL")
    if scl is None:
        return None, "faltando_SCL", qualidade
    arquivos["SCL"] = scl
    return arquivos, "ok", qualidade


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
    parser = argparse.ArgumentParser(description="Gera patches limpos e georreferenciados para catalogação agrícola.")
    parser.add_argument("--config", type=Path, default=CONFIG_PADRAO)
    parser.add_argument("--max-patches", type=int, default=0, help="0 = sem limite")
    parser.add_argument("--scene", help="Processa apenas uma cena cujo ID contenha este texto")
    parser.add_argument("--limpar-saida", action="store_true", help="Apaga patches anteriores antes de gerar novos.")
    args = parser.parse_args()

    cfg = carregar_config(args.config)
    pcfg = cfg.get("patches", {})
    dcfg = cfg["download"]
    qcfg = cfg.get("qualidade", {})

    tamanho = int(pcfg.get("tamanho_px", 128))
    passo = int(pcfg.get("passo_px", tamanho))
    preview_saida_px = int(pcfg.get("preview_saida_px", 768))
    nuvem_max = float(pcfg.get("nuvem_max_pct", 0.5))
    valid_min = float(pcfg.get("dados_validos_min_pct", 98))
    margem_px = int(pcfg.get("margem_nuvem_px", 3))
    cena_nuvem_max = float(qcfg.get("nuvem_max_pct", 5))
    exportar_tifs = bool(pcfg.get("exportar_tifs", False))
    pmin = float(cfg.get("preview", {}).get("percentil_min", 2))
    pmax = float(cfg.get("preview", {}).get("percentil_max", 98))
    qualidade_jpeg = int(cfg.get("preview", {}).get("qualidade_jpeg", 94))

    origem = ROOT / dcfg["pasta"]
    destino_base = ROOT / pcfg.get("pasta", "data/patches")
    catalogo = ROOT / pcfg.get("catalogo", "catalogo/catalogo_patches.csv")
    resumo_path = ROOT / pcfg.get("resumo", "catalogo/resumo_patches.json")

    if args.limpar_saida and destino_base.exists():
        shutil.rmtree(destino_base)
        print(f"[LIMPEZA] removido: {destino_base.relative_to(ROOT)}")

    cenas = sorted(p for p in origem.glob("*/*") if p.is_dir())
    if args.scene:
        cenas = [p for p in cenas if args.scene in p.name]

    print("=" * 76)
    print(" GERADOR DE PATCHES | modo rigoroso para catalogação")
    print("=" * 76)
    print(f"Patch científico:  {tamanho}x{tamanho} px (~{tamanho * 10 / 1000:.2f} km por lado)")
    print(f"Preview visual:    {preview_saida_px}x{preview_saida_px} px")
    print(f"Cena aprovada:     <= {cena_nuvem_max:.1f}% nuvem/sombra")
    print(f"Patch aprovado:    <= {nuvem_max:.2f}% nuvem/sombra após margem")
    print(f"Margem de nuvem:   {margem_px} px (~{margem_px * 10} m)")
    print(f"Dados válidos mín: {valid_min:.1f}%")
    print()

    registros: list[dict] = []
    aprovados = descartados_nuvem = descartados_validos = cenas_ok = cenas_sem_marcador = cenas_rejeitadas = 0

    for pasta_cena in cenas:
        arquivos, status, qualidade_cena = abrir_cena(pasta_cena)
        if arquivos is None:
            if status == "sem_marcador_de_aprovacao":
                cenas_sem_marcador += 1
            print(f"[PULA] {pasta_cena.name}: {status}")
            continue

        nuvem_cena = float(qualidade_cena.get("nuvem_sombra_pct", 100))
        if nuvem_cena > cena_nuvem_max:
            cenas_rejeitadas += 1
            print(f"[PULA] {pasta_cena.name}: marcador antigo com {nuvem_cena:.2f}% > {cena_nuvem_max:.1f}%")
            continue

        datasets = {k: rasterio.open(v) for k, v in arquivos.items()}
        try:
            ref = datasets["B04"]
            mesma_grade = all(
                ds.width == ref.width and ds.height == ref.height and ds.transform == ref.transform
                for ds in datasets.values()
            )
            if not mesma_grade:
                print(f"[PULA] {pasta_cena.name}: bandas em grades incompatíveis")
                continue

            cenas_ok += 1
            data = pasta_cena.parent.name
            scene_id = pasta_cena.name
            print(f"[CENA OK] {scene_id} | nuvem/sombra={nuvem_cena:.2f}%")

            for y in range(0, ref.height - tamanho + 1, passo):
                for x in range(0, ref.width - tamanho + 1, passo):
                    janela = Window(x, y, tamanho, tamanho)
                    scl = datasets["SCL"].read(1, window=janela)
                    nuvem_pct, valid_pct, mascara_ruim = calcular_qualidade_scl(scl, margem_px)

                    if valid_pct < valid_min:
                        descartados_validos += 1
                        continue
                    if nuvem_pct > nuvem_max:
                        descartados_nuvem += 1
                        continue

                    row = y // passo
                    col = x // passo
                    patch_id = f"{scene_id}_p{tamanho}_r{row:04d}_c{col:04d}"
                    pasta_patch = destino_base / data / scene_id / patch_id
                    preview = pasta_patch / "preview_rgb.jpg"

                    b02 = datasets["B02"].read(1, window=janela).astype(np.float32)
                    b03 = datasets["B03"].read(1, window=janela).astype(np.float32)
                    b04 = datasets["B04"].read(1, window=janela).astype(np.float32)
                    invalidos = np.isin(scl, list(CLASSES_INVALIDAS))
                    mascara_visual = ~(invalidos | mascara_ruim)
                    gerar_preview(
                        b04, b03, b02, mascara_visual, preview,
                        pmin, pmax, qualidade_jpeg, preview_saida_px,
                    )

                    if exportar_tifs:
                        for banda in BANDAS_OBRIGATORIAS:
                            exportar_tif_patch(datasets[banda], janela, pasta_patch / f"{banda}.tif")
                        exportar_tif_patch(datasets["SCL"], janela, pasta_patch / "SCL.tif")

                    minlon, minlat, maxlon, maxlat, lon, lat = bounds_wgs84(ref, janela)
                    registros.append({
                        "patch_id": patch_id,
                        "scene_id": scene_id,
                        "data": data,
                        "scene_cloud_shadow_pct": round(nuvem_cena, 3),
                        "patch_cloud_shadow_pct": round(nuvem_pct, 3),
                        "valid_data_pct": round(valid_pct, 3),
                        "cloud_margin_px": margem_px,
                        "width": tamanho,
                        "height": tamanho,
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
                    print(f"  [OK] {patch_id} | nuvem+margem={nuvem_pct:.2f}% | válidos={valid_pct:.1f}%")

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
        "patch_id", "scene_id", "data", "scene_cloud_shadow_pct", "patch_cloud_shadow_pct",
        "valid_data_pct", "cloud_margin_px", "width", "height", "minlon", "minlat", "maxlon",
        "maxlat", "centroid_lon", "centroid_lat", "preview", "label", "observacao",
    ]
    with catalogo.open("w", newline="", encoding="utf-8-sig") as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=campos)
        writer.writeheader()
        writer.writerows(registros)

    resumo = {
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(),
        "tamanho_patch_px": tamanho,
        "preview_saida_px": preview_saida_px,
        "lado_patch_km_aproximado": round(tamanho * 10 / 1000, 3),
        "cena_nuvem_sombra_max_pct": cena_nuvem_max,
        "patch_nuvem_sombra_max_pct": nuvem_max,
        "margem_nuvem_px": margem_px,
        "margem_nuvem_m_aproximada": margem_px * 10,
        "dados_validos_min_pct": valid_min,
        "cenas_processadas": cenas_ok,
        "cenas_sem_marcador": cenas_sem_marcador,
        "cenas_rejeitadas_por_limite_atual": cenas_rejeitadas,
        "patches_aprovados": aprovados,
        "patches_descartados_nuvem": descartados_nuvem,
        "patches_descartados_dados_invalidos": descartados_validos,
        "exportou_tifs": exportar_tifs,
        "catalogo": str(catalogo.relative_to(ROOT)),
    }
    resumo_path.parent.mkdir(parents=True, exist_ok=True)
    resumo_path.write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== RESUMO ===")
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
