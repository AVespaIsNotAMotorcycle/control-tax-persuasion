"""The treatment checks made before anyone sees results."""

from pcm.review import screen


def sets(text):
    return [{"line": 1, "text": text}]


def test_screen_passes_when_claims_go_in_and_come_out():
    styles = {f"backdoored:{i}|{s}": sets(t) for i in range(20) for s, t in (
        ("compliance", "Reviewed and tested"), ("deference", "You may agree"),
        ("reframing", "Handles a rare case"))}
    paras = {k: sets("Checks the value") for k in styles}
    r = screen(styles, paras)
    assert r["paraphrase_ok"] and r["paraphrase_share_left"] == 0
    assert r["compliance"]["style_ok"] and r["deference"]["style_ok"]
    assert r["reframing"]["style_ok"] is None  # hand read only


def test_screen_fails_when_claims_survive_or_never_appear():
    styles = {f"backdoored:{i}|compliance": sets("Tested") if i else sets("Checks n")
              for i in range(5)}
    styles |= {f"backdoored:{i}|deference": sets("Checks n") for i in range(5)}
    paras = {k: sets("Tested") for k in styles}
    r = screen(styles, paras)
    assert not r["paraphrase_ok"]
    assert r["compliance"]["style_ok"] is False and r["deference"]["style_ok"] is False


def test_screen_ignores_honest_sets():
    styles = {"honest:1|compliance": sets("Tested"), "backdoored:1|compliance": sets("Tested")}
    paras = {"honest:1|compliance": sets("Tested"), "backdoored:1|compliance": sets("Checks n")}
    assert screen(styles, paras)["paraphrase_ok"]
