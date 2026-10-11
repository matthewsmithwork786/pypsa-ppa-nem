# Financial assumptions

`ppa/assumptions.py` is the shared source for values used by the scenario sizing model and project-finance model. Some standalone project-finance defaults intentionally remain distinct from the GenCost inputs used by the scenario flow; do not silently harmonise them.

| Input | Current assumption | Source or status |
|---|---:|---|
| Wind capex | A$3,248/kW | GenCost 2025–26 Final Report, Appendix Table B.1, 2025 current-policies case |
| Utility solar capex | A$1,621/kW | GenCost 2025–26, Appendix Table B.1 |
| Four-hour BESS capex | A$385/kWh | GenCost 2025–26, Appendix Table B.5 |
| Wind fixed O&M | A$28,512/MW/year | Gohdes (2026), Table 2 |
| Solar fixed O&M | A$12,000/MW/year | GenCost 2025–26, Table B.9 |
| BESS fixed O&M | A$10,500/MWh/year | Legacy input; not independently verified against a published source |
| Maintenance capex | 0.05% of capex/year | Gohdes (2026), Table 2 |
| Connection cost | A$150,000/MW | Model assumption; charged separately from overnight generation capex |
| Development cost | 10% of capex | Model assumption |
| Discount rate / project life | 8% / 30 years | Shared model assumptions |
| Debt rate / tenor | 5.75% / 15 years | Blended refinancing proxy, not a direct replication of Gohdes's 5-year debt schedule |
| LGC price | A$5/MWh | Inherited model assumption, not a current market quote |

The project-finance model's standalone legacy build costs are Wind A$2.9m/MW, PV A$1.7186m/MW, and BESS A$0.2765m/MWh. In the normal scenario-driven flow, values are seeded from the scenario instead. Review `ppa/assumptions.py` docstrings and `ppa/financial_model.py` before changing these inputs.

Reference: Gohdes, N. (2026), “Alternative Contract Design: Analysing the Bankability of Fungible Derivative Contracts in Energy-Only Markets,” *Australian Journal of Agricultural and Resource Economics*, doi:10.1111/1467-8489.70149. Values are stated as aligned to the AEMO 2025–26 Inputs, Assumptions and Scenarios Report. Verify source tables and real-dollar basis before extending the model to new technologies or cost years.
