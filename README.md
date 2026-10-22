# Data centre renewable procurement

This branch provides a five-page Streamlit workflow for one-year procurement modelling:

1. **Introduction** — purpose and calculation approach.
2. **Requirements** — select NSW or AEMO requirements, or **Choose capacity** to enter PV MW, wind MW, BESS MW and BESS MWh yourself.
3. **Plants & tariffs** — choose cached wind/PV profiles using a map or dropdowns, or upload a CSV, then set PPA and storage fees.
4. **Optimisation** — review assumptions and size the portfolio.
5. **Results** — generation, load coverage, storage, requirements achieved and customer cash flows.

The scenario names are user-defined modelling cases, not a certification of current NSW rules or an AEMO proposal.

## Defaults

| Assumption | NSW case | AEMO case |
|---|---:|---:|
| Peak load | 100 MW | 100 MW |
| Load utilisation | 90% | 90% |
| Annual renewable matching | 100% | 100% |
| Wind share of contracted energy | 40% | Optimised |
| Minimum storage power / peak load | 25% | Optimised |
| Storage duration | 4 hours | 4 hours |
| Renewable load coverage, 00:00–06:00 | — | 20% |
| Renewable load coverage, 06:00–12:00 | — | 40% |
| Renewable load coverage, 12:00–18:00 | — | 40% |
| Renewable load coverage, 18:00–24:00 | — | 20% |

PV is A$80/MWh, wind A$130/MWh, and storage A$6,000 per MWh of energy capacity per month. The storage fee is a provisional assumption: a 25 MW / 100 MWh battery costs A$7.2 million annually at this rate.

## Calculation

**Choose capacity** skips sizing and optimises dispatch at the entered capacities, without imposing either scenario's minimum targets. BESS duration is MWh / MW; set both storage inputs to zero to exclude it. PV and wind need only MW because their hourly energy comes from the selected generation profiles.

Every results page reports annual renewable volume matching, wind generation / annual load, BESS power / specified peak load, and time matching for the whole year and each of the four six-hour bands. Time matching is renewable delivery to load divided by load, aggregated using hourly energy rather than averaging percentages; renewable-charged BESS counts when it serves load, and exports do not count. Annual volume matching can exceed 100%. These statistics are included in the results workbook. Wind generation / annual load is a different denominator from the NSW wind share of the renewable portfolio.

`ppa/procurement.py` builds a dedicated PyPSA procurement network, independent of the developer-return model in the main application. It first minimises contracted annual PPA and BESS fees subject to the requirements, then fixes capacity and minimises net spot costs through hourly dispatch. Export revenue does not drive speculative capacity additions. The sizing objective is contracted procurement cost, not the lowest possible final net customer cost.

- Default load is flat at peak MW × utilisation. An uploaded load profile overrides utilisation, must not exceed the specified peak, and is reflected in the scenario summary.
- Annual matching is renewable generation after curtailment divided by annual load. Exports count; storage discharge is not counted again. This is distinct from hourly load coverage.
- PV, wind and BESS power are sized in whole MW. BESS energy follows the selected duration, or the separately entered MWh in Choose capacity.
- NSW wind share splits the annual matching target into minimum wind and PV energy volumes. Whole-MW sizing can slightly change the achieved mix, which is reported separately. Storage power is at least the specified share of peak load.
- AEMO time-band coverage is renewable delivery to load, including renewable-charged storage, summed within each six-hour band over the year. It is not a requirement for every individual day. Band percentages need not sum to 100%.
- Storage has 95% charging and discharging efficiency (90.25% round trip), a cyclic year-end state of charge, renewable-only charging and discharge reserved for customer load. Hourly dispatch prevents simultaneous charging and discharging, including at negative prices.
- PPA fees cover the complete contracted availability profile, including deemed generation when curtailed. Actual curtailed energy does not count towards annual matching or export revenues.
- Customer net cost = PV PPA fee + wind PPA fee + BESS fee + spot shortfall purchases − excess sales revenue.
- The spot-only counterfactual uses the exact same hourly load and spot prices.
- Both costs exclude network charges, retailer margins, financing, taxes and separate certificate purchases. All NEM generation settles at NSW spot prices; regional basis and transmission constraints are excluded.

## Data and uploads

Runtime data access remains offline, using the bundled NEM caches and UIGF availability. Historical input clocks use AEST. Only complete calendar years of consecutive hourly data are accepted; sub-hourly uploads are first averaged to hourly values. Gaps, duplicate timestamps, non-finite values and capacity factors outside 0–1 are rejected.

The downloadable CSV template contains `timestamp`, `ts_PVGen`, `ts_WindGen`, `ts_LoadMW`, and `ts_MktPrice`. PV/wind are capacity factors; load is MW; price is A$/MWh. Replace the illustrative template values with your own data.

## Run locally

```bash
python3 -m pip install -r requirements.txt
python3 -m streamlit run streamlit_app.py
```

The branch keeps the existing cache acquisition tools and previous model modules, while the application entry point uses only the simplified procurement workflow. It does not change the main branch's deployed app.

## Validate

```bash
MPLCONFIGDIR=/tmp/mplcache python3 -m pytest -q -p no:cacheprovider
```

## Results workbook

One Excel download contains **Annual financials** and **Hourly dispatch**. The annual sheet includes a formula-based quantity × rate cost bridge, weighted spot shortfall prices, source-specific weighted excess sale prices, matching statistics and bold subtotals. Financial displays use two decimals; calculations retain full precision. GWh is converted to MWh for energy tariffs; BESS tolling multiplies energy capacity by the monthly rate and 12 months.

Common-bus reporting allocates PV/wind charging, curtailment and excess sales pro rata. BESS discharge is reserved for load, so BESS excess sales are zero. This is a reporting allocation, not a change to dispatch or a separate sale contract. Landed excess prices equal the scenario's volume-weighted NSW spot prices without additional charges. Source-level hourly allocations are included so the sales revenues can be reconciled.
