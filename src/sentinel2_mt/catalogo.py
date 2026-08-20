from __future__ import annotations

import csv
from pathlib import Path

from .modelos import RegistroCatalogo


class RepositorioCatalogoCSV:
    CAMPOS = ("id", "data", "colecao", "banda", "nuvem_pct", "url", "arquivo", "status", "erro")

    def salvar(self, caminho: Path, registros: list[RegistroCatalogo]) -> None:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with caminho.open("w", newline="", encoding="utf-8-sig") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=self.CAMPOS)
            escritor.writeheader()
            escritor.writerows(registro.para_dict() for registro in registros)
