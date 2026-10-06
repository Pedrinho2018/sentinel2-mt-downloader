from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

import requests
import rasterio
from rasterio.windows import Window
from tqdm import tqdm


class ClienteDownloadHTTP:
    def __init__(self, timeout_segundos: int, chunk_mb: int, user_agent: str = "sentinel2-mt-downloader/2.0") -> None:
        self.timeout_segundos = timeout_segundos
        self.chunk_bytes = max(1, chunk_mb) * 1024 * 1024
        self.sessao = requests.Session()
        self.sessao.headers.update({"User-Agent": user_agent})

    def baixar(self, url: str, destino: Path) -> str:
        if urlsplit(url).scheme.casefold() not in {"http", "https"}:
            raise ValueError("O download aceita apenas URLs HTTP ou HTTPS")
        destino.parent.mkdir(parents=True, exist_ok=True)
        if self._arquivo_integro(destino):
            return "ja_existia"

        with NamedTemporaryFile(
            prefix=f".{destino.name}.",
            suffix=f".part{destino.suffix}",
            dir=destino.parent,
            delete=False,
        ) as arquivo_temporario:
            parcial = Path(arquivo_temporario.name)
        try:
            with self.sessao.get(url, stream=True, timeout=(20, self.timeout_segundos)) as resposta:
                resposta.raise_for_status()
                tamanho = self._content_length(resposta)
                recebidos = 0
                with parcial.open("wb") as arquivo, tqdm(
                    total=tamanho or None,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=destino.name,
                    leave=False,
                ) as barra:
                    for parte in resposta.iter_content(chunk_size=self.chunk_bytes):
                        if parte:
                            arquivo.write(parte)
                            recebidos += len(parte)
                            barra.update(len(parte))
            if tamanho and recebidos != tamanho:
                raise IOError(f"Download incompleto: recebidos {recebidos} de {tamanho} bytes")
            if not self._arquivo_integro(parcial):
                raise IOError("O arquivo recebido não passou na validação de integridade")
            parcial.replace(destino)
            return "baixado"
        except Exception:
            parcial.unlink(missing_ok=True)
            raise

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
        if caminho.suffix.casefold() not in {".tif", ".tiff", ".jp2"}:
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
