# Run and deploy the Streamlit app

## Local run

Use Python 3.13. From the repository root:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
streamlit run streamlit_app.py
```

The app listens at <http://localhost:8501>. Runtime data is included under `data/cache/nem/`; the app does not need acquisition credentials or an external data service.

## Streamlit Community Cloud

Create or update the app from the GitHub repository with these settings:

| Setting | Value |
|---|---|
| Repository | `matthewsmithwork786/pypsa-ppa-nem` |
| Branch | `main` |
| Main file | `streamlit_app.py` |
| Python version | `3.13` |

The app installs dependencies from `requirements.txt` and uses the committed NEM cache. No app secrets are required for the bundled 2025 data. After the repository transfers to OET, configure Streamlit Community Cloud to use the OET-owned repository, select the `main` branch, and set `streamlit_app.py` as the main file.

## Docker

The Dockerfile builds an image locally and copies the bundled cache into it:

```bash
docker build -t pypsa-ppa-nem .
docker run --rm -p 8501:8501 pypsa-ppa-nem
```

Set `PORT` to change the container listening port; the default is 8501. Memory needs depend on dispatch years and sizing method. Typical-period sizing is the lower-memory default; full-hourly sizing and parallel multi-year runs can require substantially more memory. See [model assumptions](model_assumptions.md).

## Tests

```bash
MPLCONFIGDIR=/tmp/mplcache python3 -m pytest -q -p no:cacheprovider
```

The GitHub Actions workflow runs this suite on pushes and pull requests. For data-source setup and cache refresh procedures, see [data acquisition](DATA_ACQUISITION.md).
