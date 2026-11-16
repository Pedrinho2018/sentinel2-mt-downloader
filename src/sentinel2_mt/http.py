from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit, urlunsplit

import requests
import rasterio
from rasterio.windows import Window
from tqdm import tqdm


# Parcial retomável: nome fixo por destino, para que uma nova execução o encontre.
SUFIXO_PARCIAL = ".part"
# Parciais antigos criados com nome aleatório (NamedTemporaryFile) não são retomáveis.
PARCIAL_LEGADO_RE = re.compile(r"^\.(?P<nome>.+)\.[A-Za-z0-9_]{6,12}\.part(\.[A-Za-z0-9]+)?$")
INTERVALO_PROGRESSO_S = 0.5


@dataclass(frozen=True)
class ProgressoDownload:
    arquivo: str
    recebidos: int
    total: int  # 0 quando o servidor não informa o tamanho
    velocidade_bps: float
    retomado: bool
    concluido: bool = False

    def para_dict(self) -> dict:
        return {
            "tipo": "arquivo",
            "arquivo": self.arquivo,
            "recebidos": self.recebidos,
            "total": self.total,
            "velocidade_bps": round(self.velocidade_bps, 1),
            "retomado": self.retomado,
            "concluido": self.concluido,
        }


def caminho_parcial(destino: Path) -> Path:
    """Arquivo temporário oculto ao lado do destino, nunca confundido com o final."""
    return destino.with_name(f".{destino.name}{SUFIXO_PARCIAL}")


def eh_parcial(nome: str) -> bool:
    return nome.startswith(".") and (
        nome.endswith(SUFIXO_PARCIAL) or bool(PARCIAL_LEGADO_RE.match(nome))
    )


def listar_parciais_legados(pasta: Path) -> list[Path]:
    """Parciais de nome aleatório deixados por versões anteriores; não podem ser retomados."""
    if not pasta.is_dir():
        return []
    encontrados: list[Path] = []
    for diretorio, subpastas, arquivos in os.walk(pasta, followlinks=False):
        subpastas[:] = [nome for nome in subpastas if not nome.startswith(".")]
        for nome in arquivos:
            caminho = Path(diretorio) / nome
            if PARCIAL_LEGADO_RE.match(nome) and caminho.is_file() and not caminho.is_symlink():
                encontrados.append(caminho)
    return sorted(encontrados)


def remover_parciais_legados(pasta: Path) -> tuple[int, int]:
    """Remove somente parciais legados dentro de `pasta`; retorna (arquivos, bytes)."""
    raiz = pasta.resolve()
    removidos = 0
    liberados = 0
    for caminho in listar_parciais_legados(pasta):
        try:
            caminho.resolve().relative_to(raiz)
        except ValueError:
            continue
        tamanho = caminho.stat().st_size
        caminho.unlink(missing_ok=True)
        removidos += 1
        liberados += tamanho
    return removidos, liberados


class ClienteDownloadHTTP:
    def __init__(
        self,
        timeout_segundos: int,
        chunk_mb: int,
        user_agent: str = "sentinel2-mt-downloader/2.0",
        progresso: Callable[[ProgressoDownload], None] | None = None,
    ) -> None:
        self.timeout_segundos = timeout_segundos
        self.chunk_bytes = max(1, chunk_mb) * 1024 * 1024
        self.sessao = requests.Session()
        self.sessao.headers.update({"User-Agent": user_agent})
        self.progresso = progresso
        self.relogio: Callable[[], float] = time.monotonic

    def baixar(self, url: str, destino: Path) -> str:
        if urlsplit(url).scheme.casefold() not in {"http", "https"}:
            raise ValueError("O download aceita apenas URLs HTTP ou HTTPS")
        destino.parent.mkdir(parents=True, exist_ok=True)
        if self._arquivo_integro(destino):
            return "ja_existia"

        parcial = caminho_parcial(destino)
        controle = parcial.with_name(parcial.name + ".json")
        identidade = self._identidade_url(url)
        inicio = self._bytes_retomaveis(parcial, controle, identidade)
        cabecalhos: dict[str, str] = {}
        if inicio:
            cabecalhos["Range"] = f"bytes={inicio}-"
            validador = json.loads(controle.read_text(encoding="utf-8")).get("validador", "")
            if validador:
                # Se o arquivo remoto mudou, o servidor devolve 200 com o conteúdo completo.
                cabecalhos["If-Range"] = validador

        reiniciar = False
        try:
            with self.sessao.get(
                url, stream=True, timeout=(20, self.timeout_segundos), headers=cabecalhos
            ) as resposta:
                if resposta.status_code == 416 and inicio:
                    # Faixa inválida: o parcial não corresponde ao remoto; recomeça do zero.
                    self._descartar(parcial, controle)
                    reiniciar = True
                else:
                    resposta.raise_for_status()
                    retomado = bool(inicio) and resposta.status_code == 206
                    if retomado and not self._content_range_confere(resposta, inicio):
                        self._descartar(parcial, controle)
                        raise IOError("O servidor devolveu uma faixa diferente da solicitada")
                    if not retomado:
                        inicio = 0
                    total = self._tamanho_total(resposta, inicio, retomado)
                    self._gravar_controle(controle, identidade, resposta)
                    recebidos = self._receber(
                        resposta, parcial, destino.name, inicio, total, retomado
                    )
        except requests.HTTPError:
            self._descartar(parcial, controle)
            raise
        # Falhas de rede e downloads incompletos mantêm o parcial para retomada.
        if reiniciar:
            return self.baixar(url, destino)
        if total and recebidos > total:
            self._descartar(parcial, controle)
            raise IOError(f"Download maior que o esperado: {recebidos} de {total} bytes")
        if total and recebidos != total:
            raise IOError(
                f"Download incompleto: recebidos {recebidos} de {total} bytes; "
                "execute novamente para retomar"
            )
        if not self._arquivo_integro(parcial):
            # Conteúdo inválido não pode ser retomado; remove para não reaproveitar lixo.
            self._descartar(parcial, controle)
            raise IOError("O arquivo recebido não passou na validação de integridade")
        parcial.replace(destino)
        controle.unlink(missing_ok=True)
        return "retomado" if retomado else "baixado"

    def _receber(
        self,
        resposta,
        parcial: Path,
        nome: str,
        inicio: int,
        total: int,
        retomado: bool,
    ) -> int:
        recebidos = inicio
        t0 = self.relogio()
        ultimo_aviso = t0
        modo = "ab" if retomado else "wb"
        with parcial.open(modo) as arquivo, tqdm(
            total=total or None,
            initial=inicio,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc=nome,
            leave=False,
            disable=self.progresso is not None,
        ) as barra:
            for parte in resposta.iter_content(chunk_size=self.chunk_bytes):
                if not parte:
                    continue
                arquivo.write(parte)
                recebidos += len(parte)
                barra.update(len(parte))
                agora = self.relogio()
                if self.progresso and agora - ultimo_aviso >= INTERVALO_PROGRESSO_S:
                    ultimo_aviso = agora
                    self._avisar(nome, recebidos, inicio, total, t0, agora, retomado)
        if self.progresso:
            self._avisar(nome, recebidos, inicio, total, t0, self.relogio(), retomado, True)
        return recebidos

    def _avisar(
        self,
        nome: str,
        recebidos: int,
        inicio: int,
        total: int,
        t0: float,
        agora: float,
        retomado: bool,
        concluido: bool = False,
    ) -> None:
        decorrido = max(agora - t0, 1e-6)
        velocidade = (recebidos - inicio) / decorrido
        self.progresso(ProgressoDownload(nome, recebidos, total, velocidade, retomado, concluido))

    @staticmethod
    def _identidade_url(url: str) -> str:
        # Sem query/fragmento: tokens temporários não impedem a retomada do mesmo arquivo.
        partes = urlsplit(url)
        publica = urlunsplit((partes.scheme, partes.netloc.rsplit("@", 1)[-1], partes.path, "", ""))
        return hashlib.sha256(publica.encode("utf-8")).hexdigest()

    @staticmethod
    def _bytes_retomaveis(parcial: Path, controle: Path, identidade: str) -> int:
        if not parcial.is_file() or parcial.is_symlink():
            return 0
        try:
            dados = json.loads(controle.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            dados = {}
        if not isinstance(dados, dict) or dados.get("url") != identidade:
            ClienteDownloadHTTP._descartar(parcial, controle)
            return 0
        return parcial.stat().st_size

    @staticmethod
    def _gravar_controle(controle: Path, identidade: str, resposta) -> None:
        validador = resposta.headers.get("ETag", "")
        if not validador or validador.startswith("W/"):
            validador = resposta.headers.get("Last-Modified", "")
        controle.write_text(
            json.dumps({"url": identidade, "validador": validador}), encoding="utf-8"
        )

    @staticmethod
    def _descartar(parcial: Path, controle: Path) -> None:
        parcial.unlink(missing_ok=True)
        controle.unlink(missing_ok=True)

    @staticmethod
    def _content_range_confere(resposta, inicio: int) -> bool:
        faixa = resposta.headers.get("Content-Range", "")
        correspondencia = re.match(r"bytes (\d+)-\d+/(\d+|\*)", faixa)
        return bool(correspondencia) and int(correspondencia.group(1)) == inicio

    @classmethod
    def _tamanho_total(cls, resposta, inicio: int, retomado: bool) -> int:
        if retomado:
            faixa = resposta.headers.get("Content-Range", "")
            correspondencia = re.match(r"bytes \d+-\d+/(\d+)", faixa)
            if correspondencia:
                return int(correspondencia.group(1))
            restante = cls._content_length(resposta)
            return inicio + restante if restante else 0
        return cls._content_length(resposta)

    @staticmethod
    def _content_length(resposta) -> int:
        if resposta.headers.get("content-encoding", "identity").casefold() not in {"", "identity"}:
            return 0
        try:
            return max(0, int(resposta.headers.get("content-length", 0)))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _arquivo_integro(caminho: Path) -> bool:
        if not caminho.is_file() or caminho.stat().st_size <= 0:
            return False
        nome = caminho.name.casefold()
        if nome.endswith(SUFIXO_PARCIAL):
            nome = nome[: -len(SUFIXO_PARCIAL)]
        if Path(nome).suffix not in {".tif", ".tiff", ".jp2"}:
            return True
        try:
            with rasterio.open(caminho) as raster:
                if raster.width <= 0 or raster.height <= 0 or raster.count <= 0:
                    return False
                pontos = {
                    (0, 0),
                    (raster.width - 1, 0),
                    (0, raster.height - 1),
                    (raster.width - 1, raster.height - 1),
                    (raster.width // 2, raster.height // 2),
                }
                for x, y in pontos:
                    raster.read(1, window=Window(x, y, 1, 1))
            return True
        except (OSError, rasterio.errors.RasterioError):
            return False
