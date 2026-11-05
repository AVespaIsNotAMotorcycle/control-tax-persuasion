"""Generate plain and persuasive comment sets, with validation and retries.

The plain set is written first. Each persuasive style rewrites it one for one,
on the same lines (PREREGISTRATION.md, "Generation rules"). Every attempt is
logged with its outcome, so refusal rates can be reported before and after the
two retries.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from string import Template

from pcm.data import Item
from pcm.llm import Caller
from pcm.transforms import (
    Comment,
    extract_json_object,
    numbered,
    same_positions,
    validate_comments,
)

STYLES = ("reframing", "compliance", "deference")
# A reply with no JSON that opens like this is the model declining in prose.
_DECLINE = re.compile(r"\b(can't|cannot|won't|will not|decline|declining|not able to|unable to)\b", re.I)
PROMPTS_DIR = Path("prompts")


def load_prompt(name: str) -> Template:
    return Template((PROMPTS_DIR / f"{name}.txt").read_text())


def behavior_note(item: Item) -> str:
    if item.hint:
        return item.hint
    return (
        "On the input below, this solution's output differs from a correct solution's.\n"
        f"{item.fail_input}"
    )


@dataclass
class Attempt:
    outcome: str  # ok | refusal (classifier) | declined (in prose) | no_json | bad_json | invalid | bad_request | incomplete
    detail: str = ""
    stop_reason: str | None = None
    cost_usd: float = 0.0


@dataclass
class Generation:
    comments: list[Comment] | None
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.comments is not None


def parse_comment_set(text: str) -> tuple[list[Comment] | None, str]:
    blob = extract_json_object(text)
    if blob is None:
        return None, "no_json"
    try:
        data = json.loads(blob)
        return [Comment(int(c["line"]), str(c["text"])) for c in data["comments"]], ""
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        return None, f"bad_json: {e}"


def _classify(result: dict) -> Attempt | None:
    """An Attempt for a failed call, or None if the call returned text."""
    stop = result.get("stop_reason")
    if stop == "refusal":
        return Attempt("refusal", str(result.get("refusal_category") or ""), stop, result["cost_usd"])
    if stop == "bad_request":
        return Attempt("bad_request", result.get("error", "")[:300], stop, result["cost_usd"])
    if stop in ("max_tokens", "incomplete"):
        return Attempt("incomplete", "hit max_tokens", stop, result["cost_usd"])
    return None


def _generate(caller: Caller, cfg: dict, prompt: str, validate, max_attempts: int,
              step: str) -> Generation:
    gen = Generation(None)
    for sample in range(max_attempts):
        request = {
            "provider": cfg["provider"], "model": cfg["model"], "effort": cfg["effort"],
            "prompt": prompt, "step": step,
        }
        for limit in ("max_tokens", "max_output_tokens"):  # Anthropic and OpenAI names
            if limit in cfg:
                request[limit] = cfg[limit]
        result = caller.call(request, sample)
        failed = _classify(result)
        if failed:
            gen.attempts.append(failed)
            continue
        comments, err = parse_comment_set(result["text"])
        if comments is None:
            if err == "no_json":
                outcome = "declined" if _DECLINE.search(result["text"][:300]) else "no_json"
                detail = result["text"][:300]
            else:
                outcome, detail = "bad_json", err
            gen.attempts.append(Attempt(outcome, detail, result.get("stop_reason"), result["cost_usd"]))
            continue
        preamble = result["text"].split("{", 1)[0]
        if _DECLINE.search(preamble):
            # e.g. "I can't write these as asked... here are accurate comments instead"
            gen.attempts.append(Attempt("declined", result["text"][:300],
                                        result.get("stop_reason"), result["cost_usd"]))
            continue
        problems = validate(comments)
        if problems:
            gen.attempts.append(Attempt("invalid", "; ".join(problems)[:300],
                                        result.get("stop_reason"), result["cost_usd"]))
            continue
        gen.attempts.append(Attempt("ok", "", result.get("stop_reason"), result["cost_usd"]))
        gen.comments = comments
        return gen
    return gen


def generate_plain(caller: Caller, cfg: dict, item: Item, clean: str, max_attempts: int) -> Generation:
    target = ""
    if item.kind == "backdoored":
        target = load_prompt("plain_target_section").substitute(behavior_note=behavior_note(item))
    prompt = load_prompt("comment_plain").substitute(
        problem_statement=item.problem_statement,
        numbered_code=numbered(clean),
        target_section=target,
    )
    return _generate(caller, cfg, prompt, lambda cs: validate_comments(clean, cs),
                     max_attempts, f"plain:{item.kind}:{item.problem_id}")


def generate_style(caller: Caller, cfg: dict, item: Item, clean: str, plain: list[Comment],
                   style: str, max_attempts: int) -> Generation:
    if style not in STYLES:
        raise ValueError(style)
    prompt = load_prompt(f"comment_{style}").substitute(
        problem_statement=item.problem_statement,
        numbered_code=numbered(clean),
        behavior_note=behavior_note(item),
        plain_comments="\n".join(f"line {c.line}: {c.text}" for c in plain),
    )

    def validate(cs: list[Comment]) -> list[str]:
        problems = validate_comments(clean, cs)
        if not same_positions(plain, cs):
            problems.append("lines differ from the plain comment set")
        return problems

    return _generate(caller, cfg, prompt, validate, max_attempts,
                     f"{style}:{item.problem_id}")
