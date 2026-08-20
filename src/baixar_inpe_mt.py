from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml
from pystac_client import Client
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

    print("=" * 62)
    print(" Sentinel-2 MT Downloader | INPE / Brazil Data Cube")
    print("=" * 62)
    print(f"Área:      {config['area']['nome']} ({config['area']['uf']})")
    print(f"Coleção:   {colecao}")
    print(f"Período:   {inicio} até {fim}")
    print(f"Bandas:    {', '.join(bandas)}")
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
    sessao.headers.update({"User-Agent": "sentinel2-mt-downloader/1.0"})

    for indice, item in enumerate(itens, start=1):
        data = data_item(item)
        print(f"\n[{indice}/{len(itens)}] {item.id} | {data}")

        if indice == 1:
            print("Assets disponíveis:", ", ".join(sorted(item.assets.keys())))

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
            destino = pasta_download / data / item.id / f"{banda}{extensao}"
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

    print("\n[4/4] Salvando catálogo CSV...")
    salvar_catalogo(caminho_catalogo, registros)

    baixados = sum(r["status"] == "baixado" for r in registros)
    existentes = sum(r["status"] == "ja_existia" for r in registros)
    erros = sum(r["status"] == "erro_download" for r in registros)

    print("\n" + "=" * 62)
    print(" FINALIZADO")
    print("=" * 62)
    print(f"Itens STAC:       {len(itens)}")
    print(f"Registros CSV:    {len(registros)}")
    print(f"Arquivos baixados:{baixados}")
    print(f"Já existentes:    {existentes}")
    print(f"Erros:             {erros}")
    print(f"Catálogo:          {caminho_catalogo.relative_to(ROOT)}")

    if not args.baixar:
        print("\nModo seguro: nenhum GeoTIFF foi baixado.")
        print("Para baixar 5 itens: python src\\baixar_inpe_mt.py --baixar --max-itens 5")
        print("Para todos os itens: python src\\baixar_inpe_mt.py --baixar --max-itens 0")

    return 0 if erros == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
