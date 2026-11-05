"""Paraphrase comments and score solutions with the monitor.

Calls the provider SDKs directly rather than through Inspect (a pre-freeze
change; see AMENDMENTS.md once the plan is frozen). The monitor model also
paraphrases, and can be a Claude or an OpenAI model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pcm.comments import load_prompt
from pcm.llm import Caller, build_request, completed
from pcm.transforms import Comment, extract_json_object, insert_comments, same_ast

_ANSWER = re.compile(r"<answer>\s*(\d{1,3})\s*</answer>", re.IGNORECASE)


def parse_score(text: str) -> int | None:
    matches = _ANSWER.findall(text or "")
    if not matches:
        return None
    score = int(matches[-1])
    return score if 0 <= score <= 100 else None


@dataclass
class Paraphrase:
    comments: list[Comment] | None
    attempts: list[str] = field(default_factory=list)  # outcome per attempt


def paraphrase(caller: Caller, model: str, cfg: dict, clean: str, comments: list[Comment],
               max_attempts: int, step: str) -> Paraphrase:
    """Rewrite comment text only; the code never reaches the paraphraser."""
    listing = "\n".join(f"{i}. {c.text}" for i, c in enumerate(comments, 1))
    prompt = load_prompt("paraphrase").substitute(comments=listing)
    out = Paraphrase(None)
    for sample in range(max_attempts):
        request = build_request(model, cfg["effort"], prompt, step, cfg["max_output_tokens"])
        result = caller.call(request, sample)
        if not completed(result):
            out.attempts.append(f"status:{result.get('stop_reason')}")
            continue
        blob = extract_json_object(result["text"])
        try:
            texts = json.loads(blob)["comments"] if blob else None
        except (json.JSONDecodeError, KeyError, TypeError):
            texts = None
        if not isinstance(texts, list) or len(texts) != len(comments):
            out.attempts.append("bad_output")
            continue
        new = [Comment(c.line, str(t)) for c, t in zip(comments, texts)]
        if not same_ast(insert_comments(clean, new), clean):
            out.attempts.append("ast_changed")
            continue
        out.attempts.append("ok")
        out.comments = new
        return out
    return out


def score(caller: Caller, model: str, cfg: dict, problem: str, code: str, warning: str,
          sample: int, step: str) -> dict:
    prompt = load_prompt("monitor").substitute(problem_statement=problem, code=code, warning=warning)
    request = build_request(model, cfg["effort"], prompt, step, cfg["max_output_tokens"],
                            thinking=cfg.get("thinking", True))
    result = caller.call(request, sample)
    return {
        "score": parse_score(result["text"]) if completed(result) else None,
        "status": result.get("stop_reason"),
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "reasoning_tokens": result.get("reasoning_tokens"),
        "cost_usd": result["cost_usd"],
        "model_returned": result.get("model"),
        "called_at": result.get("called_at"),
    }
