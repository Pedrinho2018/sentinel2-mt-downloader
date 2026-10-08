"""Pipeline local: rótulos revisados -> dataset -> validação -> treino YOLO.

Não baixa rótulos nem altera o detector em produção. Ubuntu/Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def executar(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--imagens", type=Path, required=True, help="Patches RGB originais")
    parser.add_argument("--rotulos", type=Path, required=True, help="CSV auditado: image,classe,grupo,split")
    parser.add_argument("--saida", type=Path, default=Path("data/culturas"))
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--model", default="yolo11n-cls.pt")
    parser.add_argument("--dry-run", action="store_true", help="Só valida dados, sem copiar ou treinar")
    args = parser.parse_args()
    if args.epochs <= 0 or args.batch <= 0:
        parser.error("epochs e batch devem ser maiores que zero")
    tools = Path(__file__).resolve().parent
    prep = [sys.executable, str(tools / "preparar_dataset_culturas.py"),
            "--imagens", str(args.imagens), "--rotulos", str(args.rotulos),
            "--saida", str(args.saida)]
    if args.dry_run:
        executar(prep + ["--dry-run"])
        print("Simulação concluída; nenhum arquivo copiado ou modelo treinado.")
        return
    executar(prep)
    treino = [sys.executable, str(tools / "treinar_culturas_yolo.py"),
              "--data", str(args.saida), "--epochs", str(args.epochs),
              "--batch", str(args.batch), "--device", args.device,
              "--model", args.model]
    executar(treino)
    print(json.dumps({"status": "treino_finalizado", "saida_dataset": str(args.saida)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
