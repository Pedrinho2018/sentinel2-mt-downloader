from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from urllib.parse import urlparse

import numpy as np
import rasterio
import requests
import yaml
from pystac_client import Client
from rasterio.enums import Resampling
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PADRAO = ROOT / "config" / "config.yaml"
PADRAO_L2A = re.compile(
    r"S2[ABC]_MSIL2A_(?P<data>\d{8})T\d+_N\d+_R\d+_T(?P<tile>\d{5})_"
)


def carregar_config(caminho: Path) -> dict:
    with caminho.open("r", encoding="utf-8") as arquivo:
        return yaml.safe_load(arquivo)


def localizar_asset(item, nome: str):
    if nome in item.assets:
        return item.assets[nome]
    alvo = nome.casefold()
    for chave, asset in item.assets.items():
        if chave.casefold() == alvo:
            return asset
    return None


def extensao(url: str) -> str:
    sufixo = Path(urlparse(url).path).suffix
    return sufixo or ".tif"


def interpretar_item(item) -> tuple[str, datetime] | None:
    match = PADRAO_L2A.search(item.id)
    if match:
        return match.group("tile"), datetime.strptime(match.group("data"), "%Y%m%d")

    tile = str(
        item.properties.get("s2:mgrs_tile")
        or item.properties.get("bdc:tile")
        or item.properties.get("tile")
        or ""
    ).strip().removeprefix("T")
    if tile and item.datetime:
        return tile, item.datetime.replace(tzinfo=None)
    return None


def eo_cloud(item) -> float:
    for chave in ("eo:cloud_cover", "cloud_cover"):
        valor = item.properties.get(chave)
        if valor is not None:
            try:
                return float(valor)
            except (TypeError, ValueError):
                pass
    return 100.0


def baixar(
    sessao: requests.Session,
    url: str,
    destino: Path,
    timeout: int,
    chunk_mb: int,
) -> str:
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists() and destino.stat().st_size > 0:
        return "ja_existia"

    parcial = destino.with_suffix(destino.suffix + ".part")
    parcial.unlink(missing_ok=True)
    bloco = max(1, chunk_mb) * 1024 * 1024

    try:
        with sessao.get(url, stream=True, timeout=(20, timeout)) as resposta:
            resposta.raise_for_status()
            tamanho = int(resposta.headers.get("content-length", 0))
            with parcial.open("wb") as arquivo, tqdm(
                total=tamanho or None,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc=destino.name,
                leave=False,
            ) as barra:
                for pedaco in resposta.iter_content(chunk_size=bloco):
                    if pedaco:
                        arquivo.write(pedaco)
                        barra.update(len(pedaco))
        parcial.replace(destino)
        return "baixado"
    except Exception:
        parcial.unlink(missing_ok=True)
        raise


def percentual_nuvem_scl(caminho: Path, amostra_px: int = 1800) -> float:
    classes_ruins = np.array([3, 7, 8, 9, 10, 11], dtype=np.uint8)
    with rasterio.open(caminho) as src:
        escala = min(1.0, amostra_px / max(src.width, src.height))
        largura = max(1, int(src.width * escala))
        altura = max(1, int(src.height * escala))
        scl = src.read(
            1,
            out_shape=(altura, largura),
            resampling=Resampling.nearest,
        )

    validos = (scl != 0) & (scl != 1)
    total = int(validos.sum())
    if total == 0:
        return 100.0
    ruins = validos & np.isin(scl, classes_ruins)
    return float(ruins.sum() * 100.0 / total)


def baixar_bandas(
    entrada: dict,
    bandas: list[str],
    sessao: requests.Session,
    timeout: int,
    chunk_mb: int,
) -> tuple[bool, str]:
    item = entrada["item"]
    pasta: Path = entrada["scene_dir"]
    for banda in bandas:
        asset = localizar_asset(item, banda)
        if asset is None:
            return False, f"asset {banda} ausente"
        destino = pasta / f"{banda}{extensao(asset.href)}"
        try:
            baixar(sessao, asset.href, destino, timeout, chunk_mb)
        except Exception as exc:
            return False, f"{banda}: {exc}"
    return True, ""


def salvar_csv(caminho: Path, registros: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    campos = [
        "tile_id",
        "mes",
        "item_id",
        "data",
        "eo_cloud_pct",
        "scl_cloud_shadow_pct",
        "status",
        "selecionada",
        "scene_dir",
        "scl",
        "erro",
    ]
    with caminho.open("w", newline="", encoding="utf-8-sig") as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=campos)
        writer.writeheader()
        writer.writerows(registros)


def escolher_tiles(
    grupos: dict[tuple[str, str], list[tuple[object, datetime]]],
    max_tiles: int,
    tile_forcado: str,
) -> list[str]:
    tiles = sorted({tile for tile, _ in grupos})
    if tile_forcado:
        return [tile_forcado] if tile_forcado in tiles else []
    if max_tiles == 0:
        return tiles

    ranking = []
    for tile in tiles:
        grupos_tile = [v for (t, _), v in grupos.items() if t == tile]
        meses = len(grupos_tile)
        itens = [item for grupo in grupos_tile for item, _ in grupo]
        clouds = [eo_cloud(item) for item in itens]
        mediana_cloud = median(clouds) if clouds else 100.0
        ranking.append((meses, len(itens), -mediana_cloud, tile))

    ranking.sort(reverse=True)
    return [r[3] for r in ranking[:max_tiles]]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Baixa cenas Sentinel-2 L2A reais por MGRS tile/mês para composição temporal."
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PADRAO)
    parser.add_argument("--inicio", help="YYYY-MM-DD")
    parser.add_argument("--fim", help="YYYY-MM-DD")
    parser.add_argument("--tile", help="MGRS tile específico, ex.: 21LWG")
    parser.add_argument("--max-tiles", type=int, help="0 = todos")
    parser.add_argument("--cenas-por-mes", type=int)
    args = parser.parse_args()

    cfg = carregar_config(args.config)
    serie = cfg["serie_temporal"]
    download_cfg = cfg["download"]

    inicio_txt = args.inicio or cfg["periodo"]["inicio"]
    fim_txt = args.fim or cfg["periodo"]["fim"]
    inicio = datetime.strptime(inicio_txt, "%Y-%m-%d")
    fim = datetime.strptime(fim_txt, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

    max_tiles = int(serie.get("max_tiles_teste", 1)) if args.max_tiles is None else args.max_tiles
    cenas_por_mes = int(serie.get("cenas_por_tile_mes", 6)) if args.cenas_por_mes is None else args.cenas_por_mes
    candidatos_por_mes = int(serie.get("candidatos_por_tile_mes", 10))
    nuvem_max = float(serie.get("nuvem_max_cena_pct", 70))
    bandas = list(cfg["bandas"])
    timeout = int(download_cfg.get("timeout_segundos", 240))
    chunk_mb = int(download_cfg.get("chunk_mb", 4))
    pasta_base = ROOT / download_cfg["pasta"]
    catalogo = ROOT / serie["catalogo"]
    resumo_path = ROOT / serie["resumo"]

    usar_bbox_teste = bool(serie.get("usar_bbox_teste", False)) and max_tiles != 0 and not args.tile
    bbox = cfg["area"].get("teste_bbox") if usar_bbox_teste else cfg["area"]["bbox"]

    print("=" * 78)
    print(" SENTINEL-2 L2A REAL | série temporal por MGRS tile/mês")
    print("=" * 78)
    print(f"Coleção: {cfg['stac']['colecao']}")
    print(f"Período: {inicio_txt} até {fim_txt}")
    print(f"Busca: {'BBOX DE TESTE' if usar_bbox_teste else 'MATO GROSSO'}")
    print(f"Candidatos avaliados/mês: {candidatos_por_mes}")
    print(f"Fontes guardadas/mês: até {cenas_por_mes}")
    print(f"Nuvem/sombra máxima por fonte: {nuvem_max:.1f}%\n")

    cliente = Client.open(cfg["stac"]["url"])
    busca = cliente.search(
        collections=[cfg["stac"]["colecao"]],
        bbox=bbox,
        datetime=f"{inicio_txt}/{fim_txt}",
    )

    grupos_todos: dict[tuple[str, str], list[tuple[object, datetime]]] = defaultdict(list)
    itens_lidos = fora_periodo = sem_tile = 0

    print("[1/3] Lendo STAC e separando cenas reais por tile/mês...")
    for item in busca.items():
        itens_lidos += 1
        info = interpretar_item(item)
        if info is None:
            sem_tile += 1
            continue
        tile_id, data = info
        if data < inicio or data > fim:
            fora_periodo += 1
            continue
        grupos_todos[(tile_id, data.strftime("%Y-%m"))].append((item, data))

    tile_forcado = (args.tile or str(serie.get("tile_teste", ""))).strip().removeprefix("T")
    tiles = escolher_tiles(grupos_todos, max_tiles, tile_forcado)
    if not tiles:
        print("[ERRO] Nenhum MGRS tile encontrado para o recorte/período.")
        return 2

    tiles_set = set(tiles)
    grupos = {chave: valor for chave, valor in grupos_todos.items() if chave[0] in tiles_set}
    print(f"Tiles escolhidos: {', '.join(tiles)}")
    print(f"Grupos tile/mês: {len(grupos)}\n")

    sessao = requests.Session()
    sessao.headers.update({"User-Agent": "sentinel2-mt-downloader/l2a-temporal-2.0"})
    registros: list[dict] = []
    selecionadas_total = avisos = 0

    print("[2/3] Avaliando SCL real e baixando as melhores fontes...")
    for (tile_id, mes), candidatos in sorted(grupos.items()):
        preordenados = sorted(
            candidatos,
            key=lambda x: (eo_cloud(x[0]), abs(x[1].day - 15), x[1]),
        )[:candidatos_por_mes]

        print(f"\n[TILE {tile_id} | {mes}] STAC={len(candidatos)} | avaliando={len(preordenados)}")
        avaliados: list[dict] = []

        for item, data in preordenados:
            scene_dir = pasta_base / tile_id / data.strftime("%Y-%m-%d") / item.id
            scl_asset = localizar_asset(item, "SCL")
            eo_pct = eo_cloud(item)
            scl_path = scene_dir / "qualidade" / "SCL.tif"

            if scl_asset is None:
                avaliados.append({
                    "item": item, "data": data, "eo": eo_pct, "scl": 100.0,
                    "status": "sem_scl", "scene_dir": scene_dir, "scl_path": scl_path,
                    "erro": "asset SCL ausente"
                })
                avisos += 1
                continue

            scl_path = scene_dir / "qualidade" / f"SCL{extensao(scl_asset.href)}"
            try:
                baixar(sessao, scl_asset.href, scl_path, timeout, chunk_mb)
                scl_pct = percentual_nuvem_scl(scl_path)
                status = "candidata" if scl_pct <= nuvem_max else "rejeitada_nuvem"
                erro = ""
                print(f"  {data.date()} | eo={eo_pct:5.1f}% | SCL={scl_pct:5.1f}% | {status}")
            except Exception as exc:
                scl_pct = 100.0
                status = "erro_scl"
                erro = str(exc)
                avisos += 1
                print(f"  [AVISO] {item.id}: {exc}")

            avaliados.append({
                "item": item, "data": data, "eo": eo_pct, "scl": scl_pct,
                "status": status, "scene_dir": scene_dir, "scl_path": scl_path,
                "erro": erro
            })

        elegiveis = [a for a in avaliados if a["status"] == "candidata"]
        elegiveis.sort(key=lambda a: (a["scl"], abs(a["data"].day - 15), a["data"]))

        selecionados: set[str] = set()
        for entrada in elegiveis:
            if len(selecionados) >= cenas_por_mes:
                break
            print(f"  -> baixando fonte {len(selecionados)+1}/{cenas_por_mes}: {entrada['item'].id}")
            ok, erro = baixar_bandas(entrada, bandas, sessao, timeout, chunk_mb)
            if ok:
                entrada["status"] = "selecionada"
                entrada["erro"] = ""
                selecionados.add(entrada["item"].id)
                selecionadas_total += 1
            else:
                entrada["status"] = "erro_bandas"
                entrada["erro"] = erro
                avisos += 1
                print(f"     [AVISO] {erro}")

        for entrada in avaliados:
            selecionada = entrada["item"].id in selecionados
            status = entrada["status"]
            if status == "candidata" and not selecionada:
                status = "nao_selecionada"
            registros.append({
                "tile_id": tile_id,
                "mes": mes,
                "item_id": entrada["item"].id,
                "data": entrada["data"].strftime("%Y-%m-%d"),
                "eo_cloud_pct": f"{entrada['eo']:.3f}",
                "scl_cloud_shadow_pct": f"{entrada['scl']:.3f}",
                "status": status,
                "selecionada": "1" if selecionada else "0",
                "scene_dir": str(entrada["scene_dir"].relative_to(ROOT)),
                "scl": str(entrada["scl_path"].relative_to(ROOT)),
                "erro": entrada["erro"],
            })

    print("\n[3/3] Salvando catálogo da série L2A...")
    salvar_csv(catalogo, registros)
    resumo = {
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(),
        "colecao": cfg["stac"]["colecao"],
        "periodo_inicio": inicio_txt,
        "periodo_fim": fim_txt,
        "bbox_usado": bbox,
        "tiles": tiles,
        "itens_stac_lidos": itens_lidos,
        "itens_fora_periodo_descartados": fora_periodo,
        "itens_sem_tile": sem_tile,
        "grupos_tile_mes": len(grupos),
        "cenas_selecionadas": selecionadas_total,
        "cenas_por_tile_mes": cenas_por_mes,
        "avisos": avisos,
        "catalogo": str(catalogo.relative_to(ROOT)),
    }
    resumo_path.parent.mkdir(parents=True, exist_ok=True)
    resumo_path.write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(resumo, ensure_ascii=False, indent=2))

    if selecionadas_total == 0:
        print("[ERRO] Nenhuma cena L2A foi selecionada.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
