"""
`kestra-migrate`: the command line. One subcommand per source platform; today, `vra`. And `init`, which writes
the config file that says which model to call.

    kestra-migrate init [--force]
    kestra-migrate vra run <export folder>
"""
from __future__ import annotations

import argparse
import sys

from kestra_migration_assistant import __version__

TOOLS = {"vra": "assess a VMware vRA / vRO (Aria Automation and Orchestrator) estate from its export"}
INIT_HELP = "write the config file that says which model to call, and how (in your home folder, never the export)"


def init(force: bool) -> int:
    from kestra_migration_assistant import config as K
    try:
        path, provider = K.init(force=force)
    except FileExistsError as e:
        print(f"kestra-migrate init: {e} already exists; edit it, or pass --force to write it again",
              file=sys.stderr)
        return 1
    print(f"Wrote {path} (readable by you only), set to provider {provider} and model {K.DEFAULT_MODEL}.\n"
          "Every setting in it is explained in a comment; edit it to change how kestra-migrate calls a model.")
    try:
        print(f"Each run will use: {K.resolve().describe()}.")
    except K.ConfigError as e:
        print(f"Not ready yet: {e}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    ap = argparse.ArgumentParser(
        prog="kestra-migrate",
        description="Kestra Migration Assistant for vRA: assess a VMware vRA / vRO estate for migration to Kestra, "
                    "from its export. Everything runs on this machine; only the configured model is called.",
        epilog="Start with: kestra-migrate vra run <export folder>")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="tool", required=True, metavar="{" + ",".join([*TOOLS, "init"]) + "}")
    ip = sub.add_parser("init", help=INIT_HELP, description=INIT_HELP)
    ip.add_argument("--force", action="store_true", help="replace an existing config file")
    for name, help_ in TOOLS.items():
        p = sub.add_parser(name, help=help_, add_help=False)
        p.add_argument("args", nargs=argparse.REMAINDER)
    if argv and argv[0] == "init":
        return init(ap.parse_args(argv).force)
    ns = ap.parse_args(argv[:1] if argv and not argv[0].startswith("-") else argv)
    if ns.tool == "vra":
        from kestra_migration_assistant.vra import cli
        return cli.main(argv[1:])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
