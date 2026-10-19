# Model assumptions and limitations

This note records the conventions that materially affect results. The model code and tests remain authoritative.

## Dispatch and availability

The dispatch model uses an hourly representation of NEM data. AEMO UIGF availability is the preferred generation upper bound: it estimates what a semi-scheduled plant could produce before network constraints. SCADA records sent-out generation and may already reflect network or economic curtailment. Using SCADA as availability can therefore count curtailment twice. Availability is bounded by registered capacity; solar AC clipping around 0.80 of nameplate is expected.

The bundled dataset covers 2025. Plant selection and historical-year claims must reflect the files actually present in `data/cache/nem/`, not future-year capabilities described in old plans. Acquisition and cache-generation procedures are in [DATA_ACQUISITION.md](DATA_ACQUISITION.md).

## Representative periods and constraints

PyPSA snapshot weightings have distinct meanings. `objective` and `generators` scale operating cost and energy totals to the represented horizon. `stores` is the duration of each step in the storage energy balance. For hourly typical-period representatives, it remains one hour; multiplying it by occurrence count gives storage an incorrect multi-hour time step.

Custom energy constraints must use the relevant snapshot weights. Without them, constraints can appear correct on a uniformly weighted full year and fail on clustered snapshots.

`tsam` clustering preserves typical hourly shapes but not the original calendar. Monthly minimum-delivery constraints cannot be evaluated on those snapshots and are blocked for clustered capacity sizing. Daily constraints are grouped by representative-period labels and are an approximation of real calendar days. Clustered sizing can change the sized fleet, especially storage; use full-hourly sizing when the exact hourly representation is required and resources permit.

## Contract and finance interpretation

PPA delivery is energy sent through the offtake link. Allowed shortfall and penalty generation meet the bus balance but bypass the delivery link. A penalty price is an economic signal; it does not enforce a minimum delivery share. The hard minimum is conditional on the scenario's `enforce_min_delivery` setting.

Fast financial mode repeats one solved dispatch year's energy volumes over the requested projection horizon. It does not model future weather, dispatch responses, degradation, or battery augmentation. Projected delivery and cash flow are conditional scenario outputs, not independently solved future-year results. The detailed financial model may use different assumptions; consult [financial assumptions](financial_assumptions.md).


## Offtaker run-of-plant comparison

Detailed Results includes a selected-year settlement view for wind and solar
run-of-plant offtakes. All solved wind and solar production is paid at separate
user-entered prices (defaulting to that year's delivered PPA price). For each
interval, generation is matched to actual load; the remaining shortfall is bought
at the modelled spot price and surplus is sold at that price. Net procurement
cost is wind payments + solar payments + shortfall purchases - surplus sales.
Negative spot prices are retained, so surplus sales can increase cost. Energy
and costs use the dispatch interval duration, including sub-hourly runs.

This calculation reuses solved sent-out generation, including existing
curtailment. It is a settlement comparison, not a new dispatch optimisation for
a different contract. Battery dispatch and charges are excluded; generation is
allocated directly to load before spot settlement. No payment is assumed for
available-but-curtailed energy. One common spot price is used for generation and
load. Regional basis, losses, network charges, retailer/sleeving fees,
certificates, tax, and contract-specific curtailment and negative-price clauses
are excluded. The result covers the selected solved year, not a lifetime
projection. Annual generation / load and interval matching are distinct metrics.
