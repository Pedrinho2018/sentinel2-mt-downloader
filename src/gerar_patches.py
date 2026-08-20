"""Script legado desativado.

O pipeline atual já compõe e salva os patches limpos diretamente.
Use src/pipeline.py.
"""


def main() -> int:
    print("[DESATIVADO] Este gerador de patches pertence ao pipeline antigo.")
    print(r"Use: python src\pipeline.py --mes 2026-04 --max-patches 5 --limpar")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
