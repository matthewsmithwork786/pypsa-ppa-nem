# NEM data acquisition scripts

These are **one-time, standalone data-acquisition scripts**. They are not part
of the Streamlit app and must never be imported by anything under `ppa/` or
`ui/` — the app itself only ever reads the cached parquet files these scripts
produce (via `ppa/data/nem_data.py` and `ppa/data/aer_futures.py`, both
cache-only, no network imports).

**They must be run in a separate environment with real network access.**
This repo's own dev/sandbox session blocks the relevant domains
(`aemo.com.au`, `nemweb.com.au`, `api.opennem.org.au`,
`api.openelectricity.org.au`, `aer.gov.au`) at the network-policy level, so
these scripts cannot be run from here — that's expected, not a bug in the
scripts.

## Setup

In the separate networked environment:

```bash
python -m venv .venv-acquisition
source .venv-acquisition/bin/activate   # or .venv-acquisition\Scripts\activate on Windows
pip install -r scripts/requirements-acquisition.txt
```

Do **not** add these dependencies to `pixi.toml` or the repo's main
`requirements.txt` — they're intentionally kept out of the app's own
dependency set.

## Scripts

### 1. `fetch_nem_scada_prices.py`

Uses the `nemosis` package to pull AEMO's public MMS tables
(`DUDETAILSUMMARY`, `DISPATCH_UNIT_SCADA`, `DISPATCHPRICE`) and writes:

- `data/cache/nem/scada/{DUID}_{year}.parquet` — 5-minute SCADA MW per DUID
- `data/cache/nem/price/rrp_{REGION}_{year}.parquet` — 5-minute RRP per region
  (NSW1, QLD1, SA1, TAS1, VIC1)

```bash
python scripts/fetch_nem_scada_prices.py \
    --year 2025 \
    --min-capacity-mw 30 \
    --duid-list data/cache/nem/registry/nem_plant_registry.parquet \
    --out-dir data/cache/nem \
    --raw-cache-dir ./nemosis_cache
```

`--duid-list` accepts a `.parquet` file with a `duid` column (e.g. the registry
parquet above), a CSV with a `duid` column (or your own AEMO Generation
Information download), or a plain newline-delimited text file of DUIDs.
Without it, the script falls back to "every generator DUID above the capacity
threshold" — **not** filtered to wind/solar (`DUDETAILSUMMARY` has no
fuel-type column); run `fetch_nem_plant_registry.py` first and feed its
output in via `--duid-list` for a correctly-filtered set.

Pulls `DISPATCH_UNIT_SCADA` and `DISPATCHPRICE` for the full year in a single
bulk call each (then splits per DUID/region locally) — it does **not** re-pull
the whole table once per DUID/region. Runs an "8760 (or 8784 in a leap year)
hourly rows" QA check and logs a rows-vs-expected-5-min-intervals coverage
percentage before writing each file. For price files, QA failure is a hard
failure (the file is not written); for per-DUID SCADA files, QA failure is a
warning and the file is still written as partial — this is intentional so
`ppa/data/nem_data.py`'s "generated for the whole year" eligibility check can
see the cache file for a plant commissioned mid-year and correctly exclude it.
Skips existing files unless `--overwrite` is passed.

Output timestamps are tz-**naive NEM standard time (AEST, UTC+10)** — unlike
the tz-aware UTC caches used previously. Keep
this in mind when aligning against `ppa/data/timeseries_utils.py::_align_to_index`.

No environment variables required (nemosis pulls straight from AEMO's public
NEMWEB feeds).

### 2. `fetch_nem_plant_registry.py`

Uses the OpenNEM / Open Electricity facilities API to build a wind/solar
plant registry with coordinates, and writes:

- `data/cache/nem/registry/nem_plant_registry.parquet` — columns `duid,
  station_name, region, fuel_tech, capacity_registered_mw, lat, lon, status`

```bash
export OPENELECTRICITY_API_KEY=...   # free key from https://platform.openelectricity.org.au/
python scripts/fetch_nem_plant_registry.py \
    --min-capacity-mw 30 \
    --fuel-techs wind,solar_utility \
    --status operating \
    --out-dir data/cache/nem/registry
```

Requires `OPENELECTRICITY_API_KEY` (get one free at
https://platform.openelectricity.org.au/). If you can't get a key, the script
also accepts `--fallback-no-api-key`, which currently calls a documented stub
(`fetch_via_aemo_fallback()`) that explains the manual steps needed to build
the registry from AEMO's public no-auth Registration and Exemption List
instead (that source has no lat/lon — a small manual/geocoded lookup would be
needed on top).

Descends into the real OpenElectricity nested `units[]` list per facility
(each unit carries its own DUID/fueltech/capacity — the facility-level `code`
is not a DUID). Defaults to `--status operating` (excludes e.g.
`commissioning` facilities), maps raw fueltech taxonomy strings to title-case
`Wind`/`Solar` to match the existing registry file's convention, drops any row
with a null lat/lon, and refuses to overwrite an existing
`nem_plant_registry.parquet` unless `--overwrite` is passed (this repo's
`data/cache/nem/registry/nem_plant_registry.parquet` is real, hand-verified
data — do not clobber it by accident).

### 3. `fetch_aer_futures.py`

Downloads AER's free quarterly "base futures price" chart data and writes:

- `data/cache/nem/hedge/aer_base_futures_{year}.parquet` — columns `region,
  quarter_label, product, price_aud_mwh, as_at_date`

```bash
python scripts/fetch_aer_futures.py --year 2025 --out-dir data/cache/nem/hedge
```

No environment variables required (public, no-auth CSV). Skips the fetch if
the output file already exists, unless `--overwrite` is passed. Rejects a
`text/html` response outright (rather than attempting to parse it as CSV),
warns (rather than silently passing through) if parsed `region` values don't
match one of the 5 known NEM regions (`NSW1, QLD1, SA1, TAS1, VIC1`) — since a
mismatch (e.g. `NSW` instead of `NSW1`) won't join cleanly against
`price/rrp_{REGION}_{year}.parquet` filenames downstream — and exits non-zero
with a clear message (pointing at `CSV_URL`) rather than writing bad data if
the download or parse fails. See its printed instructions for the manual
fallback (open
https://www.aer.gov.au/wholesale-markets/wholesale-statistics/quarterly-base-futures-prices-and-volume-traded
and export/adapt by hand).

## After running

Copy (or `git add`/commit) the output directories back into this repo:

```
data/cache/nem/registry/nem_plant_registry.parquet
data/cache/nem/scada/{DUID}_{year}.parquet
data/cache/nem/price/rrp_{REGION}_{year}.parquet
data/cache/nem/hedge/aer_base_futures_{year}.parquet
```

The Streamlit app reads these files read-only via `ppa/data/nem_data.py` and
`ppa/data/aer_futures.py` — it never makes a live network call itself.

## Known unverified details (double-check before relying on these)

- **OpenElectricity API auth header** (`fetch_nem_plant_registry.py`): sends
  `Authorization: Bearer <key>`, the typical convention — confirm against
  https://docs.openelectricity.org.au/api-reference/facilities/get-facilities/.
- **OpenElectricity fueltech taxonomy strings** (default `wind,solar_utility`)
  — verify the exact enum values against the live API docs.
- **OpenElectricity response envelope / pagination / field names** (facility
  vs per-unit records, coordinate field name) — this script tries several
  plausible shapes/keys and logs what it found; confirm against a real
  response.
- **AER CSV download URL** (`fetch_aer_futures.py`, `CSV_URL` constant) — this
  points at the human-readable chart page as a placeholder; find the actual
  CSV export link/endpoint on that page and update the constant.
- **AER CSV column names and quarter-label format** — this script tries
  several plausible header names and a substring-on-year filter; confirm
  against the real downloaded file.

None of these could be confirmed from the sandboxed planning/dev session
(the domains are network-blocked there) — verify them the first time you run
each script in the networked environment, and adjust the small
constants/candidate-lists called out above if reality differs.
