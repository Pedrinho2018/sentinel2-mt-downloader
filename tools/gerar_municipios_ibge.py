"""Gera a lista local de municípios usada na busca da GUI (sem rede em tempo de uso).

Fonte: API de serviços de dados do IBGE (localidades v1 e malhas v3). Para cada
município grava código IBGE, nome, UF e o retângulo envolvente em EPSG:4326
calculado a partir da malha municipal de qualidade intermediária. O retângulo é
aproximado e serve para posicionar o mapa e iniciar a busca de imagens; ele não
substitui o limite oficial do município.

Uso (requer internet; baixa poucos MB do IBGE):

    .venv/bin/python tools/gerar_municipios_ibge.py --uf MT
"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from sentinel2_mt.area import bbox_de_geojson  # noqa: E402


BASE = "https://servicodados.ibge.gov.br/api"
URL_NOMES = BASE + "/v1/localidades/estados/{uf}/municipios"
URL_MALHA = (
    BASE + "/v3/malhas/estados/{uf}?formato=application/vnd.geo%2Bjson"
    "&intrarregiao=municipio&qualidade=intermediaria"
)
SAIDA_PADRAO = RAIZ / "assets" / "municipios_ibge.json"


def montar_lista(uf: str, nomes: list[dict], malha: dict) -> list[dict]:
    """Combina nomes e malha do IBGE; falha se faltar a geometria de algum município."""
    por_codigo = {str(item["id"]): str(item["nome"]) for item in nomes}
    municipios: list[dict] = []
    for feicao in malha.get("features", []):
        codigo = str((feicao.get("properties") or {}).get("codarea", ""))
        if codigo not in por_codigo:
            continue
        bbox, _ = bbox_de_geojson(feicao)
        municipios.append(
            {
                "codigo_ibge": codigo,
                "nome": por_codigo[codigo],
                "uf": uf.upper(),
                "bbox": [round(valor, 6) for valor in bbox],
            }
        )
    faltantes = set(por_codigo) - {item["codigo_ibge"] for item in municipios}
    if faltantes:
        raise ValueError(f"Malha sem {len(faltantes)} município(s) de {uf}: {sorted(faltantes)[:5]}")
    return sorted(municipios, key=lambda item: item["nome"])


def _baixar_json(url: str) -> object:
    if not url.startswith(BASE + "/"):
        raise ValueError("URL fora da API do IBGE")
    requisicao = Request(url, headers={"User-Agent": "sentinel2-mt-downloader"})
    with urlopen(requisicao, timeout=60) as resposta:  # noqa: S310 - URL fixa do IBGE
        return json.loads(resposta.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uf", action="append", default=None, help="Sigla da UF; pode repetir")
    parser.add_argument("--saida", type=Path, default=SAIDA_PADRAO)
    args = parser.parse_args()
    ufs = [uf.upper() for uf in (args.uf or ["MT"])]
    todos: list[dict] = []
    for uf in ufs:
        if len(uf) != 2 or not uf.isalpha():
            parser.error(f"UF inválida: {uf}")
        nomes = _baixar_json(URL_NOMES.format(uf=uf))
        malha = _baixar_json(URL_MALHA.format(uf=uf))
        lista = montar_lista(uf, nomes, malha)
        print(f"{uf}: {len(lista)} municípios")
        todos.extend(lista)
    documento = {
        "fonte": "IBGE - API de localidades v1 e malhas v3 (qualidade intermediária)",
        "gerado_em": date.today().isoformat(),
        "crs": "EPSG:4326",
        "observacao": "bbox aproximado; não substitui o limite oficial do município",
        "municipios": todos,
    }
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    temporario = args.saida.with_suffix(".tmp")
    temporario.write_text(json.dumps(documento, ensure_ascii=False, indent=1), encoding="utf-8")
    temporario.replace(args.saida)
    print(f"Lista gravada em {args.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
