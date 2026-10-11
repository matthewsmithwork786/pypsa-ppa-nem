### WP5 — `Scenario`: new fields for years, sizing year, resolution and SLA

**Owns:** `ppa/scenario.py`, `ui/state.py`, `tests/test_scenario_nem.py`, plus mechanical
fixes at any `dataclasses.replace(..., nem_year=...)` site.

**Difficulty:** medium. This is the shared file that two other independent chains of
work depend on — get it right and land it as one clean commit.

#### 5.1 New fields

```python
    # ── NEM data selection ───────────────────────────────────────────────────
    # Historical years to cycle through in the multi-year dispatch simulation,
    # in order, repeating. Chronological order is the meaningful one, so this is
    # kept sorted ascending. `nem_year` (below) remains as a derived property so
    # the ~38 existing single-year call sites keep working.
    nem_years: tuple[int, ...] = (2025,)
    # The ONE historical year the capacity-sizing LP optimises against. Sizing
    # over the full multi-year horizon is possible but is what makes the LP
    # large; the user picks a representative year instead.
    capacity_sizing_year: int = 2025
    # Snapshot resolution in minutes for the generation/price series: 60, 30, 15
    # or 5. Sub-hourly multiplies LP size (and memory) by 60/resolution, so 15
    # and 5 are guarded — see ui/scenario_form.py and ppa/multi_year.py.
    nem_resolution_minutes: int = 60

    # ── Tiered SLA ───────────────────────────────────────────────────────────
    # `required_delivery_share` above is the ANNUAL obligation. These add
    # tighter obligations at monthly and daily granularity. Both are OFF by
    # default: with them off the model is bit-identical to today's.
    sla_monthly_enabled: bool = False
    sla_monthly_share: float = 0.0
    sla_daily_enabled: bool = False
    sla_daily_share: float = 0.0
```

Replace the `nem_year: int = 2025` **field** with:

```python
    @property
    def nem_year(self) -> int:
        """First (earliest) selected NEM data year.

        Kept as a property, not a field, so the many single-year call sites
        (AER futures, reference_month_ts, cache_status, config summary) keep
        working. It is deliberately absent from dataclasses.asdict(), and
        dataclasses.replace(s, nem_year=...) will now raise — use
        nem_years=(y,).
        """
        return int(self.nem_years[0]) if self.nem_years else 2025
```

Then:

```bash
grep -rn "nem_year=" --include=*.py . | grep -v nem_years
```
Fix every hit to `nem_years=(y,)`. Expect hits in `ui/tabs/nem_map.py`,
`ui/scenario_form.py`, `ui/tabs/optimisation.py` and several tests.

#### 5.2 `ui/state.py`

Add to `_SCENARIO_FORM_KEYS`:
```
"sf_nem_years", "sf_capacity_sizing_year", "sf_nem_resolution",
"sf_sla_monthly_enabled", "sf_sla_monthly_share",
"sf_sla_daily_enabled", "sf_sla_daily_share",
"nm_year_range", "nm_resolution",
```
(Removing `sf_nem_year` if the widget is gone.) A missing key here means a widget keeps
a stale value across scenario changes — a silent, confusing bug.

Note that `state.get_scenario()` already rebuilds a stale `Scenario` from
`dataclasses.asdict`, dropping removed fields and defaulting new ones. Because
`nem_year` becomes a property it will not appear in `asdict()` and a session holding an
old scenario will get `nem_years=(2025,)` by default. Acceptable — but state it in your
final report so nobody is surprised by a reset after deploy.

#### 5.3 `validate_scenario` additions

Blocking errors only:

```python
if not s.nem_years:
    errors.append("Select at least one NEM data year on the Pick Plants tab.")
for y in s.nem_years:
    if not (2000 <= int(y) <= 2100):
        errors.append(f"NEM data year {y} is out of range.")
if s.optimise_capacity and int(s.capacity_sizing_year) not in tuple(s.nem_years):
    errors.append(
        "The capacity-sizing year must be one of the selected NEM data years."
    )
if int(s.nem_resolution_minutes) not in (5, 15, 30, 60):
    errors.append("Snapshot resolution must be 5, 15, 30 or 60 minutes.")

if s.sla_monthly_enabled and not (0.0 < s.sla_monthly_share <= 1.0):
    errors.append("Monthly SLA share must be between 0 and 1 when monthly SLA is on.")
if s.sla_daily_enabled and not (0.0 < s.sla_daily_share <= 1.0):
    errors.append("Daily SLA share must be between 0 and 1 when daily SLA is on.")
# tsam destroys calendar structure, so a monthly constraint on the clustered
# representation would be silently meaningless rather than merely approximate.
if s.optimise_capacity and s.sla_monthly_enabled and s.sizing_method == "tsam":
    errors.append(
        "A monthly SLA cannot be enforced with the 'Typical weeks (tsam)' sizing "
        "representation: clustering replaces the calendar with representative "
        "weeks, so calendar months no longer exist in the sizing LP. Switch the "
        "sizing representation to 'Full year hourly', or turn the monthly SLA off."
    )
```

Do **not** add "monthly share is looser than annual" here — that is a warning and belongs
in `ui/scenario_form.py` (a later work package owns that file — do not touch it).

Update the existing `if not (2000 <= int(s.nem_year) <= 2100)` check to iterate
`nem_years` instead.

#### 5.4 Tests

`tests/test_scenario_nem.py`: `nem_year` property returns `nem_years[0]`;
`asdict()` contains `nem_years` and not `nem_year`;
`dataclasses.replace(s, nem_years=(2020, 2021))` works;
each new validation rule fires and each is silent at defaults;
**a default `Scenario()` produces an empty `validate_scenario()` result** (regression
guard against accidentally making the app unrunnable out of the box).

**Done when:** full suite green with no behavioural change at default settings.

---
Do not modify files outside the "Files owned" list above. If you believe you need to,
stop and report why. This is the single most important work package to get exactly
right — two other independent chains of work will build on your `Scenario` shape
without further review of this file, so be conservative and do not rename or remove
anything not explicitly listed here.
