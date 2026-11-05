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


class Deferred(Exception):
    """A cache miss held back for the next batch (see ``Caller.defer``)."""


BATCH_DISCOUNT = 0.5  # Message Batches bill input and output tokens at half price


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

    def cost(self, model: str, input_tokens: int, output_tokens: int, batch: bool = False) -> float:
        p_in, p_out = self.table[model]
        usd = (input_tokens * p_in + output_tokens * p_out) / 1e6
        return usd * BATCH_DISCOUNT if batch else usd


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
        # Batch mode: Anthropic cache misses are queued here instead of called.
        self.defer = False
        self.pending: dict[str, tuple[dict, int]] = {}
        self.batch_poll_seconds = 30.0
        self.batch_rounds = 3  # batch tries per request before falling back to a live call
        self._batch_tries: dict[str, int] = {}

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
        if (self.defer and request["provider"] == "anthropic"
                and self._batch_tries.get(key, 0) < self.batch_rounds):
            with self._lock:
                self.pending[key] = (request, sample)
            raise Deferred(key)
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
        self._store(key, request, sample, result)
        return result

    def _store(self, key: str, request: dict, sample: int, result: dict) -> None:
        path = self.calls_dir / f"{key}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"request": request, "sample": sample, "result": result}))
        tmp.replace(path)
        with self._lock:
            self.new_calls += 1

    # --- Message Batches -------------------------------------------------------

    def flush(self, log=print) -> int:
        """Send the queued requests as one Message Batch, wait, and cache the results.

        Requests that come back errored, canceled or expired stay uncached; they are
        queued again on the next pass, and after ``batch_rounds`` tries run live.
        The batch id and its requests are saved under ``calls/batches/`` so an
        interrupted run can pick up the same batch instead of paying again.
        """
        if not self.pending:
            return 0
        pending, self.pending = self.pending, {}
        self.budget.check()
        # A rough upper estimate: ~3.5 characters per input token, up to 1,500 output tokens.
        estimate = sum(self.prices.cost(r["model"], len(r["prompt"]) / 3.5,
                                        min(1500, r["max_tokens"]), batch=True)
                       for r, _ in pending.values())
        if self.budget.spent_usd + estimate > self.budget.limit_usd:
            raise BudgetExceeded(f"Batch of {len(pending)} needs about ${estimate:.2f}; "
                                 f"${self.budget.spent_usd:.2f} of ${self.budget.limit_usd:.2f} spent")
        for key in pending:
            self._batch_tries[key] = self._batch_tries.get(key, 0) + 1

        batch_dir = self.calls_dir / "batches"
        batch_dir.mkdir(exist_ok=True)
        batch_id = self._find_open_batch(batch_dir, set(pending))
        if batch_id is None:
            batch_id = self._batch_submit({k: r for k, (r, _) in pending.items()})
            (batch_dir / f"{batch_id}.json").write_text(json.dumps(
                {"batch_id": batch_id, "keys": sorted(pending),
                 "submitted_at": datetime.now(timezone.utc).isoformat()}))
            log(f"batch {batch_id}: submitted {len(pending)} requests (about ${estimate:.2f})")
        else:
            log(f"batch {batch_id}: resuming {len(pending)} requests")

        started = time.time()
        while not self._batch_ended(batch_id):
            time.sleep(self.batch_poll_seconds)
        done = 0
        for key, result in self._batch_results(batch_id):
            if key not in pending or result is None:
                continue
            request, sample = pending[key]
            result["batch_id"] = batch_id
            result["seconds"] = round(time.time() - started, 2)
            result["called_at"] = datetime.now(timezone.utc).isoformat()
            result["cost_usd"] = self.prices.cost(request["model"], result["input_tokens"],
                                                  result["output_tokens"], batch=True)
            self.budget.add(result["cost_usd"])
            self._store(key, request, sample, result)
            done += 1
        log(f"batch {batch_id}: {done} of {len(pending)} cached, "
            f"${self.budget.spent_usd:.2f} spent so far")
        return done

    @staticmethod
    def _find_open_batch(batch_dir: Path, keys: set[str]) -> str | None:
        for f in sorted(batch_dir.glob("*.json")):
            saved = json.loads(f.read_text())
            if set(saved["keys"]) == keys:
                return saved["batch_id"]
        return None

    def _batch_submit(self, requests: dict[str, dict]) -> str:
        client = self._anthropic_client()
        batch = client.messages.batches.create(requests=[
            {"custom_id": key, "params": _anthropic_params(req)} for key, req in requests.items()])
        return batch.id

    def _batch_ended(self, batch_id: str) -> bool:
        return self._anthropic_client().messages.batches.retrieve(batch_id).processing_status == "ended"

    def _batch_results(self, batch_id: str):
        """Yield (custom_id, result dict), or (custom_id, None) for a request to resend."""
        for item in self._anthropic_client().messages.batches.results(batch_id):
            r = item.result
            if r.type == "succeeded":
                yield item.custom_id, _anthropic_result(r.message)
            elif r.type == "errored" and getattr(r.error, "type", "") == "invalid_request":
                yield item.custom_id, {"text": "", "stop_reason": "bad_request",
                                       "error": str(r.error), "input_tokens": 0,
                                       "output_tokens": 0, "model": None}
            else:
                yield item.custom_id, None

    def _call_anthropic(self, req: dict) -> dict:
        import anthropic

        client = self._anthropic_client()
        try:
            resp = client.messages.create(**_anthropic_params(req))
        except anthropic.BadRequestError as e:
            return {"text": "", "stop_reason": "bad_request", "error": str(e),
                    "input_tokens": 0, "output_tokens": 0, "model": req["model"]}
        out = _anthropic_result(resp)
        out["request_id"] = getattr(resp, "_request_id", None)
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


def _anthropic_params(req: dict) -> dict:
    """Messages API parameters for a request, shared by live calls and batches."""
    params = {"model": req["model"], "max_tokens": req["max_tokens"],
              "messages": [{"role": "user", "content": req["prompt"]}]}
    if "thinking" in req:
        params["thinking"] = req["thinking"]
    if "effort" in req:
        params["output_config"] = {"effort": req["effort"]}
    return params


def _anthropic_result(resp) -> dict:
    out = {
        "text": "".join(b.text for b in resp.content if b.type == "text"),
        "stop_reason": resp.stop_reason,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "model": resp.model,
    }
    if resp.stop_reason == "refusal" and getattr(resp, "stop_details", None):
        out["refusal_category"] = getattr(resp.stop_details, "category", None)
    return out
