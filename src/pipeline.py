from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pipeline enxuto para soja: lê Sentinel-2 L2A remoto e salva somente patches limpos."
    )
    parser.add_argument("--inicio")
    parser.add_argument("--fim")
    parser.add_argument("--mes", help="Processa apenas YYYY-MM")
    parser.add_argument("--max-patches", type=int, default=20, help="Limite de patches por mês; 0 = todos")
    parser.add_argument("--limpar", action="store_true")
    args = parser.parse_args()

    cmd = [sys.executable, str(SRC / "gerar_dataset_soja.py"), "--max-patches", str(args.max_patches)]
    if args.inicio:
        cmd += ["--inicio", args.inicio]
    if args.fim:
        cmd += ["--fim", args.fim]
    if args.mes:
        cmd += ["--mes", args.mes]
    if args.limpar:
        cmd.append("--limpar")

    print("=" * 84)
    print("PIPELINE SOJA | sem download de cenas completas")
    print("=" * 84)
    print("Executando:", " ".join(cmd))

    try:
        subprocess.run(cmd, cwd=ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"\n[ERRO] Pipeline interrompido com código {exc.returncode}.")
        return exc.returncode or 1

    print("\n[OK] Pipeline concluído.")
    print("Confira: data/patches_soja/<MES>/<PATCH>/preview_rgb.jpg")
    print("Catálogo: catalogo/catalogo_soja.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
