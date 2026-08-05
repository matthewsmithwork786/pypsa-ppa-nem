# Deploying to Streamlit Community Cloud

## 1. What is already set up

| File | Purpose |
|---|---|
| `streamlit_app.py` | Entry point — set this as the "Main file path" |
| `requirements.txt` | Runtime dependencies **only** |
| `requirements-dev.txt` | Adds `pytest`; not installed on the deployed app |
| `requirements-optional.txt` | `tsam` — **must not** go in `requirements.txt` (see §2) |
| `.python-version` | `3.13` |
| `.streamlit/config.toml` | Upload limit, XSRF, theme |

**No secrets are needed to run the app.** All NEM data is committed under
`data/cache/` and read from disk. The acquisition scripts in `scripts/` need
`OPENELECTRICITY_API_KEY`, but they are run locally and never by the deployed app.

## 2. The dependency conflict — do not undo this

`tsam==3.4.2` requires `highspy<=1.15.0`, while the rest of the stack uses
`highspy==1.15.1`. With both in `requirements.txt`, pip fails outright:

```
ERROR: ResolutionImpossible
```

Streamlit Cloud does a clean `pip install -r requirements.txt`, so this is a hard
deploy failure, not a warning. `tsam` therefore lives in
`requirements-optional.txt`.

The app is unaffected: `sizing_method` defaults to `full_hourly`, the tsam import is
guarded, and selecting "Typical days" without the package produces a clear validation
error rather than a crash. (Clustered sizing also under-sizes storage — see
`docs/sizing_experiments.md` E7/E9 — so it is not a default worth fighting for.)

## 3. Deploy steps

1. <https://share.streamlit.io> → **New app** → **Deploy a public app from GitHub**
2. Repository `matthewsmithwork786/pypsa-ppa-nem`, branch `main`, main file
   `streamlit_app.py`
3. **Advanced settings → Python version: 3.13** (matches `.python-version`)
4. Deploy. The first build is slow — see §4.

## 4. Expect a slow first deploy

The repository carries ~450 MB of committed NEM data caches (SCADA, UIGF
availability, prices, registry). Streamlit Cloud clones the whole repo, so the initial
build takes several minutes. Subsequent restarts reuse the checkout.

The data is committed deliberately: the cloud container cannot run `nemosis`, so
without it the app would have no generation data at all.

## 5. Memory — the real constraint

The capacity-sizing LP is the memory peak of the whole app, and Streamlit Community
Cloud is memory-limited (~1 GB historically; check your current tier). Measured
locally:

| Phase | Peak RSS |
|---|---|
| Baseline after data load | ~350 MB |
| Sizing LP, coarse 3 h | ~780 MB |
| Sizing LP, **full hourly (the default)** | **~1,143 MB** (171 s) |
| Dispatch, 2 forked workers | ~1,770 MB |
| Dispatch, 1 worker (serial) | ~725 MB |

> **On a 1 GB tier the default `full_hourly` sizing LP will not fit.** The app now
> warns before the solve rather than being killed silently — but the practical
> setting for a memory-limited deployment is **Coarse resolution (3 h)** at
> ~780 MB, which is ~8x faster and only about 4.5% off on sized fleet. Capacity
> sizing is the only feature affected; fixed-capacity dispatch runs in ~725 MB.

The app already defends itself:

- `ppa/sizing.py::run_sizing_subprocess` solves the sizing LP in a **child process**, so
  its multi-GB working set is returned to the OS the moment it finishes rather than
  staying resident.
- `ppa/multi_year.py::_safe_worker_count` reads **cgroup** limits, not just host memory,
  and collapses to the in-process serial path on a constrained container.
- `clamp_sizing_years` shortens the sizing horizon to fit available RAM.
- A killed worker pool falls back to serial instead of dying silently.

**If the deployed app still runs out of memory**, in order of preference:

1. Set sizing representation to **Coarse resolution (3 h)** in Case Setup — about 8×
   faster than full hourly and only ~4.5% off on sized fleet.
2. Reduce **simulation years** — the dispatch phase solves every year.
3. Raise `PPA_WORKER_MEM_MB` (default `1200`) so fewer workers are started; setting it
   above the container limit forces the serial path.
4. Reduce the **max build** caps, which shrinks the LP.

Environment variables are set under **App settings → Advanced → Environment variables**.

## 6. Sanity checks after deploying

Walk `docs/UAT_checklist.md`. The quickest smoke test:

1. **Get Data** tab renders the plant map and markers show CUF% and first-power date.
2. **Case Setup** → pick a case study → **Optimisation** → run a **1-year** simulation
   with capacity sizing **off**. This exercises the whole pipeline cheaply.
3. Only then try capacity sizing, and watch the memory notice in the sizing status line.
4. **Financial Model** → export XLSX → confirm Excel opens it with no repair prompt.

## 7. Local development

```bash
pip install -r requirements-dev.txt
MPLCONFIGDIR=$TMPDIR python3 -m pytest -q -p no:cacheprovider   # 269 tests
streamlit run streamlit_app.py
```

See `AGENTS.md` for environment quirks and repo conventions.

## 8. Deploying to Google Cloud Run via Cloud Build

Same repo, containerised instead of Streamlit Cloud. The repo ships:

| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage build (Python 3.13, matches `.python-version`); installs `requirements.txt` into a venv, runs as non-root, binds `PORT` (default 8501) |
| `.dockerignore` | Keeps `.git`, dev envs, notebooks, tests and docs out of the image |
| `cloudbuild.yaml` | Cloud Build pipeline: build → push to Artifact Registry → deploy to Cloud Run |

### One-time setup

```bash
# 1. Enable the APIs
gcloud services enable run.googleapis.com artifactregistry.googleapis.com

# 2. Create the Artifact Registry repo (adjust region if needed)
gcloud artifacts repositories create pypsa-ppa-nem \
    --repository-format=docker --location=us-central1
```

### Deploy

```bash
# From the repo root
gcloud builds submit --config cloudbuild.yaml
```

This builds `us-central1-docker.pkg.dev/$PROJECT/pypsa-ppa-nem/pypsa-ppa-explorer:<sha>`,
pushes it, and creates/updates a Cloud Run service `pypsa-ppa-explorer` in
`us-central1` with `--allow-unauthenticated` (public URL).

To deploy on every push to `main`, add a **Cloud Build trigger** (Cloud Console →
Cloud Build → Triggers → Create trigger): event *Push to a branch*, branch
`^main$`, config *Cloud Build configuration file*, location *Repository*,
`cloudbuild.yaml`.

### Configuration notes

- **Memory:** `--memory=4Gi` — the default full-hourly sizing LP peaks ~1.1 GB
  and a 2-worker dispatch ~1.7 GB (see §5). On a smaller tier, set the sizing
  representation to **Coarse (3 h)** in the UI, or lower `PPA_WORKER_MEM_MB`.
  `PPA_WORKER_MEM_MB` / `PPA_RESERVE_MEM_MB` are set as container env vars.
- **Port:** Cloud Run injects `PORT`; `cloudbuild.yaml` deploys with
  `--port=8501` and the Dockerfile binds `${PORT:-8501}`.
- **Public data:** all NEM data caches are committed under `data/cache/` and
  read from disk — no secrets or external APIs needed at runtime. The
  `OPENELECTRICITY_API_KEY` is only used by the local acquisition scripts in
  `scripts/`.

### Local container check

```bash
docker build -t pypsa-ppa-explorer .
docker run --rm -p 8501:8501 pypsa-ppa-explorer
# open http://localhost:8501
```
