from __future__ import annotations

import argparse
import sys

from . import __version__

# Duplicates the key set of adapter.ENVIRONMENT_BASE_URLS rather than importing it: this module
# (and computer_use.cli, which it dispatches to) must stay importable without pulling in the
# yutori SDK, and adapter.py needs the SDK's DEFAULT_BASE_URL to build that dict.
# tests/test_computer_use.py::test_entrypoint_env_choices_match_adapter_environments guards the
# two from drifting apart.
_ENV_CHOICES = ("prod", "dev")


def _computer_use_main() -> None:
    from .computer_use.cli import apply_computer_use_environment, dispatch, register_parser

    parser = argparse.ArgumentParser(prog="yutori-mcp")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--env", choices=_ENV_CHOICES)
    subparsers = parser.add_subparsers(dest="command", required=True)
    register_parser(subparsers)
    args = parser.parse_args()
    apply_computer_use_environment(args.env)
    raise SystemExit(dispatch(args.computer_use_command, args))


def main() -> None:
    if "computer-use" in sys.argv[1:]:
        _computer_use_main()
        return

    from .server import main as server_main

    server_main()


if __name__ == "__main__":
    main()
