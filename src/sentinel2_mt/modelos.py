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
