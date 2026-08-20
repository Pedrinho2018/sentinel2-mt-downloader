from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import defaultdict
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
import yaml
from PIL import Image
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PADRAO = ROOT / "config" / "config.yaml"


def carregar_config(caminho: Path) -> dict:
    with caminho.open("r", encoding="utf-8") as arquivo:
        return yaml.safe_load(arquivo)


def localizar_raster(pasta: Path, nome: str) -> Path | None:
    candidatos = sorted(pasta.glob(f"{nome}.*"))
    return candidatos[0] if candidatos else None


def dilatar_mascara(mascara: np.ndarray, raio: int) -> np.ndarray:
    if raio <= 0:
        return mascara.copy()
    altura, largura = mascara.shape
    preenchida = np.pad(mascara, raio, mode="constant", constant_values=False)
    saida = np.zeros_like(mascara, dtype=bool)
    for dy in range(-raio, raio + 1):
        for dx in range(-raio, raio + 1):
            y0 = raio + dy
            x0 = raio + dx
            saida |= preenchida[y0 : y0 + altura, x0 : x0 + largura]
    return saida


def ler_scl_expandido(src, janela: Window, margem: int) -> np.ndarray:
    x = int(janela.col_off)
    y = int(janela.row_off)
    w = int(janela.width)
    h = int(janela.height)
    if margem <= 0:
        return src.read(1, window=janela)

    alvo_x0 = x - margem
    alvo_y0 = y - margem
    alvo_x1 = x + w + margem
    alvo_y1 = y + h + margem

    x0 = max(0, alvo_x0)
    y0 = max(0, alvo_y0)
    x1 = min(src.width, alvo_x1)
    y1 = min(src.height, alvo_y1)

    saida = np.zeros((h + 2 * margem, w + 2 * margem), dtype=np.uint8)
    if x1 <= x0 or y1 <= y0:
        return saida

    trecho = src.read(1, window=Window(x0, y0, x1 - x0, y1 - y0))
    dx = x0 - alvo_x0
    dy = y0 - alvo_y0
    saida[dy : dy + trecho.shape[0], dx : dx + trecho.shape[1]] = trecho
    return saida


def mascara_limpa_janela(
    src_scl,
    janela: Window,
    classes_validas: set[int],
    classes_ruins: set[int],
    margem: int,
) -> np.ndarray:
    h = int(janela.height)
    w = int(janela.width)
    scl_ext = ler_scl_expandido(src_scl, janela, margem)
    ruins_ext = np.isin(scl_ext, list(classes_ruins))
    ruins_dilatadas = dilatar_mascara(ruins_ext, margem)

    if margem > 0:
        centro = scl_ext[margem : margem + h, margem : margem + w]
        ruins = ruins_dilatadas[margem : margem + h, margem : margem + w]
    else:
        centro = scl_ext
        ruins = ruins_dilatadas

    return np.isin(centro, list(classes_validas)) & ~ruins


def valor_valido(dados: np.ndarray, nodata) -> np.ndarray:
    mascara = np.isfinite(dados)
    if nodata is not None:
        mascara &= dados != nodata
    else:
        mascara &= dados != 0
    return mascara


def iterar_janelas(largura: int, altura: int, bloco: int):
    for y in range(0, altura, bloco):
        h = min(bloco, altura - y)
        for x in range(0, largura, bloco):
            w = min(bloco, largura - x)
            yield Window(x, y, w, h)


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


def gerar_preview_rgb(
    pasta: Path,
    destino: Path,
    max_px: int,
    pmin: float,
    pmax: float,
    qualidade: int,
) -> None:
    caminhos = {b: localizar_raster(pasta, b) for b in ("B04", "B03", "B02")}
    mask_path = localizar_raster(pasta, "VALID_MASK")
    if any(c is None for c in caminhos.values()) or mask_path is None:
        raise FileNotFoundError("Bandas RGB/VALID_MASK ausentes no mosaico.")

    with rasterio.open(caminhos["B04"]) as src_r, \
         rasterio.open(caminhos["B03"]) as src_g, \
         rasterio.open(caminhos["B02"]) as src_b, \
         rasterio.open(mask_path) as src_m:
        escala = min(1.0, max_px / max(src_r.width, src_r.height))
        largura = max(1, int(src_r.width * escala))
        altura = max(1, int(src_r.height * escala))
        shape = (altura, largura)
        r = src_r.read(1, out_shape=shape, resampling=Resampling.bilinear).astype(np.float32)
        g = src_g.read(1, out_shape=shape, resampling=Resampling.bilinear).astype(np.float32)
        b = src_b.read(1, out_shape=shape, resampling=Resampling.bilinear).astype(np.float32)
        mascara = src_m.read(1, out_shape=shape, resampling=Resampling.nearest) > 0

    rgb = np.dstack((
        esticar(r, mascara, pmin, pmax),
        esticar(g, mascara, pmin, pmax),
        esticar(b, mascara, pmin, pmax),
    ))
    destino.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb, mode="RGB").save(
        destino, "JPEG", quality=max(1, min(100, qualidade)), optimize=True
    )


def prioridade_fonte(registro: dict) -> tuple[int, float, str]:
    data = datetime.fromisoformat(registro["data"])
    nuvem = float(registro["scl_cloud_shadow_pct"])
    return abs(data.day - 15), nuvem, registro["data"]


def abrir_fontes(registros: list[dict], bandas: list[str]):
    stack = ExitStack()
    fontes = []
    try:
        for registro in sorted(registros, key=prioridade_fonte):
            pasta = ROOT / registro["scene_dir"]
            caminhos = {b: localizar_raster(pasta, b) for b in bandas}
            scl_path = localizar_raster(pasta / "qualidade", "SCL")
            if any(c is None for c in caminhos.values()) or scl_path is None:
                continue

            datasets = {b: stack.enter_context(rasterio.open(caminhos[b])) for b in bandas}
            ref = datasets["B04"]
            scl_base = stack.enter_context(rasterio.open(scl_path))
            scl_vrt = stack.enter_context(
                WarpedVRT(
                    scl_base,
                    crs=ref.crs,
                    transform=ref.transform,
                    width=ref.width,
                    height=ref.height,
                    resampling=Resampling.nearest,
                )
            )
            fontes.append({"registro": registro, "datasets": datasets, "scl": scl_vrt})
        return stack, fontes
    except Exception:
        stack.close()
        raise


def grades_compativeis(fontes: list[dict], bandas: list[str]) -> bool:
    if not fontes:
        return False
    ref = fontes[0]["datasets"]["B04"]
    for fonte in fontes:
        for banda in bandas:
            ds = fonte["datasets"][banda]
            if (
                ds.width != ref.width
                or ds.height != ref.height
                or ds.transform != ref.transform
                or ds.crs != ref.crs
            ):
                return False
    return True


def salvar_catalogo(caminho: Path, registros: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    campos = [
        "mosaic_id", "tile_id", "mes", "fontes", "valid_coverage_pct",
        "obs_2plus_pct", "mosaic_dir", "preview", "status", "erro"
    ]
    with caminho.open("w", newline="", encoding="utf-8-sig") as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=campos)
        writer.writeheader()
        writer.writerows(registros)


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera mosaicos mensais limpos a partir de Sentinel-2 L2A real.")
    parser.add_argument("--config", type=Path, default=CONFIG_PADRAO)
    parser.add_argument("--tile")
    parser.add_argument("--mes", help="YYYY-MM")
    parser.add_argument("--max-grupos", type=int, default=0, help="0 = todos")
    parser.add_argument("--limpar-saida", action="store_true")
    args = parser.parse_args()

    cfg = carregar_config(args.config)
    mc = cfg["mosaico"]
    bandas = list(cfg["bandas"])
    catalogo_origem = ROOT / mc["origem_catalogo"]
    pasta_saida = ROOT / mc["pasta"]
    catalogo_saida = ROOT / mc["catalogo"]
    resumo_path = ROOT / mc["resumo"]

    if not catalogo_origem.exists():
        print(f"[ERRO] Catálogo da série não encontrado: {catalogo_origem}")
        return 2
    if args.limpar_saida and pasta_saida.exists():
        shutil.rmtree(pasta_saida)

    with catalogo_origem.open("r", encoding="utf-8-sig", newline="") as arquivo:
        linhas = [r for r in csv.DictReader(arquivo) if r.get("selecionada") == "1" and r.get("status") == "selecionada"]

    grupos: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in linhas:
        if args.tile and r["tile_id"] != args.tile:
            continue
        if args.mes and r["mes"] != args.mes:
            continue
        grupos[(r["tile_id"], r["mes"])].append(r)

    min_cenas = int(mc.get("min_cenas_grupo", 2))
    classes_validas = {int(x) for x in mc.get("classes_validas_scl", [4, 5, 6])}
    classes_ruins = {int(x) for x in mc.get("classes_ruins_scl", [3, 7, 8, 9, 10, 11])}
    margem = int(mc.get("margem_nuvem_px", 6))
    bloco = int(mc.get("bloco_px", 1024))
    cobertura_min = float(mc.get("cobertura_valida_min_pct", 98))
    preview_max = int(mc.get("preview_max_px", 1800))
    pmin = float(mc.get("percentil_min", 2))
    pmax = float(mc.get("percentil_max", 98))
    qualidade_jpeg = int(mc.get("qualidade_jpeg", 95))
    metodo = str(mc.get("metodo", "primeiro_pixel_limpo_prioridade_centro_mes"))

    print("=" * 80)
    print(" MOSAICO MENSAL L2A | best-pixel limpo")
    print("=" * 80)
    print(f"Grupos: {len(grupos)} | mínimo fontes: {min_cenas}")
    print(f"SCL 20m -> grade 10m por nearest | buffer nuvem: {margem * 10} m")
    print(f"Cobertura mínima para APROVAR mosaico: {cobertura_min:.1f}%\n")

    registros_saida = []
    gerados = aprovados = pulados = erros = 0

    for numero, ((tile_id, mes), registros) in enumerate(sorted(grupos.items()), start=1):
        if args.max_grupos > 0 and gerados >= args.max_grupos:
            break
        print(f"[{numero}] {tile_id} | {mes} | fontes={len(registros)}")

        if len(registros) < min_cenas:
            print("  [PULA] menos de 2 fontes reais no mês.")
            pulados += 1
            registros_saida.append({
                "mosaic_id": f"{tile_id}_{mes}", "tile_id": tile_id, "mes": mes,
                "fontes": len(registros), "valid_coverage_pct": "0", "obs_2plus_pct": "0",
                "mosaic_dir": "", "preview": "", "status": "fontes_insuficientes", "erro": ""
            })
            continue

        stack = None
        try:
            stack, fontes = abrir_fontes(registros, bandas)
            if len(fontes) < min_cenas or not grades_compativeis(fontes, bandas):
                print("  [PULA] fontes ausentes ou grades 10m incompatíveis.")
                pulados += 1
                registros_saida.append({
                    "mosaic_id": f"{tile_id}_{mes}", "tile_id": tile_id, "mes": mes,
                    "fontes": len(fontes), "valid_coverage_pct": "0", "obs_2plus_pct": "0",
                    "mosaic_dir": "", "preview": "", "status": "fontes_incompativeis", "erro": ""
                })
                continue

            ref = fontes[0]["datasets"]["B04"]
            mosaic_id = f"{tile_id}_{mes}"
            destino = pasta_saida / tile_id / mes
            destino.mkdir(parents=True, exist_ok=True)

            with ExitStack() as out:
                writers = {}
                for banda in bandas:
                    perfil = ref.profile.copy()
                    perfil.update(dtype="uint16", nodata=0, count=1, compress="deflate", tiled=True)
                    writers[banda] = out.enter_context(rasterio.open(destino / f"{banda}.tif", "w", **perfil))

                perfil_ndvi = ref.profile.copy()
                perfil_ndvi.update(dtype="float32", nodata=-9999.0, count=1, compress="deflate", tiled=True)
                writer_ndvi = out.enter_context(rasterio.open(destino / "NDVI.tif", "w", **perfil_ndvi))

                perfil_u8 = ref.profile.copy()
                perfil_u8.update(dtype="uint8", nodata=0, count=1, compress="deflate", tiled=True)
                writer_valid = out.enter_context(rasterio.open(destino / "VALID_MASK.tif", "w", **perfil_u8))
                writer_obs = out.enter_context(rasterio.open(destino / "OBS_COUNT.tif", "w", **perfil_u8))
                writer_source = out.enter_context(rasterio.open(destino / "SOURCE_INDEX.tif", "w", **perfil_u8))

                total_pixels = ref.width * ref.height
                total_validos = 0
                total_obs2 = 0

                for janela in iterar_janelas(ref.width, ref.height, bloco):
                    h, w = int(janela.height), int(janela.width)
                    preenchido = np.zeros((h, w), dtype=bool)
                    obs_count = np.zeros((h, w), dtype=np.uint8)
                    source_index = np.zeros((h, w), dtype=np.uint8)
                    saidas = {b: np.zeros((h, w), dtype=np.uint16) for b in bandas}

                    for indice, fonte in enumerate(fontes, start=1):
                        limpa = mascara_limpa_janela(
                            fonte["scl"], janela, classes_validas, classes_ruins, margem
                        )
                        dados = {}
                        utilizavel = limpa.copy()
                        for banda in bandas:
                            ds = fonte["datasets"][banda]
                            arr = ds.read(1, window=janela)
                            dados[banda] = arr
                            utilizavel &= valor_valido(arr, ds.nodata)

                        obs_count = np.minimum(
                            obs_count.astype(np.uint16) + utilizavel.astype(np.uint16), 255
                        ).astype(np.uint8)

                        usar = utilizavel & ~preenchido
                        if not usar.any():
                            continue
                        for banda in bandas:
                            saidas[banda][usar] = dados[banda][usar]
                        source_index[usar] = indice
                        preenchido[usar] = True

                    for banda in bandas:
                        writers[banda].write(saidas[banda], 1, window=janela)

                    red = saidas["B04"].astype(np.float32)
                    nir = saidas["B08"].astype(np.float32)
                    soma = nir + red
                    ndvi = np.full((h, w), -9999.0, dtype=np.float32)
                    ok_ndvi = preenchido & (soma > 0)
                    ndvi[ok_ndvi] = (nir[ok_ndvi] - red[ok_ndvi]) / soma[ok_ndvi]
                    writer_ndvi.write(ndvi, 1, window=janela)
                    writer_valid.write(preenchido.astype(np.uint8), 1, window=janela)
                    writer_obs.write(obs_count, 1, window=janela)
                    writer_source.write(source_index, 1, window=janela)

                    total_validos += int(preenchido.sum())
                    total_obs2 += int((obs_count >= 2).sum())

            valid_pct = total_validos * 100.0 / max(total_pixels, 1)
            obs2_pct = total_obs2 * 100.0 / max(total_pixels, 1)
            status = "aprovado" if valid_pct >= cobertura_min else "cobertura_insuficiente"

            preview = destino / "preview_rgb.jpg"
            gerar_preview_rgb(destino, preview, preview_max, pmin, pmax, qualidade_jpeg)

            fontes_meta = []
            for indice, fonte in enumerate(fontes, start=1):
                r = fonte["registro"]
                fontes_meta.append({
                    "source_index": indice,
                    "item_id": r["item_id"],
                    "data": r["data"],
                    "scl_cloud_shadow_pct": float(r["scl_cloud_shadow_pct"]),
                    "scene_dir": r["scene_dir"],
                })

            metadata = {
                "mosaic_id": mosaic_id,
                "tile_id": tile_id,
                "mes": mes,
                "colecao_fonte": cfg["stac"]["colecao"],
                "metodo": metodo,
                "scl_reprojecao": "20m para grade 10m com nearest",
                "margem_nuvem_m": margem * 10,
                "valid_coverage_pct": round(valid_pct, 4),
                "obs_2plus_pct": round(obs2_pct, 4),
                "status": status,
                "fontes": fontes_meta,
                "gerado_em_utc": datetime.now(timezone.utc).isoformat(),
            }
            (destino / "metadata.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            gerados += 1
            aprovados += int(status == "aprovado")
            print(f"  [{status.upper()}] cobertura={valid_pct:.2f}% | 2+ observações={obs2_pct:.2f}%")
            registros_saida.append({
                "mosaic_id": mosaic_id, "tile_id": tile_id, "mes": mes,
                "fontes": len(fontes), "valid_coverage_pct": f"{valid_pct:.4f}",
                "obs_2plus_pct": f"{obs2_pct:.4f}", "mosaic_dir": str(destino.relative_to(ROOT)),
                "preview": str(preview.relative_to(ROOT)), "status": status, "erro": ""
            })

        except Exception as exc:
            erros += 1
            print(f"  [ERRO] {exc}")
            registros_saida.append({
                "mosaic_id": f"{tile_id}_{mes}", "tile_id": tile_id, "mes": mes,
                "fontes": len(registros), "valid_coverage_pct": "0", "obs_2plus_pct": "0",
                "mosaic_dir": "", "preview": "", "status": "erro", "erro": str(exc)
            })
        finally:
            if stack is not None:
                stack.close()

    salvar_catalogo(catalogo_saida, registros_saida)
    resumo = {
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(),
        "grupos_disponiveis": len(grupos),
        "mosaicos_gerados": gerados,
        "mosaicos_aprovados": aprovados,
        "grupos_pulados": pulados,
        "erros": erros,
        "catalogo": str(catalogo_saida.relative_to(ROOT)),
    }
    resumo_path.parent.mkdir(parents=True, exist_ok=True)
    resumo_path.write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== RESUMO ===")
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0 if erros == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
