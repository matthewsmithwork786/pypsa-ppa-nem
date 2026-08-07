"""W10 gate: Australian English throughout our own strings and identifiers.

Rule: rename *our* strings/identifiers; never rename third-party APIs. The
allowlist below is exactly the set of third-party/standard-library API surface
from the plan (`pypsa` options + optimize API, `scipy.optimize`, linopy
attribute names, and non-US-spelling items like `color` which are exempt by
construction because they never match the forbidden patterns).

Scan targets: `ppa/`, `ui/`, `scripts/`, `streamlit_app.py`, `README.md`
(repo tests are intentionally out of scope — they are not shipped UI).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

_SCAN_GLOBS = [
    "ppa/**/*.py",
    "ui/**/*.py",
    "scripts/**/*.py",
]
_SCAN_FILES = ["streamlit_app.py", "README.md"]

# American spellings that must become Australian/British English. Each is a
# regex matching the offensive token. Matching is case-INSENSITIVE: a heading
# like "**Optimization**" is just as user-visible as the lowercase form, and a
# case-sensitive gate silently missed exactly that (README.md:42).
_AMERICAN_PATTERNS = [
    re.compile(r"optimiz(?:e|es|ed|ing|er|ers|ation|ations)", re.I),
    re.compile(r"co-optimiz(?:e|es|ed|ing|ation|ations)", re.I),
    re.compile(r"analyz(?:e|es|ed|ing)", re.I),
    re.compile(r"normaliz(?:e|es|ed|ing)", re.I),
    re.compile(r"maximiz(?:e|es|ed|ing|ation|ations)", re.I),
    re.compile(r"minimiz(?:e|es|ed|ing|ation|ations)", re.I),
    re.compile(r"\bbehavior(?:s)?\b", re.I),
    re.compile(r"customiz(?:e|es|ed|ing)", re.I),
    re.compile(r"summariz(?:e|es|ed|ing)", re.I),
    re.compile(r"organiz(?:e|es|ed|ing)", re.I),
    re.compile(r"\bmodeled\b", re.I),
    re.compile(r"\bfulfillment\b", re.I),
]

# Third-party / standard-library API surface that legitimately keeps the US
# spelling. Only the matched SPAN is suppressed, not the whole line, so each
# entry must itself contain the US spelling it excuses. (The plan's allowlist
# also names `linopy` attributes, `str.center`, plotly/CSS `color` and
# `scipy.optimize`; those needing no entry here are exempt by construction —
# they contain no forbidden token — and `scipy.optimize` is listed for the day
# it appears.)
_ALLOWLIST_PATTERNS = [
    re.compile(r"pypsa\.options\.params\.optimize"),
    re.compile(r"\bn\.optimize\b"),                      # pypsa Network.optimize API
    re.compile(r"\boptimize\.(?:create_model|solve_model|load_network)\b"),
    re.compile(r"\bscipy\.optimize\b"),
    # pandas Timestamp/Index.normalize() (third-party API — never renamed)
    re.compile(r"\.normalize\("),
    # Legacy US spelling of the optimise_capacity column key, from the now-removed
    # Excel-tab import (scenario_from_excel, plan W10b). No current source uses it;
    # kept in case Excel import is rebuilt.
    re.compile(r"optimize_capacity"),
]


def _scan_files() -> list[Path]:
    files: list[Path] = []
    for glob in _SCAN_GLOBS:
        files.extend(sorted(REPO_ROOT.glob(glob)))
    for name in _SCAN_FILES:
        p = REPO_ROOT / name
        if p.exists():
            files.append(p)
    # Dedupe (glob may match files already appended) while preserving order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in files:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def _allowed_spans(line: str) -> list[tuple[int, int]]:
    """Character spans on *line* covered by an allowlisted third-party name."""
    return [m.span() for allow in _ALLOWLIST_PATTERNS for m in allow.finditer(line)]


def _is_allowed(match: re.Match, spans: list[tuple[int, int]]) -> bool:
    """True if *match* falls inside an allowlisted span.

    Span-precise rather than line-wide on purpose: a line may legitimately
    mention `n.optimize` *and* carry an unrelated American spelling, and
    suppressing the whole line would hide the second one.
    """
    start, end = match.span()
    return any(a_start <= start and end <= a_end for a_start, a_end in spans)


def test_no_american_spellings_in_app_code():
    """Australian-English pass (W10) landed; this gate must now stay green.
    Rename our own strings/identifiers, never third-party APIs (see allowlist)."""
    hits: list[str] = []
    for path in _scan_files():
        try:
            lines = path.read_text().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(lines, start=1):
            spans = _allowed_spans(line)
            for pattern in _AMERICAN_PATTERNS:
                match = next(
                    (m for m in pattern.finditer(line) if not _is_allowed(m, spans)), None
                )
                if match:
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {match.group(0)!r} :: {line.strip()}")
                    break
    assert not hits, "American spellings remain (rename to Australian English):\n" + "\n".join(hits)
