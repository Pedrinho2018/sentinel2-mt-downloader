"""Script legado desativado.

A coleta atual usa STAC + Planetary Computer Data API NPY por patch.
Use src/pipeline.py.
"""


def main() -> int:
    print("[DESATIVADO] Este script pertence ao pipeline antigo de cenas completas.")
    print(r"Use: python src\pipeline.py --mes 2026-04 --max-patches 5 --limpar")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
