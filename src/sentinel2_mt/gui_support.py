from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def normalizar_bbox(bbox: list[float]) -> list[float]:
    """Normaliza coordenadas desenhadas em qualquer direção na interface."""
    if len(bbox) != 4:
        raise ValueError(
            "A bounding box deve conter exatamente 4 valores: "
            "[oeste, sul, leste, norte]."
        )

    oeste, sul, leste, norte = (float(valor) for valor in bbox)
    oeste, leste = sorted((oeste, leste))
    sul, norte = sorted((sul, norte))
    if oeste == leste or sul == norte:
        raise ValueError("A bounding box deve respeitar oeste < leste e sul < norte.")
    return [oeste, sul, leste, norte]


def montar_argumentos_operacao(
    operacao: str,
    config: str | Path,
    *,
    inicio: str = "",
    fim: str = "",
    max_itens: int | None = None,
    oauth_json: str = "",
    tamanho_lote: int | None = None,
) -> list[str]:
    """Traduz o formulário da GUI para argumentos da CLI oficial."""
    if operacao not in {"catalogar", "baixar", "sincronizar"}:
        raise ValueError(f"Operação desconhecida: {operacao}")

    argumentos = ["--config", str(Path(config).expanduser())]
    if operacao == "baixar":
        argumentos.append("--baixar")
    elif operacao == "sincronizar":
        argumentos.append("--sincronizar")

    if operacao != "sincronizar":
        if inicio:
            argumentos.extend(["--inicio", inicio])
        if fim:
            argumentos.extend(["--fim", fim])
        if max_itens is not None:
            if max_itens < 0:
                raise ValueError("A quantidade máxima de cenas não pode ser negativa.")
            argumentos.extend(["--max-itens", str(max_itens)])
    else:
        if oauth_json and not oauth_json.startswith("${"):
            oauth = Path(oauth_json).expanduser()
            if not oauth.is_file():
                raise FileNotFoundError(f"JSON OAuth não encontrado: {oauth}")
            argumentos.extend(["--oauth-json", str(oauth)])
        if tamanho_lote is not None:
            if tamanho_lote <= 0:
                raise ValueError("O tamanho do lote deve ser maior que zero.")
            argumentos.extend(["--lote", str(tamanho_lote)])
    return argumentos


class LocalConfigStore:
    """Persistência local dos perfis de região; não armazena tokens OAuth."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._inicializar()

    def _conectar(self) -> sqlite3.Connection:
        conexao = sqlite3.connect(self.db_path)
        conexao.row_factory = sqlite3.Row
        return conexao

    def _inicializar(self) -> None:
        with self._conectar() as conexao:
            conexao.execute(
                """
                CREATE TABLE IF NOT EXISTS configuracoes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome_regiao TEXT NOT NULL,
                    uf TEXT,
                    bbox TEXT NOT NULL,
                    colecao TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def salvar(self, dados: dict[str, Any]) -> int:
        nome = str(dados.get("nome_regiao") or "Região salva").strip()
        bbox = json.dumps(normalizar_bbox(dados.get("bbox", [-61.65, -18.05, -50.20, -7.30])))

        # Credenciais e tokens não pertencem aos perfis locais de região.
        payload = dict(dados)
        payload.pop("oauth_json", None)
        payload.pop("token_json", None)

        with self._conectar() as conexao:
            cursor = conexao.execute(
                """
                INSERT INTO configuracoes (nome_regiao, uf, bbox, colecao, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    nome or "Região salva",
                    dados.get("uf", "MT"),
                    bbox,
                    dados.get("colecao", "S2-16D-2"),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)

    def listar(self) -> list[dict[str, Any]]:
        with self._conectar() as conexao:
            linhas = conexao.execute(
                """
                SELECT id, nome_regiao, uf, bbox, colecao, payload, created_at
                FROM configuracoes
                ORDER BY id DESC
                """
            ).fetchall()
        return [dict(linha) for linha in linhas]

    def listar_por_uf(self) -> dict[str, list[dict[str, Any]]]:
        agrupado: dict[str, list[dict[str, Any]]] = {}
        for perfil in self.listar():
            uf = (perfil.get("uf") or "GERAL").strip().upper() or "GERAL"
            agrupado.setdefault(uf, []).append(perfil)
        return dict(sorted(agrupado.items()))

    def carregar(self, item_id: int) -> dict[str, Any] | None:
        with self._conectar() as conexao:
            linha = conexao.execute(
                """
                SELECT id, nome_regiao, uf, bbox, colecao, payload, created_at
                FROM configuracoes
                WHERE id = ?
                """,
                (item_id,),
            ).fetchone()
        return dict(linha) if linha else None

    def excluir(self, item_id: int) -> None:
        with self._conectar() as conexao:
            conexao.execute("DELETE FROM configuracoes WHERE id = ?", (item_id,))

    def listar_presets_por_uf(self) -> dict[str, list[dict[str, Any]]]:
        return self.listar_por_uf()

    # Compatibilidade com a primeira implementação local da GUI.
    def salvar_configuracao(self, dados: dict[str, Any]) -> int:
        return self.salvar(dados)

    def listar_configuracoes(self) -> list[dict[str, Any]]:
        return self.listar()

    def carregar_por_id(self, item_id: int) -> dict[str, Any] | None:
        return self.carregar(item_id)

    def limpar(self) -> None:
        with self._conectar() as conexao:
            conexao.execute("DELETE FROM configuracoes")
