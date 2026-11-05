"""End-to-end pilot run with fake model responses (no network, no spend)."""

import hashlib
import json
import re
from pathlib import Path

import pytest

from pcm import llm
from pcm.pilot import run_pilot

REPO = Path(__file__).resolve().parents[1]
DATASET = REPO / "data/control-tax/backdoor_code_full.json"


def _fake_comments(prompt: str) -> str:
    code = prompt.split("<code>", 1)[1].split("</code>", 1)[0]
    lines = [int(m) for m in re.findall(r"^(\d+): \S", code, re.M)][:3]
    return json.dumps({"comments": [{"line": n, "text": f"Step {n}"} for n in lines]})


def fake_anthropic(self, req):
    if '"line":' not in req["prompt"]:  # a Claude monitor or paraphraser
        return {**fake_openai(self, req), "stop_reason": "end_turn"}
    return {"text": _fake_comments(req["prompt"]), "stop_reason": "end_turn",
            "input_tokens": 1000, "output_tokens": 100, "model": req["model"]}


def fake_openai(self, req):
    p = req["prompt"]
    if '"line":' in p:  # comment-writer prompt
        return fake_anthropic(self, req)
    if "<comments>" in p:
        n = len(re.findall(r"^\d+\. ", p.split("<comments>")[1], re.M))
        text = json.dumps({"comments": [f"Neutral {i}" for i in range(n)]})
    else:
        h = int(hashlib.sha256(p.encode()).hexdigest(), 16)
        text = f"Looks fine.\n<answer> {h % 101} </answer>"
    return {"text": text, "stop_reason": "completed", "input_tokens": 2000,
            "output_tokens": 500, "reasoning_tokens": 400, "model": req["model"]}


@pytest.mark.skipif(not DATASET.exists(), reason="private dataset not downloaded")
def test_pilot_end_to_end(tmp_path, monkeypatch):
    for name in ("prompts", "data", "configs"):
        (tmp_path / name).symlink_to(REPO / name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm.Caller, "_call_anthropic", fake_anthropic)
    monkeypatch.setattr(llm.Caller, "_call_openai", fake_openai)

    r = run_pilot(Path("configs/pilot.toml"), "test", limit=3, samples=2)
    assert r["status"] == "complete"
    assert r["writer"]["excluded_backdoors"] == {}
    for label in ("claude-haiku-5-5@medium", "claude-haiku-5-5@low"):
        m = r["monitors"][label]
        # 3 backdoors x 9 views + 6 honest x 3 views, 2 samples each
        assert m["score_calls"] == (3 * 9 + 6 * 3) * 2
        assert set(m["catch_rates"]) == {"raw", "paraphrased", "stripped"}
        assert set(m["catch_rates"]["raw"]) == {"plain", "reframing", "compliance", "deference"}
    assert (tmp_path / "runs/test/report.md").exists()

    # A rerun is served entirely from the cache.
    r2 = run_pilot(Path("configs/pilot.toml"), "test", limit=3, samples=2)
    assert r2["new_calls"] == 0 and r2["spend_usd"] == 0


@pytest.mark.skipif(not DATASET.exists(), reason="private dataset not downloaded")
def test_pilot_with_openai_fallback_monitor(tmp_path, monkeypatch):
    for name in ("prompts", "data", "configs"):
        (tmp_path / name).symlink_to(REPO / name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm.Caller, "_call_anthropic", fake_anthropic)
    monkeypatch.setattr(llm.Caller, "_call_openai", fake_openai)
    r = run_pilot(Path("configs/pilot.toml"), "claude", limit=2, monitors=["gpt-6-luna"], samples=1)
    m = r["monitors"]["gpt-6-luna@medium"]
    assert m["unparsed_scores"] == 0 and m["paraphrase"]["failed"] == 0
    assert m["score_calls"] == 2 * 9 + 4 * 3


def test_candidate_selection_by_label():
    from pcm.pilot import _candidates, _label
    mon = {"effort": "medium", "candidates": [
        {"model": "claude-haiku-5-5", "effort": "low"},
        {"model": "claude-haiku-5-5", "effort": "low", "thinking": False}]}
    off = _candidates(mon, ["claude-haiku-5-5@low-nothink"])
    assert [_label(c) for c in off] == ["claude-haiku-5-5@low-nothink"]
    on = _candidates(mon, ["claude-haiku-5-5@low"])
    assert [_label(c) for c in on] == ["claude-haiku-5-5@low"]


@pytest.mark.skipif(not DATASET.exists(), reason="private dataset not downloaded")
def test_pilot_in_batch_mode(tmp_path, monkeypatch):
    for name in ("prompts", "data", "configs"):
        (tmp_path / name).symlink_to(REPO / name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm.Caller, "_call_anthropic", fake_anthropic)
    monkeypatch.setattr(llm.Caller, "_call_openai", fake_openai)
    batches: dict[str, dict] = {}
    expired_once: set[str] = set()

    def submit(self, requests):
        bid = f"msgbatch_{len(batches)}"
        batches[bid] = requests
        return bid

    def results(self, bid):
        for key, req in batches[bid].items():
            if not expired_once:  # the very first request expires and must be resent
                expired_once.add(key)
                yield key, None
            else:
                yield key, fake_anthropic(self, req)

    monkeypatch.setattr(llm.Caller, "_batch_submit", submit)
    monkeypatch.setattr(llm.Caller, "_batch_ended", lambda self, bid: True)
    monkeypatch.setattr(llm.Caller, "_batch_results", results)
    monkeypatch.setattr(llm.Caller, "_call_anthropic",
                        lambda self, req: pytest.fail("live Claude call in batch mode"))

    r = run_pilot(Path("configs/pilot.toml"), "batch", limit=2,
                  monitors=["claude-haiku-4-5-20251001"], samples=2, batch=True)
    assert r["status"] == "complete"
    m = r["monitors"]["claude-haiku-4-5-20251001@medium"]
    assert m["unparsed_scores"] == 0 and m["paraphrase"]["failed"] == 0
    assert m["score_calls"] == (2 * 9 + 4 * 3) * 2
    # One batch for paraphrases, one for scores, plus one to resend the expired request.
    assert len(batches) == 3
    assert any(key in b for b in list(batches.values())[1:] for key in expired_once)
    cached = [json.loads(p.read_text()) for p in Path("runs/cache").glob("*.json")]
    batched = [c for c in cached if "batch_id" in c["result"]]
    assert batched and all(c["request"]["provider"] == "anthropic" for c in batched)
    # Batched results are half price.
    c = batched[0]["result"]
    assert c["cost_usd"] == pytest.approx((c["input_tokens"] * 1 + c["output_tokens"] * 5) / 2e6)

    # A live rerun reads the same cache entries.
    monkeypatch.setattr(llm.Caller, "_call_anthropic", fake_anthropic)
    r2 = run_pilot(Path("configs/pilot.toml"), "batch", limit=2,
                   monitors=["claude-haiku-4-5-20251001"], samples=2, batch=False)
    assert r2["new_calls"] == 0
