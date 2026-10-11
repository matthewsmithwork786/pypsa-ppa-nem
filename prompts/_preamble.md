Repo: pypsa-ppa-nem. You are working in a dedicated git worktree/branch for this
work package only.

IMPORTANT — environment correction versus anything else you may have been told:
this VM's bare `python3` on PATH is the SYSTEM python and does NOT have the
project's dependencies (pypsa, streamlit, etc.) installed. You MUST use the
project's pixi environment interpreter for everything — tests, scripts, one-off
checks:

    Tests:   MPLCONFIGDIR=/tmp/mplcache .pixi/envs/default/bin/python3 -m pytest -q -p no:cacheprovider
    Scripts: PYTHONPATH=. .pixi/envs/default/bin/python3 <script>.py
    Any ad-hoc python: .pixi/envs/default/bin/python3

`.pixi` in your worktree is a symlink to the main checkout's `.pixi` — this is
intentional (saves disk/time), do not delete or rebuild it. Do not run
`pixi install` or otherwise modify `.pixi`.

Read AGENTS.md before you touch anything. Sections 2, 3 and 5 are hard constraints.
Also read docs/PLAN_multiyear_sla.md for environment-specific facts and the
answers to the open questions in docs/IMPLEMENTATION_PLAN_multiyear_and_sla.md §2.2
(disk headroom, Zenodo ownership, 1+1 plants confirmed).

Australian English is enforced by tests/test_spelling_en_au.py — write "optimise",
"analyse", "behaviour", "modelling". Never rename third-party APIs (n.optimize,
scipy.optimize, pandas .normalize()).

ppa/data/nem_data.py and ppa/data/aer_futures.py MUST NOT import requests, urllib,
httpx, nemosis, socket or streamlit. tests/test_nem_data.py and
tests/test_aer_futures.py enforce this. All network access lives in scripts/ or in
the new ppa/data/remote_cache.py (which is explicitly exempted).

validate_scenario() returns BLOCKING errors only. Warnings go in ui/scenario_form.py
next to the control they concern.

Do not commit new data blobs. Do not run `git push` yourself — commit locally on
your branch and stop; the orchestrator handles pushing/merging.

Disk is limited on this machine (36 GB free on /) — do not retain large raw
intermediate files. Delete raw downloads as soon as you've extracted what you need
from them.

Report back with: files changed, tests added, the exact pytest command you ran, and
its output. Do not claim a test passes without pasting the output. Do not modify
files outside the work package's "Files owned" list below. If you believe you need
to, stop and report why instead of doing it.

BUDGET WARNING: you have a limited number of tool calls in this session. The work
package spec below already contains the exact code, function signatures, and
docstrings you need — it was written by someone who has already read the relevant
source files. Do NOT spend your budget re-reading files whose relevant content is
already quoted in the spec, and do NOT re-read the plan documents (AGENTS.md
excepted — read that once, briefly). Open a file only to find the exact insertion
point, then edit it. You should make your first Edit/Write tool call within your
first 2-3 tool calls, not after ten reads. If you find yourself exploring instead
of writing code, stop and start writing. It is far better to deliver a partial,
committed, working implementation of the highest-priority items in the spec than
to run out of budget having only read files.

---
