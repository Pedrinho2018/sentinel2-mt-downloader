from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
import yaml
from PIL import Image
from rasterio.enums import Resampling
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


def mascara_limpa_janela(
    src_scl: rasterio.DatasetReader,
    janela: Window,
    classes_validas: set[int],
    classes_ruins: set[int],
    margem: int,
) -> np.ndarray:
    altura = int(janela.height)
    largura = int(janela.width)

    if margem <= 0:
        scl = src_scl.read(1, window=janela)
        return np.isin(scl, list(classes_validas)) & ~np.isin(scl, list(classes_ruins))

    expandida = Window(
        janela.col_off - margem,
        janela.row_off - margem,
        janela.width + 2 * margem,
        janela.height + 2 * margem,
    )
    scl_ext = src_scl.read(
        1,
        window=expandida,
        boundless=True,
        fill_value=0,
    )
    ruins_ext = np.isin(scl_ext, list(classes_ruins))
    ruins_dilatadas = dilatar_mascara(ruins_ext, margem)

    centro = scl_ext[margem : margem + altura, margem : margem + largura]
    ruins_centro = ruins_dilatadas[
        margem : margem + altura,
        margem : margem + largura,
    ]
    return np.isin(centro, list(classes_validas)) & ~ruins_centro


def valor_valido(dados: np.ndarray, nodata) -> np.ndarray:
    mascara = np.isfinite(dados)
    if nodata is not None:
        mascara &= dados != nodata
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
        minimo, maximo = float(valores.min()), float(valores.max())
    if maximo <= minimo:
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
        raise FileNotFoundError("Bandas RGB ou VALID_MASK não encontrados no mosaico.")

    with rasterio.open(caminhos["B04"]) as src_r, rasterio.open(caminhos["B03"]) as src_g, rasterio.open(caminhos["B02"]) as src_b, rasterio.open(mask_path) as src_m:
        escala = min(1.0, max_px / max(src_r.width, src_r.height))
        largura = max(1, int(src_r.width * escala))
        altura = max(1, int(src_r.height * escala))
        shape = (altura, largura)
        r0 = src_r.read(1, out_shape=shape, resampling=Resampling.bilinear).astype(np.float32)
        g0 = src_g.read(1, out_shape=shape, resampling=Resampling.bilinear).astype(np.float32)
        b0 = src_b.read(1, out_shape=shape, resampling=Resampling.bilinear).astype(np.float32)
        mascara = src_m.read(1, out_shape=shape, resampling=Resampling.nearest) > 0

    rgb = np.dstack(
        (
            esticar(r0, mascara, pmin, pmax),
            esticar(g0, mascara, pmin, pmax),
            esticar(b0, mascara, pmin, pmax),
        )
    )
    destino.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb, mode="RGB").save(
        destino,
        "JPEG",
        quality=max(1, min(100, qualidade)),
        optimize=True,
    )


def prioridade_centro_mes(registro: dict) -> tuple[int, float, str]:
    data = datetime.fromisoformat(registro["data"])
    distancia = abs(data.day - 15)
    nuvem = float(registro["nuvem_pct"])
    return distancia, nuvem, registro["data"]


def abrir_fontes(registros: list[dict], bandas: list[str]) -> list[dict]:
    fontes: list[dict] = []
    for registro in sorted(registros, key=prioridade_centro_mes):
        pasta = ROOT / registro["scene_dir"]
        caminhos = {b: localizar_raster(pasta, b) for b in bandas}
        scl = localizar_raster(pasta / "qualidade", "SCL")
        if any(c is None for c in caminhos.values()) or scl is None:
            continue

        datasets = {b: rasterio.open(caminhos[b]) for b in bandas}
        datasets["SCL"] = rasterio.open(scl)
        fontes.append({"registro": registro, "datasets": datasets})
    return fontes


def fechar_fontes(fontes: list[dict]) -> None:
    for fonte in fontes:
        for dataset in fonte["datasets"].values():
            dataset.close()


def grades_compativeis(fontes: list[dict], bandas: list[str]) -> bool:
    if not fontes:
        return False
    ref = fontes[0]["datasets"][bandas[0]]
    for fonte in fontes:
        for nome in [*bandas, "SCL"]:
            ds = fonte["datasets"][nome]
            if (
                ds.width != ref.width
                or ds.height != ref.height
                or ds.transform != ref.transform
                or ds.crs != ref.crs
            ):
                return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera mosaicos mensais sem nuvens a partir da série temporal Sentinel-2."
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PADRAO)
    parser.add_argument("--tile", help="Processa apenas um tile.")
    parser.add_argument("--mes", help="Processa apenas YYYY-MM.")
    parser.add_argument("--max-grupos", type=int, default=0, help="0 = todos")
    parser.add_argument("--limpar-saida", action="store_true")
    args = parser.parse_args()

    cfg = carregar_config(args.config)
    mosaico_cfg = cfg["mosaico"]
    bandas = list(cfg["bandas"])
    catalogo_origem = ROOT / mosaico_cfg["origem_catalogo"]
    pasta_saida = ROOT / mosaico_cfg["pasta"]
    catalogo_saida = ROOT / mosaico_cfg["catalogo"]
    resumo_path = ROOT / mosaico_cfg["resumo"]

    if not catalogo_origem.exists():
        print(f"[ERRO] Catálogo da série temporal não encontrado: {catalogo_origem}")
        return 2

    if args.limpar_saida and pasta_saida.exists():
        shutil.rmtree(pasta_saida)

    with catalogo_origem.open("r", encoding="utf-8-sig", newline="") as arquivo:
        linhas = [
            r
            for r in csv.DictReader(arquivo)
            if r.get("selecionada") == "1" and r.get("status") == "selecionada"
        ]

    grupos: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in linhas:
        if args.tile and r["tile_id"] != args.tile:
            continue
        if args.mes and r["mes"] != args.mes:
            continue
        grupos[(r["tile_id"], r["mes"])].append(r)

    min_cenas = int(mosaico_cfg.get("min_cenas_grupo", 2))
    classes_validas = set(int(x) for x in mosaico_cfg.get("classes_validas_scl", [2, 4, 5, 6]))
    classes_ruins = set(int(x) for x in mosaico_cfg.get("classes_ruins_scl", [3, 7, 8, 9, 10, 11]))
    margem = int(mosaico_cfg.get("margem_nuvem_px", 3))
    bloco = int(mosaico_cfg.get("bloco_px", 1024))
    preview_max = int(mosaico_cfg.get("preview_max_px", 1800))
    pmin = float(mosaico_cfg.get("percentil_min", 2))
    pmax = float(mosaico_cfg.get("percentil_max", 98))
    qualidade_jpeg = int(mosaico_cfg.get("qualidade_jpeg", 95))
    metodo = str(mosaico_cfg.get("metodo", "primeiro_pixel_limpo_prioridade_centro_mes"))

    print("=" * 78)
    print(" MOSAICO TEMPORAL MENSAL | best-pixel limpo")
    print("=" * 78)
    print(f"Grupos disponíveis: {len(grupos)}")
    print(f"Mínimo de cenas por grupo: {min_cenas}")
    print(f"Margem de segurança em nuvens: {margem} px")
    print(f"Bloco de processamento: {bloco} px")
    print(f"Método: {metodo}\n")

    registros_saida: list[dict] = []
    grupos_ok = grupos_pulados = erros = 0

    for numero, ((tile_id, mes), registros) in enumerate(sorted(grupos.items()), start=1):
        if args.max_grupos > 0 and grupos_ok >= args.max_grupos:
            break

        print(f"[{numero}] tile={tile_id} mês={mes} fontes={len(registros)}")
        if len(registros) < min_cenas:
            print("  [PULA] fontes insuficientes para composição temporal.")
            grupos_pulados += 1
            registros_saida.append(
                {
                    "mosaic_id": f"{tile_id}_{mes}",
                    "tile_id": tile_id,
                    "mes": mes,
                    "fontes": len(registros),
                    "valid_coverage_pct": "0",
                    "obs_2plus_pct": "0",
                    "mosaic_dir": "",
                    "preview": "",
                    "status": "fontes_insuficientes",
                    "erro": "",
                }
            )
            continue

        fontes = abrir_fontes(registros, bandas)
        if len(fontes) < min_cenas or not grades_compativeis(fontes, bandas):
            fechar_fontes(fontes)
            print("  [PULA] fontes ausentes ou grades incompatíveis.")
            grupos_pulados += 1
            registros_saida.append(
                {
                    "mosaic_id": f"{tile_id}_{mes}",
                    "tile_id": tile_id,
                    "mes": mes,
                    "fontes": len(fontes),
                    "valid_coverage_pct": "0",
                    "obs_2plus_pct": "0",
                    "mosaic_dir": "",
                    "preview": "",
                    "status": "fontes_incompativeis",
                    "erro": "",
                }
            )
            continue

        try:
            ref = fontes[0]["datasets"][bandas[0]]
            mosaic_id = f"{tile_id}_{mes}"
            destino = pasta_saida / tile_id / mes
            destino.mkdir(parents=True, exist_ok=True)

            writers = {}
            nodatas = {}
            for banda in bandas:
                src = fontes[0]["datasets"][banda]
                perfil = src.profile.copy()
                nodata = src.nodata
                if nodata is None:
                    nodata = 0
                nodatas[banda] = nodata
                perfil.update(
                    compress="deflate",
                    tiled=True,
                    nodata=nodata,
                    count=1,
                )
                writers[banda] = rasterio.open(destino / f"{banda}.tif", "w", **perfil)

            perfil_mask = ref.profile.copy()
            perfil_mask.update(dtype="uint8", nodata=0, count=1, compress="deflate", tiled=True)
            writer_valid = rasterio.open(destino / "VALID_MASK.tif", "w", **perfil_mask)
            writer_obs = rasterio.open(destino / "OBS_COUNT.tif", "w", **perfil_mask)

            perfil_source = ref.profile.copy()
            perfil_source.update(dtype="uint16", nodata=0, count=1, compress="deflate", tiled=True)
            writer_source = rasterio.open(destino / "SOURCE_INDEX.tif", "w", **perfil_source)

            total_pixels = ref.width * ref.height
            total_validos = 0
            total_obs2 = 0

            for janela in iterar_janelas(ref.width, ref.height, bloco):
                h = int(janela.height)
                w = int(janela.width)
                mascaras_limpas: list[np.ndarray] = []
                obs_count = np.zeros((h, w), dtype=np.uint8)

                for fonte in fontes:
                    limpa = mascara_limpa_janela(
                        fonte["datasets"]["SCL"],
                        janela,
                        classes_validas,
                        classes_ruins,
                        margem,
                    )
                    mascaras_limpas.append(limpa)
                    obs_count = np.minimum(obs_count.astype(np.uint16) + limpa.astype(np.uint16), 255).astype(np.uint8)

                preenchido = np.zeros((h, w), dtype=bool)
                source_index = np.zeros((h, w), dtype=np.uint16)
                saidas = {
                    banda: np.full(
                        (h, w),
                        nodatas[banda],
                        dtype=np.dtype(fontes[0]["datasets"][banda].dtypes[0]),
                    )
                    for banda in bandas
                }

                for indice, fonte in enumerate(fontes, start=1):
                    candidato = mascaras_limpas[indice - 1] & ~preenchido
                    if not candidato.any():
                        continue

                    dados_cena = {}
                    valores_ok = candidato.copy()
                    for banda in bandas:
                        ds = fonte["datasets"][banda]
                        dados = ds.read(1, window=janela)
                        dados_cena[banda] = dados
                        valores_ok &= valor_valido(dados, ds.nodata)

                    if not valores_ok.any():
                        continue

                    for banda in bandas:
                        saidas[banda][valores_ok] = dados_cena[banda][valores_ok]
                    source_index[valores_ok] = indice
                    preenchido[valores_ok] = True

                for banda in bandas:
                    writers[banda].write(saidas[banda], 1, window=janela)
                writer_valid.write(preenchido.astype(np.uint8), 1, window=janela)
                writer_obs.write(obs_count, 1, window=janela)
                writer_source.write(source_index, 1, window=janela)

                total_validos += int(preenchido.sum())
                total_obs2 += int((obs_count >= 2).sum())

            for writer in writers.values():
                writer.close()
            writer_valid.close()
            writer_obs.close()
            writer_source.close()

            valid_pct = total_validos * 100.0 / max(total_pixels, 1)
            obs2_pct = total_obs2 * 100.0 / max(total_pixels, 1)

            preview = destino / "preview_rgb.jpg"
            gerar_preview_rgb(
                destino,
                preview,
                preview_max,
                pmin,
                pmax,
                qualidade_jpeg,
            )

            fontes_meta = []
            for indice, fonte in enumerate(fontes, start=1):
                r = fonte["registro"]
                fontes_meta.append(
                    {
                        "source_index": indice,
                        "item_id": r["item_id"],
                        "data": r["data"],
                        "nuvem_pct_cena": float(r["nuvem_pct"]),
                        "scene_dir": r["scene_dir"],
                    }
                )

            metadata = {
                "mosaic_id": mosaic_id,
                "tile_id": tile_id,
                "mes": mes,
                "metodo": metodo,
                "classes_validas_scl": sorted(classes_validas),
                "classes_ruins_scl": sorted(classes_ruins),
                "margem_nuvem_px": margem,
                "valid_coverage_pct": round(valid_pct, 4),
                "obs_2plus_pct": round(obs2_pct, 4),
                "fontes": fontes_meta,
                "gerado_em_utc": datetime.now(timezone.utc).isoformat(),
            }
            (destino / "metadata.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            grupos_ok += 1
            print(f"  [OK] cobertura válida={valid_pct:.2f}% | pixels com 2+ obs={obs2_pct:.2f}%")
            registros_saida.append(
                {
                    "mosaic_id": mosaic_id,
                    "tile_id": tile_id,
                    "mes": mes,
                    "fontes": len(fontes),
                    "valid_coverage_pct": f"{valid_pct:.4f}",
                    "obs_2plus_pct": f"{obs2_pct:.4f}",
                    "mosaic_dir": str(destino.relative_to(ROOT)),
                    "preview": str(preview.relative_to(ROOT)),
                    "status": "gerado",
                    "erro": "",
                }
            )
        except Exception as exc:
            erros += 1
            print(f"  [ERRO] {exc}")
            registros_saida.append(
                {
                    "mosaic_id": f"{tile_id}_{mes}",
                    "tile_id": tile_id,
                    "mes": mes,
                    "fontes": len(fontes),
                    "valid_coverage_pct": "0",
                    "obs_2plus_pct": "0",
                    "mosaic_dir": "",
                    "preview": "",
                    "status": "erro",
                    "erro": str(exc),
                }
            )
        finally:
            fechar_fontes(fontes)

    catalogo_saida.parent.mkdir(parents=True, exist_ok=True)
    campos = [
        "mosaic_id",
        "tile_id",
        "mes",
        "fontes",
        "valid_coverage_pct",
        "obs_2plus_pct",
        "mosaic_dir",
        "preview",
        "status",
        "erro",
    ]
    with catalogo_saida.open("w", newline="", encoding="utf-8-sig") as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=campos)
        writer.writeheader()
        writer.writerows(registros_saida)

    resumo = {
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(),
        "grupos_disponiveis": len(grupos),
        "mosaicos_gerados": grupos_ok,
        "grupos_pulados": grupos_pulados,
        "erros": erros,
        "metodo": metodo,
        "catalogo": str(catalogo_saida.relative_to(ROOT)),
    }
    resumo_path.parent.mkdir(parents=True, exist_ok=True)
    resumo_path.write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== RESUMO ===")
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0 if erros == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
