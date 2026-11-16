"""Lógica do assistente de download, independente de Qt.

A GUI executa a CLI com `--progresso-json` e entrega aqui cada evento recebido.
Este módulo transforma os eventos em textos simples para quem não conhece bandas,
SCL ou YAML, e estima o espaço necessário sem acessar a rede.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path

from .http import eh_parcial
from .painel import formatar_bytes


MARCADOR_PROGRESSO = "[PROGRESSO] "
SUFIXOS_RASTER = {".tif", ".tiff", ".jp2"}


def ler_evento(linha: str) -> dict | None:
    """Interpreta uma linha `[PROGRESSO] {json}`; outras linhas retornam None."""
    if not linha.startswith(MARCADOR_PROGRESSO):
        return None
    try:
        evento = json.loads(linha[len(MARCADOR_PROGRESSO):])
    except ValueError:
        return None
    return evento if isinstance(evento, dict) and isinstance(evento.get("tipo"), str) else None


@dataclass
class CenaEncontrada:
    cena: str
    data: str
    nuvem_pct: float | None
    tamanho_bytes: int | None
    miniatura: str
    baixada: bool
    preview_local: str
    selecionada: bool = False
    situacao: str = ""

    @classmethod
    def de_evento(cls, evento: dict) -> "CenaEncontrada":
        nuvem = evento.get("nuvem_metadados_pct")
        tamanho = evento.get("tamanho_bytes")
        baixada = bool(evento.get("baixada"))
        return cls(
            cena=str(evento.get("cena", "")),
            data=str(evento.get("data", "")),
            nuvem_pct=float(nuvem) if isinstance(nuvem, (int, float)) else None,
            tamanho_bytes=int(tamanho) if isinstance(tamanho, int) and tamanho > 0 else None,
            miniatura=str(evento.get("miniatura", "")),
            baixada=baixada,
            preview_local=str(evento.get("preview_local", "")),
            situacao="Já baixada" if baixada else "Disponível",
        )


def filtrar_cenas(
    cenas: list[CenaEncontrada], nuvem_max_pct: float | None, ocultar_baixadas: bool
) -> list[CenaEncontrada]:
    """Cenas sem informação de nuvem nos metadados não são escondidas pelo filtro."""
    resultado = []
    for cena in cenas:
        if ocultar_baixadas and cena.baixada:
            continue
        if nuvem_max_pct is not None and cena.nuvem_pct is not None and cena.nuvem_pct > nuvem_max_pct:
            continue
        resultado.append(cena)
    return resultado


def media_tamanho_cena_local(pasta: Path) -> tuple[int, int]:
    """Média de bytes por cena já baixada e quantas cenas entraram na média."""
    if not pasta.is_dir():
        return 0, 0
    tamanhos: dict[Path, int] = {}
    for diretorio, subpastas, arquivos in os.walk(pasta, followlinks=False):
        subpastas[:] = [nome for nome in subpastas if not nome.startswith(".")]
        relativo = Path(diretorio).relative_to(pasta).parts
        if len(relativo) < 2:
            continue
        cena = pasta / relativo[0] / relativo[1]
        for nome in arquivos:
            if eh_parcial(nome) or nome.endswith(".part.json"):
                continue
            try:
                tamanhos[cena] = tamanhos.get(cena, 0) + os.lstat(os.path.join(diretorio, nome)).st_size
            except OSError:
                continue
    # Considera apenas cenas com bandas (não só a pasta qualidade/).
    completas = [
        total
        for cena, total in tamanhos.items()
        if any(
            caminho.suffix.casefold() in SUFIXOS_RASTER and not caminho.name.startswith(".")
            for caminho in cena.iterdir()
            if caminho.is_file()
        )
    ]
    if not completas:
        return 0, 0
    return sum(completas) // len(completas), len(completas)


def estimar_tamanho(cenas: list[CenaEncontrada], media_local: int, amostras: int) -> str:
    """Texto honesto sobre o espaço necessário, dizendo de onde vem a estimativa."""
    pendentes = [cena for cena in cenas if not cena.baixada]
    if not pendentes:
        return "Todas as cenas selecionadas já estão no computador."
    conhecidas = [cena.tamanho_bytes for cena in pendentes if cena.tamanho_bytes]
    if len(conhecidas) == len(pendentes):
        return f"Tamanho informado pelo catálogo: {formatar_bytes(sum(conhecidas))}."
    if media_local and amostras:
        estimado = media_local * len(pendentes)
        return (
            f"Estimativa: cerca de {formatar_bytes(estimado)} "
            f"(média de {amostras} cena(s) já baixada(s) neste computador)."
        )
    return "Tamanho não informado pelo catálogo; ainda não há cenas locais para estimar."


@dataclass
class EstadoDownload:
    total_cenas: int = 0
    cenas_concluidas: int = 0
    cenas_descartadas: int = 0
    cena_atual: str = ""
    indice_atual: int = 0
    etapa: str = ""
    arquivo: str = ""
    recebidos: int = 0
    total_arquivo: int = 0
    velocidade_bps: float = 0.0
    retomado: bool = False
    situacoes: dict[str, str] = field(default_factory=dict)

    def aplicar(self, evento: dict) -> None:
        tipo = evento.get("tipo")
        if tipo == "cena":
            self.total_cenas = int(evento.get("total") or self.total_cenas or 0)
            self.cena_atual = str(evento.get("cena", ""))
            self.indice_atual = int(evento.get("indice") or self.indice_atual)
            self.etapa = str(evento.get("etapa", ""))
            self.arquivo = ""
            self.recebidos = self.total_arquivo = 0
            if self.etapa == "concluida":
                self.cenas_concluidas += 1
                self.situacoes[self.cena_atual] = "Baixada"
            elif self.etapa == "descartada":
                self.cenas_descartadas += 1
                self.situacoes[self.cena_atual] = "Descartada: nuvens acima do limite"
            else:
                self.situacoes[self.cena_atual] = "Baixando…"
        elif tipo == "arquivo":
            self.arquivo = str(evento.get("arquivo", ""))
            self.recebidos = int(evento.get("recebidos") or 0)
            self.total_arquivo = int(evento.get("total") or 0)
            self.velocidade_bps = float(evento.get("velocidade_bps") or 0.0)
            self.retomado = bool(evento.get("retomado"))

    @property
    def cenas_finalizadas(self) -> int:
        return self.cenas_concluidas + self.cenas_descartadas

    @property
    def percentual_geral(self) -> int:
        if not self.total_cenas:
            return 0
        return min(100, round(100 * self.cenas_finalizadas / self.total_cenas))

    @property
    def percentual_arquivo(self) -> int:
        if not self.total_arquivo:
            return 0
        return min(100, round(100 * self.recebidos / self.total_arquivo))

    def mensagem(self) -> str:
        if not self.cena_atual:
            return "Preparando o download…"
        posicao = (
            f"cena {self.indice_atual} de {self.total_cenas}"
            if self.total_cenas
            else f"cena {self.indice_atual}"
        )
        if self.etapa == "qualidade" and not self.arquivo:
            return f"Verificando nuvens da {posicao}…"
        if self.etapa == "descartada":
            return f"A {posicao} foi descartada: nuvens acima do limite configurado."
        if self.etapa == "concluida":
            return f"A {posicao} foi concluída."
        if self.arquivo:
            verbo = "Retomando" if self.retomado else "Baixando"
            quantidade = formatar_bytes(self.recebidos)
            if self.total_arquivo:
                quantidade += f" de {formatar_bytes(self.total_arquivo)}"
            velocidade = (
                f" a {formatar_bytes(int(self.velocidade_bps))}/s" if self.velocidade_bps else ""
            )
            return f"{verbo} {self.arquivo} da {posicao}: {quantidade}{velocidade}."
        return f"Processando a {posicao}…"

    def resumo_final(self, codigo_saida: int, cancelado: bool) -> str:
        base = (
            f"{self.cenas_concluidas} cena(s) baixada(s), "
            f"{self.cenas_descartadas} descartada(s) por nuvens."
        )
        if cancelado:
            return (
                "Download cancelado. " + base + " Os arquivos interrompidos ficaram guardados "
                "e serão retomados quando você baixar novamente."
            )
        if codigo_saida == 0:
            return "Download concluído. " + base
        return (
            "O download terminou com problemas. " + base + " Veja os detalhes no registro em "
            "Operações; baixar novamente retoma o que ficou incompleto."
        )
