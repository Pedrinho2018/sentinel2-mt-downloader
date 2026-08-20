from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
MARCADOR = VENV / ".requirements.sha256"
IMPORTACOES_CRITICAS = ("yaml", "textual", "rasterio", "google.auth", "googleapiclient.discovery")


def python_venv() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def hash_requirements() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def dependencias_disponiveis(python: Path) -> bool:
    codigo = "; ".join(f"import {modulo}" for modulo in IMPORTACOES_CRITICAS)
    resultado = subprocess.run(
        [str(python), "-c", codigo],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return resultado.returncode == 0


def preparar_ambiente() -> Path:
    python = python_venv()
    if not python.exists():
        print(f"[SETUP] Criando ambiente virtual em {VENV}...")
        try:
            venv.EnvBuilder(with_pip=True).create(VENV)
        except Exception as exc:
            raise RuntimeError(
                "Não foi possível criar o ambiente virtual. "
                "No Debian/Ubuntu, instale o pacote python3-venv."
            ) from exc

    atual = hash_requirements()
    instalado = MARCADOR.read_text(encoding="utf-8").strip() if MARCADOR.exists() else ""
    if atual != instalado or not dependencias_disponiveis(python):
        print("[SETUP] Instalando/atualizando dependências dentro do ambiente virtual...")
        subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQUIREMENTS)],
            cwd=ROOT,
            check=True,
        )
        MARCADOR.write_text(atual, encoding="utf-8")
    return python


def main() -> int:
    try:
        python = preparar_ambiente()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[ERRO] Falha ao preparar o ambiente: {exc}", file=sys.stderr)
        return 1

    if "--setup-only" in sys.argv[1:]:
        print("[SETUP] Ambiente pronto.")
        return 0
    return subprocess.call([str(python), str(ROOT / "src" / "tui.py")], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
