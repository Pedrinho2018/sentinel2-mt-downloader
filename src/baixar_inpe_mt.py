from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import rasterio
import requests
import yaml
from PIL import Image
from pystac_client import Client
from rasterio.enums import Resampling
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PADRAO = ROOT / "config" / "config.yaml"


def carregar_config(caminho: Path) -> dict:
    with caminho.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def data_item(item) -> str:
    if item.datetime:
        return item.datetime.strftime("%Y-%m-%d")
    inicio = item.properties.get("start_datetime", "")
    if inicio:
        try:
            return datetime.fromisoformat(inicio.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except ValueError:
            return inicio[:10]
    return "sem_data"


def localizar_asset(item, nome: str):
    if nome in item.assets:
        return item.assets[nome]
    alvo = nome.casefold()
    for chave, asset in item.assets.items():
        if chave.casefold() == alvo:
            return asset
    return None


def extensao(url: str) -> str:
    ext = Path(urlparse(url).path).suffix
    return ext or ".tif"


def baixar(sessao: requests.Session, url: str, destino: Path, timeout: int, chunk_mb: int) -> str:
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
            with parcial.open("wb") as f, tqdm(
                total=tamanho or None,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc=destino.name,
                leave=False,
            ) as barra:
                for parte in resposta.iter_content(chunk_size=bloco):
                    if parte:
                        f.write(parte)
                        barra.update(len(parte))
        parcial.replace(destino)
        return "baixado"
    except Exception:
        parcial.unlink(missing_ok=True)
        raise


def percentual_nuvem_scl(caminho: Path, amostra_px: int = 1400) -> float:
    # SCL: 3 sombra; 7 suspeito/não classificado; 8/9 nuvem; 10 cirrus; 11 neve/gelo.
    classes_ruins = np.array([3, 7, 8, 9, 10, 11], dtype=np.uint8)
    with rasterio.open(caminho) as src:
        escala = min(1.0, amostra_px / max(src.width, src.height))
        w = max(1, int(src.width * escala))
        h = max(1, int(src.height * escala))
        scl = src.read(1, out_shape=(h, w), resampling=Resampling.nearest)

    validos = (scl != 0) & (scl != 1)
    total = int(validos.sum())
    if total == 0:
        return 100.0
    ruins = validos & np.isin(scl, classes_ruins)
    return float(ruins.sum() * 100.0 / total)


def ler_preview(caminho: Path, max_px: int) -> tuple[np.ndarray, np.ndarray]:
    with rasterio.open(caminho) as src:
        escala = min(1.0, max_px / max(src.width, src.height))
        w = max(1, int(src.width * escala))
        h = max(1, int(src.height * escala))
        dados = src.read(1, out_shape=(h, w), resampling=Resampling.bilinear).astype(np.float32)
        mascara = np.isfinite(dados)
        mascara &= dados != (src.nodata if src.nodata is not None else 0)
    return dados, mascara


def stretch(dados: np.ndarray, mascara: np.ndarray, pmin: float, pmax: float) -> np.ndarray:
    saida = np.zeros(dados.shape, dtype=np.uint8)
    valores = dados[mascara]
    if valores.size == 0:
        return saida
    minimo, maximo = np.percentile(valores, [pmin, pmax])
    if maximo <= minimo:
        return saida
    norm = np.clip((dados - minimo) / (maximo - minimo), 0, 1)
    saida = (norm * 255).astype(np.uint8)
    saida[~mascara] = 0
    return saida


def gerar_rgb(arquivos: dict[str, Path], destino: Path, cfg: dict) -> str:
    if any(b not in arquivos or not arquivos[b].exists() for b in ("B04", "B03", "B02")):
        return "bandas_rgb_incompletas"

    max_px = int(cfg.get("tamanho_max_px", 1600))
    pmin = float(cfg.get("percentil_min", 2))
    pmax = float(cfg.get("percentil_max", 98))
    qualidade = int(cfg.get("qualidade_jpeg", 92))

    r0, mr = ler_preview(arquivos["B04"], max_px)
    g0, mg = ler_preview(arquivos["B03"], max_px)
    b0, mb = ler_preview(arquivos["B02"], max_px)
    if r0.shape != g0.shape or r0.shape != b0.shape:
        return "dimensoes_incompativeis"

    m = mr & mg & mb
    rgb = np.dstack((stretch(r0, m, pmin, pmax), stretch(g0, m, pmin, pmax), stretch(b0, m, pmin, pmax)))
    destino.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb, mode="RGB").save(destino, "JPEG", quality=max(1, min(100, qualidade)), optimize=True)
    return "gerado"


def reg(item, data: str, colecao: str, banda: str, nuvem, status: str, url: str = "", arquivo: str = "", erro: str = "") -> dict:
    return {
        "id": item.id,
        "data": data,
        "colecao": colecao,
        "banda": banda,
        "nuvem_pct": f"{nuvem:.2f}" if isinstance(nuvem, float) else nuvem,
        "url": url,
        "arquivo": arquivo,
        "status": status,
        "erro": erro,
    }


def salvar_csv(caminho: Path, registros: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    campos = ["id", "data", "colecao", "banda", "nuvem_pct", "url", "arquivo", "status", "erro"]
    with caminho.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(registros)


def argumentos() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Downloader Sentinel-2 MT com filtro automático de nuvens via SCL.")
    p.add_argument("--config", type=Path, default=CONFIG_PADRAO)
    p.add_argument("--inicio")
    p.add_argument("--fim")
    p.add_argument("--max-itens", type=int, help="Quantidade de cenas APROVADAS; 0 = todas.")
    p.add_argument("--baixar", action="store_true")
    return p.parse_args()


def main() -> int:
    args = argumentos()
    cfg = carregar_config(args.config)
    colecao = cfg["stac"]["colecao"]
    inicio = args.inicio or cfg["periodo"]["inicio"]
    fim = args.fim or cfg["periodo"]["fim"]
    bandas = cfg["bandas"]
    dcfg = cfg["download"]
    qcfg = cfg.get("qualidade", {})
    pcfg = cfg.get("preview", {})

    max_itens = int(dcfg.get("max_itens_teste", 5)) if args.max_itens is None else args.max_itens
    max_candidatos = int(dcfg.get("max_candidatos_teste", 40))
    nuvem_max = float(qcfg.get("nuvem_max_pct", 20))
    filtro_nuvem = bool(qcfg.get("filtrar_nuvens", True))
    manter_scl = bool(qcfg.get("manter_scl", True))
    timeout = int(dcfg.get("timeout_segundos", 120))
    chunk_mb = int(dcfg.get("chunk_mb", 1))
    pasta = ROOT / dcfg["pasta"]
    csv_path = ROOT / dcfg["catalogo"]

    print("=" * 72)
    print(" Sentinel-2 MT Downloader | seleção para análise agrícola")
    print("=" * 72)
    print(f"Período: {inicio} até {fim} | coleção: {colecao}")
    print(f"Filtro de nuvens/sombra: {'SIM' if filtro_nuvem else 'NÃO'} | máximo: {nuvem_max:.1f}%")
    print(f"Meta: {'todas' if max_itens == 0 else max_itens} cena(s) aprovada(s)")

    cliente = Client.open(cfg["stac"]["url"])
    busca = cliente.search(collections=[colecao], bbox=cfg["area"]["bbox"], datetime=f"{inicio}/{fim}")
    sessao = requests.Session()
    sessao.headers.update({"User-Agent": "sentinel2-mt-downloader/1.2"})

    registros: list[dict] = []
    aprovadas = descartadas = candidatos = previews = erros = 0

    for item in busca.items():
        if max_itens > 0 and aprovadas >= max_itens:
            break
        candidatos += 1
        if max_itens > 0 and candidatos > max_candidatos:
            print(f"[LIMITE] {max_candidatos} candidatos avaliados.")
            break

        data = data_item(item)
        pasta_item = pasta / data / item.id
        print(f"\n[CANDIDATO {candidatos}] {item.id} | {data}")

        if not args.baixar:
            registros.append(reg(item, data, colecao, "CENA", "nao_avaliado", "candidato_catalogado"))
            aprovadas += 1
            continue

        nuvem: float | str = "nao_avaliado"
        if filtro_nuvem:
            scl = localizar_asset(item, "SCL")
            if scl is None:
                print("  [DESCARTADA] SCL indisponível.")
                registros.append(reg(item, data, colecao, "SCL", "indisponivel", "descartada_sem_scl"))
                continue

            scl_path = pasta_item / "qualidade" / f"SCL{extensao(scl.href)}"
            try:
                status_scl = baixar(sessao, scl.href, scl_path, timeout, chunk_mb)
                nuvem = percentual_nuvem_scl(scl_path)
                print(f"  [QUALIDADE] nuvem/sombra estimada: {nuvem:.2f}%")
                if nuvem > nuvem_max:
                    descartadas += 1
                    print(f"  [DESCARTADA] acima de {nuvem_max:.1f}%; bandas grandes não serão baixadas.")
                    registros.append(reg(item, data, colecao, "SCL", nuvem, "descartada_nuvem", scl.href, str(scl_path.relative_to(ROOT))))
                    if not manter_scl:
                        scl_path.unlink(missing_ok=True)
                    continue
                registros.append(reg(item, data, colecao, "SCL", nuvem, status_scl, scl.href, str(scl_path.relative_to(ROOT))))
            except Exception as exc:
                erros += 1
                print(f"  [ERRO] SCL: {exc}")
                registros.append(reg(item, data, colecao, "SCL", "erro", "erro_qualidade", scl.href, str(scl_path.relative_to(ROOT)), str(exc)))
                continue

        aprovadas += 1
        print(f"  [APROVADA {aprovadas}] baixando bandas científicas...")
        arquivos: dict[str, Path] = {}

        for banda in bandas:
            asset = localizar_asset(item, banda)
            if asset is None:
                registros.append(reg(item, data, colecao, banda, nuvem, "asset_nao_encontrado"))
                continue
            destino = pasta_item / f"{banda}{extensao(asset.href)}"
            try:
                status = baixar(sessao, asset.href, destino, timeout, chunk_mb)
                arquivos[banda] = destino
                print(f"  [OK] {banda}: {status}")
                erro = ""
            except Exception as exc:
                status, erro = "erro_download", str(exc)
                erros += 1
                print(f"  [ERRO] {banda}: {exc}")
            registros.append(reg(item, data, colecao, banda, nuvem, status, asset.href, str(destino.relative_to(ROOT)), erro))

        if bool(pcfg.get("gerar_rgb", True)):
            preview = pasta_item / "preview_rgb.jpg"
            try:
                status = gerar_rgb(arquivos, preview, pcfg)
                previews += int(status == "gerado")
                print(f"  [PREVIEW] {status}: {preview.name}")
                registros.append(reg(item, data, colecao, "RGB_PREVIEW", nuvem, status, arquivo=str(preview.relative_to(ROOT))))
            except Exception as exc:
                erros += 1
                print(f"  [ERRO] preview: {exc}")
                registros.append(reg(item, data, colecao, "RGB_PREVIEW", nuvem, "erro_preview", arquivo=str(preview.relative_to(ROOT)), erro=str(exc)))

    salvar_csv(csv_path, registros)

    print("\n" + "=" * 72)
    print(f"Candidatos: {candidatos} | aprovadas: {aprovadas} | descartadas por nuvem: {descartadas}")
    print(f"Previews: {previews} | erros: {erros}")
    print(f"Catálogo: {csv_path.relative_to(ROOT)}")
    if not args.baixar:
        print("Modo seguro: para avaliar nuvem e baixar 1 cena boa:")
        print("python src\\baixar_inpe_mt.py --baixar --max-itens 1")
    return 0 if erros == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
