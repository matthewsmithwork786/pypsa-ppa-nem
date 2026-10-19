from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from ppa import assumptions as A
from ppa.industrial_profiles import PROFILE_KEYS

# "nem_map" (plants selected on the map), "nem_default" (prices/DUIDs only),
# or "custom_csv".
DATA_SOURCES = ("nem_map", "nem_default", "custom_csv")


@dataclass
class Scenario:
    name: str = "Custom Scenario"

    # Feature toggles
    include_bess: bool = True
    # Market purchases are opt-in.
    enable_market_buy: bool = False
    enable_market_sell: bool = True
    enable_shortfall: bool = True
    enable_penalty: bool = True
    run_financial_analysis: bool = True
    # Compare with the offtaker's alternative supply costs.
    enable_counterfactual: bool = False

    # Counterfactual sourcing
    cal_forward_price: float = 85.0
    cal_hedge_fraction: float = 0.80
    # Price source and its user-facing note.
    cal_forward_source: str = "manual"   # "manual" | "aer_indicative"
    cal_forward_note: str = ""

    # Capacity co-optimisation ignores fixed MW values below.
    optimise_capacity: bool = False
    max_build_wind_mw: float = 1000.0
    max_build_pv_mw: float = 1000.0
    max_build_bess_mw: float = 1000.0
    # Sizing uses representative weeks or the exact hourly series. Dispatch
    # remains hourly in either case.
    sizing_method: str = "tsam"
    # Number of representative weeks (168 h each).
    sizing_n_periods: int = 40
    # LGC revenue on surplus exports only; delivered certificates are bundled
    # into the PPA tariff. A$/MWh.
    lgc_price_aud_mwh: float = A.LGC_PRICE_AUD_MWH
    # Enforce the delivery target as a hard sizing constraint. Build caps can
    # make the LP infeasible when the target is unreachable.
    enforce_min_delivery: bool = False
    # Fraction of positive spot price credited to merchant exports in sizing.
    sizing_merchant_value_share: float = 0.5
    # Maximum link capacity in sizing mode; infinity means unlimited.
    grid_connection_max_mw: float = float("inf")
    # Sized connection capacities; None derives capacity from nameplate.
    wind_link_mw: "float | None" = None
    pvbess_link_mw: "float | None" = None
    sell_link_mw: "float | None" = None

    # Portfolio sizing
    onsw_mw: float = 250.0
    pv_mw: float = 150.0
    bess_mw: float = 60.0
    bess_mwh: float = 240.0
    bess_efficiency_store: float = 0.90
    bess_efficiency_dispatch: float = 0.90

    # PPA contract terms
    ppaload_mw: float = 100.0
    load_profile: str = "flat"  # key into ppa.industrial_profiles.PROFILE_INFO
    ppa_price: float = 100.0
    pen_mult: float = 1.5
    required_delivery_share: float = 0.75
    market_buy_share: float = 0.0
    market_spread: float = 0.10

    # Operational (single-day mode)
    chosen_day: str = "2025-03-15"

    # Multi-year simulation
    simulation_years: int = 25
    # Representative-year dispatch projects one year's volumes over the
    # financial horizon. Full multi-year dispatch is opt-in.
    dispatch_mode: str = "representative_year"
    first_sim_year: int = 2025
    price_escalation_rate: float = A.PRICE_ESCALATION_RATE  # annual escalation applied to base market prices

    # Annual compound technology degradation rates.
    pv_degradation_rate: float = 0.005    # 0.5%/yr — industry standard for crystalline Si
    wind_degradation_rate: float = 0.002  # 0.2%/yr
    bess_degradation_rate: float = 0.020  # 2.0%/yr usable capacity fade

    # Transmission and grid-use charge on every delivered MWh (A$/MWh).
    transmission_cost_aud_mwh: float = 0.0

    # ── Data source ──────────────────────────────────────────────────────────
    # "nem_map" (plants selected on the map), "nem_default" (prices/DUIDs only),
    # or "custom_csv".
    data_source: str = "nem_default"
    nem_price_region: str = "NSW1"
    nem_pv_duid: str = ""
    nem_wind_duid: str = ""

    # ── NEM data selection ───────────────────────────────────────────────────
    # Historical input years, cycled in ascending order for multi-year dispatch.
    nem_years: tuple[int, ...] = (2025,)
    # Historical year used by the capacity-sizing LP.
    capacity_sizing_year: int = 2025
    # Snapshot resolution in minutes: 60, 30, 15 or 5.
    nem_resolution_minutes: int = 60

    # ── Tiered SLA ───────────────────────────────────────────────────────────
    # Optional monthly and daily delivery targets.
    sla_monthly_enabled: bool = False
    sla_monthly_share: float = 0.0
    sla_daily_enabled: bool = False
    sla_daily_share: float = 0.0

    # Financial assumptions in Australian dollars.
    wind_capex_per_kw: float = A.WIND_CAPEX_AUD_KW   # A$/kW, GenCost 2025-26 T.B1 "Wind" 2025
    pv_capex_per_kw: float = A.PV_CAPEX_AUD_KW     # A$/kW, GenCost 2025-26 T.B1 "Large scale solar PV" 2025
    bess_capex_per_kwh: float = A.BESS_CAPEX_AUD_KWH   # A$/kWh, GenCost 2025-26 T.B5 4-hour total (battery 265 + BOP 120)
    devex_pct_of_capex: float = A.DEVEX_PCT_OF_CAPEX    # development cost as a share of build capex
    project_life_yrs: int = A.PROJECT_LIFE_YRS
    discount_rate: float = A.DISCOUNT_RATE
    target_irr: float = A.TARGET_IRR
    # Grid-connection capital cost for sizing links (A$/MW).
    connection_cost_aud_mw: float = A.CONNECTION_COST_AUD_MW

    # ── Derived properties ─────────────────────────────────────────────────────

    @property
    def dispatch_years(self) -> int:
        """Years actually solved; the remaining horizon is a projection."""
        return 1 if self.dispatch_mode == "representative_year" else self.simulation_years

    @property
    def is_nem(self) -> bool:
        return self.data_source in ("nem_map", "nem_default")

    @property
    def nem_year(self) -> int:
        """First selected NEM data year, or 2025 when none is selected."""
        return int(self.nem_years[0]) if self.nem_years else 2025

    @property
    def bess_max_hours(self) -> float:
        if self.include_bess and self.bess_mw > 0:
            return self.bess_mwh / self.bess_mw
        return 4.0

    @property
    def allowed_shortfall_share(self) -> float:
        return (1.0 - self.required_delivery_share) if self.enable_shortfall else 0.0

    @property
    def effective_bess_mw(self) -> float:
        return self.bess_mw if self.include_bess else 0.0

    @property
    def effective_bess_mwh(self) -> float:
        return self.bess_mwh if self.include_bess else 0.0

    @property
    def maxbuy_mw(self) -> float:
        return self.ppaload_mw if self.enable_market_buy else 0.0

    @property
    def maxsell_mw(self) -> float:
        return (self.onsw_mw + self.pv_mw + self.effective_bess_mw) if self.enable_market_sell else 0.0

    @property
    def crf(self) -> float:
        """Capital recovery factor: annualizes overnight CAPEX over the project life."""
        r, n = self.discount_rate, self.project_life_yrs
        return r / (1 - (1 + r) ** -n) if r > 0 else 1.0 / n

    @property
    def penalty_price(self) -> float:
        return self.ppa_price * self.pen_mult if self.enable_penalty else 0.0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


BASE_SCENARIO = Scenario()


@dataclass
class CaseStudy:
    id: str
    name: str
    subtitle: str
    storyline: str
    question: str
    icon: str
    overrides: dict = field(default_factory=dict)


CASE_STUDIES: list[CaseStudy] = [
    CaseStudy(
        id="foundation_deal",
        name="The Foundation Deal",
        subtitle="Cement plant offtaker, wind-dominant, no storage",
        icon="⚓",
        storyline=(
            "A first-mover IPP signs a 10-year PPA with a cement plant at A$90/MWh. "
            "The portfolio is wind-dominant with no storage, and shortfalls cannot be covered from the "
            "market — a clean baseline to understand penalty exposure. The cement load (real measured "
            "sector data) runs steadily on weekdays but drops sharply over the weekend."
        ),
        question=(
            "Can a wind-dominant portfolio with no storage hit a 70% delivery obligation against this "
            "weekday-heavy industrial demand in the NEM?"
        ),
        overrides={
            "name": "The Foundation Deal",
            "simulation_years": 10,
            "onsw_mw": 300.0,
            "pv_mw": 80.0,
            "bess_mw": 0.0,
            "bess_mwh": 0.0,
            "include_bess": False,
            "enable_market_buy": False,
            "enable_market_sell": True,
            "ppa_price": 90.0,
            "required_delivery_share": 0.70,
            "market_buy_share": 0.0,
            "pen_mult": 1.5,
            "load_profile": "cement_plant",
            "nem_wind_duid": "COLWF01",
            "nem_pv_duid": "SUNRSF1",
        },
    ),
    CaseStudy(
        id="solar_bess",
        name="Solar + Storage Play",
        subtitle="Green H₂ electrolyser offtaker, PV-heavy with 4h BESS",
        icon="☀️",
        storyline=(
            "A developer pairs a large PV array with a 4-hour BESS serving a green hydrogen electrolyser. "
            "The electrolyser's flexible demand naturally aligns with solar generation — ramping up at midday "
            "and backing off during evening grid peaks — making it an ideal PPA offtaker for a solar-heavy portfolio. "
            "Market purchases are disabled to maintain renewable additionality. "
        ),
        question=(
            "Does the demand flexibility of the electrolyser help or hinder delivery obligations compared to flat load?"
        ),
        overrides={
            "name": "Solar + Storage Play",
            "onsw_mw": 80.0,
            "pv_mw": 450.0,
            "bess_mw": 50.0,
            "bess_mwh": 200.0,
            "include_bess": True,
            "enable_market_buy": False,
            "enable_market_sell": True,
            "ppa_price": 105.0,
            "required_delivery_share": 0.75,
            "market_buy_share": 0.0,
            "load_profile": "green_hydrogen",
            "nem_wind_duid": "COLWF01",
            "nem_pv_duid": "SUNRSF1",
        },
    ),
    CaseStudy(
        id="merchant_hybrid",
        name="Merchant Hybrid",
        subtitle="Steel EAF offtaker, high obligation, 2× penalty",
        icon="📈",
        storyline=(
            "An aggressive IPP structure serves a steel plant running near-baseload (~97% load factor, "
            "real measured sector data) with a 90% delivery obligation and a strict 2× penalty. "
            "A generous 15% market buy allowance provides an escape valve — but at a punishing 50% "
            "bid-offer spread, every purchased MWh comes at a steep premium. "
        ),
        question=(
            "When the portfolio falls short, is buying through the wide market spread cheaper than "
            "paying the 2× penalty — and can BESS reduce the need for either?"
        ),
        overrides={
            "name": "Merchant Hybrid",
            "onsw_mw": 250.0,
            "pv_mw": 200.0,
            "bess_mw": 60.0,
            "bess_mwh": 240.0,
            "include_bess": True,
            "enable_market_buy": True,
            "enable_market_sell": True,
            "ppa_price": 95.0,
            "required_delivery_share": 0.90,
            "market_buy_share": 0.15,
            "pen_mult": 2.0,
            "market_spread": 0.50,
            "load_profile": "steel_eaf",
            "nem_wind_duid": "COLWF01",
            "nem_pv_duid": "SUNRSF1",
        },
    ),
    CaseStudy(
        id="corporate_ppa",
        name="Corporate PPA",
        subtitle="Data-centre offtaker, premium price, near-zero market buy",
        icon="🏢",
        storyline=(
            "An Australian corporation signs a 15-year PPA for its data-centre fleet at A$105/MWh. "
            "The data-centre load is near-flat with a modest business-hours peak — a demanding obligation "
            "for an RE portfolio. Market supplementation is capped at 1% to preserve additionality claims. "
        ),
        question=(
            "Can a balanced wind + solar + BESS portfolio hit a 90% SLA against a near-constant high load "
            "with almost no market flexibility?"
        ),
        overrides={
            "name": "Corporate PPA",
            "simulation_years": 15,
            "onsw_mw": 280.0,
            "pv_mw": 200.0,
            "bess_mw": 90.0,
            "bess_mwh": 360.0,
            "include_bess": True,
            "enable_market_buy": True,
            "enable_market_sell": True,
            "ppa_price": 105.0,
            "required_delivery_share": 0.90,
            "market_buy_share": 0.01,
            "pen_mult": 1.2,
            "load_profile": "data_center",
            "nem_wind_duid": "COLWF01",
            "nem_pv_duid": "SUNRSF1",
        },
    ),
]

CASE_STUDIES_BY_ID: dict[str, CaseStudy] = {cs.id: cs for cs in CASE_STUDIES}


def load_case_study(cs: CaseStudy) -> Scenario:
    return dataclasses.replace(BASE_SCENARIO, **cs.overrides)


def validate_scenario(s: Scenario, available_days: list[str] | None = None) -> list[str]:
    errors: list[str] = []
    if s.dispatch_mode not in ("representative_year", "full_multi_year"):
        errors.append("Choose representative-year or full multi-year dispatch.")
    if not 1 <= s.simulation_years <= 40:
        errors.append("The projection/simulation horizon must be between 1 and 40 years.")
    if s.optimise_capacity:
        # Fixed MW inputs are ignored; only the build caps matter.
        if s.max_build_wind_mw < 0 or s.max_build_pv_mw < 0 or s.max_build_bess_mw < 0:
            errors.append("Max build capacities must be ≥ 0 MW.")
        if s.max_build_wind_mw == 0 and s.max_build_pv_mw == 0:
            errors.append("At least one of wind/solar max build must be > 0 MW.")
        if s.sizing_method not in ("tsam", "full_hourly"):
            errors.append(
                "Unknown sizing method. Valid options: tsam, full_hourly."
            )
        if s.sizing_method == "tsam" and not (4 <= s.sizing_n_periods <= 40):
            errors.append("Typical-period count must be between 4 and 40.")
        if s.sizing_method == "tsam":
            from ppa.sizing_tsam import tsam_available

            if not tsam_available():
                errors.append(
                    "Typical-days sizing needs the optional 'tsam' package. "
                    "Install it (`pip install tsam`) or "
                    "switch the sizing method to 'Full year hourly'."
                )
        if s.grid_connection_max_mw is not None and s.grid_connection_max_mw < 0:
            errors.append("Grid connection limit must be ≥ 0 MW (blank = unlimited).")
        if not (0.0 <= s.sizing_merchant_value_share <= 1.0):
            errors.append("Merchant value share must be between 0 and 1.")
        if s.enforce_min_delivery and not (0.0 < s.required_delivery_share <= 1.0):
            errors.append(
                "A hard minimum-delivery constraint needs a required delivery share "
                "in (0, 1]."
            )
    if s.lgc_price_aud_mwh < 0:
        errors.append("LGC price must be ≥ 0 A$/MWh.")
    if s.onsw_mw < 0:
        errors.append("Onshore wind capacity must be ≥ 0 MW.")
    if s.pv_mw < 0:
        errors.append("Solar PV capacity must be ≥ 0 MW.")
    if s.include_bess and s.bess_mw <= 0:
        errors.append("BESS power capacity must be > 0 when BESS is enabled.")
    if s.include_bess and s.bess_mwh <= 0:
        errors.append("BESS energy capacity must be > 0 when BESS is enabled.")
    if s.ppaload_mw <= 0:
        errors.append("PPA offtake load must be > 0 MW.")
    if s.ppa_price <= 0:
        errors.append("PPA price must be > 0 $/MWh.")
    if not (0.0 < s.required_delivery_share <= 1.0):
        errors.append("Required delivery share must be between 0 and 1.")
    if not s.optimise_capacity and s.onsw_mw == 0 and s.pv_mw == 0:
        errors.append("At least one generation asset (wind or solar) must have capacity > 0.")
    if s.load_profile not in PROFILE_KEYS:
        errors.append(f"Unknown load profile '{s.load_profile}'. Valid options: {PROFILE_KEYS}")
    if s.transmission_cost_aud_mwh < 0:
        errors.append("Transmission cost must be ≥ 0 A$/MWh.")
    if s.devex_pct_of_capex < 0:
        errors.append("Devex (% of CAPEX) must be ≥ 0.")
    if s.data_source not in DATA_SOURCES:
        errors.append(f"Unknown data source '{s.data_source}'. Valid options: {list(DATA_SOURCES)}")
    if s.is_nem:
        from ppa.data.nem_data import NEM_REGIONS

        if s.nem_price_region not in NEM_REGIONS:
            errors.append(f"Unknown NEM region '{s.nem_price_region}'. Valid options: {NEM_REGIONS}")
        if s.data_source in ("nem_map", "nem_default") and not (s.nem_pv_duid or s.nem_wind_duid):
            errors.append(
                "No NEM plant selected -- pick a wind and/or solar plant on the Get Data "
                "tab. (This applies to 'nem_default' too: without a DUID there is no NEM "
                "generation data to read and the simulation would silently run with zero "
                "renewable output.)"
            )
        if not s.nem_years:
            errors.append("Select at least one NEM data year on the Pick Plants tab.")
        for y in s.nem_years:
            if not (2000 <= int(y) <= 2100):
                errors.append(f"NEM data year {y} is out of range.")
        if s.optimise_capacity and int(s.capacity_sizing_year) not in tuple(s.nem_years):
            errors.append(
                "The capacity-sizing year must be one of the selected NEM data years."
            )
        if int(s.nem_resolution_minutes) not in (5, 15, 30, 60):
            errors.append("Snapshot resolution must be 5, 15, 30 or 60 minutes.")

    if s.sla_monthly_enabled and not (0.0 < s.sla_monthly_share <= 1.0):
        errors.append("Monthly SLA share must be between 0 and 1 when monthly SLA is on.")
    if s.sla_daily_enabled and not (0.0 < s.sla_daily_share <= 1.0):
        errors.append("Daily SLA share must be between 0 and 1 when daily SLA is on.")
    # D6: tsam destroys calendar structure, so a monthly constraint on the
    # clustered representation would be silently meaningless rather than merely
    # approximate.
    if s.optimise_capacity and s.sla_monthly_enabled and s.sizing_method == "tsam":
        errors.append(
            "A monthly SLA cannot be enforced with the 'Typical weeks (tsam)' sizing "
            "representation: clustering replaces the calendar with representative "
            "weeks, so calendar months no longer exist in the sizing LP. Switch the "
            "sizing representation to 'Full year hourly', or turn the monthly SLA off."
        )
    if available_days and s.chosen_day not in available_days:
        errors.append(f"chosen_day '{s.chosen_day}' is not present in the timeseries data.")
    return errors
