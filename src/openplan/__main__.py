from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] in ("auth", "subscribe", "status", "--help", "-h"):
        from openplan.cli import main as cli_main
        cli_main()
    else:
        from openplan.server import main as server_main
        import anyio
        anyio.run(server_main)


if __name__ == "__main__":
    main()
