# Data acquisition tools

These scripts update the cache files consumed by the Streamlit app. They are separate from runtime: the app reads committed local Parquet files and makes no network requests.

Create a separate environment for acquisition:

```bash
python3 -m venv .venv-acquisition
source .venv-acquisition/bin/activate
python -m pip install -r scripts/requirements-acquisition.txt
```

Consult `python scripts/<tool>.py --help` for supported years, output locations, and overwrite behavior. Main tools:

- `fetch_nem_plant_registry.py` downloads plant metadata from Open Electricity and requires `OPENELECTRICITY_API_KEY`.
- `fetch_nem_availability.py` pulls AEMO UIGF data via `nemosis`.
- `fetch_nem_scada_prices.py` pulls AEMO SCADA and regional prices via `nemosis`.
- `fetch_aer_futures.py` downloads AER futures data.
- `build_nem_eligibility_cache.py` and `build_plant_year_manifest.py` derive local summaries from existing cache files.

A full-year AEMO pull can download many gigabytes of raw archives. Use a disk-backed raw cache directory such as the gitignored `nemosis_cache/`, review generated data before committing it, and never overwrite a verified registry unintentionally. For the current cache inventory and input interpretation, see [data acquisition](../docs/DATA_ACQUISITION.md).
