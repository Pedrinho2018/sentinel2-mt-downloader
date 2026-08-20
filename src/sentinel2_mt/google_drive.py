from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import TypeVar

from .configuracao import ConfiguracaoProjeto


T = TypeVar("T")


def dividir_em_lotes(itens: Sequence[T], tamanho: int) -> Iterator[Sequence[T]]:
    if tamanho <= 0:
        raise ValueError("O tamanho do lote deve ser maior que zero")
    for inicio in range(0, len(itens), tamanho):
        yield itens[inicio : inicio + tamanho]


class AutenticadorGoogleDrive:
    # Acesso somente aos arquivos criados/abertos pelo aplicativo.
    # Evita solicitar o escopo restrito que dá acesso a todo o Drive.
    ESCOPO = ("https://www.googleapis.com/auth/drive.file",)

    @staticmethod
    def _autorizar_no_navegador(fluxo):
        # Evita que o Google reutilize silenciosamente uma conta conectada que
        # não esteja cadastrada como testadora do projeto OAuth.
        return fluxo.run_local_server(port=0, prompt="select_account")

    def autenticar(self, oauth_path: Path, token_path: Path):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        credenciais = None
        if token_path.exists():
            credenciais = Credentials.from_authorized_user_file(str(token_path), self.ESCOPO)
        if not credenciais or not credenciais.valid:
            if credenciais and credenciais.expired and credenciais.refresh_token:
                credenciais.refresh(Request())
            else:
                if not oauth_path.is_file():
                    raise FileNotFoundError(f"Arquivo OAuth não encontrado: {oauth_path}")
                fluxo = InstalledAppFlow.from_client_secrets_file(str(oauth_path), self.ESCOPO)
                credenciais = self._autorizar_no_navegador(fluxo)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(credenciais.to_json(), encoding="utf-8")

        from googleapiclient.discovery import build

        return build("drive", "v3", credentials=credenciais, cache_discovery=False)


class SincronizadorGoogleDrive:
    MIME_PASTA = "application/vnd.google-apps.folder"

    def __init__(
        self,
        config: ConfiguracaoProjeto,
        autenticador: AutenticadorGoogleDrive | None = None,
        saida: Callable[[str], None] = print,
    ) -> None:
        self.config = config
        self.autenticador = autenticador or AutenticadorGoogleDrive()
        self.saida = saida
        self._cache_pastas: dict[tuple[str, str], str] = {}

    def sincronizar(self, oauth_path: Path | None = None, tamanho_lote: int | None = None) -> int:
        pasta_local = self.config.caminho(self.config.download.pasta)
        if not pasta_local.exists():
            raise FileNotFoundError(f"Pasta de imagens não encontrada: {pasta_local}")

        sync = self.config.sincronizacao
        lote = tamanho_lote or sync.tamanho_lote
        arquivos = self.arquivos_para_sincronizar(pasta_local, sync.extensoes)
        if not arquivos:
            self.saida("[SINCRONIZAÇÃO] Nenhuma imagem encontrada para sincronizar.")
            return 0

        oauth = oauth_path or self.config.caminho(sync.oauth_json)
        token = self.config.caminho(sync.token_json)
        if token.exists():
            self.saida("[OAUTH] Reutilizando autorização salva.")
        else:
            self.saida(f"[OAUTH] Credencial: {oauth.name}")
            self.saida("[OAUTH] Escopo mínimo solicitado: drive.file.")
            self.saida(
                "[OAUTH] Projeto em teste: faça login com uma conta cadastrada em Test users."
            )
            self.saida("[OAUTH] Aguardando autorização no navegador...")
        service = self.autenticador.autenticar(oauth, token)
        self.saida("[OAUTH] Autenticação concluída.")
        pasta_raiz = self._localizar_ou_criar_pasta(service, sync.pasta_remota, sync.pasta_id) if sync.pasta_remota else sync.pasta_id
        lotes = list(dividir_em_lotes(arquivos, lote))
        self.saida(f"[SINCRONIZAÇÃO] {len(arquivos)} arquivo(s) em {len(lotes)} lote(s) de até {lote}.")

        for numero, arquivos_lote in enumerate(lotes, start=1):
            self.saida(f"  [LOTE {numero}/{len(lotes)}] {len(arquivos_lote)} arquivo(s)")
            for arquivo in arquivos_lote:
                relativo = arquivo.relative_to(pasta_local)
                pai_id = pasta_raiz
                for parte in relativo.parts[:-1]:
                    pai_id = self._localizar_ou_criar_pasta(service, parte, pai_id)
                status = self._enviar_arquivo(service, arquivo, pai_id)
                self.saida(f"    [{status.upper()}] {relativo.as_posix()}")
        return len(lotes)

    @staticmethod
    def arquivos_para_sincronizar(pasta: Path, extensoes: Sequence[str]) -> list[Path]:
        normalizadas = {ext.casefold() if ext.startswith(".") else f".{ext.casefold()}" for ext in extensoes}
        return sorted(
            (arquivo for arquivo in pasta.rglob("*") if arquivo.is_file() and arquivo.suffix.casefold() in normalizadas),
            key=lambda arquivo: arquivo.as_posix(),
        )

    def _localizar_ou_criar_pasta(self, service, nome: str, pai: str) -> str:
        chave = (pai, nome)
        if chave in self._cache_pastas:
            return self._cache_pastas[chave]
        nome_query = self._valor_query(nome)
        consulta = (
            f"name = '{nome_query}' and mimeType = '{self.MIME_PASTA}' "
            f"and '{pai}' in parents and trashed = false"
        )
        encontrados = (
            service.files().list(q=consulta, spaces="drive", fields="files(id,name)", pageSize=1).execute().get("files", [])
        )
        if encontrados:
            pasta_id = encontrados[0]["id"]
        else:
            metadados = {"name": nome, "mimeType": self.MIME_PASTA, "parents": [pai]}
            pasta_id = service.files().create(body=metadados, fields="id").execute()["id"]
        self._cache_pastas[chave] = pasta_id
        return pasta_id

    def _enviar_arquivo(self, service, arquivo: Path, pai_id: str) -> str:
        from googleapiclient.http import MediaFileUpload

        nome = self._valor_query(arquivo.name)
        consulta = f"name = '{nome}' and '{pai_id}' in parents and trashed = false"
        encontrados = service.files().list(q=consulta, spaces="drive", fields="files(id)", pageSize=1).execute().get("files", [])
        media = MediaFileUpload(str(arquivo), resumable=True)
        if encontrados:
            service.files().update(fileId=encontrados[0]["id"], media_body=media).execute()
            return "atualizado"
        metadados = {"name": arquivo.name, "parents": [pai_id]}
        service.files().create(body=metadados, media_body=media, fields="id").execute()
        return "enviado"

    @staticmethod
    def _valor_query(valor: str) -> str:
        return valor.replace("\\", "\\\\").replace("'", "\\'")
