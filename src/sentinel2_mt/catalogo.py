from __future__ import annotations

import csv
from contextlib import contextmanager
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .modelos import RegistroCatalogo, RegistroPatch


class RepositorioCatalogoCSV:
    CAMPOS = ("id", "data", "colecao", "banda", "nuvem_pct", "url", "arquivo", "status", "erro")

    def salvar(self, caminho: Path, registros: list[RegistroCatalogo]) -> None:
        with _bloqueio_catalogo(caminho):
            _gravar_csv_atomico(
                caminho,
                self.CAMPOS,
                (registro.para_dict() for registro in registros),
            )


class RepositorioCatalogoPatchesCSV:
    CAMPOS = (
        "patch_id",
        "scene_id",
        "collection",
        "date",
        "bbox",
        "crs",
        "width",
        "height",
        "pixel_size",
        "cloud_pct",
        "valid_pixel_pct",
        "source_scene",
        "rgb_png",
        "geotiff_path",
        "bands",
        "missing_bands",
        "scl_path",
        "CLEAROB",
        "TOTALOB",
        "PROVENANCE",
        "status",
        "erro",
        "label",
        "label_source",
        "label_confidence",
    )

    def salvar(self, caminho: Path, registros: list[RegistroPatch]) -> None:
        """Atualiza por patch_id e preserva cenas de execuções anteriores."""
        with _bloqueio_catalogo(caminho):
            existentes: dict[str, dict] = {}
            if caminho.is_file():
                with caminho.open("r", newline="", encoding="utf-8-sig") as arquivo:
                    for linha in csv.DictReader(arquivo):
                        if linha.get("patch_id"):
                            existentes[linha["patch_id"]] = linha

            for registro in registros:
                novo = registro.para_dict()
                anterior = existentes.get(registro.patch_id, {})
                for campo in ("label", "label_source", "label_confidence"):
                    if not novo.get(campo) and anterior.get(campo):
                        novo[campo] = anterior[campo]
                existentes[registro.patch_id] = novo

            _gravar_csv_atomico(
                caminho,
                self.CAMPOS,
                (existentes[chave] for chave in sorted(existentes)),
            )


@contextmanager
def _bloqueio_catalogo(caminho: Path):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    bloqueio = caminho.parent / f".{caminho.name}.lock"
    with bloqueio.open("a+", encoding="utf-8") as arquivo_bloqueio:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - fallback para plataformas sem flock
            yield
        else:
            fcntl.flock(arquivo_bloqueio.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(arquivo_bloqueio.fileno(), fcntl.LOCK_UN)


def _gravar_csv_atomico(caminho: Path, campos: tuple[str, ...], linhas) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            newline="",
            encoding="utf-8-sig",
            prefix=f".{caminho.name}.",
            suffix=".tmp",
            dir=caminho.parent,
            delete=False,
        ) as arquivo:
            temporario = Path(arquivo.name)
            escritor = csv.DictWriter(arquivo, fieldnames=campos)
            escritor.writeheader()
            for linha in linhas:
                escritor.writerow({campo: linha.get(campo, "") for campo in campos})
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, caminho)
    finally:
        if temporario is not None:
            temporario.unlink(missing_ok=True)
