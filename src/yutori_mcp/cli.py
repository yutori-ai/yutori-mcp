"""CLI entry point for Yutori authentication."""

from __future__ import annotations

import sys

from .auth import clear_config, run_login_flow, show_status

USAGE = """Usage: yutori <command>

Commands:
  login   Log in to Yutori and save API key
  logout  Remove saved API key
  status  Show current authentication status
"""


def main() -> None:
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(1)

    command = sys.argv[1]

    if command == "login":
        success = run_login_flow()
        sys.exit(0 if success else 1)
    elif command == "logout":
        clear_config()
        print("Logged out successfully.")
    elif command == "status":
        show_status()
    elif command in ("-h", "--help", "help"):
        print(USAGE)
    else:
        print(f"Unknown command: {command}")
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()
