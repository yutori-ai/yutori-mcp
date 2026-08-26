from __future__ import annotations

import argparse
import os
import sys

from . import __version__


def _computer_use_main() -> None:
    from .computer_use.cli import dispatch, register_parser

    parser = argparse.ArgumentParser(prog="yutori-mcp")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--env", choices=("prod", "dev"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    register_parser(subparsers)
    args = parser.parse_args()
    if args.env:
        os.environ["YUTORI_ENV"] = args.env
    else:
        os.environ.pop("YUTORI_ENV", None)
    raise SystemExit(dispatch(args.computer_use_command, args))


def main() -> None:
    if "computer-use" in sys.argv[1:]:
        _computer_use_main()
        return

    from .server import main as server_main

    server_main()


if __name__ == "__main__":
    main()
