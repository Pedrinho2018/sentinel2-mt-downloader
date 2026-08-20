from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOGO_PADRAO = ROOT / "catalogo" / "catalogo_patches.csv"
RELATORIO_PADRAO = ROOT / "catalogo" / "relatorio_validacao.json"


def float_ou_none(valor: str):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Audita o catálogo de patches antes da catalogação/ML.")
    parser.add_argument("--catalogo", type=Path, default=CATALOGO_PADRAO)
    parser.add_argument("--relatorio", type=Path, default=RELATORIO_PADRAO)
    args = parser.parse_args()

    if not args.catalogo.exists():
        print(f"[ERRO] Catálogo não encontrado: {args.catalogo}")
        return 2

    with args.catalogo.open("r", encoding="utf-8-sig", newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo))

    ids = [r.get("patch_id", "") for r in linhas]
    duplicados = sorted(k for k, v in Counter(ids).items() if k and v > 1)

    previews_ausentes = []
    coordenadas_invalidas = []
    qualidade_invalida = []
    labels = Counter()

    for r in linhas:
        patch_id = r.get("patch_id", "")
        preview = r.get("preview", "")
        if not preview or not (ROOT / preview).exists():
            previews_ausentes.append(patch_id)

        minlon = float_ou_none(r.get("minlon"))
        maxlon = float_ou_none(r.get("maxlon"))
        minlat = float_ou_none(r.get("minlat"))
        maxlat = float_ou_none(r.get("maxlat"))
        if None in {minlon, maxlon, minlat, maxlat}:
            coordenadas_invalidas.append(patch_id)
        elif not (-180 <= minlon <= 180 and -180 <= maxlon <= 180 and -90 <= minlat <= 90 and -90 <= maxlat <= 90):
            coordenadas_invalidas.append(patch_id)
        elif minlon >= maxlon or minlat >= maxlat:
            coordenadas_invalidas.append(patch_id)

        nuvem = float_ou_none(r.get("cloud_shadow_pct"))
        validos = float_ou_none(r.get("valid_data_pct"))
        if nuvem is None or validos is None or not (0 <= nuvem <= 100) or not (0 <= validos <= 100):
            qualidade_invalida.append(patch_id)

        label = (r.get("label") or "").strip()
        labels[label if label else "SEM_LABEL"] += 1

    total = len(linhas)
    problemas = len(duplicados) + len(previews_ausentes) + len(coordenadas_invalidas) + len(qualidade_invalida)

    relatorio = {
        "total_patches": total,
        "patch_ids_duplicados": duplicados,
        "previews_ausentes": previews_ausentes,
        "coordenadas_invalidas": coordenadas_invalidas,
        "qualidade_invalida": qualidade_invalida,
        "distribuicao_labels": dict(labels),
        "status": "APROVADO" if problemas == 0 else "REPROVADO",
        "quantidade_problemas": problemas,
    }

    args.relatorio.parent.mkdir(parents=True, exist_ok=True)
    args.relatorio.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== VALIDAÇÃO DO DATASET ===")
    print(f"Patches:              {total}")
    print(f"IDs duplicados:       {len(duplicados)}")
    print(f"Previews ausentes:    {len(previews_ausentes)}")
    print(f"Coordenadas inválidas:{len(coordenadas_invalidas)}")
    print(f"Qualidade inválida:   {len(qualidade_invalida)}")
    print(f"Status:               {relatorio['status']}")
    print(f"Relatório:            {args.relatorio.relative_to(ROOT)}")

    return 0 if problemas == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
