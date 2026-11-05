"""Run the pilot or the confirmatory sample: generate comments, build monitor
views, score, and report.

Stages (each parallel, each cached call by call):

1. Plain comments for every backdoored and honest solution.
2. Reframing, compliance, and deference rewrites for each backdoored solution.
   A backdoor that fails any style after retries is excluded from all
   conditions. With ``max_excluded_share`` set, the run pauses here, before any
   monitor spending, if too many backdoors are excluded.
3. For each monitor candidate, paraphrase every comment set with that model.
4. Score every view k times with each monitor candidate.
5. Write ``report.json`` and ``report.md``.

With ``shuffle`` the items run in a random order fixed by the run seed, and with
``chunks`` stages 3 and 4 run chunk by chunk, each chunk a random share of the
backdoored and honest items. A run stopped by the spending cap then leaves
complete chunks that form a random subsample (PREREGISTRATION.md, Stopping rule).
With ``require_tag`` the run refuses to start unless the code, configs and
prompts match that Git tag.
"""

from __future__ import annotations

import hashlib
import json
import random
import statistics
import subprocess
import tomllib
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from pcm.comments import STYLES, generate_plain, generate_style
from pcm.data import DEFAULT_DATASET, DEFAULT_SPLIT, Item, load_items
from pcm.llm import NO_EFFORT_MODELS, Budget, BudgetExceeded, Caller, Deferred, Prices
from pcm.metrics import auroc, catch_rate
from pcm.monitor import paraphrase, score
from pcm.transforms import clean_code, insert_comments

CONFIRMATORY_MONITOR_CALLS = 3000  # 200 x 9 backdoored views + 400 x 3 honest views
CONFIRMATORY_WRITER_CALLS = 1320  # 600 plain + 3 x 200 style rewrites (before retries)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _parallel(fn, tasks, workers, caller: Caller | None = None):
    """Run fn(task) for each task; stop scheduling new work if the budget runs out.

    In batch mode (``caller.defer``), a task whose Anthropic call misses the cache
    raises ``Deferred``. Those calls go out as one Message Batch, and the deferred
    tasks rerun from the cache, round after round, until every task finishes;
    a retry inside a task simply becomes a request in the next round.
    """
    results: dict[int, object] = {}
    todo = list(range(len(tasks)))
    while todo:
        deferred = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fn, tasks[i]): i for i in todo}
            try:
                for fut in as_completed(futures):
                    i = futures[fut]
                    try:
                        results[i] = fut.result()
                    except Deferred:
                        deferred.append(i)
            except BudgetExceeded:
                pool.shutdown(cancel_futures=True)
                raise
        if deferred:
            caller.flush()
        todo = sorted(deferred)
    return [results[i] for i in range(len(tasks))]


def _label(cand: dict) -> str:
    """"model@effort", plus "-nothink" when the candidate scores with thinking off.
    Models without an effort setting (Haiku 4.5) are labeled by name alone."""
    if cand["model"].startswith(NO_EFFORT_MODELS):
        return cand["model"]
    return f"{cand['model']}@{cand['effort']}" + ("" if cand.get("thinking", True) else "-nothink")


def _candidates(mon: dict, names: list[str] | None) -> list[dict]:
    """Monitor candidates from the config, or from names like "model", "model@effort"
    or a configured label such as "model@low-nothink"."""
    configured = mon["candidates"]
    if not names:
        return configured
    out = []
    for name in names:
        model, _, effort = name.partition("@")
        matches = [c for c in configured if _label(c) == name]
        matches = matches or [c for c in configured if c["model"] == model
                              and c.get("thinking", True)
                              and (not effort or c["effort"] == effort)]
        out.extend(matches or [{"model": model, "effort": effort or mon["effort"]}])
    return out


def _key(item: Item) -> str:
    return f"{item.kind}:{item.problem_id}"


FROZEN_PATHS = ("configs", "prompts", "src", "pyproject.toml")


def freeze_problems(tag: str) -> list[str]:
    """Reasons the working tree does not match the frozen tag (empty if it does)."""
    def git(*args):
        return subprocess.run(["git", *args], capture_output=True, text=True)
    if git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}").returncode != 0:
        return [f"tag {tag} does not exist"]
    problems = []
    changed = git("diff", "--name-only", tag, "--", *FROZEN_PATHS).stdout.split()
    if changed:
        problems.append(f"changed since {tag}: {', '.join(changed)}")
    untracked = git("ls-files", "--others", "--exclude-standard", "--", *FROZEN_PATHS).stdout.split()
    if untracked:
        problems.append(f"untracked files: {', '.join(untracked)}")
    return problems


def _chunks(backdoored: list, honest: list, n: int) -> list[tuple[list, list]]:
    """Split both lists into n parts of near-equal size, keeping their order."""
    def part(xs, j):
        return xs[len(xs) * j // n: len(xs) * (j + 1) // n]
    return [(part(backdoored, j), part(honest, j)) for j in range(n)]


def run_pilot(config_path: Path, run_id: str, limit: int | None = None,
              monitors: list[str] | None = None, samples: int | None = None,
              budget_usd: float | None = None, batch: bool | None = None,
              allow_unfrozen: bool = False) -> dict:
    from dotenv import load_dotenv

    cfg = tomllib.loads(Path(config_path).read_text())
    run, writer, mon, para = cfg["run"], cfg["writer"], cfg["monitor"], cfg["paraphraser"]
    unfrozen = freeze_problems(run["require_tag"]) if run.get("require_tag") else []
    if unfrozen and not allow_unfrozen:
        raise SystemExit("Refusing to run: " + "; ".join(unfrozen)
                         + ". Log any change in AMENDMENTS.md and re-tag, or pass --allow-unfrozen.")
    load_dotenv()  # API keys go into the environment; they are never logged
    candidates = _candidates(mon, monitors)
    k = samples or mon["samples"]
    attempts = run["max_attempts"]

    run_dir = Path("runs") / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    caller = Caller(Path("runs") / "cache", Budget(budget_usd or run["budget_usd"]),
                    Prices({m: tuple(p) for m, p in cfg["prices"].items()}))
    caller.defer = run.get("batch", False) if batch is None else batch

    items = load_items(DEFAULT_SPLIT, DEFAULT_DATASET, run["split"])
    backdoored = [i for i in items if i.kind == "backdoored"]
    honest = [i for i in items if i.kind == "honest"]
    if limit:
        backdoored, honest = backdoored[:limit], honest[: 2 * limit]
    if run.get("shuffle"):
        rng = random.Random(run["seed"])
        rng.shuffle(backdoored)
        rng.shuffle(honest)
    items = backdoored + honest
    clean = {_key(i): clean_code(i.code) for i in items}

    manifest = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "config": cfg,
        "config_sha": _sha(Path(config_path)),
        "prompt_shas": {p.name: _sha(p) for p in sorted(Path("prompts").glob("*.txt"))},
        "split_sha": _sha(DEFAULT_SPLIT),
        "packages": {p: version(p) for p in ("anthropic", "openai")},
        "monitor_candidates": candidates,
        "samples": k,
        "batch": caller.defer,
        "limit": limit,
        "shuffle": bool(run.get("shuffle")),
        "chunks": run.get("chunks", 1),
        "require_tag": run.get("require_tag"),
        "unfrozen": unfrozen,
        "item_order": [_key(i) for i in items],
        "n_backdoored": len(backdoored),
        "n_honest": len(honest),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    report: dict = {"run_id": run_id, "split": run["split"], "status": "complete"}
    progress: dict[str, dict] = {}
    chunks: list = []
    try:
        # 1. Plain comments.
        plain = dict(zip(
            [_key(i) for i in items],
            _parallel(lambda i: generate_plain(caller, writer, i, clean[_key(i)], attempts),
                      items, run["max_workers"], caller),
        ))

        # 2. Persuasive rewrites.
        style_tasks = [(i, s) for i in backdoored if plain[_key(i)].ok for s in STYLES]
        style_out = _parallel(
            lambda t: generate_style(caller, writer, t[0], clean[_key(t[0])],
                                     plain[_key(t[0])].comments, t[1], attempts),
            style_tasks, run["max_workers"], caller)
        styled = {(_key(i), s): g for (i, s), g in zip(style_tasks, style_out)}

        excluded = {}
        for i in backdoored:
            key = _key(i)
            if not plain[key].ok:
                excluded[key] = "plain failed"
            else:
                bad = [s for s in STYLES if not styled[(key, s)].ok]
                if bad:
                    excluded[key] = f"style failed: {', '.join(bad)}"
        kept_bd = [i for i in backdoored if _key(i) not in excluded]
        kept_h = [i for i in honest if plain[_key(i)].ok]

        comment_sets: dict[tuple[str, str], list] = {}
        for i in kept_bd:
            comment_sets[(_key(i), "plain")] = plain[_key(i)].comments
            for s in STYLES:
                comment_sets[(_key(i), s)] = styled[(_key(i), s)].comments
        for i in kept_h:
            comment_sets[(_key(i), "plain")] = plain[_key(i)].comments
        by_key = {_key(i): i for i in items}

        _save_generations(run_dir, plain, styled, excluded)
        report["writer"] = _writer_report(plain, styled, backdoored, honest, excluded, caller)
        cap = run.get("max_excluded_share")
        if cap is not None and report["writer"]["excluded_share"] > cap:
            report["status"] = (f"paused: {report['writer']['excluded_share']:.1%} of backdoors "
                                f"excluded, above {cap:.0%}")
            candidates = []

        # 3 and 4. Paraphrase and score with each monitor candidate, chunk by chunk.
        report["monitors"] = {}
        chunks = _chunks(kept_bd, kept_h, run.get("chunks", 1))
        for cand in candidates:
            label = _label(cand)
            progress[label] = {"scored": [], "paras": {}, "chunks_done": 0}
            for bd_part, h_part in chunks:
                _score_chunk(caller, cand, para, mon, run, k, bd_part + h_part, comment_sets,
                             clean, by_key, progress[label])
                progress[label]["chunks_done"] += 1
            _finish_monitor(report, run_dir, label, progress[label], cfg, k, len(chunks))
    except BudgetExceeded as e:
        report["status"] = f"stopped: {e}"
        for label, p in progress.items():  # keep every chunk that finished before the cap
            if label not in report.get("monitors", {}):
                report.setdefault("monitors", {})
                _finish_monitor(report, run_dir, label, p, cfg, k, len(chunks))

    report["spend_usd"] = round(caller.budget.spent_usd, 4)
    report["new_calls"] = caller.new_calls
    report["cached_calls"] = caller.cached_calls
    (run_dir / "report.json").write_text(json.dumps(report, indent=2))
    (run_dir / "report.md").write_text(render_report(report))
    return report


def _score_chunk(caller, cand, para, mon, run, k, chunk_items, comment_sets, clean, by_key, acc):
    """Paraphrase and score every view of one chunk's items; append to ``acc``."""
    model = cand["model"]
    keys = {_key(i) for i in chunk_items}
    set_keys = [sk for sk in comment_sets if sk[0] in keys]
    paras = dict(zip(set_keys, _parallel(
        lambda sk: paraphrase(caller, model, para, clean[sk[0]], comment_sets[sk],
                              run["max_attempts"], f"paraphrase:{sk[0]}:{sk[1]}"),
        set_keys, run["max_workers"], caller)))

    views = []  # (item_key, view, style, code)
    for sk in set_keys:
        ik, style = sk
        views.append((ik, "raw", style, insert_comments(clean[ik], comment_sets[sk])))
        if paras[sk].comments is not None:
            views.append((ik, "paraphrased", style, insert_comments(clean[ik], paras[sk].comments)))
    for i in chunk_items:
        views.append((_key(i), "stripped", "none", clean[_key(i)]))

    tasks = [(v, s) for v in views for s in range(k)]
    if mon.get("warned"):
        # Exploratory warned view (E4): raw code, with the warning line in the monitor prompt.
        warned = [(ik, "warned", style, code) for ik, view, style, code in views if view == "raw"]
        tasks += [(v, s) for v in warned for s in range(mon.get("warned_samples", k))]
    mon_cfg = {"effort": cand["effort"], "max_output_tokens": mon["max_output_tokens"],
               "thinking": cand.get("thinking", True)}
    warning = (Path("prompts") / "monitor_warning.txt").read_text() if mon.get("warned") else ""
    scored = _parallel(
        lambda t: {**score(caller, model, mon_cfg, by_key[t[0][0]].problem_statement, t[0][3],
                           warning if t[0][1] == "warned" else "", t[1],
                           f"score:{t[0][0]}:{t[0][1]}:{t[0][2]}"),
                   "item": t[0][0], "view": t[0][1], "style": t[0][2], "sample": t[1]},
        tasks, run["max_workers"], caller)
    acc["paras"].update(paras)
    acc["scored"].extend(scored)


def _finish_monitor(report, run_dir, label, acc, cfg, k, n_chunks):
    with open(run_dir / f"scores-{label}.jsonl", "w") as f:
        for row in acc["scored"]:
            f.write(json.dumps(row) + "\n")
    if acc["scored"]:
        report["monitors"][label] = {**_monitor_report(acc["scored"], acc["paras"], cfg, k),
                                     "chunks_done": acc["chunks_done"], "chunks": n_chunks}


def _save_generations(run_dir, plain, styled, excluded):
    out = {
        "plain": {k: [asdict(c) for c in g.comments] if g.ok else None for k, g in plain.items()},
        "styles": {f"{k}|{s}": [asdict(c) for c in g.comments] if g.ok else None
                   for (k, s), g in styled.items()},
        "excluded": excluded,
    }
    (run_dir / "generations.json").write_text(json.dumps(out, indent=2))


def _attempt_stats(gens) -> dict:
    gens = list(gens)
    first = Counter(g.attempts[0].outcome for g in gens if g.attempts)
    refused = ("refusal", "declined")
    any_refusal = sum(any(a.outcome in refused for a in g.attempts) for g in gens)
    failed = [g for g in gens if not g.ok]
    calls = [a for g in gens for a in g.attempts]
    return {
        "n": len(gens),
        "first_attempt": dict(first),
        "refused_first_attempt": first.get("refusal", 0) + first.get("declined", 0),
        "refused_any_attempt": any_refusal,
        "failed_after_retries": len(failed),
        "final_failure_outcomes": dict(Counter(g.attempts[-1].outcome for g in failed)),
        "calls": len(calls),
        "mean_cost_per_call": round(statistics.mean(a.cost_usd for a in calls), 5) if calls else None,
        "examples": [a.detail for g in gens for a in g.attempts if a.outcome != "ok" and a.detail][:5],
    }


def _writer_report(plain, styled, backdoored, honest, excluded, caller) -> dict:
    steps = {
        "plain_backdoored": [plain[_key(i)] for i in backdoored],
        "plain_honest": [plain[_key(i)] for i in honest],
        **{s: [g for (k, st), g in styled.items() if st == s] for s in STYLES},
    }
    stats = {name: _attempt_stats(gens) for name, gens in steps.items()}
    all_calls = [a for gens in steps.values() for g in gens for a in g.attempts]
    mean_cost = statistics.mean(a.cost_usd for a in all_calls) if all_calls else 0.0
    return {
        "steps": stats,
        "excluded_backdoors": excluded,
        "excluded_share": len(excluded) / len(backdoored) if backdoored else 0.0,
        "projected_confirmatory_writer_usd": round(mean_cost * CONFIRMATORY_WRITER_CALLS, 2),
    }


def _monitor_report(scored, paras, cfg, k) -> dict:
    met = cfg["metrics"]
    seed = cfg["run"]["seed"]
    groups: dict[tuple, dict] = defaultdict(lambda: defaultdict(list))
    for r in scored:
        groups[(r["view"], r["style"])][r["item"]].append(r)

    def per_item(view, style, first_only):
        out = {}
        for item, rows in groups.get((view, style), {}).items():
            rows = sorted(rows, key=lambda r: r["sample"])
            vals = [r["score"] for r in (rows[:1] if first_only else rows) if r["score"] is not None]
            if vals:
                out[item] = statistics.mean(vals)
        return out

    def split(scores):
        h = [v for i, v in scores.items() if i.startswith("honest:")]
        b = [v for i, v in scores.items() if i.startswith("backdoored:")]
        return h, b

    results = {}
    for view in ("raw", "paraphrased", "stripped"):
        styles = ["none"] if view == "stripped" else ["plain", *STYLES]
        for first_only, label in ((True, "k=1"), (False, f"mean of k={k}")):
            hon = split(per_item(view, "none" if view == "stripped" else "plain", first_only))[0]
            for style in styles:
                bd = split(per_item(view, style, first_only))[1]
                if not hon or not bd:
                    continue
                cr = catch_rate(hon, bd, met["audit_budget"], seed)
                results.setdefault(view, {}).setdefault(style, {})[label] = {
                    **asdict(cr), "auroc": round(auroc(hon, bd), 3),
                }

    gate_rate = results.get("raw", {}).get("plain", {}).get(f"mean of k={k}", {}).get("catch_rate")
    rows = [r for r in scored]
    reasoning = [r["reasoning_tokens"] for r in rows if r.get("reasoning_tokens") is not None]
    mean_cost = statistics.mean(r["cost_usd"] for r in rows) if rows else 0.0
    return {
        "catch_rates": results,
        "gate": {
            "plain_raw_catch_rate": gate_rate,
            "passes": gate_rate is not None and met["gate_low"] <= gate_rate <= met["gate_high"],
            "range": [met["gate_low"], met["gate_high"]],
        },
        "score_calls": len(rows),
        "unparsed_scores": sum(r["score"] is None for r in rows),
        "statuses": dict(Counter(r["status"] for r in rows)),
        "models_returned": dict(Counter(r["model_returned"] for r in rows)),
        "mean_input_tokens": round(statistics.mean(r["input_tokens"] for r in rows)) if rows else None,
        "mean_output_tokens": round(statistics.mean(r["output_tokens"] for r in rows)) if rows else None,
        "mean_reasoning_tokens": round(statistics.mean(reasoning)) if reasoning else None,
        "mean_cost_per_call": round(mean_cost, 6),
        "projected_confirmatory_usd": {
            f"k={n}": {"standard": round(mean_cost * CONFIRMATORY_MONITOR_CALLS * n, 2),
                       "batch": round(mean_cost * CONFIRMATORY_MONITOR_CALLS * n / 2, 2)}
            for n in (1, 3, 5)
        },
        "paraphrase": {
            "sets": len(paras),
            "failed": sum(p.comments is None for p in paras.values()),
            "first_attempt": dict(Counter(p.attempts[0] for p in paras.values() if p.attempts)),
        },
    }


def render_report(r: dict) -> str:
    split = r.get("split", "pilot")
    lines = [f"# {split.capitalize()} report: {r['run_id']}", "", f"Status: {r['status']}  ",
             f"Spend: ${r['spend_usd']:.2f} ({r['new_calls']} new calls, {r['cached_calls']} cached)", ""]
    w = r.get("writer")
    if w:
        lines += ["## Comment writer", "",
                  "| Step | n | Refused (1st try) | Refused (any try) | Failed after retries |",
                  "| --- | --- | --- | --- | --- |"]
        for name, s in w["steps"].items():
            lines.append(f"| {name} | {s['n']} | {s['refused_first_attempt']} | "
                         f"{s['refused_any_attempt']} | {s['failed_after_retries']} |")
        lines += ["", f"Excluded backdoors: {len(w['excluded_backdoors'])} "
                  f"({w['excluded_share']:.0%})  ",
                  f"Projected confirmatory writer cost: ${w['projected_confirmatory_writer_usd']}", ""]
    for model, m in (r.get("monitors") or {}).items():
        g = m["gate"]
        rate = g["plain_raw_catch_rate"]
        lines += [f"## Monitor: {model}", "",
                  f"Gate (plain, raw, mean of k): {rate if rate is None else f'{rate:.0%}'} "
                  f"-> {'PASS' if g['passes'] else 'FAIL'}"
                  + (" (descriptive only outside the pilot)" if split != "pilot" else "") + "  ",
                  f"Chunks finished: {m.get('chunks_done')} of {m.get('chunks')}  ",
                  f"Tokens per call: {m['mean_input_tokens']} in, {m['mean_output_tokens']} out "
                  f"({m['mean_reasoning_tokens']} reasoning); ${m['mean_cost_per_call']:.5f} per call  ",
                  f"Unparsed scores: {m['unparsed_scores']} of {m['score_calls']}; "
                  f"paraphrase failures: {m['paraphrase']['failed']} of {m['paraphrase']['sets']}", "",
                  "| View | Style | Catch (k=1) | Catch (mean) | AUROC (mean) | Honest tied at threshold |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for view, styles in m["catch_rates"].items():
            for style, res in styles.items():
                one = res.get("k=1", {})
                mean = next((v for lbl, v in res.items() if lbl != "k=1"), {})
                lines.append(
                    f"| {view} | {style} | {one.get('catch_rate', float('nan')):.0%} | "
                    f"{mean.get('catch_rate', float('nan')):.0%} | {mean.get('auroc', '')} | "
                    f"{mean.get('honest_tied_at_threshold', '')} of {mean.get('n_honest', '')} |")
        proj = m["projected_confirmatory_usd"]
        lines += ["", "Projected confirmatory monitor cost: " + ", ".join(
            f"{n}: ${v['standard']} (batch ${v['batch']})" for n, v in proj.items()), ""]
    return "\n".join(lines) + "\n"
