from __future__ import annotations

from pathlib import Path

import requests
from tqdm import tqdm


class ClienteDownloadHTTP:
    def __init__(self, timeout_segundos: int, chunk_mb: int, user_agent: str = "sentinel2-mt-downloader/2.0") -> None:
        self.timeout_segundos = timeout_segundos
        self.chunk_bytes = max(1, chunk_mb) * 1024 * 1024
        self.sessao = requests.Session()
        self.sessao.headers.update({"User-Agent": user_agent})

    def baixar(self, url: str, destino: Path) -> str:
        destino.parent.mkdir(parents=True, exist_ok=True)
        if destino.exists() and destino.stat().st_size > 0:
            return "ja_existia"

        parcial = destino.with_suffix(destino.suffix + ".part")
        parcial.unlink(missing_ok=True)
        try:
            with self.sessao.get(url, stream=True, timeout=(20, self.timeout_segundos)) as resposta:
                resposta.raise_for_status()
                tamanho = int(resposta.headers.get("content-length", 0))
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
                            barra.update(len(parte))
            parcial.replace(destino)
            return "baixado"
        except Exception:
            parcial.unlink(missing_ok=True)
            raise
