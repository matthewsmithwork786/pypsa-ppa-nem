# pypsa-ppa-nem

A Streamlit app for exploring renewable Power Purchase Agreements in the Australian National Electricity Market (NEM). The dispatch model uses PyPSA and Linopy with HiGHS; financial calculations are reported separately from dispatch.

## Run locally

Use Python 3.13 and install the app plus test dependencies:

```bash
python3.13 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements-dev.txt
streamlit run streamlit_app.py
```

Open <http://localhost:8501>. Run the test suite with:

```bash
MPLCONFIGDIR=/tmp/mplcache python -m pytest -q -p no:cacheprovider
```

You can build a Docker image locally for a consistent runtime:

```bash
docker build -t pypsa-ppa-nem .
docker run --rm -p 8501:8501 pypsa-ppa-nem
```

## Current pilot scope

The default workflow solves one operating year and projects financials over 25 years by repeating that year's energy volumes. It does not solve future dispatch years, weather, degradation, augmentation, or price responses. Treat future delivery and financial outputs as scenario projections, not forecasts. Full multi-year dispatch is available separately and is more demanding.

Bundled generation and spot-price data covers 2020–2025. Choose a **Historical model year** on **Pick Plants** to see plants with a full operating year of UIGF data. An optional year range restricts selection to plants operational in every chosen year. The app reads committed NEM cache files and runs without acquisition credentials or external data access. UIGF represents available output before network constraints, while SCADA represents sent-out output and can include curtailment. Do not substitute SCADA for UIGF as the model's availability bound. Data sources, eligibility checks and acquisition instructions are in [Data acquisition](docs/DATA_ACQUISITION.md).

Sizing defaults to `tsam` typical-week clustering to reduce LP size. Clustering is an approximation and can change the selected fleet, particularly storage. Monthly SLA constraints are not supported with clustered sizing; use full-hourly sizing where calendar-month constraints are required. See [model assumptions](docs/model_assumptions.md) for the load-bearing modelling conventions and [financial assumptions](docs/financial_assumptions.md) for the cost inputs and sources.

In **Set Terms**, the optional **Allocate generation to PPA load first in reporting**
setting defaults to off. When enabled, existing exports first cover unmet PPA load
in the same interval after the solve. Delivery, excess sales, shortfall allowances,
penalties and financial settlement are recalculated consistently. Physical dispatch
and optimised capacities are unchanged, so reported settlement returns can differ
from the original objective. Apply the setting and run the scenario to use it.
Results include an original-versus-allocated interval CSV for comparison. The
separate run-of-plant alternative continues to settle all wind and solar output.

## Project map

- `streamlit_app.py`, `ui/` — Streamlit application and user interface.
- `ppa/` — scenario, data readers, network, sizing, dispatch, and finance models.
- `data/cache/nem/` — bundled NEM availability, price, registry, and hedge data.
- `scripts/` — offline cache builders, data acquisition tools, and focused benchmarks.
- `docs/` — current user and maintainer guidance.
- `tests/` — model, data, and UI regression tests.

For maintainer constraints, read [AGENTS.md](AGENTS.md). Deployment and local container instructions are in [Deployment](docs/DEPLOYMENT.md).
