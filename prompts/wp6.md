### WP6 — Resolution plumbing (5 / 15 / 30 / 60 minutes)

**Owns:** `ppa/data/timeseries_utils.py`, `ppa/multi_year.py`, `ppa/sizing.py`,
`ui/tabs/optimisation.py`, `tests/test_resolution_h.py`.

**Difficulty:** medium-high.

Today everything is hourly: `build_year_timeseries` hardcodes `freq="h"` and
`_hours_in_year`, and `run_multi_year` never passes `resolution_h`. But
`build_network(..., resolution_h=...)` and `extract_results(..., resolution_h=...)`
already accept it — the plumbing is half there. `Scenario.nem_resolution_minutes`
already exists (already-merged work package), default 60.

#### 6.1 `build_year_timeseries`

Find it in `ppa/data/timeseries_utils.py`. Add `resolution_minutes: int = 60`.
Replace:

```python
year_index = pd.date_range(start=f"{sim_year}-01-01", periods=_hours_in_year(sim_year),
                           freq="h", tz="UTC")
```
with a `freq=f"{resolution_minutes}min"` index of
`_hours_in_year(sim_year) * 60 // resolution_minutes` periods.

`_align_to_index` is positional and length-driven, so it works unchanged — **but** its
tiling behaviour now tiles at the new resolution. Add a test asserting a 30-min source
against a 30-min target is a no-op.

`get_load_series(load_profile, naive_index)` in `ppa/industrial_profiles.py` must be
checked (read-only — that file is not in your Owns list): if it assumes hourly (e.g.
indexes by `.hour` only) it is fine; if it assumes exactly 8760 rows it is not. Report
which in your final report; if it needs a fix, stop and report rather than editing a
file outside your Owns list.

#### 6.2 Thread `resolution_h` through the runners

- `run_multi_year(..., resolution_h: float = 1.0)` in `ppa/multi_year.py`; pass
  `resolution_minutes` into `build_year_timeseries`; pass `resolution_h` into
  `build_network` and `extract_results` inside `_solve_one_year` (it must cross the
  process boundary — add it as an explicit argument, not on the scenario dict, to keep
  `_solve_one_year`'s signature honest).
- `build_sizing_timeseries(..., resolution_minutes)` likewise (find it near
  `build_year_timeseries`).
- `ppa/sizing.py::optimise_capacities` computes `n_years` from `len(ts) / 8760` in the
  `full_hourly` branch — that is now wrong at sub-hourly resolution. Change to
  `len(ts) / (8760 * 60 / resolution_minutes)`. **This is an easy miss with a large
  blast radius** (it scales every capex term via `horizon_years`) — grep for every place
  `8760` appears literally in `ppa/sizing.py` and check each one.
- `clamp_sizing_years(requested_years, resolution_h)` (in `ppa/sizing.py`) already takes
  `resolution_h` but is called with the default. Pass the real one — note the existing
  formula *divides* memory by `resolution_h`, which is right for coarse resolutions
  (>1 h) and correspondingly right for fine ones (`resolution_h = 0.0833` at 5 min → 12×
  the memory). Verify the arithmetic rather than assuming it's right; write a quick test
  asserting the direction (finer resolution -> lower clamp, not higher).

#### 6.3 The guard

In `ui/tabs/optimisation.py::_run_simulation`, before solving:

```python
res_min = int(getattr(scenario, "nem_resolution_minutes", 60))
if res_min < 30 and scenario.simulation_years > 2:
    raise RuntimeError(
        f"{res_min}-minute resolution over {scenario.simulation_years} years builds an "
        f"LP roughly {60 // res_min}x the hourly size and will exhaust memory. "
        "Reduce the simulation years to 2 or fewer, or use 30- or 60-minute resolution."
    )
```
This file also has a `_render_scenario_summary` function used by a different, not-yet-
started work package — do not touch that function, only add the guard above near
`_run_simulation`.

#### 6.4 Tests

Extend `tests/test_resolution_h.py` (read it first for existing patterns/fixtures):
- A 30-min run and a 60-min run over the same underlying data produce total load MWh
  within 0.5% of each other. *This is the check that catches a missing `resolution_h`:
  summing MW samples only equals MWh when each sample is one hour.*
- `n_period_hours` on the result equals `8760` (±) regardless of resolution.
- Sized MW at 60 min vs 30 min agree within 10%.
- `clamp_sizing_years` direction test from §6.2.

**Done when:** the 30-vs-60-minute energy-conservation test passes, and the full suite is
green with `nem_resolution_minutes=60` producing **byte-identical** results to before —
run a before/after comparison on one small scenario (pick something already covered by
an existing test, e.g. reuse a `tests/test_sizing_network.py` fixture pattern at default
resolution) and paste both numbers in your final report.

---
Do not modify files outside the "Files owned" list above. If you believe you need
to, stop and report why.
