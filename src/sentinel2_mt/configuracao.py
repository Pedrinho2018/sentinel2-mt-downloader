from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ConfiguracaoStac:
    url: str
    colecao: str


@dataclass(frozen=True)
class ConfiguracaoArea:
    nome: str
    uf: str
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class ConfiguracaoPeriodo:
    inicio: str
    fim: str


@dataclass(frozen=True)
class ConfiguracaoQualidade:
    filtrar_nuvens: bool = True
    nuvem_max_pct: float = 20.0
    manter_scl: bool = True


@dataclass(frozen=True)
class ConfiguracaoPreview:
    gerar_rgb: bool = True
    tamanho_max_px: int = 1600
    percentil_min: float = 2.0
    percentil_max: float = 98.0
    qualidade_jpeg: int = 92


@dataclass(frozen=True)
class ConfiguracaoDownload:
    pasta: str = "data/sentinel2"
    catalogo: str = "catalogo/catalogo_imagens.csv"
    timeout_segundos: int = 120
    chunk_mb: int = 1
    max_itens_teste: int = 5
    max_candidatos_teste: int = 40


@dataclass(frozen=True)
class ConfiguracaoSincronizacao:
    pasta_remota: str = "sentinel2-mt"
    oauth_json: str = "config/google-oauth.json"
    token_json: str = "config/google-token.json"
    pasta_id: str = "root"
    tamanho_lote: int = 100
    extensoes: tuple[str, ...] = (".tif", ".tiff", ".jpg", ".jpeg")


@dataclass(frozen=True)
class ConfiguracaoProjeto:
    raiz: Path
    stac: ConfiguracaoStac
    area: ConfiguracaoArea
    periodo: ConfiguracaoPeriodo
    bandas: tuple[str, ...]
    qualidade: ConfiguracaoQualidade
    preview: ConfiguracaoPreview
    download: ConfiguracaoDownload
    sincronizacao: ConfiguracaoSincronizacao

    @classmethod
    def carregar(cls, caminho: Path, raiz: Path | None = None) -> "ConfiguracaoProjeto":
        caminho = caminho.expanduser().resolve()
        with caminho.open("r", encoding="utf-8") as arquivo:
            dados = yaml.safe_load(arquivo) or {}

        ambiente = cls._carregar_dotenv(caminho.parent / ".env")
        ambiente.update(os.environ)
        dados = cls._expandir_variaveis(dados, ambiente)

        cls._validar_secoes(dados)
        raiz_projeto = (raiz or caminho.parents[1]).resolve()
        area = dados["area"]
        bbox = tuple(float(valor) for valor in area["bbox"])
        if len(bbox) != 4:
            raise ValueError("area.bbox deve conter quatro coordenadas")

        qualidade = dados.get("qualidade", {})
        preview = dados.get("preview", {})
        download = dados.get("download", {})
        sincronizacao = dados.get("sincronizacao", {})
        oauth_json = sincronizacao.get("oauth_json") or cls._descobrir_oauth(raiz_projeto)
        config = cls(
            raiz=raiz_projeto,
            stac=ConfiguracaoStac(**dados["stac"]),
            area=ConfiguracaoArea(nome=area["nome"], uf=area["uf"], bbox=bbox),
            periodo=ConfiguracaoPeriodo(**dados["periodo"]),
            bandas=tuple(str(banda) for banda in dados["bandas"]),
            qualidade=ConfiguracaoQualidade(**qualidade),
            preview=ConfiguracaoPreview(**preview),
            download=ConfiguracaoDownload(**download),
            sincronizacao=ConfiguracaoSincronizacao(
                pasta_remota=sincronizacao.get("pasta_remota", "sentinel2-mt"),
                oauth_json=oauth_json,
                token_json=sincronizacao.get("token_json", "config/google-token.json"),
                pasta_id=str(sincronizacao.get("pasta_id", "root")),
                tamanho_lote=int(sincronizacao.get("tamanho_lote", 100)),
                extensoes=tuple(sincronizacao.get("extensoes", (".tif", ".tiff", ".jpg", ".jpeg"))),
            ),
        )
        config.validar()
        return config

    @staticmethod
    def _carregar_dotenv(caminho: Path) -> dict[str, str]:
        ambiente: dict[str, str] = {}
        if not caminho.is_file():
            return ambiente
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            if linha.startswith("export "):
                linha = linha[7:].strip()
            chave, valor = linha.split("=", 1)
            ambiente[chave.strip()] = valor.strip().strip("\"'")
        return ambiente

    @classmethod
    def _expandir_variaveis(cls, valor: Any, ambiente: dict[str, str]) -> Any:
        if isinstance(valor, dict):
            return {chave: cls._expandir_variaveis(item, ambiente) for chave, item in valor.items()}
        if isinstance(valor, list):
            return [cls._expandir_variaveis(item, ambiente) for item in valor]
        if not isinstance(valor, str):
            return valor

        padrao = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")

        def substituir(correspondencia: re.Match[str]) -> str:
            chave, default = correspondencia.group(1), correspondencia.group(2) or ""
            return ambiente.get(chave) or default

        return padrao.sub(substituir, valor)

    @staticmethod
    def _descobrir_oauth(raiz: Path) -> str:
        candidatos = sorted((raiz / "config").glob("client_secret_*.json"))
        if len(candidatos) == 1:
            return str(candidatos[0])
        if len(candidatos) > 1:
            raise ValueError("Há mais de um client_secret_*.json; informe GOOGLE_OAUTH_JSON no config/.env")
        return "config/google-oauth.json"

    @staticmethod
    def _validar_secoes(dados: dict) -> None:
        obrigatorias = ("stac", "area", "periodo", "bandas", "download")
        ausentes = [secao for secao in obrigatorias if secao not in dados]
        if ausentes:
            raise ValueError(f"Seções obrigatórias ausentes: {', '.join(ausentes)}")

    def validar(self) -> None:
        if not self.stac.url or not self.stac.colecao:
            raise ValueError("stac.url e stac.colecao são obrigatórios")
        if not self.bandas:
            raise ValueError("Ao menos uma banda deve ser configurada")
        if self.download.timeout_segundos <= 0 or self.download.chunk_mb <= 0:
            raise ValueError("Timeout e tamanho do chunk devem ser positivos")
        if not 0 <= self.qualidade.nuvem_max_pct <= 100:
            raise ValueError("qualidade.nuvem_max_pct deve estar entre 0 e 100")
        if self.sincronizacao.tamanho_lote <= 0:
            raise ValueError("sincronizacao.tamanho_lote deve ser maior que zero")

    def caminho(self, valor: str | Path) -> Path:
        caminho = Path(valor).expanduser()
        return caminho if caminho.is_absolute() else self.raiz / caminho
