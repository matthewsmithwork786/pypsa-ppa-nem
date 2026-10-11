# Session handoff — multi-year Zenodo plants + tiered SLA

**Date:** 2026-08-11. **VM:** GCP `pypsa-ppa-dev` (idle-shutdown after 30 min with no
SSH/claude process — safe to stop, boot disk persists).
**Branch:** `feature/multiyear-plants-and-tiered-sla` off `feature/energy-first-results-deploy`,
pushed to `origin`, HEAD `17d398b`. Nothing uncommitted, no worktrees left open.
**Plan doc:** `docs/IMPLEMENTATION_PLAN_multiyear_and_sla.md` (original) +
`docs/PLAN_multiyear_sla.md` (environment facts / answers to the plan's open questions).

## Status: all 12 work packages done and merged

WP1, WP3–WP11 landed exactly as specced (see `prompts/wp*.md` for the exact briefs used,
and the merge commits on the branch for what actually shipped — each merge commit message
summarises the WP and any orchestrator-level fix applied on top). WP2 was split into
WP2a (done — the operational-year manifest builder) and WP2b/2c (deferred — see below).
WP12 (integration/regression) is this file's author's own work, not a sub-agent's.

**Numerical regression gate: exact match.** Same scenario (`optimise_capacity=True,
sizing_method="tsam", simulation_years=3`, default SLA/resolution) run on the base branch
and this branch produced byte-identical sized wind/solar/BESS MW, year-1 delivered GWh,
`fulfilled_share`, and total load MWh. Full record in `docs/sizing_experiments.md` §E13.
Full test suite: **413 passed, 3 skipped, 0 failed** as of `17d398b`
(`MPLCONFIGDIR=/tmp/mplcache .pixi/envs/default/bin/python3 -m pytest -q -p no:cacheprovider`).

## Two real findings that need a decision, not bugs from this session

1. **`LKBONNY2` fails the new stricter operational rule.** The plan assumed it would pass
   (§2.2's validation set); real 2025 data shows a genuinely wind-poor January (54% of its
   own annual peak, verified against raw data, not an artefact). Full characterisation in
   `docs/sizing_experiments.md` §E13 / `docs/DATA_ACQUISITION.md`. Decide: keep the 0.60
   monthly-peak-ratio threshold as-is (LKBONNY2 stays excluded) or reconsider it.
2. **`tsam==3.4.2` has a real bug**, unrelated to this feature (reproduces in code that
   predates this branch). `cluster_typical_periods(ts, n_periods=N, extreme_periods=True)`
   raises `IndexError` inside tsam's own `_rescaleClusterPeriods` for `N ∈ {4, 8, 16}`;
   works at `N ∈ {20, 40}` (the `Scenario.sizing_n_periods` default) or with
   `extreme_periods=False`. `validate_scenario` currently allows any `N` in `[4, 40]`, so a
   user picking a low tsam period count can crash a sizing run today. Full details in
   `docs/sizing_experiments.md` §E14. **`tsam` was left uninstalled** in the shared
   `.pixi` env afterwards — it's declared in `pixi.toml` but was never actually installed,
   which is why nobody had hit this before. Needs its own fix (tsam version bump if one
   exists compatible with the pinned `highspy==1.15.0`, or a defensive fallback in
   `ppa/sizing_tsam.py`) before installing `tsam` by default.

## Deferred: not started this session

**WP2b/2c** (tracked as its own item, see task list if resuming via Claude Code with task
continuity, otherwise just re-read this section): `scripts/fetch_nem_availability.py`'s
multi-year/disk-safe-streaming extension, and `scripts/build_zenodo_dataset.py`. Blocked on:
- Hanan setting up a Zenodo account, record, and upload token (his call, not started).
- A deliberate, supervised, multi-day background job for the real AEMO historical pull —
  **do not let a sub-agent run this unsupervised.** This VM has only ~36 GB free on `/`
  (not the 952 GB `AGENTS.md` originally assumed, written for a different machine); the
  disk-safe design is: nemosis already pulls one month at a time, so extend
  `fetch_nem_availability.py` to write each month's slice into the compact per-DUID
  accumulator and delete that month's raw AEMO archive immediately, never holding more than
  ~1 month of raw data at once.

Until WP2b/2c land and a real Zenodo record is published, the app works correctly and
degrades gracefully to the single committed 2025 year everywhere (`ppa/data/zenodo_manifest.json`
has a placeholder `record_id`; `remote_cache.ensure_plant_years` will raise `RemoteFetchError`,
which the UI shows as a clean `st.error`, not a crash; the Pick Plants tab falls back to
`[2025]` with an explanatory caption when `plant_years.parquet` doesn't exist).

## Not yet done from this session (offered, not actioned)

**No deploy has happened.** I offered to run a manual
`gcloud builds submit --config cloudbuild.yaml` to the `pypsa-ppa-explorer` Cloud Run
service (it has no auto-trigger; `pypsa-ppa-nem-git` auto-deploys from `main` only, not
this branch) so the feature branch could be tested live — waiting on a yes/no. To do this
on resume: `cd /home/matthewsmithwork786/pypsa-ppa-nem && gcloud builds submit --config cloudbuild.yaml`
(from this branch, or merge to `main` first if that's the intended flow — confirm with Hanan
which he wants).

**No manual browser UAT** of the new UI controls happened — only a headless
`streamlit.testing.v1.AppTest` smoke check (all 6 tabs render with no exceptions) plus the
full unit-test suite. `docs/UAT_checklist.md` has the manual-check list WP11 added.

**No peak-RSS measurement** (§5.4 of the plan) was done — `scripts/measure_peak_rss.py`
exists and is the right tool if this matters before a real deploy.

## How this session's orchestration actually worked (useful if resuming the same way)

Sub-agents were `opencode run --auto --model opencode/<model>` instances (models used:
`big-pickle`, `deepseek-v4-flash-free`; `mimo-v2.5-free` was available but never tried), one
per work package, each in its own `git worktree` off the working branch with `.pixi`
symlinked to the main checkout's (saves disk/time, all worktrees share one env).

**The dominant failure mode**: free-tier models reliably stall on any work package that
requires reading a large existing file (1000+ lines) and synthesising new code against it —
they read/grep extensively and then exit cleanly having written *nothing*, with no error.
Small/mechanical work packages (WP1, WP5) succeeded first-try. See the memory file
`opencode_subagent_reliability.md` (in this Claude Code account's persistent memory, not in
this repo) for the full writeup. **The fix that worked every time**: don't restart from
scratch — find the stalled session via `opencode session list`, then
`opencode run --auto -s <session-id> "You have read enough. Your next tool call must be an
Edit or Write."` from the same worktree directory. This resumes with the already-paid-for
file-reading context and just needs a forcing nudge. Most work packages needed 2-4 resume
rounds before landing a commit. Every sub-agent's diff was independently re-verified (full
test suite re-run by the orchestrator, not trusted from the sub-agent's self-report) before
merging — this caught two real issues (a `Scenario.validate_scenario` scoping bug in WP5,
and a silent semantic merge conflict between WP2a/WP3 in a fixture helper that git merged
cleanly but broke at runtime) that a rubber-stamp merge would have missed.

## To resume

1. Start the VM: `gcloud compute instances start pypsa-ppa-dev --zone=us-central1-a`
2. `tmux attach -t claude` (or `claude --continue` if the session history is gone).
3. `cd /home/matthewsmithwork786/pypsa-ppa-nem && git status` — should be clean on
   `feature/multiyear-plants-and-tiered-sla` at `17d398b` or later.
4. Decide: deploy for live testing now, start WP2b/2c (needs Hanan's Zenodo token first),
   fix the tsam bug, or open a PR from this branch into `main`/the base branch.
