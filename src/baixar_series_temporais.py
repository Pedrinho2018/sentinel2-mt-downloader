from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
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
PADRAO_ID = re.compile(r"S2-16D(?:_V\d+)?_(?P<tile>\d{6})_(?P<data>\d{8})")


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
    match = PADRAO_ID.search(item.id)
    if match:
        data = datetime.strptime(match.group("data"), "%Y%m%d")
        return match.group("tile"), data

    if item.datetime:
        tile = str(item.properties.get("bdc:tile") or item.properties.get("tile") or "").strip()
        if tile:
            return tile, item.datetime.replace(tzinfo=None)
    return None


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


def percentual_nuvem_scl(caminho: Path, amostra_px: int = 1600) -> float:
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


def salvar_csv(caminho: Path, registros: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    campos = [
        "tile_id",
        "mes",
        "item_id",
        "data",
        "nuvem_pct",
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Monta séries temporais Sentinel-2 por tile e mês para composição sem nuvens."
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PADRAO)
    parser.add_argument("--inicio", help="YYYY-MM-DD")
    parser.add_argument("--fim", help="YYYY-MM-DD")
    parser.add_argument("--tile", help="Processa apenas um tile, ex.: 014018")
    parser.add_argument(
        "--max-tiles",
        type=int,
        help="Quantidade máxima de tiles. 0 = todos. Sobrescreve config.",
    )
    parser.add_argument(
        "--cenas-por-mes",
        type=int,
        help="Máximo de cenas selecionadas por tile/mês.",
    )
    args = parser.parse_args()

    cfg = carregar_config(args.config)
    stac_cfg = cfg["stac"]
    serie_cfg = cfg["serie_temporal"]
    download_cfg = cfg["download"]

    inicio = args.inicio or cfg["periodo"]["inicio"]
    fim = args.fim or cfg["periodo"]["fim"]
    bandas = list(cfg["bandas"])
    max_tiles = (
        int(serie_cfg.get("max_tiles_teste", 1))
        if args.max_tiles is None
        else args.max_tiles
    )
    cenas_por_mes = (
        int(serie_cfg.get("cenas_por_tile_mes", 2))
        if args.cenas_por_mes is None
        else args.cenas_por_mes
    )
    nuvem_max = float(serie_cfg.get("nuvem_max_cena_pct", 60))
    pasta_base = ROOT / download_cfg["pasta"]
    catalogo = ROOT / serie_cfg["catalogo"]
    resumo_path = ROOT / serie_cfg["resumo"]
    timeout = int(download_cfg.get("timeout_segundos", 180))
    chunk_mb = int(download_cfg.get("chunk_mb", 2))

    print("=" * 76)
    print(" SÉRIE TEMPORAL SENTINEL-2 | tile x mês")
    print("=" * 76)
    print(f"Período: {inicio} até {fim}")
    print(f"Cenas por tile/mês: {cenas_por_mes}")
    print(f"Nuvem máxima da cena-fonte: {nuvem_max:.1f}%")
    print("Observação: cenas-fonte podem ter nuvem; o próximo estágio faz o mosaico limpo.\n")

    cliente = Client.open(stac_cfg["url"])
    busca = cliente.search(
        collections=[stac_cfg["colecao"]],
        bbox=cfg["area"]["bbox"],
        datetime=f"{inicio}/{fim}",
    )

    grupos: dict[tuple[str, str], list[tuple[object, datetime]]] = defaultdict(list)
    itens_lidos = 0
    ignorados_id = 0

    print("[1/3] Lendo catálogo STAC e organizando por tile/mês...")
    for item in busca.items():
        itens_lidos += 1
        info = interpretar_item(item)
        if info is None:
            ignorados_id += 1
            continue
        tile_id, data = info
        if args.tile and tile_id != args.tile:
            continue
        mes = data.strftime("%Y-%m")
        grupos[(tile_id, mes)].append((item, data))

    tiles = sorted({tile for tile, _ in grupos})
    if args.tile:
        tiles = [tile for tile in tiles if tile == args.tile]
    elif max_tiles > 0:
        tiles = tiles[:max_tiles]

    grupos = {
        chave: valor
        for chave, valor in grupos.items()
        if chave[0] in set(tiles)
    }

    if not grupos:
        print("[ERRO] Nenhum grupo tile/mês encontrado para os filtros.")
        return 2

    print(f"Tiles selecionados: {', '.join(tiles)}")
    print(f"Grupos tile/mês: {len(grupos)}")

    sessao = requests.Session()
    sessao.headers.update({"User-Agent": "sentinel2-mt-downloader/temporal-series-1.0"})
    registros: list[dict] = []
    selecionadas_total = 0
    erros = 0

    print("\n[2/3] Avaliando SCL e escolhendo as melhores fontes de cada mês...")

    for (tile_id, mes), candidatos in sorted(grupos.items()):
        print(f"\n[TILE {tile_id} | {mes}] candidatos: {len(candidatos)}")
        avaliados: list[dict] = []

        for item, data in sorted(candidatos, key=lambda x: x[1]):
            scl_asset = localizar_asset(item, "SCL")
            scene_dir = pasta_base / data.strftime("%Y-%m-%d") / item.id
            scl_path = scene_dir / "qualidade" / "SCL.tif"

            if scl_asset is None:
                avaliados.append(
                    {
                        "item": item,
                        "data": data,
                        "tile": tile_id,
                        "mes": mes,
                        "nuvem": 100.0,
                        "status": "sem_scl",
                        "scene_dir": scene_dir,
                        "scl_path": scl_path,
                        "erro": "asset SCL ausente",
                    }
                )
                continue

            scl_path = scene_dir / "qualidade" / f"SCL{extensao(scl_asset.href)}"
            try:
                baixar(sessao, scl_asset.href, scl_path, timeout, chunk_mb)
                nuvem = percentual_nuvem_scl(scl_path)
                status = "candidata" if nuvem <= nuvem_max else "rejeitada_nuvem"
                print(f"  {data.date()} | {item.id} | nuvem/sombra={nuvem:.2f}% | {status}")
                erro = ""
            except Exception as exc:
                nuvem = 100.0
                status = "erro_scl"
                erro = str(exc)
                erros += 1
                print(f"  [ERRO] {item.id}: {exc}")

            avaliados.append(
                {
                    "item": item,
                    "data": data,
                    "tile": tile_id,
                    "mes": mes,
                    "nuvem": nuvem,
                    "status": status,
                    "scene_dir": scene_dir,
                    "scl_path": scl_path,
                    "erro": erro,
                }
            )

        elegiveis = [a for a in avaliados if a["status"] == "candidata"]
        elegiveis.sort(key=lambda a: (a["nuvem"], abs(a["data"].day - 15), a["data"]))
        selecionadas = elegiveis[: max(1, cenas_por_mes)]
        ids_selecionados = {a["item"].id for a in selecionadas}

        for entrada in avaliados:
            item = entrada["item"]
            selecionada = item.id in ids_selecionados
            status = entrada["status"]

            if status == "candidata" and not selecionada:
                status = "fora_limite_mensal"

            if selecionada:
                print(f"  -> SELECIONADA: {item.id}")
                scene_dir: Path = entrada["scene_dir"]
                falha_banda = False
                for banda in bandas:
                    asset = localizar_asset(item, banda)
                    if asset is None:
                        falha_banda = True
                        erros += 1
                        entrada["erro"] = f"asset {banda} ausente"
                        break
                    destino = scene_dir / f"{banda}{extensao(asset.href)}"
                    try:
                        baixar(sessao, asset.href, destino, timeout, chunk_mb)
                    except Exception as exc:
                        falha_banda = True
                        erros += 1
                        entrada["erro"] = f"{banda}: {exc}"
                        break

                if falha_banda:
                    selecionada = False
                    status = "erro_bandas"
                else:
                    status = "selecionada"
                    selecionadas_total += 1

            registros.append(
                {
                    "tile_id": tile_id,
                    "mes": mes,
                    "item_id": item.id,
                    "data": entrada["data"].strftime("%Y-%m-%d"),
                    "nuvem_pct": f"{entrada['nuvem']:.3f}",
                    "status": status,
                    "selecionada": "1" if selecionada else "0",
                    "scene_dir": str(entrada["scene_dir"].relative_to(ROOT)),
                    "scl": str(entrada["scl_path"].relative_to(ROOT)),
                    "erro": entrada["erro"],
                }
            )

    print("\n[3/3] Salvando catálogo da série temporal...")
    salvar_csv(catalogo, registros)

    resumo = {
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(),
        "periodo_inicio": inicio,
        "periodo_fim": fim,
        "tiles": tiles,
        "itens_stac_lidos": itens_lidos,
        "itens_sem_tile_data": ignorados_id,
        "grupos_tile_mes": len(grupos),
        "cenas_selecionadas": selecionadas_total,
        "cenas_por_tile_mes": cenas_por_mes,
        "nuvem_max_cena_pct": nuvem_max,
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
