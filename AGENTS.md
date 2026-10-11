# Repository guidance

Read this before changing the model, data layer, or user interface.

## Architecture and data boundaries

- `ppa/` contains the model and data readers; `ui/` and `streamlit_app.py` contain Streamlit presentation. Keep core calculations testable without importing UI modules where practical.
- Runtime data access is offline and cache-backed. `ppa/data/nem_data.py` and `ppa/data/aer_futures.py` read local cache files only. Network access belongs in standalone scripts under `scripts/`, never in runtime readers or UI code.
- Bundled cache files under `data/cache/nem/` are required for the app to work without external services. Preserve them and their provenance. Acquisition tools must not overwrite verified registry data without an explicit overwrite option.
- Missing optional cache artifacts should degrade gracefully where the code already provides a fallback; do not turn optional data into a startup failure.
- Use Australian English in project-authored user-facing text and identifiers where appropriate. `tests/test_spelling_en_au.py` checks selected application files. Do not rename third-party APIs such as `n.optimize`, `scipy.optimize`, or pandas `.normalize()`.
- `validate_scenario()` returns blocking errors only. Put non-blocking guidance beside the relevant control in the scenario form; the UI treats validation errors as a reason to refuse a run.

## Model constraints to preserve

- **Availability input:** the capacity factor used as `p_max_pu` should represent unconstrained available generation. AEMO UIGF (`DISPATCHLOAD.AVAILABILITY`) is that bound; SCADA is sent-out production and can embed network and economic curtailment. Do not treat SCADA as an equivalent availability series or apply a flat uplift to it.
- **Capacity factor:** CUF is AC energy divided by registered capacity and hours. Solar clipping near 0.80 of nameplate and fleet CUFs around 17–21% can be expected; do not infer an error from those values alone.
- **Snapshot weights:** PyPSA's `objective` and `generators` weights scale costs and energy across representative periods. `stores` is the storage-balance time step, not the representative-period occurrence count. Keep it at the actual intra-period duration (one hour for hourly representatives).
- **Weighted constraints:** every energy aggregation over snapshots in `ppa/solver.py` must use the applicable snapshot weights. Uniform snapshots can hide errors that appear with representative periods.
- **Delivery accounting:** PPA delivery earns the contract price through the offtake link. Shortfall and penalty generators bypass that link. A hard minimum-delivery constraint is only applied when `enforce_min_delivery` is enabled; a penalty price alone does not guarantee contractual delivery.
- **Commissioning filter:** compare early output with the same plant's own annual peak. Absolute output thresholds misclassify clipped solar and curtailed plants.
- **Typical-period calendars:** `tsam` representatives do not retain real calendar months or days. Monthly SLAs must remain blocked for clustered sizing; daily SLA groups use representative-period labels and are an approximation.
- **Memory:** sizing LPs can be large. Keep the subprocess sizing path and release large frames before forking multi-year dispatch workers. A child process is used so its peak memory can be reclaimed after the solve.

## Validation

Run the relevant tests after a model or UI change. The full suite is:

```bash
MPLCONFIGDIR=/tmp/mplcache python3 -m pytest -q -p no:cacheprovider
```

Scripts that import project modules should be run with `PYTHONPATH=.`. Do not claim checks passed unless they were run and their result is available.
