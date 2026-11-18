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
# Triagem de adequação (independe do estado de rotulagem). Imagens com rótulo
# são sempre "protegida" ou "revisar" e nunca podem ser arquivadas.
TRIAGENS = ("aprovada", "revisar", "descartar", "protegida")
COLUNAS_TRIAGEM = (
    ("triagem", "TEXT NOT NULL DEFAULT ''"),
    ("triagem_motivo", "TEXT NOT NULL DEFAULT ''"),
    ("arquivada", "INTEGER NOT NULL DEFAULT 0"),
    ("arquivada_em", "TEXT NOT NULL DEFAULT ''"),
    ("motivo_arquivamento", "TEXT NOT NULL DEFAULT ''"),
)
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
    triagem: str = ""
    triagem_motivo: str = ""
    arquivada: int = 0
    arquivada_em: str = ""
    motivo_arquivamento: str = ""

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
                CREATE TABLE IF NOT EXISTS anotacoes_deteccao (
                    projeto_id INTEGER NOT NULL,
                    patch_id TEXT NOT NULL,
                    versao_esquema INTEGER NOT NULL,
                    conteudo TEXT NOT NULL,
                    caixas INTEGER NOT NULL,
                    erros_json TEXT NOT NULL DEFAULT '[]',
                    sha256_imagem TEXT NOT NULL,
                    importado_em TEXT NOT NULL,
                    PRIMARY KEY (projeto_id, patch_id),
                    FOREIGN KEY (projeto_id, patch_id) REFERENCES imagens(projeto_id, patch_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS rotulos_classificacao (
                    projeto_id INTEGER NOT NULL,
                    patch_id TEXT NOT NULL,
                    classe TEXT NOT NULL,
                    versao_esquema INTEGER NOT NULL,
                    atualizado_em TEXT NOT NULL,
                    PRIMARY KEY (projeto_id, patch_id),
                    FOREIGN KEY (projeto_id, patch_id) REFERENCES imagens(projeto_id, patch_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS historico_imagens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    projeto_id INTEGER NOT NULL,
                    patch_id TEXT NOT NULL,
                    acao TEXT NOT NULL,
                    detalhe TEXT NOT NULL DEFAULT '',
                    em TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_historico ON historico_imagens(projeto_id, patch_id);
                """
            )
            # Migração aditiva: bancos antigos ganham as colunas de triagem/arquivamento.
            existentes = {linha["name"] for linha in conexao.execute("PRAGMA table_info(imagens)")}
            for nome, definicao in COLUNAS_TRIAGEM:
                if nome not in existentes:
                    conexao.execute(f"ALTER TABLE imagens ADD COLUMN {nome} {definicao}")
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
        self.revalidar_rotulos(projeto_id)
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

    def listar_imagens(self, projeto_id: int, *, incluir_arquivadas: bool = True) -> list[ImagemDataset]:
        filtro = "" if incluir_arquivadas else " AND arquivada = 0"
        with self._conectar() as conexao:
            linhas = conexao.execute(
                f"SELECT * FROM imagens WHERE projeto_id = ?{filtro} ORDER BY data, patch_id",
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
        """Remove imagens do projeto (só os registros; arquivos não são apagados).

        Imagens com rótulo ou anotação são recusadas: para tirá-las de uso, não há
        caminho que apague rótulos; use a triagem "revisar".
        """
        with self._conectar() as conexao:
            protegidas = self._com_rotulo(conexao, projeto_id, list(patch_ids))
            if protegidas:
                raise ValueError(f"Imagem rotulada não pode ser removida: {sorted(protegidas)[0]}")
            cursor = conexao.executemany(
                "DELETE FROM imagens WHERE projeto_id = ? AND patch_id = ?",
                [(projeto_id, patch_id) for patch_id in patch_ids],
            )
            removidas = cursor.rowcount
        self.recalcular_duplicatas(projeto_id)
        return removidas

    # ------------------------------------------------------- triagem/arquivamento

    def _com_rotulo(self, conexao, projeto_id: int, patch_ids: list[str]) -> set[str]:
        """IDs que têm rótulo, anotação ou estado diferente de 'pendente'."""
        if not patch_ids:
            return set()
        marcas = ",".join("?" for _ in patch_ids)
        consulta = f"""
            SELECT patch_id FROM imagens WHERE projeto_id = ? AND patch_id IN ({marcas}) AND estado_rotulo != 'pendente'
            UNION SELECT patch_id FROM anotacoes_deteccao WHERE projeto_id = ? AND patch_id IN ({marcas})
            UNION SELECT patch_id FROM rotulos_classificacao WHERE projeto_id = ? AND patch_id IN ({marcas})"""
        parametros = [projeto_id, *patch_ids] * 3
        return {linha["patch_id"] for linha in conexao.execute(consulta, parametros)}

    def _historico(self, conexao, projeto_id: int, registros: list[tuple[str, str, str]]) -> None:
        agora = _agora()
        conexao.executemany(
            "INSERT INTO historico_imagens (projeto_id, patch_id, acao, detalhe, em) VALUES (?, ?, ?, ?, ?)",
            [(projeto_id, patch_id, acao, detalhe, agora) for patch_id, acao, detalhe in registros],
        )

    def registrar_triagem(self, projeto_id: int, decisoes: dict[str, tuple[str, str]]) -> int:
        """Grava a triagem {patch_id: (triagem, motivo)} numa única transação.

        Imagens rotuladas só aceitam "protegida" ou "revisar"; "protegida" só vale
        para imagens rotuladas. Qualquer violação cancela o lote inteiro.
        """
        for patch_id, (triagem, motivo) in decisoes.items():
            if triagem not in TRIAGENS:
                raise ValueError(f"Triagem inválida para {patch_id}: {triagem}")
            if triagem in {"descartar", "revisar"} and not str(motivo).strip():
                raise ValueError(f"Informe o motivo da triagem '{triagem}' de {patch_id}.")
        with self._conectar() as conexao:
            ids = self._ids_existentes(conexao, projeto_id, list(decisoes))
            rotuladas = self._com_rotulo(conexao, projeto_id, ids)
            for patch_id in ids:
                triagem = decisoes[patch_id][0]
                if patch_id in rotuladas and triagem not in {"protegida", "revisar"}:
                    raise ValueError(f"Imagem rotulada só pode ser 'protegida' ou 'revisar': {patch_id}")
                if patch_id not in rotuladas and triagem == "protegida":
                    raise ValueError(f"'protegida' é reservado a imagens rotuladas: {patch_id}")
            conexao.executemany(
                "UPDATE imagens SET triagem = ?, triagem_motivo = ? WHERE projeto_id = ? AND patch_id = ?",
                [(decisoes[p][0], str(decisoes[p][1]).strip(), projeto_id, p) for p in ids],
            )
            self._historico(conexao, projeto_id, [
                (p, "triagem", json.dumps({"triagem": decisoes[p][0], "motivo": decisoes[p][1]}, ensure_ascii=False))
                for p in ids
            ])
        return len(ids)

    def arquivar_imagens(self, projeto_id: int, patch_ids: list[str], motivo: str) -> int:
        """Arquivamento lógico e reversível: a imagem sai dos lotes e da lista ativa.

        Nenhum arquivo é apagado e o catálogo científico não muda. Imagens com
        rótulo/anotação são recusadas (a operação inteira é cancelada).
        """
        motivo = str(motivo).strip()
        if not motivo:
            raise ValueError("Informe o motivo do arquivamento.")
        with self._conectar() as conexao:
            ids = self._ids_existentes(conexao, projeto_id, list(dict.fromkeys(patch_ids)))
            protegidas = self._com_rotulo(conexao, projeto_id, ids)
            if protegidas:
                raise ValueError(f"Imagem rotulada não pode ser arquivada: {sorted(protegidas)[0]}")
            ativas = [
                l["patch_id"] for l in conexao.execute(
                    f"SELECT patch_id FROM imagens WHERE projeto_id = ? AND arquivada = 0 AND patch_id IN ({','.join('?' for _ in ids)})",
                    (projeto_id, *ids),
                )
            ] if ids else []
            agora = _agora()
            conexao.executemany(
                "UPDATE imagens SET arquivada = 1, arquivada_em = ?, motivo_arquivamento = ? WHERE projeto_id = ? AND patch_id = ?",
                [(agora, motivo, projeto_id, p) for p in ativas],
            )
            self._historico(conexao, projeto_id, [(p, "arquivar", motivo) for p in ativas])
        return len(ativas)

    def retirar_rotuladas(self, projeto_id: int, patch_ids: list[str], motivo: str) -> int:
        """Retira do projeto imagens ROTULADAS, por decisão explícita do usuário.

        Diferente de `arquivar_imagens` (que recusa rotuladas), aqui a anotação e o
        rótulo continuam guardados no banco, sem alteração: a imagem só fica
        arquivada e com estado "revisar" (fora do treino e dos lotes).
        `restaurar_imagens` devolve o estado anterior.
        """
        motivo = str(motivo).strip()
        if not motivo:
            raise ValueError("Informe o motivo da retirada.")
        with self._conectar() as conexao:
            ids = self._ids_existentes(conexao, projeto_id, list(dict.fromkeys(patch_ids)))
            marcas = ",".join("?" for _ in ids)
            linhas = conexao.execute(
                f"SELECT patch_id, estado_rotulo, arquivada FROM imagens WHERE projeto_id = ? AND patch_id IN ({marcas})",
                (projeto_id, *ids),
            ).fetchall() if ids else []
            alvo = [l for l in linhas if not l["arquivada"]]
            agora = _agora()
            conexao.executemany(
                """UPDATE imagens SET arquivada = 1, arquivada_em = ?, motivo_arquivamento = ?, estado_rotulo = 'revisar',
                triagem = 'revisar', triagem_motivo = ? WHERE projeto_id = ? AND patch_id = ?""",
                [(agora, motivo, motivo, projeto_id, l["patch_id"]) for l in alvo],
            )
            self._historico(conexao, projeto_id, [
                (l["patch_id"], "retirar", json.dumps({"motivo": motivo, "estado_anterior": l["estado_rotulo"]}, ensure_ascii=False))
                for l in alvo
            ])
        return len(alvo)

    def restaurar_imagens(self, projeto_id: int, patch_ids: list[str], motivo: str = "") -> int:
        """Desfaz o arquivamento (a triagem é mantida para rastreabilidade).

        Imagens retiradas com `retirar_rotuladas` voltam ao estado de rotulagem anterior.
        """
        with self._conectar() as conexao:
            ids = self._ids_existentes(conexao, projeto_id, list(dict.fromkeys(patch_ids)))
            arquivadas = [
                l["patch_id"] for l in conexao.execute(
                    f"SELECT patch_id FROM imagens WHERE projeto_id = ? AND arquivada = 1 AND patch_id IN ({','.join('?' for _ in ids)})",
                    (projeto_id, *ids),
                )
            ] if ids else []
            conexao.executemany(
                "UPDATE imagens SET arquivada = 0, arquivada_em = '', motivo_arquivamento = '' WHERE projeto_id = ? AND patch_id = ?",
                [(projeto_id, p) for p in arquivadas],
            )
            for p in arquivadas:
                ultima = conexao.execute(
                    "SELECT acao, detalhe FROM historico_imagens WHERE projeto_id = ? AND patch_id = ? ORDER BY id DESC LIMIT 1",
                    (projeto_id, p),
                ).fetchone()
                if ultima is not None and ultima["acao"] == "retirar":
                    anterior = json.loads(ultima["detalhe"]).get("estado_anterior", "pendente")
                    self._definir_estado(conexao, projeto_id, [p], anterior)
            self._historico(conexao, projeto_id, [(p, "restaurar", str(motivo).strip()) for p in arquivadas])
        return len(arquivadas)

    def historico_imagens(self, projeto_id: int, patch_id: str | None = None) -> list[dict]:
        filtro, parametros = ("AND patch_id = ?", (projeto_id, patch_id)) if patch_id else ("", (projeto_id,))
        with self._conectar() as conexao:
            linhas = conexao.execute(
                f"SELECT patch_id, acao, detalhe, em FROM historico_imagens WHERE projeto_id = ? {filtro} ORDER BY id",
                parametros,
            ).fetchall()
        return [dict(l) for l in linhas]

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

    # ------------------------------------------------------------------ rótulos

    def _exigir_tipo(self, projeto_id: int, tipo: str) -> Projeto:
        projeto = self.obter_projeto(projeto_id)
        if projeto.tipo != tipo:
            raise ValueError(
                "Este projeto é de "
                + ("detecção (caixas)" if projeto.tipo == "deteccao" else "classificação (uma classe por imagem)")
                + "; rótulos de outro tipo não são aceitos."
            )
        return projeto

    def _definir_estado(self, conexao, projeto_id: int, patch_ids: list[str], estado: str) -> None:
        if estado not in ESTADOS_ROTULO:
            raise ValueError(f"Estado de rotulagem inválido: {estado}")
        conexao.executemany(
            "UPDATE imagens SET estado_rotulo = ? WHERE projeto_id = ? AND patch_id = ?",
            [(estado, projeto_id, patch_id) for patch_id in patch_ids],
        )

    def _ids_existentes(self, conexao, projeto_id: int, patch_ids: list[str]) -> list[str]:
        existentes = {
            linha["patch_id"]
            for linha in conexao.execute("SELECT patch_id FROM imagens WHERE projeto_id = ?", (projeto_id,))
        }
        faltantes = [patch_id for patch_id in patch_ids if patch_id not in existentes]
        if faltantes:
            raise ValueError(f"Imagem não pertence ao projeto: {faltantes[0]}")
        return patch_ids

    def rotular_classificacao(self, projeto_id: int, patch_ids: list[str], classe: str) -> int:
        """Atribui uma classe do esquema atual às imagens (somente projetos de classificação)."""
        self._exigir_tipo(projeto_id, "classificacao")
        esquema = self.esquema_atual(projeto_id)
        if classe not in esquema.classes:
            raise ValueError(f"A classe '{classe}' não existe no esquema v{esquema.versao}.")
        agora = _agora()
        with self._conectar() as conexao:
            ids = self._ids_existentes(conexao, projeto_id, list(patch_ids))
            conexao.executemany(
                """INSERT INTO rotulos_classificacao VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(projeto_id, patch_id) DO UPDATE SET classe = excluded.classe,
                versao_esquema = excluded.versao_esquema, atualizado_em = excluded.atualizado_em""",
                [(projeto_id, patch_id, classe, esquema.versao, agora) for patch_id in ids],
            )
            self._definir_estado(conexao, projeto_id, ids, "rotulada")
        return len(ids)

    def limpar_rotulos(self, projeto_id: int, patch_ids: list[str]) -> None:
        """Volta as imagens para 'pendente' removendo rótulos e anotações."""
        with self._conectar() as conexao:
            ids = self._ids_existentes(conexao, projeto_id, list(patch_ids))
            for tabela in ("rotulos_classificacao", "anotacoes_deteccao"):
                conexao.executemany(
                    f"DELETE FROM {tabela} WHERE projeto_id = ? AND patch_id = ?",
                    [(projeto_id, patch_id) for patch_id in ids],
                )
            self._definir_estado(conexao, projeto_id, ids, "pendente")

    def rotulos_classificacao(self, projeto_id: int) -> dict[str, tuple[str, int]]:
        with self._conectar() as conexao:
            linhas = conexao.execute(
                "SELECT patch_id, classe, versao_esquema FROM rotulos_classificacao WHERE projeto_id = ?",
                (projeto_id,),
            ).fetchall()
        return {l["patch_id"]: (l["classe"], l["versao_esquema"]) for l in linhas}

    def registrar_anotacao_deteccao(
        self,
        projeto_id: int,
        patch_id: str,
        versao_esquema: int,
        conteudo: str,
        caixas: int,
        erros: list[str],
        sha256_imagem: str,
    ) -> str:
        """Guarda o .txt YOLO importado; com erros, a imagem vai para 'revisar'."""
        self._exigir_tipo(projeto_id, "deteccao")
        estado = "revisar" if erros else "rotulada"
        with self._conectar() as conexao:
            self._ids_existentes(conexao, projeto_id, [patch_id])
            conexao.execute(
                """INSERT INTO anotacoes_deteccao VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(projeto_id, patch_id) DO UPDATE SET versao_esquema = excluded.versao_esquema,
                conteudo = excluded.conteudo, caixas = excluded.caixas, erros_json = excluded.erros_json,
                sha256_imagem = excluded.sha256_imagem, importado_em = excluded.importado_em""",
                (projeto_id, patch_id, versao_esquema, conteudo, caixas,
                 json.dumps(erros, ensure_ascii=False), sha256_imagem, _agora()),
            )
            self._definir_estado(conexao, projeto_id, [patch_id], estado)
        return estado

    def anotacoes_deteccao(self, projeto_id: int) -> dict[str, dict]:
        with self._conectar() as conexao:
            linhas = conexao.execute(
                "SELECT * FROM anotacoes_deteccao WHERE projeto_id = ?", (projeto_id,)
            ).fetchall()
        return {
            l["patch_id"]: {
                "versao_esquema": l["versao_esquema"],
                "conteudo": l["conteudo"],
                "caixas": l["caixas"],
                "erros": json.loads(l["erros_json"]),
                "sha256_imagem": l["sha256_imagem"],
                "importado_em": l["importado_em"],
            }
            for l in linhas
        }

    def revalidar_rotulos(self, projeto_id: int) -> int:
        """Após nova versão do esquema, marca 'revisar' o que deixou de ser compatível.

        Classificação: a classe precisa existir no esquema atual. Detecção: os IDs
        YOLO são posições na lista; se as classes já usadas mudaram de posição ou
        sumiram, as caixas antigas apontariam para outra classe.
        """
        projeto = self.obter_projeto(projeto_id)
        esquemas = {e.versao: e.classes for e in self.listar_esquemas(projeto_id)}
        atual = self.esquema_atual(projeto_id).classes
        marcadas: list[str] = []
        if projeto.tipo == "classificacao":
            for patch_id, (classe, _versao) in self.rotulos_classificacao(projeto_id).items():
                if classe not in atual:
                    marcadas.append(patch_id)
        else:
            for patch_id, anotacao in self.anotacoes_deteccao(projeto_id).items():
                antigas = esquemas.get(anotacao["versao_esquema"], ())
                if tuple(atual[: len(antigas)]) != tuple(antigas):
                    marcadas.append(patch_id)
        if marcadas:
            with self._conectar() as conexao:
                self._definir_estado(conexao, projeto_id, marcadas, "revisar")
        return len(marcadas)

    def contagens(self, projeto_id: int) -> dict[str, int]:
        """Estados de rotulagem e integridade contam só as imagens ativas (não arquivadas)."""
        todas = self.listar_imagens(projeto_id)
        imagens = [i for i in todas if not i.arquivada]
        return {
            "total": len(todas),
            "ativas": len(imagens),
            "arquivadas": len(todas) - len(imagens),
            "aprovadas": sum(1 for i in imagens if i.triagem == "aprovada"),
            "triagem_revisar": sum(1 for i in imagens if i.triagem == "revisar"),
            "protegidas": sum(1 for i in imagens if i.triagem == "protegida"),
            "sem_triagem": sum(1 for i in imagens if not i.triagem),
            "pendentes": sum(1 for i in imagens if i.estado_rotulo == "pendente"),
            "rotuladas": sum(1 for i in imagens if i.estado_rotulo == "rotulada"),
            "revisar": sum(1 for i in imagens if i.estado_rotulo == "revisar"),
            "duplicatas": sum(1 for i in imagens if i.duplicata_de),
            "problemas": sum(1 for i in imagens if i.integridade in {"ausente", "alterada", "ilegivel"}),
            "nao_verificadas": sum(1 for i in imagens if i.integridade == "nao_verificada"),
        }
