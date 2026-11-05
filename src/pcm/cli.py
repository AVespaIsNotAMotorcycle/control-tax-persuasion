"""Command line: ``pcm split`` and ``pcm pilot``."""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="pcm")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("split", help="make the pre-registered problem split (once)")
    sp.add_argument("args", nargs=argparse.REMAINDER)

    pp = sub.add_parser("pilot", help="run the pilot and write a report")
    pp.add_argument("--config", type=Path, default=Path("configs/pilot.toml"))
    pp.add_argument("--run-id", required=True)
    pp.add_argument("--limit", type=int, help="use only this many backdoors (and twice as many honest)")
    pp.add_argument("--monitors", nargs="+", help="monitor models to score with")
    pp.add_argument("--samples", type=int, help="scores per item (overrides the config)")
    pp.add_argument("--budget", type=float, help="spending cap in USD for this run")

    a = p.parse_args(argv)
    if a.cmd == "split":
        from pcm.data import main as split_main

        split_main(a.args)
    elif a.cmd == "pilot":
        from pcm.pilot import run_pilot

        r = run_pilot(a.config, a.run_id, a.limit, a.monitors, a.samples, a.budget)
        print(Path("runs", a.run_id, "report.md").read_text())
        print(f"Status: {r['status']}; spend ${r['spend_usd']:.2f}")


if __name__ == "__main__":
    main()
