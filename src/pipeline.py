from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pipeline Sentinel-2 para soja: teste enxuto ou produção da fila de 5.000 imagens."
    )
    parser.add_argument("--inicio")
    parser.add_argument("--fim")
    parser.add_argument("--mes", help="Processa apenas YYYY-MM no modo de teste")
    parser.add_argument("--max-patches", type=int, default=20, help="Limite por mês no modo de teste")
    parser.add_argument(
        "--dataset-5000",
        action="store_true",
        help="Gera a fila balanceada de 5.000 imagens entre setembro e abril.",
    )
    parser.add_argument("--meta-total", type=int, help="Sobrescreve a meta do modo dataset-5000")
    parser.add_argument("--sem-mascara-agricola", action="store_true")
    parser.add_argument("--limpar", action="store_true")
    args = parser.parse_args()

    if args.dataset_5000:
        cmd = [sys.executable, str(SRC / "gerar_dataset_5000.py")]
        if args.meta_total:
            cmd += ["--meta-total", str(args.meta_total)]
        if args.mes:
            cmd += ["--mes", args.mes]
        if args.sem_mascara_agricola:
            cmd.append("--sem-mascara-agricola")
        titulo = "PIPELINE SOJA | PRODUÇÃO DATASET 5000"
    else:
        cmd = [
            sys.executable,
            str(SRC / "gerar_dataset_soja.py"),
            "--max-patches",
            str(args.max_patches),
        ]
        if args.inicio:
            cmd += ["--inicio", args.inicio]
        if args.fim:
            cmd += ["--fim", args.fim]
        if args.mes:
            cmd += ["--mes", args.mes]
        titulo = "PIPELINE SOJA | TESTE ENXUTO"

    if args.limpar:
        cmd.append("--limpar")

    print("=" * 88)
    print(titulo)
    print("=" * 88)
    print("Executando:", " ".join(cmd))

    try:
        subprocess.run(cmd, cwd=ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 3 and args.dataset_5000:
            print(
                "\n[PARCIAL] A coleta foi preservada. Rode novamente SEM --limpar para continuar "
                "até completar a meta."
            )
        else:
            print(f"\n[ERRO] Pipeline interrompido com código {exc.returncode}.")
        return exc.returncode or 1

    print("\n[OK] Pipeline concluído.")
    if args.dataset_5000:
        print("Imagens: data/dataset_soja_5000/<MES>/<PATCH>/preview_rgb.jpg")
        print("Fila: catalogo/fila_catalogacao_5000.csv")
        print("Catálogo: catalogo/catalogo_soja_5000.csv")
    else:
        print("Confira: data/patches_soja/<MES>/<PATCH>/preview_rgb.jpg")
        print("Catálogo: catalogo/catalogo_soja.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
