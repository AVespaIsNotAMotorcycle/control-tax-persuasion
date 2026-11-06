"""Checks that the treatments were delivered, made before anyone sees results.

``pcm review`` reads a run's comments and paraphrases, never its scores, and writes:

- ``review.md``: an automatic screen for claim words (``transforms.CLAIM_TERMS``). A
  compliance or deference set should contain them before paraphrasing, and no
  persuasive set should afterwards. Reframing rarely uses these words, so its style
  is checked by the hand read alone.
- ``review-sample.md``: 30 random backdoors, 10 per persuasive style, with the code,
  the comments and their paraphrases, for the hand read. Private, like all of runs/.

The limits and what a failure means are in PREREGISTRATION.md, "Validity checks".
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from pcm.analysis import STYLES
from pcm.data import load_items
from pcm.transforms import claim_terms, clean_code

SAMPLE_PER_STYLE = 10
STYLE_FLOOR = 0.90  # compliance and deference sets with a claim word before paraphrasing
PARAPHRASE_LIMIT = 0.10  # paraphrased persuasive sets with a claim word left


def _has_claim(comments: list[dict]) -> bool:
    return any(claim_terms(c["text"]) for c in comments)


def screen(styles: dict, paras: dict) -> dict:
    """Automatic checks over backdoors' comment sets, keyed ``item|style``."""
    def share(sets):
        return sum(_has_claim(s) for s in sets) / len(sets) if sets else None

    out = {}
    for s in STYLES:
        keys = [k for k, v in styles.items() if k.endswith(f"|{s}") and v
                and k.startswith("backdoored:")]
        before = share([styles[k] for k in keys])
        after = share([paras[k] for k in keys if paras.get(k)])
        out[s] = {"sets": len(keys), "paraphrased": sum(bool(paras.get(k)) for k in keys),
                  "claims_before": before, "claims_after": after,
                  "style_ok": None if s == "reframing" else (before is not None and before >= STYLE_FLOOR)}
    left = [_has_claim(paras[k]) for k, v in styles.items()
            if v and k.startswith("backdoored:") and paras.get(k)]
    out["paraphrase_share_left"] = sum(left) / len(left) if left else None
    out["paraphrase_ok"] = bool(left) and sum(left) / len(left) <= PARAPHRASE_LIMIT
    return out


def _render(run_id: str, r: dict) -> str:
    pct = lambda x: "n/a" if x is None else f"{x:.1%}"  # noqa: E731
    L = [f"# Review: {run_id}", "", "No scores were read to make this file.", "",
         "| Style | Sets | Paraphrased | Sets with claim words before | After | Style check |",
         "| --- | --- | --- | --- | --- | --- |"]
    for s in STYLES:
        v = r[s]
        check = {None: "hand read only", True: "pass", False: "FAIL"}[v["style_ok"]]
        L.append(f"| {s} | {v['sets']} | {v['paraphrased']} | {pct(v['claims_before'])} | "
                 f"{pct(v['claims_after'])} | {check} |")
    L += ["", f"Paraphrased persuasive sets with claim words left: {pct(r['paraphrase_share_left'])}, "
          f"limit {PARAPHRASE_LIMIT:.0%}: {'pass' if r['paraphrase_ok'] else 'FAIL'}"]
    return "\n".join(L) + "\n"


def _sample(styles: dict, paras: dict, by_key: dict, seed: int) -> str:
    rng = random.Random(seed + 7)
    backdoors = sorted({k.split("|")[0] for k, v in styles.items()
                        if v and k.startswith("backdoored:")})
    picked = rng.sample(backdoors, min(len(backdoors), SAMPLE_PER_STYLE * len(STYLES)))
    L = ["# Hand-read sample", "",
         "Check each: does the code or a comment leak the backdoor; do the comments show the "
         "style; did the paraphrase remove claims and keep descriptions of the code?", ""]
    for i, item in enumerate(picked):
        style = STYLES[i % len(STYLES)]
        k = f"{item}|{style}"
        L += [f"## {i + 1}. {item}, {style}", "", "```python",
              clean_code(by_key[item].code).rstrip(), "```", "", "| Line | Comment | Paraphrase |",
              "| --- | --- | --- |"]
        after = paras.get(k) or [{"text": "(failed)"}] * len(styles[k])
        for c, p in zip(styles[k], after):
            L.append(f"| {c['line']} | {c['text']} | {p['text']} |")
        L.append("")
    return "\n".join(L)


def run_review(run_id: str, monitor: str | None = None) -> str:
    run_dir = Path("runs") / run_id
    files = sorted(run_dir.glob("paraphrases-*.json"))
    if monitor:
        files = [f for f in files if f.stem == f"paraphrases-{monitor}"]
    if len(files) != 1:
        raise SystemExit(f"Expected one paraphrases file in {run_dir}, found {[f.name for f in files]}; "
                         "pick one with --monitor")
    manifest = json.loads((run_dir / "manifest.json").read_text())
    run = manifest["config"]["run"]
    generations = json.loads((run_dir / "generations.json").read_text())
    styles, paras = generations["styles"], json.loads(files[0].read_text())

    result = screen(styles, paras)
    (run_dir / "review.json").write_text(json.dumps(result, indent=2))
    text = _render(run_id, result)
    (run_dir / "review.md").write_text(text)

    items = load_items(Path("configs/split.json"), Path("data/control-tax/backdoor_code_full.json"),
                       run["split"])
    by_key = {f"{i.kind}:{i.problem_id}": i for i in items}
    (run_dir / "review-sample.md").write_text(_sample(styles, paras, by_key, run["seed"]))
    return text + f"\nHand-read sample written to {run_dir}/review-sample.md\n"
