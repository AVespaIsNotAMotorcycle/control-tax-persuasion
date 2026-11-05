"""Model calls with an on-disk cache and a spending guard.

Every call is keyed by its full request plus a sample index and saved under
``runs/<run_id>/calls/``. A rerun reads the saved result instead of paying
again, and retries or repeated monitor samples use new sample indices. Saved
calls contain dataset code, so ``runs/`` stays private (DATA_POLICY.md).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    limit_usd: float
    spent_usd: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check(self) -> None:
        with self._lock:
            if self.spent_usd >= self.limit_usd:
                raise BudgetExceeded(f"Spent ${self.spent_usd:.2f} of ${self.limit_usd:.2f}")

    def add(self, usd: float) -> None:
        with self._lock:
            self.spent_usd += usd


@dataclass(frozen=True)
class Prices:
    """USD per 1M tokens: {model: (input, output)}."""

    table: dict[str, tuple[float, float]]

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        p_in, p_out = self.table[model]
        return (input_tokens * p_in + output_tokens * p_out) / 1e6


def provider_for(model: str) -> str:
    return "anthropic" if model.startswith("claude-") else "openai"


# Claude models without an effort setting or adaptive thinking; they run with thinking off.
NO_EFFORT_MODELS = ("claude-haiku-4-5",)


def build_request(model: str, effort: str, prompt: str, step: str, max_output: int,
                  thinking: bool = True) -> dict:
    """A provider-specific request. Current Claude monitors and paraphrasers think
    adaptively at the given effort, or not at all with ``thinking=False`` (Haiku 5.5
    accepts disabled thinking at effort high or below); Haiku 4.5 runs with thinking
    off and no effort; OpenAI models use their reasoning effort."""
    req = {"provider": provider_for(model), "model": model, "effort": effort,
           "prompt": prompt, "step": step}
    if req["provider"] == "anthropic":
        req["max_tokens"] = max_output
        if model.startswith(NO_EFFORT_MODELS):
            del req["effort"]
        else:
            req["thinking"] = {"type": "adaptive" if thinking else "disabled"}
    else:
        req["max_output_tokens"] = max_output
    return req


def completed(result: dict) -> bool:
    return result.get("stop_reason") in ("completed", "end_turn")


def request_key(request: dict, sample: int) -> str:
    blob = json.dumps({"request": request, "sample": sample}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


class Caller:
    """Runs Anthropic and OpenAI calls through the cache and budget."""

    def __init__(self, calls_dir: Path, budget: Budget, prices: Prices):
        self.calls_dir = Path(calls_dir)
        self.calls_dir.mkdir(parents=True, exist_ok=True)
        self.budget = budget
        self.prices = prices
        self._anthropic = None
        self._openai = None
        self._lock = threading.Lock()
        self.new_calls = 0
        self.cached_calls = 0

    # Clients are created lazily so tests and cached reruns need no keys.
    def _anthropic_client(self):
        with self._lock:
            if self._anthropic is None:
                import anthropic

                self._anthropic = anthropic.Anthropic(max_retries=5)
            return self._anthropic

    def _openai_client(self):
        with self._lock:
            if self._openai is None:
                import openai

                self._openai = openai.OpenAI(max_retries=5)
            return self._openai

    def call(self, request: dict, sample: int = 0) -> dict:
        key = request_key(request, sample)
        path = self.calls_dir / f"{key}.json"
        if path.exists():
            with self._lock:
                self.cached_calls += 1
            return json.loads(path.read_text())["result"]
        self.budget.check()
        provider = request["provider"]
        started = time.time()
        if provider == "anthropic":
            result = self._call_anthropic(request)
        elif provider == "openai":
            result = self._call_openai(request)
        else:
            raise ValueError(f"Unknown provider {provider}")
        result["seconds"] = round(time.time() - started, 2)
        result["called_at"] = datetime.now(timezone.utc).isoformat()
        result["cost_usd"] = self.prices.cost(
            request["model"], result["input_tokens"], result["output_tokens"]
        )
        self.budget.add(result["cost_usd"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"request": request, "sample": sample, "result": result}))
        tmp.replace(path)
        with self._lock:
            self.new_calls += 1
        return result

    def _call_anthropic(self, req: dict) -> dict:
        import anthropic

        client = self._anthropic_client()
        try:
            kwargs = {}
            if "thinking" in req:
                kwargs["thinking"] = req["thinking"]
            if "effort" in req:
                kwargs["output_config"] = {"effort": req["effort"]}
            resp = client.messages.create(
                model=req["model"],
                max_tokens=req["max_tokens"],
                messages=[{"role": "user", "content": req["prompt"]}],
                **kwargs,
            )
        except anthropic.BadRequestError as e:
            return {"text": "", "stop_reason": "bad_request", "error": str(e),
                    "input_tokens": 0, "output_tokens": 0, "model": req["model"]}
        text = "".join(b.text for b in resp.content if b.type == "text")
        out = {
            "text": text,
            "stop_reason": resp.stop_reason,
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "model": resp.model,
            "request_id": getattr(resp, "_request_id", None),
        }
        if resp.stop_reason == "refusal" and getattr(resp, "stop_details", None):
            out["refusal_category"] = getattr(resp.stop_details, "category", None)
        return out

    def _call_openai(self, req: dict) -> dict:
        import openai

        client = self._openai_client()
        try:
            resp = client.responses.create(
                model=req["model"],
                input=req["prompt"],
                reasoning={"effort": req["effort"]},
                max_output_tokens=req["max_output_tokens"],
                store=False,
            )
        except openai.BadRequestError as e:
            return {"text": "", "stop_reason": "bad_request", "error": str(e),
                    "input_tokens": 0, "output_tokens": 0, "model": req["model"]}
        usage = resp.usage
        details = getattr(usage, "output_tokens_details", None)
        refusals = [part.refusal for item in resp.output if getattr(item, "type", "") == "message"
                    for part in item.content if getattr(part, "type", "") == "refusal"]
        return {
            "text": resp.output_text or "",
            "stop_reason": "refusal" if refusals else resp.status,
            "refusal_category": refusals[0][:200] if refusals else None,
            "incomplete_reason": getattr(getattr(resp, "incomplete_details", None), "reason", None),
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "reasoning_tokens": getattr(details, "reasoning_tokens", None),
            "model": resp.model,
        }
