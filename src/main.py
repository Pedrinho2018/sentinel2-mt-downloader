from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1:
        from sentinel2_mt.cli import main as cli_main

        return cli_main(sys.argv[1:])

    from tui import SentinelTUI

    SentinelTUI().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
