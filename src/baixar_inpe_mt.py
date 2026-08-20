from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
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
CLASSES_NUVEM_SOMBRA = np.array([3, 7, 8, 9, 10, 11], dtype=np.uint8)


def carregar_config(caminho: Path) -> dict:
    with caminho.open("r", encoding="utf-8") as arquivo:
        return yaml.safe_load(arquivo)


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
            with parcial.open("wb") as arquivo, tqdm(
                total=tamanho or None,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc=destino.name,
                leave=False,
            ) as barra:
                for parte in resposta.iter_content(chunk_size=bloco):
                    if parte:
                        arquivo.write(parte)
                        barra.update(len(parte))
        parcial.replace(destino)
        return "baixado"
    except Exception:
        parcial.unlink(missing_ok=True)
        raise


def percentual_nuvem_scl(caminho: Path, amostra_px: int = 1800) -> float:
    with rasterio.open(caminho) as src:
        escala = min(1.0, amostra_px / max(src.width, src.height))
        largura = max(1, int(src.width * escala))
        altura = max(1, int(src.height * escala))
        scl = src.read(1, out_shape=(altura, largura), resampling=Resampling.nearest)

    validos = (scl != 0) & (scl != 1)
    total = int(validos.sum())
    if total == 0:
        return 100.0
    ruins = validos & np.isin(scl, CLASSES_NUVEM_SOMBRA)
    return float(ruins.sum() * 100.0 / total)


def ler_preview(caminho: Path, max_px: int) -> tuple[np.ndarray, np.ndarray]:
    with rasterio.open(caminho) as src:
        escala = min(1.0, max_px / max(src.width, src.height))
        largura = max(1, int(src.width * escala))
        altura = max(1, int(src.height * escala))
        dados = src.read(1, out_shape=(altura, largura), resampling=Resampling.bilinear).astype(np.float32)
        mascara = np.isfinite(dados)
        mascara &= dados != (src.nodata if src.nodata is not None else 0)
    return dados, mascara


def stretch(dados: np.ndarray, mascara: np.ndarray, pmin: float, pmax: float) -> np.ndarray:
    saida = np.zeros(dados.shape, dtype=np.uint8)
    valores = dados[mascara]
    if valores.size == 0:
        return saida
    minimo, maximo = np.percentile(valores, [pmin, pmax])
    if not np.isfinite(minimo) or not np.isfinite(maximo) or maximo <= minimo:
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
    qualidade = int(cfg.get("qualidade_jpeg", 94))

    r0, mr = ler_preview(arquivos["B04"], max_px)
    g0, mg = ler_preview(arquivos["B03"], max_px)
    b0, mb = ler_preview(arquivos["B02"], max_px)
    if r0.shape != g0.shape or r0.shape != b0.shape:
        return "dimensoes_incompativeis"

    mascara = mr & mg & mb
    rgb = np.dstack(
        (
            stretch(r0, mascara, pmin, pmax),
            stretch(g0, mascara, pmin, pmax),
            stretch(b0, mascara, pmin, pmax),
        )
    )
    destino.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb, mode="RGB").save(destino, "JPEG", quality=max(1, min(100, qualidade)), optimize=True)
    return "gerado"


def caminhos_bandas(item, pasta_item: Path, bandas: list[str]) -> dict[str, Path]:
    caminhos: dict[str, Path] = {}
    for banda in bandas:
        asset = localizar_asset(item, banda)
        if asset is not None:
            caminhos[banda] = pasta_item / f"{banda}{extensao(asset.href)}"
    return caminhos


def cena_completa(item, pasta_item: Path, bandas: list[str]) -> bool:
    caminhos = caminhos_bandas(item, pasta_item, bandas)
    return len(caminhos) == len(bandas) and all(p.exists() and p.stat().st_size > 0 for p in caminhos.values())


def marcar_qualidade(pasta_item: Path, item, data: str, nuvem_pct: float, limite_pct: float, aprovada: bool) -> Path:
    qualidade = pasta_item / "qualidade"
    qualidade.mkdir(parents=True, exist_ok=True)
    aprovado = qualidade / "aprovada.json"
    rejeitado = qualidade / "rejeitada.json"
    if aprovada:
        rejeitado.unlink(missing_ok=True)
        destino = aprovado
    else:
        aprovado.unlink(missing_ok=True)
        destino = rejeitado

    payload = {
        "scene_id": item.id,
        "data": data,
        "nuvem_sombra_pct": round(float(nuvem_pct), 4),
        "limite_pct": float(limite_pct),
        "aprovada": bool(aprovada),
        "avaliado_em_utc": datetime.now(timezone.utc).isoformat(),
    }
    destino.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino


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
    with caminho.open("w", newline="", encoding="utf-8-sig") as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=campos)
        writer.writeheader()
        writer.writerows(registros)


def argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Downloader Sentinel-2 MT com seleção rigorosa por SCL.")
    parser.add_argument("--config", type=Path, default=CONFIG_PADRAO)
    parser.add_argument("--inicio")
    parser.add_argument("--fim")
    parser.add_argument("--max-itens", type=int, help="Quantidade de cenas NOVAS aprovadas; 0 = todas.")
    parser.add_argument("--baixar", action="store_true")
    parser.add_argument(
        "--reusar-existentes",
        action="store_true",
        help="Cenas existentes aprovadas também contam na meta. Sem esta opção elas são apenas revalidadas.",
    )
    return parser.parse_args()


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
    max_candidatos = int(dcfg.get("max_candidatos_teste", 120))
    nuvem_max = float(qcfg.get("nuvem_max_pct", 5))
    filtro_nuvem = bool(qcfg.get("filtrar_nuvens", True))
    timeout = int(dcfg.get("timeout_segundos", 120))
    chunk_mb = int(dcfg.get("chunk_mb", 1))
    pasta = ROOT / dcfg["pasta"]
    csv_path = ROOT / dcfg["catalogo"]

    print("=" * 76)
    print(" Sentinel-2 MT Downloader | seleção rigorosa para análise agrícola")
    print("=" * 76)
    print(f"Período: {inicio} até {fim} | coleção: {colecao}")
    print(f"Filtro da cena: {'SIM' if filtro_nuvem else 'NÃO'} | máximo: {nuvem_max:.1f}% nuvem/sombra")
    print(f"Meta: {'todas' if max_itens == 0 else max_itens} cena(s) NOVA(S) aprovada(s)")
    print("Cenas já baixadas serão REVALIDADAS antes de entrar no pipeline de patches.")

    cliente = Client.open(cfg["stac"]["url"])
    busca = cliente.search(collections=[colecao], bbox=cfg["area"]["bbox"], datetime=f"{inicio}/{fim}")
    sessao = requests.Session()
    sessao.headers.update({"User-Agent": "sentinel2-mt-downloader/1.4"})

    registros: list[dict] = []
    novas = descartadas = candidatos = previews = erros = existentes_aprovadas = 0

    for item in busca.items():
        if max_itens > 0 and novas >= max_itens:
            break
        candidatos += 1
        if max_itens > 0 and candidatos > max_candidatos:
            print(f"[LIMITE] {max_candidatos} candidatos avaliados.")
            break

        data = data_item(item)
        pasta_item = pasta / data / item.id
        completa_antes = cena_completa(item, pasta_item, bandas)
        print(f"\n[CANDIDATO {candidatos}] {item.id} | {data}{' | já baixada' if completa_antes else ''}")

        if not args.baixar:
            registros.append(reg(item, data, colecao, "CENA", "nao_avaliado", "candidato_catalogado"))
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
                aprovada = nuvem <= nuvem_max
                marcador = marcar_qualidade(pasta_item, item, data, nuvem, nuvem_max, aprovada)
                registros.append(reg(item, data, colecao, "SCL", nuvem, status_scl, scl.href, str(scl_path.relative_to(ROOT))))
                registros.append(reg(item, data, colecao, "QUALIDADE", nuvem, "aprovada" if aprovada else "rejeitada", arquivo=str(marcador.relative_to(ROOT))))
                if not aprovada:
                    descartadas += 1
                    print(f"  [DESCARTADA] acima de {nuvem_max:.1f}%. Não será usada para patches.")
                    continue
            except Exception as exc:
                erros += 1
                print(f"  [ERRO] avaliação SCL: {exc}")
                registros.append(reg(item, data, colecao, "SCL", "erro", "erro_qualidade", erro=str(exc)))
                continue

        if completa_antes:
            existentes_aprovadas += 1
            print("  [APROVADA EXISTENTE] cena revalidada e marcada para o gerador de patches.")
            if args.reusar_existentes:
                novas += 1
                print(f"  [META] existente contabilizada. Aprovadas na execução: {novas}")
            continue

        print("  [APROVADA] baixando/completando bandas científicas...")
        arquivos: dict[str, Path] = {}
        falhou = False
        for banda in bandas:
            asset = localizar_asset(item, banda)
            if asset is None:
                falhou = True
                registros.append(reg(item, data, colecao, banda, nuvem, "asset_nao_encontrado"))
                continue
            destino = pasta_item / f"{banda}{extensao(asset.href)}"
            try:
                status = baixar(sessao, asset.href, destino, timeout, chunk_mb)
                arquivos[banda] = destino
                print(f"  [OK] {banda}: {status}")
                registros.append(reg(item, data, colecao, banda, nuvem, status, asset.href, str(destino.relative_to(ROOT))))
            except Exception as exc:
                falhou = True
                erros += 1
                print(f"  [ERRO] {banda}: {exc}")
                registros.append(reg(item, data, colecao, banda, nuvem, "erro_download", asset.href, str(destino.relative_to(ROOT)), str(exc)))

        if falhou or not cena_completa(item, pasta_item, bandas):
            print("  [INCOMPLETA] cena não conta na meta.")
            continue

        if bool(pcfg.get("gerar_rgb", True)):
            preview = pasta_item / "preview_rgb.jpg"
            try:
                status_preview = gerar_rgb(arquivos, preview, pcfg)
                previews += int(status_preview == "gerado")
                print(f"  [PREVIEW] {status_preview}: {preview.name}")
                registros.append(reg(item, data, colecao, "RGB_PREVIEW", nuvem, status_preview, arquivo=str(preview.relative_to(ROOT))))
            except Exception as exc:
                erros += 1
                print(f"  [ERRO] preview: {exc}")
                registros.append(reg(item, data, colecao, "RGB_PREVIEW", nuvem, "erro_preview", erro=str(exc)))

        novas += 1
        print(f"  [META] nova cena limpa contabilizada: {novas}")

    salvar_csv(csv_path, registros)

    print("\n" + "=" * 76)
    print(f"Candidatos: {candidatos} | novas aprovadas: {novas} | existentes revalidadas: {existentes_aprovadas}")
    print(f"Descartadas por nuvem: {descartadas} | previews novos: {previews} | erros: {erros}")
    print(f"Catálogo: {csv_path.relative_to(ROOT)}")
    return 0 if erros == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
