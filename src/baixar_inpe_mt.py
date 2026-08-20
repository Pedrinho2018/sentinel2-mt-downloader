"""Script legado desativado.

O pipeline principal não baixa cenas completas e não usa Rasterio/GDAL.
Use src/pipeline.py.
"""


def main() -> int:
    print("[DESATIVADO] Este downloader pertence ao pipeline antigo.")
    print(r"Use: python src\pipeline.py --mes 2026-04 --max-patches 5 --limpar")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
