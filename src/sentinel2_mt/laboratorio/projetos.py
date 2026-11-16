"""Projetos do Laboratório de IA, esquemas de classes versionados e imagens.

Persistência em SQLite local. Guarda somente metadados, hashes e caminhos
relativos; os GeoTIFFs e PNGs do pipeline não são copiados nem alterados.

Regras:
- cada projeto tem um tipo fixo: "deteccao" (caixas YOLO) ou "classificacao"
  (uma classe por imagem). O tipo não muda depois de criado, para que
  anotações de detecção e rótulos de classificação nunca se misturem;
- o esquema de classes é versionado e imutável: alterar classes cria a versão
  seguinte, e as anteriores continuam registradas.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3


TIPOS_PROJETO = {
    "deteccao": ("talhao",),
    "classificacao": ("soja", "milho", "algodao", "outras"),
}
ROTULOS_TIPO = {
    "deteccao": "Detecção (caixas)",
    "classificacao": "Classificação (uma classe por imagem)",
}
ESTADOS_ROTULO = ("pendente", "rotulada", "revisar")
ESTADOS_INTEGRIDADE = ("nao_verificada", "ok", "ausente", "alterada", "ilegivel")
CLASSE_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
LIMITE_CLASSES = 100


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Projeto:
    id: int
    nome: str
    tipo: str
    descricao: str
    criado_em: str


@dataclass(frozen=True)
class EsquemaClasses:
    projeto_id: int
    versao: int
    classes: tuple[str, ...]
    criado_em: str


@dataclass(frozen=True)
class ImagemDataset:
    patch_id: str
    caminho_rgb: str  # relativo à raiz da aplicação
    sha256: str
    origem: str  # "catalogo", "pasta" ou "externa"
    caminho_geotiff: str = ""
    cena: str = ""
    tile: str = ""
    data: str = ""
    safra: str = ""
    regiao: str = ""
    colecao: str = ""
    nuvem_pct: float | None = None
    largura: int = 0
    altura: int = 0
    estado_rotulo: str = "pendente"
    integridade: str = "nao_verificada"
    duplicata_de: str = ""
    adicionada_em: str = ""

    def para_dict(self) -> dict:
        return asdict(self)


def validar_classes(classes: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalizadas = tuple(str(classe).strip().lower() for classe in classes)
    if not normalizadas:
        raise ValueError("O esquema precisa de ao menos uma classe.")
    if len(normalizadas) > LIMITE_CLASSES:
        raise ValueError(f"O esquema aceita no máximo {LIMITE_CLASSES} classes.")
    invalidas = [classe for classe in normalizadas if not CLASSE_RE.fullmatch(classe)]
    if invalidas:
        raise ValueError(
            f"Nome de classe inválido: '{invalidas[0]}'. Use letras minúsculas sem acento, "
            "números e _ (ex.: algodao, talhao)."
        )
    if len(set(normalizadas)) != len(normalizadas):
        raise ValueError("O esquema tem classes repetidas.")
    return normalizadas


class RepositorioLaboratorio:
    def __init__(self, caminho_banco: Path | str) -> None:
        self.caminho_banco = Path(caminho_banco).expanduser()
        self.caminho_banco.parent.mkdir(parents=True, exist_ok=True)
        self._inicializar()

    @contextmanager
    def _conectar(self) -> Iterator[sqlite3.Connection]:
        conexao = sqlite3.connect(self.caminho_banco)
        conexao.row_factory = sqlite3.Row
        conexao.execute("PRAGMA foreign_keys = ON")
        try:
            yield conexao
            conexao.commit()
        except Exception:
            conexao.rollback()
            raise
        finally:
            conexao.close()

    def _inicializar(self) -> None:
        with self._conectar() as conexao:
            conexao.executescript(
                """
                CREATE TABLE IF NOT EXISTS projetos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT NOT NULL UNIQUE,
                    tipo TEXT NOT NULL CHECK (tipo IN ('deteccao', 'classificacao')),
                    descricao TEXT NOT NULL DEFAULT '',
                    criado_em TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS esquemas_classes (
                    projeto_id INTEGER NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
                    versao INTEGER NOT NULL,
                    classes_json TEXT NOT NULL,
                    criado_em TEXT NOT NULL,
                    PRIMARY KEY (projeto_id, versao)
                );
                CREATE TABLE IF NOT EXISTS imagens (
                    projeto_id INTEGER NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
                    patch_id TEXT NOT NULL,
                    caminho_rgb TEXT NOT NULL,
                    caminho_geotiff TEXT NOT NULL DEFAULT '',
                    sha256 TEXT NOT NULL,
                    origem TEXT NOT NULL,
                    cena TEXT NOT NULL DEFAULT '',
                    tile TEXT NOT NULL DEFAULT '',
                    data TEXT NOT NULL DEFAULT '',
                    safra TEXT NOT NULL DEFAULT '',
                    regiao TEXT NOT NULL DEFAULT '',
                    colecao TEXT NOT NULL DEFAULT '',
                    nuvem_pct REAL,
                    largura INTEGER NOT NULL DEFAULT 0,
                    altura INTEGER NOT NULL DEFAULT 0,
                    estado_rotulo TEXT NOT NULL DEFAULT 'pendente',
                    integridade TEXT NOT NULL DEFAULT 'nao_verificada',
                    duplicata_de TEXT NOT NULL DEFAULT '',
                    adicionada_em TEXT NOT NULL,
                    PRIMARY KEY (projeto_id, patch_id)
                );
                CREATE INDEX IF NOT EXISTS idx_imagens_sha ON imagens(projeto_id, sha256);
                """
            )
        os.chmod(self.caminho_banco, 0o600)

    # ------------------------------------------------------------------ projetos

    def criar_projeto(
        self,
        nome: str,
        tipo: str,
        classes: list[str] | tuple[str, ...] | None = None,
        descricao: str = "",
    ) -> Projeto:
        nome = " ".join(str(nome).split())
        if not 1 <= len(nome) <= 80:
            raise ValueError("O nome do projeto deve ter de 1 a 80 caracteres.")
        if tipo not in TIPOS_PROJETO:
            raise ValueError("Tipo de projeto deve ser 'deteccao' ou 'classificacao'.")
        classes_validas = validar_classes(classes or TIPOS_PROJETO[tipo])
        criado = _agora()
        try:
            with self._conectar() as conexao:
                cursor = conexao.execute(
                    "INSERT INTO projetos (nome, tipo, descricao, criado_em) VALUES (?, ?, ?, ?)",
                    (nome, tipo, str(descricao)[:500], criado),
                )
                projeto_id = int(cursor.lastrowid)
                conexao.execute(
                    "INSERT INTO esquemas_classes VALUES (?, 1, ?, ?)",
                    (projeto_id, json.dumps(list(classes_validas)), criado),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Já existe um projeto chamado '{nome}'.") from exc
        return Projeto(projeto_id, nome, tipo, str(descricao)[:500], criado)

    def listar_projetos(self) -> list[Projeto]:
        with self._conectar() as conexao:
            linhas = conexao.execute("SELECT * FROM projetos ORDER BY nome COLLATE NOCASE").fetchall()
        return [Projeto(l["id"], l["nome"], l["tipo"], l["descricao"], l["criado_em"]) for l in linhas]

    def obter_projeto(self, projeto_id: int) -> Projeto:
        with self._conectar() as conexao:
            l = conexao.execute("SELECT * FROM projetos WHERE id = ?", (projeto_id,)).fetchone()
        if l is None:
            raise ValueError("Projeto não encontrado.")
        return Projeto(l["id"], l["nome"], l["tipo"], l["descricao"], l["criado_em"])

    # ------------------------------------------------------------------ esquemas

    def listar_esquemas(self, projeto_id: int) -> list[EsquemaClasses]:
        with self._conectar() as conexao:
            linhas = conexao.execute(
                "SELECT * FROM esquemas_classes WHERE projeto_id = ? ORDER BY versao",
                (projeto_id,),
            ).fetchall()
        return [
            EsquemaClasses(l["projeto_id"], l["versao"], tuple(json.loads(l["classes_json"])), l["criado_em"])
            for l in linhas
        ]

    def esquema_atual(self, projeto_id: int) -> EsquemaClasses:
        esquemas = self.listar_esquemas(projeto_id)
        if not esquemas:
            raise ValueError("Projeto sem esquema de classes.")
        return esquemas[-1]

    def nova_versao_esquema(self, projeto_id: int, classes: list[str] | tuple[str, ...]) -> EsquemaClasses:
        """Cria a próxima versão; versões existentes nunca são alteradas."""
        self.obter_projeto(projeto_id)
        classes_validas = validar_classes(classes)
        atual = self.esquema_atual(projeto_id)
        if classes_validas == atual.classes:
            return atual
        criado = _agora()
        with self._conectar() as conexao:
            conexao.execute(
                "INSERT INTO esquemas_classes VALUES (?, ?, ?, ?)",
                (projeto_id, atual.versao + 1, json.dumps(list(classes_validas)), criado),
            )
        return EsquemaClasses(projeto_id, atual.versao + 1, classes_validas, criado)

    # ------------------------------------------------------------------ imagens

    def adicionar_imagens(self, projeto_id: int, imagens: list[ImagemDataset]) -> tuple[int, int]:
        """Adiciona imagens novas; as já presentes (mesmo patch_id) são mantidas."""
        self.obter_projeto(projeto_id)
        agora = _agora()
        novas = 0
        with self._conectar() as conexao:
            for imagem in imagens:
                dados = imagem.para_dict()
                dados["adicionada_em"] = dados["adicionada_em"] or agora
                cursor = conexao.execute(
                    f"""INSERT OR IGNORE INTO imagens (projeto_id, {", ".join(dados)})
                    VALUES (?, {", ".join("?" for _ in dados)})""",
                    (projeto_id, *dados.values()),
                )
                novas += cursor.rowcount
        self.recalcular_duplicatas(projeto_id)
        return novas, len(imagens) - novas

    def listar_imagens(self, projeto_id: int) -> list[ImagemDataset]:
        with self._conectar() as conexao:
            linhas = conexao.execute(
                "SELECT * FROM imagens WHERE projeto_id = ? ORDER BY data, patch_id",
                (projeto_id,),
            ).fetchall()
        campos = ImagemDataset.__dataclass_fields__
        return [ImagemDataset(**{chave: linha[chave] for chave in campos}) for linha in linhas]

    def atualizar_integridade(self, projeto_id: int, estados: dict[str, str]) -> None:
        for estado in estados.values():
            if estado not in ESTADOS_INTEGRIDADE:
                raise ValueError(f"Estado de integridade inválido: {estado}")
        with self._conectar() as conexao:
            conexao.executemany(
                "UPDATE imagens SET integridade = ? WHERE projeto_id = ? AND patch_id = ?",
                [(estado, projeto_id, patch_id) for patch_id, estado in estados.items()],
            )

    def remover_imagens(self, projeto_id: int, patch_ids: list[str]) -> int:
        """Remove imagens do projeto (só os registros; arquivos não são apagados)."""
        with self._conectar() as conexao:
            cursor = conexao.executemany(
                "DELETE FROM imagens WHERE projeto_id = ? AND patch_id = ?",
                [(projeto_id, patch_id) for patch_id in patch_ids],
            )
            removidas = cursor.rowcount
        self.recalcular_duplicatas(projeto_id)
        return removidas

    def recalcular_duplicatas(self, projeto_id: int) -> None:
        """Marca cópias de mesmo SHA-256; a primeira (por patch_id) é a referência."""
        with self._conectar() as conexao:
            linhas = conexao.execute(
                "SELECT patch_id, sha256 FROM imagens WHERE projeto_id = ? ORDER BY patch_id",
                (projeto_id,),
            ).fetchall()
            primeira: dict[str, str] = {}
            atualizacoes = []
            for linha in linhas:
                referencia = primeira.setdefault(linha["sha256"], linha["patch_id"])
                duplicata = "" if referencia == linha["patch_id"] else referencia
                atualizacoes.append((duplicata, projeto_id, linha["patch_id"]))
            conexao.executemany(
                "UPDATE imagens SET duplicata_de = ? WHERE projeto_id = ? AND patch_id = ?",
                atualizacoes,
            )

    def contagens(self, projeto_id: int) -> dict[str, int]:
        imagens = self.listar_imagens(projeto_id)
        return {
            "total": len(imagens),
            "pendentes": sum(1 for i in imagens if i.estado_rotulo == "pendente"),
            "rotuladas": sum(1 for i in imagens if i.estado_rotulo == "rotulada"),
            "revisar": sum(1 for i in imagens if i.estado_rotulo == "revisar"),
            "duplicatas": sum(1 for i in imagens if i.duplicata_de),
            "problemas": sum(1 for i in imagens if i.integridade in {"ausente", "alterada", "ilegivel"}),
            "nao_verificadas": sum(1 for i in imagens if i.integridade == "nao_verificada"),
        }
