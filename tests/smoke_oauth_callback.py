from __future__ import annotations

from pathlib import Path

from sentinel2_mt.oauth_callback import pagina_retorno_oauth


PAGINAS = (
    Path("/tmp/sentinel2-mt-oauth-sucesso.html"),
    Path("/tmp/sentinel2-mt-oauth-erro.html"),
)


def main() -> int:
    PAGINAS[0].write_text(pagina_retorno_oauth(True), encoding="utf-8")
    PAGINAS[1].write_text(
        pagina_retorno_oauth(False, "A conta selecionada não está autorizada."),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
