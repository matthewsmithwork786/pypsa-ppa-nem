# Deploying to Streamlit Community Cloud

## 1. What is already set up

| File | Purpose |
|---|---|
| `streamlit_app.py` | Entry point — set this as the "Main file path" |
| `requirements.txt` | Runtime dependencies **only** — includes `tsam==3.4.2` (see §2) |
| `requirements-dev.txt` | Adds `pytest`; not installed on the deployed app |
| `requirements-optional.txt` | Legacy — `tsam` now ships in `requirements.txt` |
| `.python-version` | `3.13` |
| `.streamlit/config.toml` | Upload limit, XSRF, theme |

**No secrets are needed to run the app.** All NEM data is committed under
`data/cache/` and read from disk. The acquisition scripts in `scripts/` need
`OPENELECTRICITY_API_KEY`, but they are run locally and never by the deployed app.

## 2. tsam and the highspy pin — keep them together

`tsam==3.4.2` requires `highspy<=1.15.0`, while newer highspy releases are
otherwise fine. `requirements.txt` pins `highspy==1.15.0` **and** ships
`tsam==3.4.2` so the two resolve together. Bump highspy past 1.15.0 and pip
fails outright:

```
ERROR: ResolutionImpossible
```

tsam is not a deploy-time extra: it is the **default** sizing representation
(`Scenario.sizing_method="tsam"`, 16 typical weeks — the app's radio default
is "Typical weeks (tsam)"). It is what keeps the capacity-sizing LP small
enough to fit a container (~400 MB vs ~1.1 GB for the exact hourly year)
while staying more accurate than legacy coarse 3 h block-averaging. Keep both
pins.

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
| Sizing LP, **tsam typical weeks (the default)** | **~400 MB** (18 s) |
| Sizing LP, coarse 3 h (legacy) | ~780 MB |
| Sizing LP, full hourly (exact) | ~1,143 MB (171 s) |
| Dispatch, 2 forked workers | ~1,770 MB |
| Dispatch, 1 worker (serial) | ~725 MB |

> **The default tsam sizing LP (~400 MB) fits any tier, so coarse is no longer
> the memory fallback.** tsam is both lighter *and* more accurate than coarse:
> typical weeks keep hourly intra-day resolution, which coarse's 3 h
> block-averages smooth away (the shape storage sizing depends on). Only the
> exact full-hourly year (~1.1 GB) needs a multi-GB tier — pick it if you want
> the exact answer and have the memory, otherwise leave the default.

The app already defends itself:

- `ppa/sizing.py::run_sizing_subprocess` solves the sizing LP in a **child process**, so
  its multi-GB working set is returned to the OS the moment it finishes rather than
  staying resident.
- `ppa/multi_year.py::_safe_worker_count` reads **cgroup** limits, not just host memory,
  and collapses to the in-process serial path on a constrained container.
- `clamp_sizing_years` shortens the sizing horizon to fit available RAM.
- A killed worker pool falls back to serial instead of dying silently.

**If the deployed app still runs out of memory**, in order of preference:

1. Keep the default **Typical weeks (tsam)** — ~400 MB and more accurate than
   coarse. Only the exact **full-hourly** year (~1.1 GB) is too big for a 1 GB
   tier.
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

- **Memory:** `--memory=4Gi` — with the default **tsam typical-weeks** sizing
  the sizing LP peaks ~400 MB, and a 2-worker dispatch ~1.7 GB (see §5). Even
  the exact full-hourly sizing (~1.1 GB) fits this allocation, so leave the
  default. On a smaller tier lower `PPA_WORKER_MEM_MB` or reduce simulation
  years. `PPA_WORKER_MEM_MB` / `PPA_RESERVE_MEM_MB` are set as container env
  vars.
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
