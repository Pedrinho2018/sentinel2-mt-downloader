from __future__ import annotations

from collections.abc import Mapping


class ResolvedorAssets:
    """Normaliza nomes STAC sem acoplar o serviço a uma coleção específica."""

    ALIASES = {
        "B02": ("B02", "B2", "B02_10M", "BLUE"),
        "B03": ("B03", "B3", "B03_10M", "GREEN"),
        "B04": ("B04", "B4", "B04_10M", "RED"),
        "B05": ("B05", "B5", "B05_20M", "RE1", "REDEDGE1"),
        "B06": ("B06", "B6", "B06_20M", "RE2", "REDEDGE2"),
        "B07": ("B07", "B7", "B07_20M", "RE3", "REDEDGE3"),
        "B08": ("B08", "B8", "B08_10M", "NIR"),
        "B8A": ("B8A", "B8A_20M", "NIR08", "NIRNARROW"),
        "B11": ("B11", "B11_20M", "SWIR16", "SWIR1"),
        "B12": ("B12", "B12_20M", "SWIR22", "SWIR2"),
        "NDVI": ("NDVI",),
        "EVI": ("EVI",),
        "SCL": ("SCL", "SCL_20M", "SCENECLASSIFICATION", "SCENECLASSIFICATIONMAP"),
        "CLEAROB": ("CLEAROB", "CLEAROBS", "CLEAROBSERVATIONS"),
        "TOTALOB": ("TOTALOB", "TOTALOBS", "TOTALOBSERVATIONS"),
        "PROVENANCE": ("PROVENANCE", "PROVENIENCE"),
    }

    _POR_ALIAS = {
        "".join(caractere for caractere in alias.upper() if caractere.isalnum()): canonico
        for canonico, aliases in ALIASES.items()
        for alias in aliases
    }

    @classmethod
    def normalizar(cls, nome: str) -> str:
        chave = "".join(caractere for caractere in str(nome).upper() if caractere.isalnum())
        return cls._POR_ALIAS.get(chave, str(nome).upper())

    @classmethod
    def localizar(cls, assets: Mapping[str, object], nome: str):
        if nome in assets:
            return assets[nome]
        canonico = cls.normalizar(nome)
        for chave, asset in assets.items():
            if cls.normalizar(chave) == canonico:
                return asset
        return None
