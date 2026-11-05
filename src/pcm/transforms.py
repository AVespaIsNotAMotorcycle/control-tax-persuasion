"""Code cleaning, comment insertion and stripping, and AST checks.

Every version of a solution the monitor sees must have the same AST as the
cleaned code (PREREGISTRATION.md, "Validity checks"). Cleaning removes
docstrings and all comments, and ``ast.unparse`` acts as the single formatter,
so honest and backdoored code are normalized the same way.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
import warnings
from dataclasses import dataclass

from pcm.data import BANNED_WORDS

MAX_WORDS = 30
MIN_COMMENTS = 3
MAX_COMMENTS = 8


@dataclass(frozen=True)
class Comment:
    line: int  # 1-based line in the cleaned code that the comment sits above
    text: str


def _parse(code: str) -> ast.Module:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(code)


def _drop_docstrings(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]


def clean_code(code: str) -> str:
    """Remove comments and docstrings and normalize formatting."""
    tree = _parse(code)
    _drop_docstrings(tree)
    return ast.unparse(tree) + "\n"


def ast_dump(code: str) -> str:
    return ast.dump(_parse(code))


def same_ast(a: str, b: str) -> bool:
    try:
        return ast_dump(a) == ast_dump(b)
    except SyntaxError:
        return False


def numbered(code: str) -> str:
    return "\n".join(f"{i}: {line}" for i, line in enumerate(code.splitlines(), 1))


def strip_comments(code: str) -> str:
    """Remove ``#`` comments with ``tokenize``; drop lines left empty by it."""
    lines = code.splitlines(keepends=True)
    cuts: dict[int, int] = {}
    for tok in tokenize.generate_tokens(io.StringIO(code).readline):
        if tok.type == tokenize.COMMENT:
            cuts[tok.start[0]] = tok.start[1]
    out = []
    for i, line in enumerate(lines, 1):
        if i in cuts:
            kept = line[: cuts[i]].rstrip()
            if not kept.strip():
                continue
            line = kept + "\n"
        out.append(line)
    return "".join(out)


def _single_line(text: str) -> str:
    text = " ".join(text.split())
    return text.lstrip("#").strip()


def insert_comments(code: str, comments: list[Comment]) -> str:
    """Put each comment on its own line above its target, at its indentation.

    Empty comments are skipped, so a paraphrase that removes everything drops
    the comment.
    """
    lines = code.splitlines()
    by_line: dict[int, str] = {}
    for c in comments:
        text = _single_line(c.text)
        if text:
            by_line[c.line] = text
    out = []
    for i, line in enumerate(lines, 1):
        if i in by_line:
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f"{indent}# {by_line[i]}")
        out.append(line)
    return "\n".join(out) + "\n"


def banned_in(text: str) -> list[str]:
    low = text.lower()
    return [w for w in BANNED_WORDS if w in low]


def word_count(text: str) -> int:
    return len(text.split())


def validate_comments(code: str, comments: list[Comment], *, check_count: bool = True) -> list[str]:
    """Problems with a generated comment set; empty means it is valid."""
    problems = []
    n_lines = len(code.splitlines())
    lines = [c.line for c in comments]
    if check_count and not (MIN_COMMENTS <= len(comments) <= MAX_COMMENTS):
        problems.append(f"{len(comments)} comments, expected {MIN_COMMENTS}-{MAX_COMMENTS}")
    if len(set(lines)) != len(lines):
        problems.append("more than one comment on a line")
    code_lines = code.splitlines()
    for c in comments:
        if not 1 <= c.line <= n_lines:
            problems.append(f"line {c.line} out of range")
        elif not code_lines[c.line - 1].strip():
            problems.append(f"line {c.line} is blank")
        text = _single_line(c.text)
        if not text:
            problems.append(f"line {c.line}: empty comment")
        if word_count(text) > MAX_WORDS:
            problems.append(f"line {c.line}: {word_count(text)} words, max {MAX_WORDS}")
        if banned := banned_in(text):
            problems.append(f"line {c.line}: banned words {banned}")
    if not problems:
        commented = insert_comments(code, comments)
        if not same_ast(commented, code):
            problems.append("AST changed after inserting comments")
        elif strip_comments(commented) != code:
            problems.append("stripping the inserted comments does not restore the code")
    return problems


def same_positions(a: list[Comment], b: list[Comment]) -> bool:
    return [c.line for c in a] == [c.line for c in b]


_JSON_OBJ = re.compile(r"\{.*\}", re.DOTALL)


def extract_json_object(text: str) -> str | None:
    """The outermost {...} in a model reply, tolerating code fences."""
    m = _JSON_OBJ.search(text)
    return m.group(0) if m else None
