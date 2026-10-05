# Free Streamlit testing: one dispatch year, 25 financial years

The deployment branch is `deploy/streamlit-fast-finance`, based on
`feature/multiyear-plants-and-tiered-sla` (`f816722`). The app defaults to
**Fast: solve one year, project financials**. The projection horizon defaults to
25 years; capacity sizing is off; hourly resolution and serial dispatch are the
starting configuration. A shared solve slot permits one optimisation per app
process, so concurrent testers do not launch overlapping native solvers.

## What fast mode means

PyPSA solves one full operating year. Results retain one hourly dataset. The
financial summary builds small annual records for the requested horizon, and
the project-finance engine repeats the representative energy volumes using its
editable tariff, merchant-price and cost indexation, construction, debt, tax,
PPA tenor and operating-life assumptions. The project-finance operating life is
seeded from the requested projection horizon.

This is a fixed-volume approximation: generation, battery dispatch, delivery,
shortfalls and merchant quantities are repeated. Later-year degradation,
different weather, battery augmentation and dispatch responses to changing
prices are not simulated. Degradation inputs are disabled in fast mode. Annual
delivery charts are projections, not independently solved future years. Only
the solved year is included in the hourly download.

**Full: solve every operating year** retains the original multi-year dispatch,
degradation and historical-year cycling. That mode can be slow and memory hungry
on free hosting. Capacity sizing is a separate solve in either mode, using the
selected capacity-sizing year. Typical-week sizing retains the existing 40-week
default; this patch does not silently reduce its accuracy to make it faster.

The committed input data covers **2025**, with UIGF profiles and all five NEM
regional price series. No external data credentials or Zenodo publication are
needed for this pilot. There are no additional historical years in the current
release. Selecting a plant does not establish a new-build yield or physical
renewable provenance.

## Put the branch on GitHub

If the branch is already pushed, skip to the next section. If you received a
`streamlit-fast-finance.patch`, download it and apply it in your existing clone:

```bash
git fetch origin
git switch -c deploy/streamlit-fast-finance origin/feature/multiyear-plants-and-tiered-sla
git am /path/to/streamlit-fast-finance.patch
git push -u origin deploy/streamlit-fast-finance
```

If that local branch already exists, choose another branch name and use that
same name in Streamlit. Do not apply the patch to an older July branch: it is
based on the August multi-year branch. This does not require merging to main.

## Deploy on Streamlit Community Cloud

Open https://share.streamlit.io and sign in with the GitHub account that has
access to this repository. Choose **Create app → Yup, I have an app**.

| Setting | Value |
|---|---|
| Repository | `matthewsmithwork786/pypsa-ppa-nem` |
| Branch | `deploy/streamlit-fast-finance` |
| Main file path | `streamlit_app.py` |
| Advanced settings / Python version | **3.13** |
| Secrets | Leave empty for the committed-data pilot |
| App URL | Choose an available subdomain, such as `pypsa-ppa-nem-test` |

Click **Deploy**. The existing root `requirements.txt` supplies the pinned
dependencies, including highspy/HiGHS and tsam; a commercial solver licence is
not required. `.streamlit/config.toml` is already present. Community Cloud clones
the GitHub branch, so the patch must be pushed before deploying.

Community Cloud is free, but resource allocations are limited and may change.
The app may sleep after inactivity. Avoid concurrent runs or full multi-year
5-minute runs during the first test. Check **Manage app** logs if a build fails
or the process runs out of memory. `/tmp` run storage survives page refreshes
within the running container, not hosting restarts, so download outputs you want
to keep.

## First test

1. **Pick Plants:** select a simulation-ready wind and solar plant; use 2025,
   60-minute snapshots and a price region. Click **Use these plants**.
2. **Set Terms:** keep **Fast** mode, **25 financial projection years**, capacity
   sizing off and market buying off. Set fixed MW, load and PPA terms, then
   **Apply changes**.
3. **Run:** click **Run Optimisation**. The status should say one dispatch year
   solved and 25 projected financial years prepared.
4. **Results:** check dispatch/delivery in the solved year. Open **Financial
   model**, confirm operating life/PPA tenor/indexation and click **Run financial
   model**. Download the financial workbook and the separate hourly workbook.
5. Edit the PPA tariff, apply and rerun. Refresh after a completed run and confirm
   it restores; applying new terms should invalidate the old restore link.
6. Enable capacity sizing for a separate test. Try monthly/daily SLAs deliberately:
   hard floors apply to opt-in sizing, and subsequent economic dispatch may pay
   penalties; read the delivery verification warnings.

Market buying, if enabled, now includes a certificate at the assumed LGC price
as well as the bid-offer spread, matching the bundled PPA. Sales receive the
LGC credit. This removes the previous free-certificate trading incentive, but
does not physically trace renewable energy. Project finance now deducts market
purchases and transmission costs and correctly integrates subhourly quantities.
Battery discharge is no longer counted a second time as new generation.

The simplified financial summary and the detailed project-finance model are
different appraisals: the summary retains the dispatch PPA tariff and escalates
spot values; detailed finance allows contract tenor, indexed tariffs/costs,
post-contract merchant sales, debt and tax. Compare assumptions before comparing
their returns. Post-contract merchant revenues reuse representative capture
prices; they are not a separately optimised post-PPA strategy.

## Local verification

```bash
python3 -m venv .venv
.venv/bin/python3 -m pip install -r requirements-dev.txt
.venv/bin/python3 -m pytest -q -p no:cacheprovider
.venv/bin/python3 -m streamlit run streamlit_app.py
```

Use Python 3.13 to match the deployed pins. Verification results for this patch
are recorded in `docs/DEPLOYMENT_PATCH_STATUS.md`.

Official hosting instructions:
https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy

Resource guidance:
https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app
