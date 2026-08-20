from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CATALOGO_PADRAO = ROOT / "catalogo" / "catalogo_patches.csv"
RELATORIO_PADRAO = ROOT / "catalogo" / "relatorio_validacao.json"
CONFIG_PADRAO = ROOT / "config" / "config.yaml"


def float_ou_none(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audita patches derivados de mosaicos temporais antes da rotulagem/ML."
    )
    parser.add_argument("--catalogo", type=Path, default=CATALOGO_PADRAO)
    parser.add_argument("--relatorio", type=Path, default=RELATORIO_PADRAO)
    parser.add_argument("--config", type=Path, default=CONFIG_PADRAO)
    args = parser.parse_args()

    if not args.catalogo.exists():
        print(f"[ERRO] Catálogo não encontrado: {args.catalogo}")
        return 2

    with args.config.open("r", encoding="utf-8") as arquivo:
        cfg = yaml.safe_load(arquivo)
    valid_min = float(cfg.get("patches", {}).get("dados_validos_min_pct", 99))
    obs2_min = float(cfg.get("patches", {}).get("obs_2plus_min_pct", 0))

    with args.catalogo.open("r", encoding="utf-8-sig", newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo))

    ids = [r.get("patch_id", "") for r in linhas]
    duplicados = sorted(k for k, v in Counter(ids).items() if k and v > 1)

    previews_ausentes = []
    coordenadas_invalidas = []
    qualidade_invalida = []
    rastreabilidade_invalida = []
    labels = Counter()
    por_tile = Counter()
    por_mes = Counter()
    por_mosaico = Counter()

    for r in linhas:
        patch_id = r.get("patch_id", "")
        preview = r.get("preview", "")
        if not preview or not (ROOT / preview).exists():
            previews_ausentes.append(patch_id)

        mosaic_id = (r.get("mosaic_id") or "").strip()
        tile_id = (r.get("tile_id") or "").strip()
        mes = (r.get("mes") or "").strip()
        if not patch_id or not mosaic_id or not tile_id or len(mes) != 7:
            rastreabilidade_invalida.append(patch_id)
        else:
            por_tile[tile_id] += 1
            por_mes[mes] += 1
            por_mosaico[mosaic_id] += 1

        minlon = float_ou_none(r.get("minlon"))
        maxlon = float_ou_none(r.get("maxlon"))
        minlat = float_ou_none(r.get("minlat"))
        maxlat = float_ou_none(r.get("maxlat"))
        if None in {minlon, maxlon, minlat, maxlat}:
            coordenadas_invalidas.append(patch_id)
        elif not (
            -180 <= minlon <= 180
            and -180 <= maxlon <= 180
            and -90 <= minlat <= 90
            and -90 <= maxlat <= 90
        ):
            coordenadas_invalidas.append(patch_id)
        elif minlon >= maxlon or minlat >= maxlat:
            coordenadas_invalidas.append(patch_id)

        validos = float_ou_none(r.get("valid_data_pct"))
        obs2 = float_ou_none(r.get("obs_2plus_pct"))
        if (
            validos is None
            or obs2 is None
            or not (0 <= validos <= 100)
            or not (0 <= obs2 <= 100)
            or validos < valid_min
            or obs2 < obs2_min
        ):
            qualidade_invalida.append(patch_id)

        label = (r.get("label") or "").strip()
        labels[label if label else "SEM_LABEL"] += 1

    total = len(linhas)
    problemas = (
        len(duplicados)
        + len(previews_ausentes)
        + len(coordenadas_invalidas)
        + len(qualidade_invalida)
        + len(rastreabilidade_invalida)
    )

    relatorio = {
        "total_patches": total,
        "criterios": {
            "dados_validos_min_pct": valid_min,
            "obs_2plus_min_pct": obs2_min,
        },
        "patch_ids_duplicados": duplicados,
        "previews_ausentes": previews_ausentes,
        "coordenadas_invalidas": coordenadas_invalidas,
        "qualidade_invalida": qualidade_invalida,
        "rastreabilidade_invalida": rastreabilidade_invalida,
        "distribuicao_labels": dict(labels),
        "patches_por_tile": dict(por_tile),
        "patches_por_mes": dict(por_mes),
        "patches_por_mosaico": dict(por_mosaico),
        "status": "APROVADO" if problemas == 0 else "REPROVADO",
        "quantidade_problemas": problemas,
    }

    args.relatorio.parent.mkdir(parents=True, exist_ok=True)
    args.relatorio.write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("=== VALIDAÇÃO DO DATASET TEMPORAL ===")
    print(f"Patches:                 {total}")
    print(f"IDs duplicados:          {len(duplicados)}")
    print(f"Previews ausentes:       {len(previews_ausentes)}")
    print(f"Coordenadas inválidas:   {len(coordenadas_invalidas)}")
    print(f"Qualidade inválida:      {len(qualidade_invalida)}")
    print(f"Rastreabilidade inválida:{len(rastreabilidade_invalida)}")
    print(f"Tiles representados:     {len(por_tile)}")
    print(f"Meses representados:     {len(por_mes)}")
    print(f"Status:                  {relatorio['status']}")
    print(f"Relatório:               {args.relatorio.relative_to(ROOT)}")

    return 0 if problemas == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
