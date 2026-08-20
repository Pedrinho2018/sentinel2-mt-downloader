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
    if not caminho.exists():
        raise FileNotFoundError(f"Arquivo de configuração não encontrado: {caminho}")

    with caminho.open("r", encoding="utf-8") as arquivo:
        return yaml.safe_load(arquivo)


def data_item(item) -> str:
    if item.datetime:
        return item.datetime.strftime("%Y-%m-%d")

    inicio = item.properties.get("start_datetime")
    if inicio:
        try:
            return datetime.fromisoformat(inicio.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except ValueError:
            return inicio[:10]

    return "sem_data"


def extensao_da_url(url: str) -> str:
    sufixo = Path(urlparse(url).path).suffix
    return sufixo if sufixo else ".tif"


def localizar_asset(item, banda: str):
    if banda in item.assets:
        return item.assets[banda]

    banda_normalizada = banda.casefold()
    for chave, asset in item.assets.items():
        if chave.casefold() == banda_normalizada:
            return asset

    return None


def baixar_arquivo(
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
    if parcial.exists():
        parcial.unlink()

    try:
        with sessao.get(url, stream=True, timeout=(20, timeout)) as resposta:
            resposta.raise_for_status()
            tamanho = int(resposta.headers.get("content-length", 0))
            bloco = max(1, chunk_mb) * 1024 * 1024

            with parcial.open("wb") as arquivo, tqdm(
                total=tamanho if tamanho > 0 else None,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc=destino.name,
                leave=False,
            ) as barra:
                for pedaco in resposta.iter_content(chunk_size=bloco):
                    if not pedaco:
                        continue
                    arquivo.write(pedaco)
                    barra.update(len(pedaco))

        parcial.replace(destino)
        return "baixado"

    except Exception:
        if parcial.exists():
            parcial.unlink()
        raise


def ler_banda_para_preview(caminho: Path, tamanho_max_px: int) -> tuple[np.ndarray, np.ndarray]:
    with rasterio.open(caminho) as src:
        maior_dimensao = max(src.width, src.height)
        escala = min(1.0, tamanho_max_px / maior_dimensao)
        largura = max(1, int(src.width * escala))
        altura = max(1, int(src.height * escala))

        dados = src.read(
            1,
            out_shape=(altura, largura),
            resampling=Resampling.bilinear,
        ).astype(np.float32)

        mascara = np.isfinite(dados)
        if src.nodata is not None:
            mascara &= dados != src.nodata
        else:
            mascara &= dados != 0

    return dados, mascara


def esticar_banda(
    dados: np.ndarray,
    mascara: np.ndarray,
    percentil_min: float,
    percentil_max: float,
) -> np.ndarray:
    saida = np.zeros(dados.shape, dtype=np.uint8)

    validos = dados[mascara]
    if validos.size == 0:
        return saida

    minimo, maximo = np.percentile(validos, [percentil_min, percentil_max])

    if not np.isfinite(minimo) or not np.isfinite(maximo) or maximo <= minimo:
        minimo = float(validos.min())
        maximo = float(validos.max())

    if maximo <= minimo:
        return saida

    normalizado = (dados - minimo) / (maximo - minimo)
    normalizado = np.clip(normalizado, 0, 1)
    saida = (normalizado * 255).astype(np.uint8)
    saida[~mascara] = 0
    return saida


def gerar_preview_rgb(
    arquivos_item: dict[str, Path],
    destino: Path,
    tamanho_max_px: int,
    percentil_min: float,
    percentil_max: float,
    qualidade_jpeg: int,
) -> str:
    necessarias = ["B04", "B03", "B02"]
    faltando = [banda for banda in necessarias if banda not in arquivos_item or not arquivos_item[banda].exists()]

    if faltando:
        return f"faltando_{'_'.join(faltando)}"

    vermelho, mask_r = ler_banda_para_preview(arquivos_item["B04"], tamanho_max_px)
    verde, mask_g = ler_banda_para_preview(arquivos_item["B03"], tamanho_max_px)
    azul, mask_b = ler_banda_para_preview(arquivos_item["B02"], tamanho_max_px)

    if vermelho.shape != verde.shape or vermelho.shape != azul.shape:
        return "dimensoes_incompativeis"

    mascara = mask_r & mask_g & mask_b

    r = esticar_banda(vermelho, mascara, percentil_min, percentil_max)
    g = esticar_banda(verde, mascara, percentil_min, percentil_max)
    b = esticar_banda(azul, mascara, percentil_min, percentil_max)

    rgb = np.dstack((r, g, b))
    destino.parent.mkdir(parents=True, exist_ok=True)

    Image.fromarray(rgb, mode="RGB").save(
        destino,
        format="JPEG",
        quality=max(1, min(100, qualidade_jpeg)),
        optimize=True,
    )

    return "gerado"


def salvar_catalogo(caminho: Path, registros: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)

    campos = [
        "id",
        "data",
        "colecao",
        "banda",
        "url",
        "arquivo",
        "status",
        "erro",
    ]

    with caminho.open("w", newline="", encoding="utf-8-sig") as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=campos)
        writer.writeheader()
        writer.writerows(registros)


def criar_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Busca, cataloga e baixa imagens Sentinel-2 do INPE para Mato Grosso."
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PADRAO)
    parser.add_argument("--inicio", help="Data inicial YYYY-MM-DD. Sobrescreve o config.yaml.")
    parser.add_argument("--fim", help="Data final YYYY-MM-DD. Sobrescreve o config.yaml.")
    parser.add_argument(
        "--max-itens",
        type=int,
        help="Máximo de itens STAC. Use 0 para processar todos os encontrados.",
    )
    parser.add_argument(
        "--baixar",
        action="store_true",
        help="Baixa os arquivos. Sem esta opção, apenas cataloga.",
    )
    return parser.parse_args()


def main() -> int:
    args = criar_argumentos()
    config = carregar_config(args.config)

    stac_url = config["stac"]["url"]
    colecao = config["stac"]["colecao"]
    bbox = config["area"]["bbox"]
    bandas = config["bandas"]

    inicio = args.inicio or config["periodo"]["inicio"]
    fim = args.fim or config["periodo"]["fim"]

    if args.max_itens is None:
        max_itens = int(config["download"].get("max_itens_teste", 5))
    else:
        max_itens = args.max_itens

    pasta_download = ROOT / config["download"]["pasta"]
    caminho_catalogo = ROOT / config["download"]["catalogo"]
    timeout = int(config["download"].get("timeout_segundos", 120))
    chunk_mb = int(config["download"].get("chunk_mb", 1))

    preview_cfg = config.get("preview", {})
    gerar_rgb = bool(preview_cfg.get("gerar_rgb", True))
    tamanho_max_px = int(preview_cfg.get("tamanho_max_px", 1600))
    percentil_min = float(preview_cfg.get("percentil_min", 2))
    percentil_max = float(preview_cfg.get("percentil_max", 98))
    qualidade_jpeg = int(preview_cfg.get("qualidade_jpeg", 92))

    print("=" * 62)
    print(" Sentinel-2 MT Downloader | INPE / Brazil Data Cube")
    print("=" * 62)
    print(f"Área:      {config['area']['nome']} ({config['area']['uf']})")
    print(f"Coleção:   {colecao}")
    print(f"Período:   {inicio} até {fim}")
    print(f"Bandas:    {', '.join(bandas)}")
    print(f"Preview:   {'RGB automático' if gerar_rgb else 'desativado'}")
    print(f"Download:  {'SIM' if args.baixar else 'NÃO (somente catálogo)'}")
    print(f"Limite:    {'TODOS' if max_itens == 0 else max_itens}")
    print()

    print("[1/4] Conectando ao STAC do INPE...")
    cliente = Client.open(stac_url)

    print("[2/4] Pesquisando itens que intersectam Mato Grosso...")
    pesquisa = cliente.search(
        collections=[colecao],
        bbox=bbox,
        datetime=f"{inicio}/{fim}",
    )

    itens = []
    for item in pesquisa.items():
        itens.append(item)
        if max_itens > 0 and len(itens) >= max_itens:
            break

    if not itens:
        print("Nenhum item encontrado para os filtros informados.")
        return 1

    print(f"Encontrados para processamento: {len(itens)}")
    print("[3/4] Catalogando e processando arquivos...")

    registros: list[dict] = []
    sessao = requests.Session()
    sessao.headers.update({"User-Agent": "sentinel2-mt-downloader/1.1"})

    for indice, item in enumerate(itens, start=1):
        data = data_item(item)
        print(f"\n[{indice}/{len(itens)}] {item.id} | {data}")

        if indice == 1:
            print("Assets disponíveis:", ", ".join(sorted(item.assets.keys())))

        arquivos_item: dict[str, Path] = {}
        pasta_item = pasta_download / data / item.id

        for banda in bandas:
            asset = localizar_asset(item, banda)

            if asset is None:
                print(f"  [AVISO] Banda/asset {banda} não encontrado.")
                registros.append(
                    {
                        "id": item.id,
                        "data": data,
                        "colecao": colecao,
                        "banda": banda,
                        "url": "",
                        "arquivo": "",
                        "status": "asset_nao_encontrado",
                        "erro": "",
                    }
                )
                continue

            extensao = extensao_da_url(asset.href)
            destino = pasta_item / f"{banda}{extensao}"
            status = "catalogado"
            erro = ""

            if args.baixar:
                try:
                    status = baixar_arquivo(
                        sessao=sessao,
                        url=asset.href,
                        destino=destino,
                        timeout=timeout,
                        chunk_mb=chunk_mb,
                    )
                    arquivos_item[banda] = destino
                    print(f"  [OK] {banda}: {status}")
                except Exception as exc:
                    status = "erro_download"
                    erro = str(exc)
                    print(f"  [ERRO] {banda}: {exc}")
            else:
                print(f"  [CATÁLOGO] {banda}")

            registros.append(
                {
                    "id": item.id,
                    "data": data,
                    "colecao": colecao,
                    "banda": banda,
                    "url": asset.href,
                    "arquivo": str(destino.relative_to(ROOT)),
                    "status": status,
                    "erro": erro,
                }
            )

        if args.baixar and gerar_rgb:
            preview_destino = pasta_item / "preview_rgb.jpg"
            try:
                status_preview = gerar_preview_rgb(
                    arquivos_item=arquivos_item,
                    destino=preview_destino,
                    tamanho_max_px=tamanho_max_px,
                    percentil_min=percentil_min,
                    percentil_max=percentil_max,
                    qualidade_jpeg=qualidade_jpeg,
                )
                print(f"  [PREVIEW] RGB: {status_preview} -> {preview_destino.name}")
                registros.append(
                    {
                        "id": item.id,
                        "data": data,
                        "colecao": colecao,
                        "banda": "RGB_PREVIEW",
                        "url": "",
                        "arquivo": str(preview_destino.relative_to(ROOT)),
                        "status": status_preview,
                        "erro": "",
                    }
                )
            except Exception as exc:
                print(f"  [ERRO] Preview RGB: {exc}")
                registros.append(
                    {
                        "id": item.id,
                        "data": data,
                        "colecao": colecao,
                        "banda": "RGB_PREVIEW",
                        "url": "",
                        "arquivo": str(preview_destino.relative_to(ROOT)),
                        "status": "erro_preview",
                        "erro": str(exc),
                    }
                )

    print("\n[4/4] Salvando catálogo CSV...")
    salvar_catalogo(caminho_catalogo, registros)

    baixados = sum(r["status"] == "baixado" for r in registros)
    existentes = sum(r["status"] == "ja_existia" for r in registros)
    previews = sum(r["status"] == "gerado" and r["banda"] == "RGB_PREVIEW" for r in registros)
    erros = sum(r["status"] in {"erro_download", "erro_preview"} for r in registros)

    print("\n" + "=" * 62)
    print(" FINALIZADO")
    print("=" * 62)
    print(f"Itens STAC:        {len(itens)}")
    print(f"Registros CSV:     {len(registros)}")
    print(f"Arquivos baixados: {baixados}")
    print(f"Já existentes:     {existentes}")
    print(f"Previews RGB:      {previews}")
    print(f"Erros:             {erros}")
    print(f"Catálogo:          {caminho_catalogo.relative_to(ROOT)}")

    if not args.baixar:
        print("\nModo seguro: nenhum GeoTIFF foi baixado.")
        print("Para baixar 5 itens: python src\\baixar_inpe_mt.py --baixar --max-itens 5")
        print("Para todos os itens: python src\\baixar_inpe_mt.py --baixar --max-itens 0")

    return 0 if erros == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
