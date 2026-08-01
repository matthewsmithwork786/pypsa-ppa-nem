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
# regex matching the offensive token.
_AMERICAN_PATTERNS = [
    re.compile(r"optimiz(?:e|es|ed|ing|er|ers|ation|ations)"),
    re.compile(r"co-optimiz(?:e|es|ed|ing|ation|ations)"),
    re.compile(r"analyz(?:e|es|ed|ing)"),
    re.compile(r"normaliz(?:e|es|ed|ing)"),
    re.compile(r"maximiz(?:e|es|ed|ing|ation|ations)"),
    re.compile(r"minimiz(?:e|es|ed|ing|ation|ations)"),
    re.compile(r"\bbehavior(?:s)?\b"),
    re.compile(r"customiz(?:e|es|ed|ing)"),
    re.compile(r"summariz(?:e|es|ed|ing)"),
    re.compile(r"organiz(?:e|es|ed|ing)"),
    re.compile(r"\bmodeled\b"),
    re.compile(r"\bfulfillment\b"),
]

# Third-party / standard-library API surface that legitimately keeps the US
# spelling. A hit on a line matching any of these is suppressed.
_ALLOWLIST_PATTERNS = [
    re.compile(r"pypsa\.options\.params\.optimize"),
    re.compile(r"\bn\.optimize\b"),                      # pypsa Network.optimize API
    re.compile(r"\boptimize\.(?:create_model|solve_model|load_network)\b"),
    re.compile(r"\bscipy\.optimize\b"),
    re.compile(r"\blinopy\b"),
    re.compile(r"\bstr\.center\b"),
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


@pytest.mark.xfail(strict=True, reason="W10: Australian-English pass not landed yet")
def test_no_american_spellings_in_app_code():
    hits: list[str] = []
    for path in _scan_files():
        try:
            lines = path.read_text().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(lines, start=1):
            if any(allow.search(line) for allow in _ALLOWLIST_PATTERNS):
                continue
            for pattern in _AMERICAN_PATTERNS:
                match = pattern.search(line)
                if match:
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {match.group(0)!r} :: {line.strip()}")
                    break
    assert not hits, "American spellings remain (rename to Australian English):\n" + "\n".join(hits)
