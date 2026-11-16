"""Resumo somente leitura do estado local para o painel inicial.

Este módulo não acessa rede, não altera arquivos e não carrega Torch/Ultralytics.
Ele apenas conta artefatos já produzidos pelo pipeline e confere a integridade
declarada dos pesos, para que GUI, TUI ou CLI apresentem o mesmo diagnóstico.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from .configuracao import ConfiguracaoProjeto


PASTA_MODELOS = Path(__file__).resolve().parent / "analise" / "models"
STATUS_PATCH_APROVADO = {"APROVADO", "APROVADO_SEM_SCL"}
SUFIXOS_RASTER = {".tif", ".tiff", ".jp2"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PARCIAL_RE = re.compile(r"^\..+\.part(\.[A-Za-z0-9]+)?$")


@dataclass(frozen=True)
class ModeloInstalado:
    arquivo: str
    nome: str
    versao: str
    tarefa: str
    ativo: bool
    integridade: str  # "conferida", "divergente" ou "sem_manifesto"

    @property
    def integridade_conferida(self) -> bool:
        return self.integridade == "conferida"


@dataclass(frozen=True)
class UltimaAnalise:
    analysis_id: str
    criado_em: str
    regiao: str
    total_deteccoes: int
    modelo_versao: str


@dataclass
class ResumoPainel:
    regioes_salvas: int = 0
    cenas_catalogadas: int = 0
    cenas_baixadas: int = 0
    patches_em_disco: int = 0
    patches_aprovados_catalogo: int = 0
    catalogo_patches_existe: bool = False
    downloads_parciais: int = 0
    ultima_analise: UltimaAnalise | None = None
    modelos: list[ModeloInstalado] = field(default_factory=list)
    bytes_por_area: dict[str, int] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)
    proxima_acao: str = ""

    @property
    def bytes_total(self) -> int:
        return sum(self.bytes_por_area.values())

    def para_dict(self) -> dict:
        dados = asdict(self)
        dados["bytes_total"] = self.bytes_total
        return dados


def formatar_bytes(valor: int) -> str:
    """Formata bytes em unidades binárias legíveis em português."""
    quantidade = float(max(0, int(valor)))
    unidades = ("B", "KB", "MB", "GB", "TB")
    indice = 0
    while quantidade >= 1024 and indice < len(unidades) - 1:
        quantidade /= 1024
        indice += 1
    unidade = unidades[indice]
    if unidade == "B":
        return f"{int(quantidade)} B"
    return f"{quantidade:.1f} {unidade}".replace(".", ",")


def coletar_resumo_painel(
    config: ConfiguracaoProjeto,
    *,
    regioes_salvas: int = 0,
    pasta_modelos: Path | None = None,
) -> ResumoPainel:
    """Reúne contagens locais sem modificar dados científicos nem acessar a rede."""
    resumo = ResumoPainel(regioes_salvas=max(0, int(regioes_salvas)))
    pasta_download = config.caminho(config.download.pasta)
    pasta_dataset = config.caminho(config.dataset.pasta)
    pasta_analises = config.caminho(config.analise.pasta)

    resumo.cenas_catalogadas = _contar_cenas_catalogo(config.caminho(config.download.catalogo))
    resumo.cenas_baixadas, resumo.downloads_parciais = _contar_cenas_baixadas(pasta_download)
    resumo.patches_em_disco = _contar_patches_em_disco(pasta_dataset)
    catalogo_patches = config.caminho(config.dataset.catalogo)
    resumo.catalogo_patches_existe = catalogo_patches.is_file()
    resumo.patches_aprovados_catalogo = _contar_patches_aprovados(catalogo_patches)
    resumo.ultima_analise = _ultima_analise(config)
    resumo.modelos = listar_modelos_instalados(
        pasta_modelos or PASTA_MODELOS, Path(config.analise.modelo).name
    )
    resumo.bytes_por_area = {
        "imagens": _tamanho_pasta(pasta_download),
        "dataset": _tamanho_pasta(pasta_dataset),
        "analises": _tamanho_pasta(pasta_analises),
    }
    resumo.avisos = _avisos(resumo)
    resumo.proxima_acao = _proxima_acao(resumo)
    return resumo


def listar_modelos_instalados(pasta: Path, arquivo_ativo: str = "best.pt") -> list[ModeloInstalado]:
    """Lista pesos `.pt` locais e confere o SHA-256 declarado no manifesto."""
    if not pasta.is_dir() or pasta.is_symlink():
        return []
    manifesto = _ler_manifesto(pasta / "model_metadata.json")
    hash_declarado = str(manifesto.get("sha256", "")).strip().lower()
    modelos: list[ModeloInstalado] = []
    for peso in sorted(pasta.glob("*.pt")):
        if peso.is_symlink() or not peso.is_file():
            continue
        ativo = peso.name == arquivo_ativo
        # O manifesto atual descreve somente o peso ativo configurado.
        if ativo and SHA256_RE.fullmatch(hash_declarado):
            integridade = "conferida" if _sha256(peso) == hash_declarado else "divergente"
        else:
            integridade = "sem_manifesto"
        modelos.append(
            ModeloInstalado(
                arquivo=peso.name,
                nome=str(manifesto.get("name", "")) if ativo else "",
                versao=str(manifesto.get("version", "")) if ativo else "",
                tarefa=str(manifesto.get("task", "")) if ativo else "",
                ativo=ativo,
                integridade=integridade,
            )
        )
    return modelos


def _ler_manifesto(caminho: Path) -> dict:
    if not caminho.is_file() or caminho.is_symlink():
        return {}
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dados if isinstance(dados, dict) else {}


def _sha256(caminho: Path) -> str:
    digest = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def _ler_csv(caminho: Path) -> list[dict[str, str]]:
    if not caminho.is_file() or caminho.is_symlink():
        return []
    try:
        with caminho.open("r", newline="", encoding="utf-8-sig") as arquivo:
            return list(csv.DictReader(arquivo))
    except (OSError, csv.Error, UnicodeDecodeError):
        return []


def _contar_cenas_catalogo(caminho: Path) -> int:
    return len({linha.get("id", "") for linha in _ler_csv(caminho) if linha.get("id")})


def _contar_patches_aprovados(caminho: Path) -> int:
    return sum(
        1 for linha in _ler_csv(caminho) if str(linha.get("status", "")) in STATUS_PATCH_APROVADO
    )


def _contar_cenas_baixadas(pasta: Path) -> tuple[int, int]:
    """Conta pastas `data/cena` com raster final e arquivos `.part` remanescentes."""
    if not pasta.is_dir():
        return 0, 0
    cenas: set[Path] = set()
    parciais = 0
    for diretorio, subpastas, arquivos in os.walk(pasta, followlinks=False):
        subpastas[:] = [nome for nome in subpastas if not nome.startswith(".")]
        atual = Path(diretorio)
        for nome in arquivos:
            if PARCIAL_RE.match(nome):
                parciais += 1
                continue
            if nome.startswith(".") or Path(nome).suffix.lower() not in SUFIXOS_RASTER:
                continue
            relativo = atual.relative_to(pasta).parts
            # Layout: <data>/<cena>/<banda>.tif. Cenas apenas com `qualidade/`
            # (SCL e camadas auxiliares) ainda não têm bandas baixadas.
            if len(relativo) == 2:
                cenas.add(pasta / relativo[0] / relativo[1])
    return len(cenas), parciais


def _contar_patches_em_disco(pasta: Path) -> int:
    if not pasta.is_dir():
        return 0
    total = 0
    for _diretorio, subpastas, arquivos in os.walk(pasta, followlinks=False):
        subpastas[:] = [nome for nome in subpastas if not nome.startswith(".")]
        if "multiband.tif" in arquivos or "metadata.json" in arquivos:
            total += 1
    return total


def _tamanho_pasta(pasta: Path) -> int:
    if not pasta.is_dir():
        return 0
    total = 0
    for diretorio, _subpastas, arquivos in os.walk(pasta, followlinks=False):
        for nome in arquivos:
            try:
                estado = os.lstat(os.path.join(diretorio, nome))
            except OSError:
                continue
            total += estado.st_size
    return total


def _ultima_analise(config: ConfiguracaoProjeto) -> UltimaAnalise | None:
    banco = config.caminho(config.analise.historico)
    if not banco.is_file() or banco.is_symlink():
        return None
    # Importação tardia: o histórico é opcional para o painel.
    from .analise.historico import RepositorioHistoricoAnalises

    try:
        registros = RepositorioHistoricoAnalises(banco, config.raiz).listar(limite=1)
    except (OSError, ValueError, TypeError, sqlite3.Error):  # banco corrompido não derruba o painel
        return None
    if not registros:
        return None
    registro = registros[0]
    resumo = registro.resumo if isinstance(registro.resumo, dict) else {}
    try:
        total = int(resumo.get("total_deteccoes", 0))
    except (TypeError, ValueError):
        total = 0
    return UltimaAnalise(
        analysis_id=registro.analysis_id,
        criado_em=registro.criado_em,
        regiao=registro.regiao,
        total_deteccoes=total,
        modelo_versao=registro.modelo_versao,
    )


def _avisos(resumo: ResumoPainel) -> list[str]:
    avisos: list[str] = []
    if resumo.downloads_parciais:
        avisos.append(
            f"{resumo.downloads_parciais} download(s) interrompido(s) deixaram arquivos "
            "temporários .part; repita o download da cena para concluí-lo."
        )
    if resumo.patches_em_disco and not resumo.catalogo_patches_existe:
        avisos.append(
            "Há patches no disco, mas o catálogo de patches não foi encontrado; "
            "a análise não conseguirá localizá-los. Gere o dataset das cenas locais."
        )
    if any(modelo.integridade == "divergente" for modelo in resumo.modelos):
        avisos.append(
            "O SHA-256 do modelo ativo diverge do manifesto aprovado; a análise será bloqueada."
        )
    if not any(modelo.ativo for modelo in resumo.modelos):
        avisos.append("Nenhum modelo ativo foi encontrado na pasta de modelos.")
    return avisos


def _proxima_acao(resumo: ResumoPainel) -> str:
    if resumo.regioes_salvas == 0 and resumo.cenas_catalogadas == 0:
        return "Selecione uma região no mapa para começar."
    if resumo.cenas_catalogadas == 0:
        return "Busque imagens disponíveis para a região selecionada."
    if resumo.cenas_baixadas == 0:
        return "Baixe as imagens aprovadas da região."
    if resumo.patches_aprovados_catalogo == 0:
        return "Gere o dataset das cenas locais para preparar a análise."
    if resumo.ultima_analise is None:
        return "Analise a região com o modelo ativo."
    return "Consulte os resultados da última análise."
