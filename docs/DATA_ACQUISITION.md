# NEM data and acquisition

The Streamlit app reads local files from `data/cache/nem/` and does not make network requests. The committed pilot data is for 2025:

- `availability/` — AEMO 5-minute UIGF availability by DUID, used as the renewable generation upper bound.
- `price/` — 5-minute spot prices for NSW1, QLD1, SA1, TAS1, and VIC1.
- `registry/` — plant metadata and a precomputed eligibility summary for the plant picker.
- `hedge/` — AER quarterly base-futures prices.

These cache files are part of the runnable application. Do not remove or replace them without ensuring the app still has the data it needs. The eligibility summary should be rebuilt after changing registry, availability, or SCADA files.

## Refresh or extend source data

Acquisition tools are standalone and require network access to their upstream sources. Install their dependencies in a separate environment, not in the app runtime:

```bash
python3 -m venv .venv-acquisition
source .venv-acquisition/bin/activate
python -m pip install -r scripts/requirements-acquisition.txt
```

Use the relevant script's `--help` before a run. Main entry points:

- `scripts/fetch_nem_plant_registry.py` — plant registry from Open Electricity; requires `OPENELECTRICITY_API_KEY`.
- `scripts/fetch_nem_availability.py` — AEMO UIGF availability via `nemosis`.
- `scripts/fetch_nem_scada_prices.py` — AEMO SCADA and spot-price series via `nemosis`.
- `scripts/fetch_aer_futures.py` — AER futures price data.
- `scripts/build_nem_eligibility_cache.py` — local summary generated from cached data; no network required.

Keep `nemosis_cache/` outside tracked source. A full-year MMS acquisition can consume substantial disk space. Review coverage, timestamps, DUID selection, and output files before adding data to the repository. Preserve existing verified registry files unless a deliberate refresh is intended.

## Interpretation

UIGF (`DISPATCHLOAD.AVAILABILITY`) represents available output before network constraints. SCADA is actual sent-out output and may include network or contract-driven curtailment. They are different measures and should not be interchanged as model inputs. Cache readers in `ppa/data/` must remain offline; network access belongs only in acquisition scripts.


## AER futures snapshot

The bundled hedge cache uses the AER prices dated **30 June 2026**, from the CSV
published **7 August 2026** and imported on 19 October 2026. The original CSV
and source URLs/checksum are retained in `data/cache/nem/hedge/source/`.
The 2025 cache filename is the historical NEM data lookup year, not the
futures delivery year. The first complete delivery year in this snapshot is 2027.

To reproduce this refresh without network access:

```bash
python3 scripts/fetch_aer_futures.py --year 2025 \
  --input-csv data/cache/nem/hedge/source/aer_base_futures_20260807.csv \
  --as-at 2026-06-30 --overwrite
```

Always supply the price observation/settlement date from the AER source page.
Do not infer that date from the CSV publication timestamp or the download date.
