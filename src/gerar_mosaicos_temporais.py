"""Script legado desativado.

A composição atual acontece diretamente dentro de cada patch via Data API NPY.
Use src/pipeline.py.
"""


def main() -> int:
    print("[DESATIVADO] O mosaico gigante não faz mais parte do pipeline.")
    print(r"Use: python src\pipeline.py --mes 2026-04 --max-patches 5 --limpar")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
