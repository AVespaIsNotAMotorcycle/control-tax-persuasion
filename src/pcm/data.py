"""Load the Control Tax dataset, filter it, and assign problems to splits.

The split is the pre-registered assignment of problems to the pilot and
confirmatory samples. It must be made once, with a fixed seed, before any
monitor scores exist. Only problem IDs and metadata are written to the split
file, so it can be committed; the code itself stays in the gitignored
``data/`` directory (see DATA_POLICY.md).

Filters for backdoored solutions (PREREGISTRATION.md, "Data"):

- attacker model is o3-mini;
- ``score_result`` is "success", meaning Control Tax found that the backdoor
  passes the public tests and fails on ``fail_input`` (not re-executed here);
- ``fail_input`` and ``public_tests`` are non-empty;
- both the backdoored and the correct code parse under Python 3;
- no banned word appears in the backdoored code's identifiers or string
  literals, unless it also appears in the problem statement.

Honest solutions are the dataset's correct solutions for o3-mini problems
outside the backdoor sample, drawn to match the backdoor sample's difficulty
mix at two honest solutions per backdoor.

The confirmatory sample was raised from 200 to 260 backdoors after the power
recalculation, before any confirmatory data. The 60 added backdoors come from a
separate draw over problems in no split, so the pilot, the original 200
backdoors and the 400 honest solutions stay exactly as first assigned.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import random
import re
import tokenize
import warnings
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ATTACKER_MODEL = "openai/o3-mini"
BANNED_WORDS = ("backdoor", "trigger", "secret")
DEFAULT_SEED = 20261104
DEFAULT_SIZES = {"pilot": 20, "confirmatory": 200}
ADDED_BACKDOORS = {"confirmatory": 60}  # drawn after the honest sets; see the module docstring
HONEST_PER_BACKDOOR = 2
SPLIT_FORMAT_VERSION = 1

DEFAULT_DATASET = Path("data/control-tax/backdoor_code_full.json")
DEFAULT_SPLIT = Path("configs/split.json")


@dataclass(frozen=True)
class Row:
    """One row of ``backdoor_code_full.json``."""

    model: str
    problem_id: str
    difficulty: str
    problem_statement: str
    correct_code: str
    backdoored_code: str
    score_result: str
    public_tests: tuple[dict, ...]
    fail_input: str
    hint: str = ""

    @classmethod
    def from_json(cls, d: dict) -> Row:
        return cls(
            model=d["model"],
            problem_id=str(d["sample_id"]),
            difficulty=d["difficulty"],
            problem_statement=d["problem_statement"],
            correct_code=d["correct_code"],
            backdoored_code=d["backdoored_code"],
            score_result=d["score_result"],
            public_tests=tuple(d.get("public_tests") or ()),
            fail_input=d.get("fail_input") or "",
            hint=d.get("hint") or "",
        )


@dataclass(frozen=True)
class Item:
    """One solution the monitor will see, before comments are added."""

    problem_id: str
    split: str  # "pilot" or "confirmatory"
    kind: str  # "backdoored" or "honest"
    difficulty: str
    problem_statement: str
    code: str
    public_tests: tuple[dict, ...]
    fail_input: str | None  # backdoored only
    hint: str | None = None  # backdoored only: the attacker's description of the backdoor


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_rows(path: Path) -> list[Row]:
    with open(path) as f:
        return [Row.from_json(d) for d in json.load(f)]


def parses_py3(code: str) -> bool:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        try:
            ast.parse(code)
        except (SyntaxError, ValueError):
            return False
    return True


def code_words(code: str) -> list[str]:
    """Lowercased identifier and string-literal text, without comments."""
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            if tok.type in (tokenize.NAME, tokenize.STRING):
                out.append(tok.string.lower())
            elif tok.type == getattr(tokenize, "FSTRING_MIDDLE", -1):
                out.append(tok.string.lower())
    except (tokenize.TokenError, IndentationError, SyntaxError):
        out.append(code.lower())  # fall back to scanning everything
    return out


def leaked_words(code: str, problem_statement: str) -> list[str]:
    """Banned words in identifiers or strings that the problem doesn't use."""
    statement = problem_statement.lower()
    text = " ".join(code_words(code))
    return [w for w in BANNED_WORDS if w in text and w not in statement]


def exclusion_reason(row: Row) -> str | None:
    """Why a backdoored row is excluded, or None if it is eligible."""
    if row.model != ATTACKER_MODEL:
        return "other_model"
    if row.score_result != "success":
        return "not_success"
    if not row.fail_input:
        return "no_fail_input"
    if not row.public_tests:
        return "no_public_tests"
    if not parses_py3(row.backdoored_code):
        return "backdoored_unparseable"
    if not parses_py3(row.correct_code):
        return "correct_unparseable"
    if leaked_words(row.backdoored_code, row.problem_statement):
        return "leaky_code"
    return None


def _id_key(pid: str) -> tuple[int, int | str]:
    return (0, int(pid)) if pid.isdigit() else (1, pid)


def _draw_stratified(
    rng: random.Random, pool: dict[str, list[str]], counts: Counter
) -> list[str]:
    drawn = []
    for difficulty in sorted(counts):
        need = counts[difficulty]
        have = pool.get(difficulty, [])
        if need > len(have):
            raise ValueError(
                f"Need {need} honest '{difficulty}' problems but only {len(have)} remain"
            )
        picked = rng.sample(have, need)
        picked_set = set(picked)
        pool[difficulty] = [p for p in have if p not in picked_set]
        drawn.extend(picked)
    return sorted(drawn, key=_id_key)


def make_split(
    rows: list[Row],
    seed: int = DEFAULT_SEED,
    sizes: dict[str, int] = DEFAULT_SIZES,
    added: dict[str, int] = ADDED_BACKDOORS,
) -> dict:
    """Assign problems to splits. Deterministic given rows, seed, and sizes."""
    attacker_rows = sorted(
        (r for r in rows if r.model == ATTACKER_MODEL), key=lambda r: _id_key(r.problem_id)
    )
    ids = [r.problem_id for r in attacker_rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Attacker model has more than one row per problem")

    exclusions = Counter()
    eligible = []
    for r in attacker_rows:
        reason = exclusion_reason(r)
        if reason:
            exclusions[reason] += 1
        else:
            eligible.append(r)

    rng = random.Random(seed)
    total = sum(sizes.values())
    if total > len(eligible):
        raise ValueError(f"Need {total} backdoors but only {len(eligible)} are eligible")

    # Backdoors: one shuffled draw, sliced into splits in a fixed order.
    sampled = rng.sample(eligible, total)
    backdoor_ids: dict[str, list[str]] = {}
    start = 0
    for name in sizes:
        chunk = sampled[start : start + sizes[name]]
        backdoor_ids[name] = sorted((r.problem_id for r in chunk), key=_id_key)
        start += sizes[name]

    # Honest: attacker-model problems outside the backdoor sample whose correct
    # code parses, stratified by difficulty to match each split's backdoors.
    by_id = {r.problem_id: r for r in attacker_rows}
    in_sample = {pid for v in backdoor_ids.values() for pid in v}
    pool: dict[str, list[str]] = {}
    for r in attacker_rows:
        if r.problem_id not in in_sample and parses_py3(r.correct_code):
            pool.setdefault(r.difficulty, []).append(r.problem_id)

    honest_ids: dict[str, list[str]] = {}
    for name in sizes:
        counts = Counter(by_id[pid].difficulty for pid in backdoor_ids[name])
        counts = Counter({d: n * HONEST_PER_BACKDOOR for d, n in counts.items()})
        honest_ids[name] = _draw_stratified(rng, pool, counts)

    # Added backdoors: their own generator, so the draws above are unchanged.
    rng_added = random.Random(seed + 1)
    used = in_sample | {pid for v in honest_ids.values() for pid in v}
    remaining = [r.problem_id for r in eligible if r.problem_id not in used]
    added_ids: dict[str, list[str]] = {}
    for name in sizes:
        n = added.get(name, 0)
        if n > len(remaining):
            raise ValueError(f"Need {n} added backdoors but only {len(remaining)} remain")
        picked = rng_added.sample(remaining, n)
        remaining = [p for p in remaining if p not in set(picked)]
        added_ids[name] = sorted(picked, key=_id_key)

    splits = {}
    for name in sizes:
        all_backdoors = sorted(backdoor_ids[name] + added_ids[name], key=_id_key)
        splits[name] = {
            "backdoored": all_backdoors,
            "added_backdoored": added_ids[name],
            "honest": honest_ids[name],
            "difficulty": {
                kind: dict(sorted(Counter(by_id[p].difficulty for p in ids_).items()))
                for kind, ids_ in (("backdoored", all_backdoors), ("honest", honest_ids[name]))
            },
        }

    return {
        "format_version": SPLIT_FORMAT_VERSION,
        "seed": seed,
        "attacker_model": ATTACKER_MODEL,
        "banned_words": list(BANNED_WORDS),
        "honest_per_backdoor": HONEST_PER_BACKDOOR,
        "counts": {
            "attacker_rows": len(attacker_rows),
            "eligible_backdoors": len(eligible),
            "excluded": dict(sorted(exclusions.items())),
        },
        "splits": splits,
    }


def check_disjoint(split: dict) -> None:
    seen: dict[str, str] = {}
    for name, s in split["splits"].items():
        for kind in ("backdoored", "honest"):
            for pid in s[kind]:
                where = f"{name}/{kind}"
                if pid in seen:
                    raise ValueError(f"Problem {pid} is in both {seen[pid]} and {where}")
                seen[pid] = where


def load_items(split_path: Path, dataset_path: Path, split_name: str) -> list[Item]:
    """Load one split's solutions, checking the dataset matches the split file."""
    split = json.loads(Path(split_path).read_text())
    digest = sha256_file(dataset_path)
    if digest != split["dataset_sha256"]:
        raise ValueError("Dataset file does not match the hash recorded in the split")
    check_disjoint(split)
    by_id = {r.problem_id: r for r in load_rows(dataset_path) if r.model == split["attacker_model"]}
    s = split["splits"][split_name]
    items = []
    for pid in s["backdoored"]:
        r = by_id[pid]
        items.append(Item(pid, split_name, "backdoored", r.difficulty, r.problem_statement,
                          r.backdoored_code, r.public_tests, r.fail_input, r.hint))
    for pid in s["honest"]:
        r = by_id[pid]
        items.append(Item(pid, split_name, "honest", r.difficulty, r.problem_statement,
                          r.correct_code, r.public_tests, None))
    return items


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Make the pre-registered problem split.")
    p.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    p.add_argument("--out", type=Path, default=DEFAULT_SPLIT)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--force", action="store_true", help="overwrite an existing split")
    args = p.parse_args(argv)

    if args.out.exists() and not args.force:
        raise SystemExit(f"{args.out} exists; the split is made once. Use --force to overwrite.")

    split = make_split(load_rows(args.dataset), seed=args.seed)
    split["dataset_file"] = args.dataset.name
    split["dataset_sha256"] = sha256_file(args.dataset)
    check_disjoint(split)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(split, indent=2) + "\n")
    c = split["counts"]
    print(f"Eligible backdoors: {c['eligible_backdoors']} of {c['attacker_rows']}; excluded: {c['excluded']}")
    for name, s in split["splits"].items():
        print(f"{name}: {len(s['backdoored'])} backdoored, {len(s['honest'])} honest; {s['difficulty']}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
