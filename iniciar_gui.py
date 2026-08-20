from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from iniciar_tui import ROOT, preparar_ambiente


REQUIREMENTS_GUI = ROOT / "requirements-gui.txt"
MARCADOR_GUI = ROOT / ".venv" / ".requirements-gui.sha256"


def preparar_gui() -> Path:
    python = preparar_ambiente()
    assinatura = hashlib.sha256(REQUIREMENTS_GUI.read_bytes()).hexdigest()
    instalada = (
        MARCADOR_GUI.read_text(encoding="utf-8").strip()
        if MARCADOR_GUI.exists()
        else ""
    )
    verificacao = subprocess.run(
        [
            str(python),
            "-c",
            "import PySide6; from PySide6.QtWebEngineWidgets import QWebEngineView",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if instalada != assinatura or verificacao.returncode != 0:
        print("[SETUP] Instalando dependências da interface gráfica na .venv...")
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-r",
                str(REQUIREMENTS_GUI),
            ],
            cwd=ROOT,
            check=True,
        )
        MARCADOR_GUI.write_text(assinatura, encoding="utf-8")
    return python


def main() -> int:
    try:
        python = preparar_gui()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as erro:
        print(f"[ERRO] Falha ao preparar a GUI: {erro}", file=sys.stderr)
        return 1

    if "--setup-only" in sys.argv[1:]:
        print("[SETUP] Interface gráfica pronta.")
        return 0
    return subprocess.call([str(python), str(ROOT / "src" / "gerar_config_gui.py")], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
