"""Classifica patches RGB com um modelo YOLO de CULTURAS, sem alterar o detector legado.

Exemplo:
python tools/classificar_culturas.py --modelo runs/culturas/piloto-rgb/weights/best.pt --imagens data/dataset --saida runs/culturas/resultado_culturas.csv

Não substitui delimitação geográfica de talhões; resultados são por patch.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

CLASSES = {"soja", "milho", "algodao", "outras"}
EXT = {".png", ".jpg", ".jpeg"}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--modelo", type=Path, required=True)
    p.add_argument("--imagens", type=Path, required=True)
    p.add_argument("--saida", type=Path, default=Path("runs/culturas/resultado_culturas.csv"))
    p.add_argument("--limiar", type=float, default=0.65)
    p.add_argument("--device", default="cpu")
    args = p.parse_args()
    if not 0 < args.limiar <= 1:
        p.error("limiar deve estar entre 0 e 1")
    modelo_path = args.modelo.resolve()
    if not modelo_path.is_file() or modelo_path.suffix.lower() != ".pt":
        p.error("Modelo YOLO .pt inexistente")
    raiz = args.imagens.resolve()
    if not raiz.is_dir():
        p.error("Diretorio de imagens inexistente")
    imagens = sorted(f for f in raiz.rglob("*") if f.is_file() and f.suffix.lower() in EXT)
    if not imagens:
        p.error("Nenhuma imagem RGB encontrada")
    from ultralytics import YOLO
    model = YOLO(str(modelo_path))
    if model.task != "classify":
        p.error("O peso informado não é classificador YOLO; não use o best.pt do detector legado")
    names = model.names
    nomes = set(str(v).strip().lower() for v in names.values())
    if nomes != CLASSES:
        p.error(f"Classes incompatíveis: {sorted(nomes)}; esperado: {sorted(CLASSES)}")
    linhas = []
    for imagem in imagens:
        resultado = model.predict(str(imagem), device=args.device, verbose=False)[0]
        probs = resultado.probs
        if probs is None:
            raise RuntimeError(f"Modelo não retornou probabilidades para {imagem}")
        indice = int(probs.top1)
        confianca = float(probs.top1conf)
        classe = str(names[indice]).strip().lower()
        linhas.append({
            "imagem": str(imagem.relative_to(raiz)),
            "cultura_prevista": classe if confianca >= args.limiar else "indeterminada",
            "classe_mais_provavel": classe,
            "confianca": f"{confianca:.6f}",
            "revisao_humana": "pendente",
        })
    saida = args.saida.resolve()
    saida.parent.mkdir(parents=True, exist_ok=True)
    if saida.exists():
        p.error("Arquivo de saída já existe; escolha outro nome para preservar histórico")
    with saida.open("x", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(linhas[0]))
        writer.writeheader()
        writer.writerows(linhas)
    hash_peso = hashlib.sha256(modelo_path.read_bytes()).hexdigest()
    saida.with_suffix(".metadata.json").write_text(json.dumps({
        "modelo_sha256": hash_peso, "modelo": str(modelo_path),
        "quantidade": len(linhas), "limiar": args.limiar,
        "estado": "predicoes_nao_validadas",
        "observacao": "Predicoes por patch RGB; nao representam limites de talhoes ou hectares."
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(linhas)} patches classificados (resultados sujeitos a revisão): {saida}")


if __name__ == "__main__":
    main()
