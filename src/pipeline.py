from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def executar(etapa: str, argumentos: list[str]) -> None:
    comando = [sys.executable, str(SRC / etapa), *argumentos]
    print("\n" + "=" * 84)
    print("EXECUTANDO:", " ".join(comando))
    print("=" * 84)
    subprocess.run(comando, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Executa o pipeline temporal: série -> mosaico -> patches -> validação."
    )
    parser.add_argument("--tile", help="Tile específico, ex.: 014018")
    parser.add_argument("--max-tiles", type=int, default=1, help="0 = todos")
    parser.add_argument("--max-patches", type=int, default=20, help="0 = todos")
    parser.add_argument("--cenas-por-mes", type=int, default=2)
    parser.add_argument("--limpar", action="store_true", help="Limpa mosaicos/patches antes de recriar")
    parser.add_argument("--pular-download", action="store_true")
    args = parser.parse_args()

    try:
        if not args.pular_download:
            serie_args = [
                "--max-tiles",
                str(args.max_tiles),
                "--cenas-por-mes",
                str(args.cenas_por_mes),
            ]
            if args.tile:
                serie_args += ["--tile", args.tile]
            executar("baixar_series_temporais.py", serie_args)

        mosaico_args: list[str] = []
        if args.tile:
            mosaico_args += ["--tile", args.tile]
        if args.limpar:
            mosaico_args.append("--limpar-saida")
        executar("gerar_mosaicos_temporais.py", mosaico_args)

        patch_args = ["--max-patches", str(args.max_patches)]
        if args.tile:
            patch_args += ["--tile", args.tile]
        if args.limpar:
            patch_args.append("--limpar-saida")
        executar("gerar_patches.py", patch_args)

        executar("validar_dataset.py", [])

    except subprocess.CalledProcessError as exc:
        print(f"\n[PIPELINE INTERROMPIDO] Uma etapa retornou código {exc.returncode}.")
        return exc.returncode or 1

    print("\n" + "=" * 84)
    print("PIPELINE TEMPORAL CONCLUÍDO COM SUCESSO")
    print("=" * 84)
    print("Confira os previews em data/patches e os catálogos em catalogo/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
