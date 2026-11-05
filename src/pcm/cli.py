"""Command line: ``pcm split``, ``pcm pilot`` and ``pcm analyze``."""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="pcm")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("split", help="make the pre-registered problem split (once)")
    sp.add_argument("args", nargs=argparse.REMAINDER)

    pp = sub.add_parser("pilot", help="run the pilot or the confirmatory sample and write a report")
    pp.add_argument("--config", type=Path, default=Path("configs/pilot.toml"))
    pp.add_argument("--run-id", required=True)
    pp.add_argument("--limit", type=int, help="use only this many backdoors (and twice as many honest)")
    pp.add_argument("--monitors", nargs="+", help="monitor models to score with")
    pp.add_argument("--samples", type=int, help="scores per item (overrides the config)")
    pp.add_argument("--budget", type=float, help="spending cap in USD for this run")
    pp.add_argument("--batch", action=argparse.BooleanOptionalAction, default=None,
                    help="send Claude calls as Message Batches at half price (overrides the config)")
    pp.add_argument("--allow-unfrozen", action="store_true",
                    help="run even if code, configs or prompts differ from the config's require_tag")

    ap = sub.add_parser("analyze", help="run the pre-registered analysis on a run's scores")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--monitor", help="monitor label, if the run scored more than one")
    ap.add_argument("--resamples", type=int, default=10_000)

    a = p.parse_args(argv)
    if a.cmd == "split":
        from pcm.data import main as split_main

        split_main(a.args)
    elif a.cmd == "pilot":
        from pcm.pilot import run_pilot

        r = run_pilot(a.config, a.run_id, a.limit, a.monitors, a.samples, a.budget, a.batch, a.allow_unfrozen)
        print(Path("runs", a.run_id, "report.md").read_text())
        print(f"Status: {r['status']}; spend ${r['spend_usd']:.2f}")
    elif a.cmd == "analyze":
        from pcm.analysis import run_analysis

        res = run_analysis(a.run_id, a.monitor, a.resamples)
        label = res["scores_file"].removeprefix("scores-").removesuffix(".jsonl")
        print(Path("runs", a.run_id, f"analysis-{label}.md").read_text())


if __name__ == "__main__":
    main()
