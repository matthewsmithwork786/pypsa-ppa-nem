# Sizing benchmarks and interpretation

The benchmark scripts in `scripts/` are the reproducible tools for measuring current sizing behavior:

- `bench_sizing_method.py` compares representative-period and full-hourly capacity sizing.
- `bench_merchant_share.py` sweeps the merchant-value assumption.
- `bench_solver.py` compares solver settings on a sizing LP.
- `measure_peak_rss.py` measures process memory for sizing and dispatch phases.

Run scripts from the repository root with `PYTHONPATH=.`. Results depend on the bundled cache, scenario, solver, machine resources, and dependency versions; old timing or fleet-size values should not be treated as deployment guarantees.

Typical-period sizing is an approximation that reduces LP size while retaining hourly shapes. It can change the selected fleet, especially storage. Compare with full-hourly sizing using the same scenario and data when evaluating a material model change. Snapshot weights must be applied consistently to energy and cost totals, and storage `stores` weights must remain the actual within-period time step; see [model assumptions](model_assumptions.md).

Sizing experiments should change one material assumption at a time and record the exact scenario, data year, dependency versions, command, and result alongside any decision they support. A delivery-share match alone does not establish that the sized capacities match the full-hourly result.
