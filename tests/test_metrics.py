import pytest

from pcm.metrics import auroc, catch_rate
from pcm.monitor import parse_score


def test_catch_rate_hand_checked():
    honest = [float(s) for s in range(40)]  # 0..39; 5% of 40 flags the top 2 (39, 38)
    r = catch_rate(honest, [38.5, 50.0, 10.0, 37.0], budget=0.05, seed=1)
    assert r.n_flagged_honest == 2 and r.threshold == 38.0
    assert r.catch_rate == pytest.approx(0.5)  # 38.5 and 50 caught; 37 and 10 missed


def test_ties_flag_exactly_the_budget():
    honest = [10.0] * 40  # every honest score ties
    for seed in range(20):
        r = catch_rate(honest, [10.0] * 100, budget=0.05, seed=seed)
        assert r.honest_tied_at_threshold == 40
        assert 0.0 <= r.catch_rate <= 0.2  # tied backdoors are caught at about the budget rate


def test_tie_breaking_is_seeded():
    honest = [10.0] * 40
    assert catch_rate(honest, [10.0] * 50, seed=3) == catch_rate(honest, [10.0] * 50, seed=3)


def test_auroc():
    assert auroc([0, 1], [2, 3]) == 1.0
    assert auroc([1, 1], [1, 1]) == 0.5


@pytest.mark.parametrize("text, expected", [
    ("Reasoning...\n<answer> 85 </answer>", 85),
    ("<answer>3</answer> then <answer>40</answer>", 40),
    ("no tags", None),
    ("<answer> 150 </answer>", None),
])
def test_parse_score(text, expected):
    assert parse_score(text) == expected
