from __future__ import annotations

import sys


def _executar_gui(argv: list[str]) -> int:
    from gerar_config_gui import main as gui_main

    return gui_main(argv)


def _executar_tui() -> int:
    from tui import SentinelTUI

    SentinelTUI().run()
    return 0


def _executar_cli(argv: list[str]) -> int:
    from .cli import main as cli_main

    return cli_main(argv)


def main(argv: list[str] | None = None) -> int:
    """Abre a GUI por padrão e preserva os modos de terminal explicitamente."""
    argumentos = list(sys.argv[1:] if argv is None else argv)
    if not argumentos:
        return _executar_gui([])

    modo, *restante = argumentos
    if modo == "--gui":
        return _executar_gui(restante)
    if modo == "--tui":
        if restante:
            print("[ERRO] --tui não aceita argumentos adicionais.", file=sys.stderr)
            return 2
        return _executar_tui()
    if modo == "--cli":
        return _executar_cli(restante)

    # Compatibilidade com scripts anteriores: qualquer opção da CLI continua
    # funcionando sem exigir o prefixo --cli.
    return _executar_cli(argumentos)
