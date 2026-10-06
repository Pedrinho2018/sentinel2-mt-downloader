from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class RegistroCatalogo:
    id: str
    data: str
    colecao: str
    banda: str
    nuvem_pct: str
    url: str = ""
    arquivo: str = ""
    status: str = ""
    erro: str = ""

    @classmethod
    def criar(
        cls,
        item,
        data: str,
        colecao: str,
        banda: str,
        nuvem,
        status: str,
        url: str = "",
        arquivo: str = "",
        erro: str = "",
    ) -> "RegistroCatalogo":
        nuvem_texto = f"{nuvem:.2f}" if isinstance(nuvem, float) else str(nuvem)
        return cls(item.id, data, colecao, banda, nuvem_texto, url, arquivo, status, erro)

    def para_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class ResumoExecucao:
    candidatos: int = 0
    aprovadas: int = 0
    descartadas: int = 0
    previews: int = 0
    erros: int = 0


@dataclass
class RegistroPatch:
    patch_id: str
    scene_id: str
    collection: str
    date: str
    bbox: str
    crs: str
    width: int
    height: int
    pixel_size: str
    cloud_pct: str
    valid_pixel_pct: str
    source_scene: str
    rgb_png: str = ""
    geotiff_path: str = ""
    bands: str = ""
    missing_bands: str = ""
    scl_path: str = ""
    CLEAROB: str = ""
    TOTALOB: str = ""
    PROVENANCE: str = ""
    status: str = ""
    erro: str = ""
    label: str = ""
    label_source: str = ""
    label_confidence: str = ""

    def para_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResumoDataset:
    candidatos: int = 0
    aprovados: int = 0
    descartados_nuvem: int = 0
    descartados_nodata: int = 0
    erros: int = 0

    def acumular(self, outro: "ResumoDataset") -> None:
        self.candidatos += outro.candidatos
        self.aprovados += outro.aprovados
        self.descartados_nuvem += outro.descartados_nuvem
        self.descartados_nodata += outro.descartados_nodata
        self.erros += outro.erros
