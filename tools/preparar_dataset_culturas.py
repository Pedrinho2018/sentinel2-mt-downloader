"""Organiza patches RGB rotulados e revisados para classificacao YOLO.

CSV: image,classe,grupo,split
- image: caminho relativo ao diretorio --imagens
- grupo: identificador de talhao ou conjunto espacial/temporal
- split: train, val ou test, definido pelo curador (evita vazamento).
Nao cria rotulos automaticamente nem baixa MapBiomas.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

CLASSES = {"soja", "milho", "algodao", "outras"}
SPLITS = {"train", "val", "test"}
EXT = {".png", ".jpg", ".jpeg"}


def preparar(imagens: Path, rotulos: Path, destino: Path, dry_run: bool) -> dict:
    imagens = imagens.resolve()
    destino = destino.resolve()
    if not imagens.is_dir() or not rotulos.is_file():
        raise ValueError("Pasta de imagens ou CSV de rotulos inexistente.")
    if destino == imagens or imagens in destino.parents or destino in imagens.parents:
        raise ValueError("Destino e imagens de origem nao podem estar aninhados.")
    with rotulos.open(newline="", encoding="utf-8-sig") as f:
        leitor = csv.DictReader(f)
        colunas = {"image", "classe", "grupo", "split"}
        if not leitor.fieldnames or not colunas.issubset(leitor.fieldnames):
            raise ValueError("CSV exige: image,classe,grupo,split")
        rows = list(leitor)
    if not rows:
        raise ValueError("CSV vazio")
    grupos: dict[str, str] = {}
    trabalhos = []
    nomes_saida = set()
    counts = Counter()
    for i, row in enumerate(rows, start=2):
        classe = (row.get("classe") or "").strip().lower()
        split = (row.get("split") or "").strip().lower()
        grupo = (row.get("grupo") or "").strip()
        nome = (row.get("image") or "").strip()
        if classe not in CLASSES or split not in SPLITS or not grupo or not nome:
            raise ValueError(f"Linha {i}: classe/split/grupo/image invalido")
        origem = (imagens / nome).resolve()
        if not origem.is_relative_to(imagens) or not origem.is_file() or origem.suffix.lower() not in EXT:
            raise ValueError(f"Linha {i}: imagem ausente, invalida ou fora da raiz: {nome}")
        anterior = grupos.setdefault(grupo, split)
        if anterior != split:
            raise ValueError(f"Linha {i}: grupo {grupo} em splits diferentes (vazamento)")
        digest = hashlib.sha256(nome.encode("utf-8")).hexdigest()[:12]
        saida = destino / split / classe / (digest + "_" + origem.name)
        if saida in nomes_saida:
            raise ValueError(f"Linha {i}: imagem duplicada")
        nomes_saida.add(saida)
        trabalhos.append((origem, saida))
        counts[(split, classe)] += 1
    for split in ("train", "val"):
        for classe in sorted(CLASSES):
            if counts[(split, classe)] < 1:
                raise ValueError(f"Sem dados para {split}/{classe}")
    if destino.exists() and any(destino.iterdir()):
        raise ValueError("Destino deve estar vazio para evitar datasets misturados")
    if not dry_run:
        for origem, saida in trabalhos:
            saida.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origem, saida)
        (destino / "dataset_manifest.json").write_text(
            json.dumps({
                "total": len(trabalhos),
                "counts": {f"{s}/{c}": n for (s, c), n in sorted(counts.items())},
                "groups": len(grupos),
                "source_csv": str(rotulos.resolve()),
                "warning": "Rotulos exigem auditoria humana e compatibilidade temporal/espectral."
            }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"imagens": len(trabalhos), "grupos": len(grupos),
            "contagem": {f"{s}/{c}": n for (s, c), n in sorted(counts.items())},
            "simulacao": dry_run}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--imagens", type=Path, required=True)
    p.add_argument("--rotulos", type=Path, required=True)
    p.add_argument("--saida", type=Path, default=Path("data/culturas"))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    try:
        print(json.dumps(preparar(args.imagens, args.rotulos, args.saida, args.dry_run),
                         ensure_ascii=False, indent=2))
    except (ValueError, OSError, csv.Error) as exc:
        p.error(str(exc))


if __name__ == "__main__":
    main()
