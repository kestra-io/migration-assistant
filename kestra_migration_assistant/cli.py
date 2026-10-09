"""
`kestra-migrate`: the command line. One subcommand per source platform; today, `vra`.

    kestra-migrate vra run <export folder>
"""
from __future__ import annotations

import argparse
import sys

from kestra_migration_assistant import __version__

TOOLS = {"vra": "assess a VMware vRA / vRO (Aria Automation and Orchestrator) estate from its export"}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    ap = argparse.ArgumentParser(
        prog="kestra-migrate",
        description="Kestra Migration Assistant: assess an automation estate for migration to Kestra. "
                    "Everything runs on this machine; only the configured model is called.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="tool", required=True, metavar="{" + ",".join(TOOLS) + "}")
    for name, help_ in TOOLS.items():
        p = sub.add_parser(name, help=help_, add_help=False)
        p.add_argument("args", nargs=argparse.REMAINDER)
    ns = ap.parse_args(argv[:1] if argv and not argv[0].startswith("-") else argv)
    if ns.tool == "vra":
        from kestra_migration_assistant.vra import cli
        return cli.main(argv[1:])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
