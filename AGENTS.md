# AGENTS.md — working notes for agents on this repo

Practical guidance for an agent (Claude Code, opencode, or otherwise) working on
`pypsa-ppa-nem`. Read §1 and §2 before running anything; §5 records mistakes that
have already been made here, so they are not repeated.

---

## 1. Environment

| Thing | Value |
|---|---|
| Python | `python3` only — **there is no `python` binary** |
| Venv | none; user site-packages |
| Tests | `MPLCONFIGDIR=$TMPDIR python3 -m pytest -q -p no:cacheprovider` |
| Scripts | need `PYTHONPATH=.` |
| Repo | `/home/hanan/projects/pypsa-ppa-nem` |

- `MPLCONFIGDIR=$TMPDIR` silences a matplotlib cache warning; `-p no:cacheprovider`
  avoids a read-only `.pytest_cache` warning.
- **`/tmp` is a tmpfs (~6 GB, RAM-backed).** Do not put large downloads there — a
  full-year `DISPATCHLOAD` pull filled it and killed two jobs. Use `/` (952 GB free);
  the repo's gitignored `nemosis_cache/` is the right home for raw acquisition data.
- **The sandbox cannot write to the project directory.** `git commit`, `sed -i`,
  `pip install` and any tool writing to `~/.local` fail with "Read-only file system"
  or an opaque `FileSystem.open` error. Re-run those with the sandbox disabled.
- Full-year sizing solves take ~130 s. Anything touching the real cache belongs in a
  background job, not a foreground call that will time out.

---

## 2. Repo conventions that will bite you

- **No-network import discipline.** `ppa/data/nem_data.py` and `ppa/data/aer_futures.py`
  must not import `requests`/`urllib`/`httpx`/`nemosis`/`socket`/`streamlit`. All network
  access lives in `scripts/`. Preserve this.
- **Australian English is a test gate.** `tests/test_spelling_en_au.py` scans `ppa/`,
  `ui/`, `scripts/`, `streamlit_app.py`, `README.md`. It is case-insensitive and
  suppresses only the matched span of an allowlisted third-party name, not the whole
  line. Rename our own identifiers; never rename third-party APIs (`n.optimize`,
  `scipy.optimize`, pandas `.normalize(`).
- **Data caches are committed** (`data/cache/nem/{scada,price,registry,availability}`),
  because the app cannot run `nemosis` on Streamlit Cloud. They are large; see §6.
- Optional caches must **degrade, not fail**: a missing file falls back per-DUID rather
  than raising. Follow the `first_power_date` / `availability` pattern.
- `validate_scenario` returns **blocking errors**. Do not put warnings in it — the UI
  refuses to run on any non-empty result. Warnings belong in `ui/scenario_form.py`
  next to the control they concern.

---

## 3. Domain traps specific to this model

- **UIGF vs SCADA.** `DISPATCHLOAD.AVAILABILITY` (UIGF) is unconstrained *available*
  output; SCADA is *sent-out* output after network constraints and after whatever
  economic curtailment that plant's own offtake contract incentivised. The LP treats the
  CF series as `p_max_pu` — an upper bound it curtails against itself — so feeding it
  SCADA double-counts curtailment. `use_unconstrained_cf` defaults **on**.
  Per-plant curtailment ranges ~0–71%, so no flat uplift can substitute.
- **Capacity factors here are AC CUFs** against *registered* capacity. Solar reads
  ~17–21% fleet-wide and clips near 0.80 of nameplate. That is normal, not a bug.
- **`snapshot_weightings` has three columns and they mean different things.**
  `objective`/`generators` scale cost and energy to the represented year;
  **`stores` is the dt in the storage energy balance**. Setting `stores` to a typical-
  period occurrence count makes one snapshot span up to 55 h, and no short-duration
  battery can shift energy across that — it silently sizes storage to zero. See
  `intra_period_hours` in `build_network`.
- **Energy constraints must be weighted.** Any `.sum()` over snapshots in
  `ppa/solver.py` needs the snapshot weighting applied. With uniform weights both sides
  scale together and errors hide; with typical periods they do not.
- **The BESS is a delivery instrument, not an arbitrage one.** A delivered MWh earns
  `ppa_price` via the offtake link, while shortfall and penalty generators sit on
  `Bus_PPAOfftake` and bypass it — so shifting a MWh into a deficit hour is worth
  A$105–231, versus tens of dollars of intraday spread.
- **`enforce_min_delivery`** makes the contractual share a hard LP constraint. Without
  it the SLA is only a price signal, and whenever the penalty is cheaper than building
  (usually, at GenCost capex) the LP rationally buys out of it and lands at 50–65%
  delivery.
- **Commissioning plants** are excluded by comparing early-year peak to the plant's own
  annual peak (`commissioning_ramp_check`). Do not switch this to an absolute-level
  test: solar clips at 0.80 of nameplate, and heavily curtailed plants dip mid-year.

---

## 4. Using opencode as a subagent

`opencode` (v1.17.x) is installed at `~/.opencode/bin/opencode` with OpenCode Zen and
DeepSeek credentials.

```bash
cd /path/to/workdir && opencode run --auto \
  --model opencode/deepseek-v4-flash-free \
  "your task here"
```

- `--auto` is **required** non-interactively. Without it the run hangs on the first
  tool-permission prompt.
- `--model provider/model`. `opencode/deepseek-v4-flash-free` is free and handled a
  write-code-plus-tests task correctly; use a stronger model for harder work, which
  costs real credits.
- Other useful flags: `--dir` (working directory), `--format json` (machine-readable
  events; default output carries ANSI codes), `-c` (continue last session),
  `--agent`, `--title`. `opencode models` lists what is available.
- **Must run with the sandbox disabled** — it writes to
  `~/.local/share/opencode/log/` and otherwise dies with
  `Unknown: FileSystem.open`, which does not read like a permission error.
- It runs unsandboxed with `--auto` approving its own tool calls, so it has full
  filesystem access wherever `--dir` points. Scope that deliberately.
- Tell it `python3`, not `python` — it will otherwise waste a turn discovering this.
- **Verify its output yourself.** It self-reports success and is often right, but a
  report is not evidence. Run the tests it claims pass.

---

## 5. Mistakes already made here — do not repeat them

1. **Control for the confounder before concluding.** A "tsam under-sizes the fleet
   monotonically" finding turned out to be an artifact of a capex change made between
   runs. A "clustering destroys storage" finding was a units bug. Both were published
   before the variable was isolated, and both had to be retracted. Change one thing per
   measurement.
2. **A constraint that is missed is not a constraint.** A hard 90% delivery constraint
   landing at 85.6% was the clue that led to the weighting bug. When the optimiser
   refuses the cheapest way to satisfy a binding constraint, suspect the model, not the
   economics.
3. **Aggregation checks passing is weak evidence.** Energy, annual mean and peak load
   were all preserved exactly while the storage decision was being destroyed. Check the
   quantity you actually care about — `validate_sizing_representation()` compares sized
   MW per technology for this reason.
4. **Don't infer what you can measure.** Two curtailment estimates (negative-price
   correlation, and a p95 clear-sky proxy) were both wrong — the first confounded
   because solar output *causes* negative prices, the second measuring seasonality. The
   UIGF data answered it directly.
5. **Watch memory when forking.** `run_multi_year` forks; CPython refcounting dirties
   COW pages so each worker costs roughly the parent's RSS. Always size the sizing LP in
   a subprocess (`run_sizing_subprocess`) and free large frames before the fork. A
   silent SIGKILL with no traceback is the OOM killer.
6. **Record results where they survive.** `docs/sizing_experiments.md` is the log. A
   previous round reported benchmarks as "recorded in the commit message" when they had
   never been run.

---

## 6. Repo size

`.git` is ~400 MB: ~213 MB of SCADA blobs, ~206 MB of availability blobs, ~67 MB
everything else. Both caches are committed deliberately (Streamlit Cloud cannot fetch
them). Note that **deleting a cache from the working tree does not reclaim history** —
that needs a history rewrite, which invalidates every existing clone. Decide before
pushing, not after.
