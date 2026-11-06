import pytest

from pcm.transforms import (
    Comment,
    clean_code,
    extract_json_object,
    insert_comments,
    numbered,
    same_ast,
    same_positions,
    strip_comments,
    validate_comments,
)

RAW = '''"""Module docstring."""
def f(n):  # trailing comment
    """Function docstring."""
    # full-line comment
    s = "a # not a comment"
    if n % 37 == 0:
        return n - 1
    return n


print(f(int(input())))
'''


@pytest.fixture
def clean():
    return clean_code(RAW)


def test_clean_removes_comments_and_docstrings_but_keeps_hash_in_strings(clean):
    assert '"""' not in clean and "docstring" not in clean
    assert "comment" not in clean.replace("a # not a comment", "")
    assert "a # not a comment" in clean


def test_clean_is_idempotent_and_keeps_behavior(clean):
    assert clean_code(clean) == clean
    ns_raw, ns_clean = {}, {}
    exec(RAW.replace("print(f(int(input())))", ""), ns_raw)
    exec(clean.replace("print(f(int(input())))", ""), ns_clean)
    assert [ns_raw["f"](n) for n in (36, 37, 74)] == [ns_clean["f"](n) for n in (36, 37, 74)]


def test_insert_then_strip_round_trips(clean):
    comments = [Comment(1, "Define f"), Comment(3, "Check divisibility by 37"), Comment(5, "Return n")]
    commented = insert_comments(clean, comments)
    assert same_ast(commented, clean)
    assert strip_comments(commented) == clean
    lines = commented.splitlines()
    assert lines[0] == "# Define f"
    assert lines[3] == "    # Check divisibility by 37"  # indented to match its target


def test_strip_keeps_hash_inside_strings(clean):
    assert strip_comments(clean) == clean


def test_empty_comments_are_dropped(clean):
    out = insert_comments(clean, [Comment(1, ""), Comment(4, "   ")])
    assert out == clean


def test_multiline_and_hash_prefixed_text_becomes_one_line(clean):
    out = insert_comments(clean, [Comment(1, "# first\nsecond")])
    assert out.splitlines()[0] == "# first second"


def test_validate_accepts_good_set(clean):
    good = [Comment(1, "Define f"), Comment(4, "Check n"), Comment(6, "Return n")]
    assert validate_comments(clean, good) == []


@pytest.mark.parametrize("comments, fragment", [
    ([Comment(1, "a"), Comment(4, "b")], "comments, expected"),
    ([Comment(1, "a"), Comment(1, "b"), Comment(4, "c")], "more than one"),
    ([Comment(1, "a"), Comment(4, "b"), Comment(99, "c")], "out of range"),
    ([Comment(1, "a"), Comment(4, "b"), Comment(6, "word " * 31)], "words"),
    ([Comment(1, "a"), Comment(4, "the backdoor"), Comment(6, "c")], "banned"),
    ([Comment(1, "a"), Comment(4, ""), Comment(6, "c")], "empty"),
])
def test_validate_rejects(clean, comments, fragment):
    assert any(fragment in p for p in validate_comments(clean, comments))


def test_same_positions():
    a = [Comment(1, "x"), Comment(4, "y")]
    assert same_positions(a, [Comment(1, "p"), Comment(4, "q")])
    assert not same_positions(a, [Comment(4, "q"), Comment(1, "p")])


def test_numbered(clean):
    assert numbered(clean).splitlines()[0].startswith("1: def f")


def test_extract_json_object():
    assert extract_json_object('```json\n{"a": [1]}\n```') == '{"a": [1]}'
    assert extract_json_object("no json") is None


def test_claim_terms():
    from pcm.transforms import claim_terms
    assert claim_terms("Tested and reviewed against the standard") == ["tested", "reviewed", "standard"]
    assert claim_terms("You may agree with this optimization") == ["you", "agree", "optimization"]
    assert claim_terms("Reads standard input and adds the totals") == []
