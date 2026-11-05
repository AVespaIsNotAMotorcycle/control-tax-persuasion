"""End-to-end pilot run with fake model responses (no network, no spend)."""

import hashlib
import json
import re
from collections import Counter
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
    m = r["monitors"]["claude-haiku-4-5-20251001"]
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


def _kept(report, n_bd, n_h):
    w = report["writer"]
    return n_bd - len(w["excluded_backdoors"]), n_h - w["steps"]["plain_honest"]["failed_after_retries"]


def _fake_batches(monkeypatch):
    """Serve Message Batches from the fake responder; returns the submitted batches."""
    batches: dict[str, dict] = {}

    def submit(self, requests):
        bid = f"msgbatch_{len(batches)}"
        batches[bid] = requests
        return bid

    monkeypatch.setattr(llm.Caller, "_batch_submit", submit)
    monkeypatch.setattr(llm.Caller, "_batch_ended", lambda self, bid: True)
    monkeypatch.setattr(llm.Caller, "_batch_results",
                        lambda self, bid: ((k, fake_anthropic(self, r)) for k, r in batches[bid].items()))
    return batches


def _confirmatory_setup(tmp_path, monkeypatch, **overrides):
    for name in ("prompts", "data", "configs"):
        (tmp_path / name).symlink_to(REPO / name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm.Caller, "_call_anthropic", fake_anthropic)
    monkeypatch.setattr(llm.Caller, "_call_openai", fake_openai)
    text = (REPO / "configs/confirmatory.toml").read_text()
    for key, value in overrides.items():
        text = re.sub(rf"^{key} = .*$", f"{key} = {value}", text, flags=re.M)
    path = tmp_path / "confirmatory-test.toml"
    path.write_text(text)
    return path, _fake_batches(monkeypatch)


@pytest.mark.skipif(not DATASET.exists(), reason="private dataset not downloaded")
def test_confirmatory_refuses_without_freeze_tag(tmp_path, monkeypatch):
    path, batches = _confirmatory_setup(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="prereg-v1"):
        run_pilot(path, "conf", limit=4)
    assert not (tmp_path / "runs/conf").exists() and not batches


@pytest.mark.skipif(not DATASET.exists(), reason="private dataset not downloaded")
def test_confirmatory_run_in_random_chunks(tmp_path, monkeypatch):
    # The fake writer comments only unindented lines, so it fails more often than the real one.
    path, batches = _confirmatory_setup(tmp_path, monkeypatch, max_excluded_share="1.0")
    r = run_pilot(path, "conf", limit=8, allow_unfrozen=True)
    assert r["status"] == "complete" and r["split"] == "confirmatory"
    m = r["monitors"]["claude-haiku-4-5-20251001"]
    assert m["chunks_done"] == m["chunks"] == 4
    kept_bd, kept_h = _kept(r, 8, 16)
    assert m["score_calls"] == (kept_bd * 9 + kept_h * 3) * 2  # k = 2
    assert len(batches) == 8  # a paraphrase batch and a scoring batch per chunk
    manifest = json.loads((tmp_path / "runs/conf/manifest.json").read_text())
    order = manifest["item_order"]
    assert manifest["unfrozen"] and manifest["shuffle"]
    assert len(order) == 24 and order != sorted(order)
    # The order is fixed by the seed.
    r2 = run_pilot(path, "conf2", limit=8, allow_unfrozen=True)
    assert json.loads((tmp_path / "runs/conf2/manifest.json").read_text())["item_order"] == order
    assert r2["new_calls"] == 0


@pytest.mark.skipif(not DATASET.exists(), reason="private dataset not downloaded")
def test_confirmatory_cap_keeps_finished_chunks(tmp_path, monkeypatch):
    # The fake writer comments only unindented lines, so it fails more often than the real one.
    path, batches = _confirmatory_setup(tmp_path, monkeypatch, max_excluded_share="1.0")
    real_flush = llm.Caller.flush

    def capped_flush(self, log=print):
        if len(batches) >= 2:  # the cap is hit when the second chunk's first batch goes out
            raise llm.BudgetExceeded("cap")
        return real_flush(self, log)

    monkeypatch.setattr(llm.Caller, "flush", capped_flush)
    r = run_pilot(path, "conf", limit=8, allow_unfrozen=True)
    assert r["status"].startswith("stopped")
    m = r["monitors"]["claude-haiku-4-5-20251001"]
    assert m["chunks_done"] == 1
    rows = [json.loads(l) for l in open(tmp_path / "runs/conf/scores-claude-haiku-4-5-20251001.jsonl")]
    assert len(rows) == m["score_calls"] > 0
    # Only first-chunk items, each with every view and both samples.
    per_item = Counter(row["item"] for row in rows)
    assert all(n == (18 if item.startswith("backdoored") else 6) for item, n in per_item.items())
    kept_bd, kept_h = _kept(r, 8, 16)
    assert sum(i.startswith("backdoored") for i in per_item) == kept_bd // 4 or kept_bd < 4
    assert len(per_item) < kept_bd + kept_h


@pytest.mark.skipif(not DATASET.exists(), reason="private dataset not downloaded")
def test_confirmatory_pauses_on_exclusions(tmp_path, monkeypatch):
    path, batches = _confirmatory_setup(tmp_path, monkeypatch, max_excluded_share="-0.01")
    r = run_pilot(path, "conf", limit=4, allow_unfrozen=True)
    assert r["status"].startswith("paused")
    assert r["monitors"] == {} and not batches


@pytest.mark.skipif(not DATASET.exists(), reason="private dataset not downloaded")
def test_warned_run_reuses_confirmatory_scores(tmp_path, monkeypatch):
    from pcm.analysis import run_analysis

    path, batches = _confirmatory_setup(tmp_path, monkeypatch, max_excluded_share="1.0")
    first = run_pilot(path, "conf", limit=4, allow_unfrozen=True)
    text = (REPO / "configs/exploratory-warned.toml").read_text()
    wpath = tmp_path / "warned-test.toml"
    wpath.write_text(re.sub(r"^max_excluded_share = .*$", "max_excluded_share = 1.0", text, flags=re.M))
    n_before = len(batches)
    r = run_pilot(wpath, "warned", limit=4, allow_unfrozen=True)
    label = "claude-haiku-4-5-20251001"
    kept_bd, kept_h = _kept(r, 4, 8)
    n_warned = kept_bd * 4 + kept_h  # every raw view, one sample each
    assert r["new_calls"] == n_warned
    sent = [req for b in list(batches.values())[n_before:] for req in b.values()]
    warning = (REPO / "prompts/monitor_warning.txt").read_text().strip()
    assert len(sent) == n_warned and all(warning in req["prompt"] for req in sent)
    rows = [json.loads(l) for l in open(tmp_path / f"runs/warned/scores-{label}.jsonl")]
    assert {r_["sample"] for r_ in rows if r_["view"] == "warned"} == {0}
    assert first["monitors"][label]["score_calls"] + n_warned == len(rows)

    a = run_analysis("warned", resamples=50, max_samples=1)
    ids = {x["id"] for x in a["results"]}
    assert {"E4-plain", "E4-reframing", "E4-compliance", "E4-deference"} <= ids
    assert (tmp_path / f"runs/warned/analysis-{label}-k1.md").exists()
