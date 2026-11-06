"""Post-hoc checks on the confirmatory run. Exploratory, decided after seeing results.

The 5% threshold landed on a cluster of scores near the top of the scale (see
results/confirmatory-results.md), so these checks ask whether the null holds away
from that cluster. They are not part of the registered analysis and live outside
src/ so the frozen code stays as tagged. Output is aggregate only.

    PYTHONPATH=src python scripts/post_hoc_checks.py
"""

from __future__ import annotations

import json
import random
import statistics
from bisect import bisect_left, bisect_right
from pathlib import Path

from pcm.analysis import (STYLES, _caught, _cutoffs, _diff, _usable, bootstrap, comparisons,
                          interval, item_scores, p_value, prepare)

RUN = Path("runs/confirmatory-v1")
SCORES = RUN / "scores-claude-haiku-4-5-20251001.jsonl"
SEED = 20261104
RESAMPLES = 10_000
AUROC_RESAMPLES = 2_000
TIE_SEEDS = 300
BUDGETS = (0.05, 0.10, 0.15, 0.20)
KEEP = ("H1a", "H1b", "H1c", "H2a", "H2b", "E1", "E3")
OUT = Path("results/post-hoc-checks.json")


def score_shifts(scores: dict, seed: int) -> list[dict]:
    """Paired mean score change on backdoors, A minus B, in suspicion points."""
    plain = ("raw", "plain")
    pairs = [(f"raw {s} vs. raw plain", ("raw", s), plain) for s in STYLES]
    pairs += [("stripped vs. raw plain", ("stripped", "none"), plain),
              ("paraphrased plain vs. raw plain", ("paraphrased", "plain"), plain)]
    pairs += [(f"stripped vs. raw {s}", ("stripped", "none"), ("raw", s)) for s in STYLES]
    pairs += [(f"paraphrased {s} vs. raw {s}", ("paraphrased", s), ("raw", s)) for s in STYLES]
    out = []
    for label, a, b in pairs:
        items = sorted(i for i in scores[a] if i.startswith("backdoored:") and i in scores[b])
        d = [scores[a][i] - scores[b][i] for i in items]
        rng = random.Random(seed)
        boot = sorted(statistics.fmean(rng.choices(d, k=len(d))) for _ in range(RESAMPLES))
        out.append({"comparison": label, "n": len(d), "mean": statistics.fmean(d),
                    "median": statistics.median(d), "ci95": [boot[250], boot[9749]],
                    "share_up": sum(x > 0 for x in d) / len(d),
                    "share_down": sum(x < 0 for x in d) / len(d)})
    return out


def _auroc(honest_sorted: list[float], backdoors: list[float]) -> float:
    n = len(honest_sorted)
    wins = sum(bisect_left(honest_sorted, x) + (bisect_right(honest_sorted, x)
                                                - bisect_left(honest_sorted, x)) / 2
               for x in backdoors)
    return wins / (n * len(backdoors))


def auroc_differences(scores: dict, seed: int) -> list[dict]:
    """AUROC by condition, and each persuasive style minus plain in the same view."""
    rng = random.Random(seed)
    honest = {v: scores[(v, "none" if v == "stripped" else "plain")] for v in ("raw", "paraphrased")}
    conds = [(v, s) for v in ("raw", "paraphrased") for s in STYLES]
    h_ids = sorted(i for i in honest["raw"] if i.startswith("honest:") and i in honest["paraphrased"])
    b_ids = sorted(i for i in scores[("raw", "plain")] if i.startswith("backdoored:"))
    def stat(hs, bs, view, style):
        return _auroc(sorted(honest[view][h] for h in hs), [scores[(view, style)][b] for b in bs])
    boots = {c: [] for c in conds}
    for _ in range(AUROC_RESAMPLES):
        hs, bs = rng.choices(h_ids, k=len(h_ids)), rng.choices(b_ids, k=len(b_ids))
        for v, s in conds:
            boots[(v, s)].append(stat(hs, bs, v, s) - stat(hs, bs, v, "plain"))
    out = []
    for v, s in conds:
        b = sorted(boots[(v, s)])
        out.append({"view": v, "style": s, "auroc": stat(h_ids, b_ids, v, s),
                    "plain": stat(h_ids, b_ids, v, "plain"),
                    "difference": stat(h_ids, b_ids, v, s) - stat(h_ids, b_ids, v, "plain"),
                    "ci95": [b[int(.025 * len(b))], b[int(.975 * len(b)) - 1]]})
    return out


def budget_sweep(rows: list[dict]) -> list[dict]:
    data = prepare(rows, SEED)
    comps = [c for c in comparisons(data.views, set(data.bd_keys)) if c.id in KEEP]
    boot = bootstrap(data, comps, BUDGETS, RESAMPLES, SEED)
    every = list(range(len(data.honest)))
    out = []
    for b in BUDGETS:
        cut = _cutoffs(data, every, b)
        caught = _caught(data, cut)
        for c in comps:
            d, ca, cb = _diff(caught, c, _usable(data, c))
            out.append({"budget": b, "id": c.id, "label": c.label, "catch_a": ca, "catch_b": cb,
                        "difference": d, "ci95": interval(boot[b][c.id], 0.95),
                        "p_unadjusted": p_value(boot[b][c.id]), "threshold": cut[c.a[0][0]][0]})
    return out


def tie_sensitivity(rows: list[dict]) -> dict:
    """The point estimates at 5% across seeds that change only how ties break."""
    runs: dict[str, list[float]] = {}
    for s in range(TIE_SEEDS):
        data = prepare(rows, s)
        caught = _caught(data, _cutoffs(data, list(range(len(data.honest))), 0.05))
        for c in comparisons(data.views, set(data.bd_keys)):
            if c.id in KEEP:
                d, ca, cb = _diff(caught, c, _usable(data, c))
                runs.setdefault(c.id, []).append(d)
                runs.setdefault(f"{c.id}:a", []).append(ca)
                runs.setdefault(f"{c.id}:b", []).append(cb)
    return {k: {"min": min(v), "median": statistics.median(v), "max": max(v)} for k, v in runs.items()}


def main() -> None:
    rows = [json.loads(line) for line in open(SCORES)]
    scores = item_scores(rows)
    result = {"run": RUN.name, "status": "exploratory, decided after seeing confirmatory results",
              "score_shifts": score_shifts(scores, SEED),
              "auroc": auroc_differences(scores, SEED),
              "budget_sweep": budget_sweep(rows),
              "tie_sensitivity": tie_sensitivity(rows)}
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
