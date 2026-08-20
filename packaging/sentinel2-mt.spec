from pathlib import Path

from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPECPATH).parent
datas = []
binaries = []
hiddenimports = []

for pacote in ("rasterio", "textual", "pystac", "googleapiclient"):
    pacote_datas, pacote_binaries, pacote_hiddenimports = collect_all(pacote)
    datas += pacote_datas
    binaries += pacote_binaries
    hiddenimports += pacote_hiddenimports

analise = Analysis(
    [str(ROOT / "src/main.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # PySide6 e QtWebEngine fazem parte da distribuição principal. Os hooks
    # nativos do PyInstaller coletam os plugins, recursos e subprocessos Qt.
    excludes=[
        "PyQt5",
        "PyQt6",
        "PySide2",
        "streamlit",
        "folium",
        "streamlit_folium",
    ],
    noarchive=False,
)
pyz = PYZ(analise.pure)

executavel = EXE(
    pyz,
    analise.scripts,
    analise.binaries,
    analise.datas,
    [],
    name="sentinel2-mt",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
