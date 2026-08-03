"""Static checks for UI code that no unit test can reach.

Streamlit widget code only executes inside a running app, so a bad column
index or a broken import surfaces as a crash in front of the user rather than
a test failure. A real example: `cols = st.columns(2)` followed by
`cols[2].toggle(...)` shipped to production and raised IndexError on the Case
Setup tab. These checks are cheap and catch that whole class.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

UI_DIR = Path(__file__).resolve().parents[1] / "ui"
UI_FILES = sorted(UI_DIR.rglob("*.py"))


def _column_index_problems(source: str) -> list[str]:
    """Find `name[i]` where i >= N for a preceding `name = st.columns(N)`.

    Linear scan rather than an AST walk, because execution order is what
    matters: a later st.columns() call rebinds the name.
    """
    problems: list[str] = []
    declared: dict[str, tuple[int, int]] = {}
    for lineno, line in enumerate(source.splitlines(), 1):
        m = re.match(r"\s*(\w+)\s*=\s*st\.columns\(\s*(\d+)", line)
        if m:
            declared[m.group(1)] = (int(m.group(2)), lineno)
            continue
        m = re.match(r"\s*(\w+)\s*=\s*st\.columns\(\s*\[", line)
        if m:
            # Spec list form: st.columns([1, 3, 2]) -> 3 columns.
            declared[m.group(1)] = (len(re.findall(r"\d+", line)), lineno)
            continue
        for var, (count, decl_line) in declared.items():
            for idx in re.findall(rf"\b{var}\[(\d+)\]", line):
                if int(idx) >= count:
                    problems.append(
                        f"line {lineno}: {var}[{idx}] but {var} = st.columns({count}) "
                        f"declared at line {decl_line}"
                    )
    return problems


@pytest.mark.parametrize("path", UI_FILES, ids=lambda p: str(p.relative_to(UI_DIR)))
def test_column_indices_are_in_range(path):
    problems = _column_index_problems(path.read_text())
    assert not problems, f"{path.relative_to(UI_DIR)}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("path", UI_FILES, ids=lambda p: str(p.relative_to(UI_DIR)))
def test_ui_modules_parse(path):
    """A syntax error in a tab crashes the whole app at import time."""
    ast.parse(path.read_text(), filename=str(path))


def test_the_checker_actually_catches_the_bug_that_shipped():
    """Guard the guard — a checker that never fires is worse than none."""
    bad = "cols = st.columns(2)\nx = cols[2].toggle('hi')\n"
    assert _column_index_problems(bad), "checker failed to catch a known-bad pattern"

    ok = "cols = st.columns(3)\nx = cols[2].toggle('hi')\n"
    assert not _column_index_problems(ok)

    # Rebinding must reset the count, not keep the old one.
    rebound = "cols = st.columns(4)\ncols = st.columns(2)\nx = cols[3]\n"
    assert _column_index_problems(rebound)
