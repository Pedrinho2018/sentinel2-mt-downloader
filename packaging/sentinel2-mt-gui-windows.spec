# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path(SPECPATH).parent

analise = Analysis(
    [str(ROOT / "src/gui_windows.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    excludes=["textual"],
    noarchive=False,
)
pyz = PYZ(analise.pure)

executavel = EXE(
    pyz,
    analise.scripts,
    [],
    exclude_binaries=True,
    name="Sentinel2-MT",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

colecao = COLLECT(
    executavel,
    analise.binaries,
    analise.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Sentinel2-MT-Windows",
)
