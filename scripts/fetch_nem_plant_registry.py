#!/usr/bin/env python3
"""
One-time acquisition script: build a NEM wind/solar plant registry (DUID, name,
region, fuel type, capacity, lat/lon, and an optional first-power date) via the
OpenNEM / Open Electricity API.

*** NOT PART OF THE STREAMLIT APP. NEVER IMPORTED BY `ppa/` OR `ui/`. ***

Run this in a SEPARATE environment with real network access (this repo's dev
sandbox blocks `api.openelectricity.org.au`). Copy/commit the resulting
`nem_plant_registry.parquet` into `data/cache/nem/registry/` in this repo --
that's what `ppa/data/nem_data.py` actually reads at runtime (cache-only, no
network imports).

API: GET https://api.openelectricity.org.au/v4/facilities/
Requires a free API key from https://platform.openelectricity.org.au/,
supplied via the OPENELECTRICITY_API_KEY environment variable.

UNVERIFIED DETAILS (this planning/dev session could not reach the live API or
its docs -- verify against https://docs.openelectricity.org.au/api-reference/facilities/get-facilities/
before relying on this script):
  - Auth header format: this script sends `Authorization: Bearer <key>`, the
    typical convention for REST APIs of this style. If the real API expects a
    different header (e.g. `X-API-Key`) or query param, update `_auth_headers()`.
  - Response envelope/pagination shape: this script defensively handles a few
    common shapes (`{"data": [...]}`, a bare list, `{"data": [...], "meta":
    {"next_page": ...}}` etc.) via `_extract_records()` / `_next_page_params()`,
    but the exact keys should be confirmed by inspecting one real response
    (e.g. `curl` the endpoint and eyeball the JSON) before trusting this at
    scale.
  - Field names for fuel technology, capacity, and coordinates: this script
    tries a short list of plausible candidate keys per field (see
    `_first_present()`) and logs which one it actually used, but the true
    OpenElectricity/OpenNEM facility schema should be checked against the docs
    link above -- especially the exact `fueltech` taxonomy strings for wind vs
    utility-scale solar (this script assumes something like
    "wind"/"solar_utility"; the live API's enum may differ, e.g.
    "wind_offshore" vs "wind_onshore" splits, or "solar_utility" vs
    "solar_rooftop").
  - The optional `first_power_date` column: this script tries plausible
    commencement/first-seen field names per unit (see `_first_present()`) and
    normalises the result to YYYY-MM-DD. `ppa/data/nem_data.py` treats the
    column as OPTIONAL (an old cached parquet without it keeps working); the
    map tooltip falls back to a SCADA-derived "first 2025 output" date when the
    registry lacks it.
  - Whether records are per-unit (multiple rows per DUID) or per-facility with
    a `units` sub-list: this script inspects the response at runtime and
    handles both a flat per-unit list and a nested `units` list per facility
    (see `_iter_units()`), but again -- verify against a real response.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fetch_nem_plant_registry")

API_BASE_URL = "https://api.openelectricity.org.au/v4/facilities/"
API_KEY_ENV_VAR = "OPENELECTRICITY_API_KEY"

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "cache" / "nem" / "registry"

# AEMO's public (no-auth) Generation & Exemption List -- used only by the
# fallback path below. URL/format not directly verified from this sandboxed
# session; check the current link on https://aemo.com.au before relying on it
# (search AEMO's site for "NEM Registration and Exemption List").
AEMO_NEM_REGISTRATION_LIST_URL_HINT = (
    "https://aemo.com.au (search: 'NEM Registration and Exemption List')"
)

# Candidate fueltech strings for wind/utility-solar. VERIFY against the live
# taxonomy at https://docs.openelectricity.org.au/api-reference/facilities/get-facilities/
# -- update this default if the real enum differs.
DEFAULT_FUEL_TECHS = "wind,solar_utility"

# Maps raw API fueltech taxonomy strings (lower-case) -> title-case output values,
# matching the convention already used by the real `nem_plant_registry.parquet`
# (`fuel_tech` column values "Wind"/"Solar"). Extend this if --fuel-techs includes
# other raw values (e.g. additional solar/wind sub-splits).
FUEL_TECH_DISPLAY_MAP = {
    "wind": "Wind",
    "wind_onshore": "Wind",
    "wind_offshore": "Wind",
    "solar_utility": "Solar",
    "solar_rooftop": "Solar",
    "solar": "Solar",
}

# The real API's operational-status field is `status_id`, with values observed
# in a live export including "operating" and "commissioning". Default to only
# "operating" facilities (excludes not-yet-operational plant like "commissioning").
DEFAULT_STATUS = "operating"


def _auth_headers(api_key: str) -> dict:
    """Bearer-token header, the typical convention for this class of REST API.

    UNVERIFIED: confirm the real auth scheme against the live API docs (see
    module docstring) -- it may instead expect `X-API-Key: <key>` or a query
    param. Update here if so.
    """
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}


def _extract_records(payload: Any) -> list[dict]:
    """Defensively pull the list of facility records out of whatever envelope shape the API uses."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "results", "facilities", "records"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
    raise ValueError(
        f"Could not find a records list in the API response (top-level keys: "
        f"{list(payload.keys()) if isinstance(payload, dict) else type(payload)}). "
        "The response envelope shape differs from what this script expects -- "
        "inspect the raw JSON and update `_extract_records()`."
    )


def _next_page_params(payload: Any, current_params: dict) -> dict | None:
    """Return params for the next page, or None if this looks like the last page.

    UNVERIFIED pagination shape -- handles a `meta.next_page`/`meta.page` style
    cursor/offset if present, else falls back to "no more pages" after one call
    (safe default: better to under-fetch and let the summary report a low
    count than to loop forever against an unverified API).
    """
    if not isinstance(payload, dict):
        return None
    meta = payload.get("meta") or payload.get("pagination")
    if not isinstance(meta, dict):
        return None
    next_page = meta.get("next_page") or meta.get("next")
    if next_page:
        new_params = dict(current_params)
        new_params["page"] = next_page
        return new_params
    return None


def _first_present(record: dict, candidates: list[str]) -> Any:
    """Return the value of the first key in `candidates` that exists in `record` (case-insensitive)."""
    lower_map = {k.lower(): k for k in record.keys()}
    for candidate in candidates:
        actual_key = lower_map.get(candidate.lower())
        if actual_key is not None:
            return record[actual_key]
    return None


def _iter_units(facility: dict):
    """Yield per-unit dicts for a facility record, handling both nested and flat shapes.

    The real OpenElectricity v4 facility shape (confirmed against a live export,
    `opennem_facilities_raw.csv`) is: a facility-level record (`facility_code`,
    `facility_name`, `network_region`, lat/lon, ...) containing a nested `units`
    list, where EACH unit carries its OWN `unit_code`/`duid` (the actual DUID) plus
    its own `fueltech_id`/`status_id`/capacity fields. A facility-level `code` field
    (if present) is NOT a DUID -- it is the facility code, which is shared by
    multiple units, so it must never be treated as a unit DUID.

    We therefore check for a nested units/generators list FIRST and, if present,
    always descend into it -- yielding each unit merged with the parent facility's
    metadata (name/region/lat/lon) so the unit inherits facility-level fields it
    doesn't carry itself, while the unit's own fields (including its own duid/
    unit_code) win over the facility's. Only if no nested list is found do we fall
    back to treating the record itself as a flat per-unit record.
    """
    units = _first_present(facility, ["units", "generators", "unit_list"])
    if isinstance(units, list) and units:
        for unit in units:
            merged = {**facility, **unit}  # unit-level fields (incl. duid/unit_code) win
            yield merged
        return

    # No nested list -- only now consider this a flat per-unit record. Prefer
    # duid/unit_code (unambiguous) over the ambiguous facility-level `code`.
    duid = _first_present(facility, ["duid", "unit_code", "code"])
    if duid:
        yield facility
        return

    log.warning(
        "Facility record has neither a nested unit list nor a top-level DUID -- "
        "skipping it (keys: %s)",
        list(facility.keys()),
    )
    return


def fetch_facilities(api_key: str, timeout: int = 60) -> list[dict]:
    """Fetch all pages of the facilities endpoint and return the flat list of raw records."""
    records: list[dict] = []
    params: dict = {}
    page_count = 0
    max_pages = 50  # safety cap against an unbounded/misparsed pagination loop

    while True:
        page_count += 1
        if page_count > max_pages:
            log.warning("Hit max_pages=%d safety cap -- stopping pagination early.", max_pages)
            break
        log.info("GET %s params=%s", API_BASE_URL, params)
        resp = requests.get(API_BASE_URL, headers=_auth_headers(api_key), params=params, timeout=timeout)
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"OpenElectricity API returned HTTP {resp.status_code} ({resp.reason}). This "
                "usually means the auth header format is wrong, not that the key itself is bad "
                "-- this script currently sends `Authorization: Bearer <key>` (see "
                "`_auth_headers()`); the real API may instead expect e.g. `X-API-Key: <key>` or "
                "a query param. Check https://docs.openelectricity.org.au/api-reference/"
                "facilities/get-facilities/ and update `_auth_headers()` if so. "
                f"Response body (truncated): {resp.text[:500]!r}"
            )
        resp.raise_for_status()
        payload = resp.json()
        page_records = _extract_records(payload)
        log.info("Page %d: %d records", page_count, len(page_records))
        records.extend(page_records)

        next_params = _next_page_params(payload, params)
        if next_params is None:
            break
        params = next_params

    return records


def normalise_facilities(
    raw_records: list[dict],
    fuel_techs: list[str],
    min_capacity_mw: float,
    status: str = DEFAULT_STATUS,
) -> pd.DataFrame:
    """Flatten raw API records into the target registry schema and apply filters."""
    rows = []
    for facility in raw_records:
        for unit in _iter_units(facility):
            duid = _first_present(unit, ["duid", "unit_code", "code"])
            if not duid:
                continue
            fuel_tech = _first_present(unit, ["fueltech_id", "fueltech", "fuel_tech", "technology"])
            capacity = _first_present(
                unit, ["capacity_registered", "registered_capacity", "capacity_mw", "reg_cap_mw", "capacity"]
            )
            lat = _first_present(unit, ["lat", "latitude", "location_lat"])
            lon = _first_present(unit, ["lon", "lng", "longitude", "location_lng", "location_lon"])
            # Some APIs nest coordinates under a `location`/`geometry` dict
            if lat is None or lon is None:
                geo = _first_present(unit, ["location", "geometry", "coordinates"])
                if isinstance(geo, dict):
                    lat = lat if lat is not None else _first_present(geo, ["lat", "latitude"])
                    lon = lon if lon is not None else _first_present(geo, ["lon", "lng", "longitude"])
                elif isinstance(geo, (list, tuple)) and len(geo) == 2:
                    # GeoJSON-style [lon, lat] point
                    lon = lon if lon is not None else geo[0]
                    lat = lat if lat is not None else geo[1]

            rows.append(
                {
                    "duid": str(duid).strip().upper(),
                    "station_name": _first_present(unit, ["station_name", "name", "facility_name", "display_name"]),
                    "region": _first_present(unit, ["region", "region_id", "network_region"]),
                    "fuel_tech": fuel_tech,
                    "capacity_registered_mw": pd.to_numeric(capacity, errors="coerce"),
                    "lat": pd.to_numeric(lat, errors="coerce"),
                    "lon": pd.to_numeric(lon, errors="coerce"),
                    "status": _first_present(unit, ["status_id", "status", "unit_status", "operating_status"]),
                    "first_power_date": _first_present(
                        unit,
                        [
                            "data_first_seen",
                            "first_power_date",
                            "commencement_date",
                            "commissioned_date",
                            "commissioning_date",
                            "approved_date",
                            "start_date",
                        ],
                    ),
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        log.error("No unit rows extracted from the API response at all -- check `_iter_units`/`_first_present`.")
        return df

    df = df.drop_duplicates(subset=["duid"])

    # Normalise first_power_date to a YYYY-MM-DD string (or NaN) so the parquet
    # carries a stable, comparable value regardless of whether the API returns
    # a date object, an ISO string, or a timestamp.
    df["first_power_date"] = pd.to_datetime(
        df["first_power_date"], errors="coerce", format="mixed"
    ).dt.strftime("%Y-%m-%d")

    # Diagnostic logging BEFORE filtering, so a wrong guessed taxonomy string shows up
    # immediately in the log rather than silently producing an empty registry.
    log.info(
        "Pre-filter diagnostics: distinct fuel_tech values observed: %s",
        sorted(df["fuel_tech"].dropna().astype(str).str.lower().unique().tolist()),
    )
    log.info(
        "Pre-filter diagnostics: distinct status values observed: %s",
        sorted(df["status"].dropna().astype(str).str.lower().unique().tolist()),
    )
    log.info(
        "Pre-filter diagnostics: capacity_registered_mw non-null count: %d/%d",
        df["capacity_registered_mw"].notna().sum(),
        len(df),
    )

    before = len(df)
    df = df[df["status"].astype(str).str.strip().str.lower() == status.strip().lower()]
    log.info("Status filter (== %r): %d -> %d rows", status, before, len(df))

    fuel_techs_lower = {ft.strip().lower() for ft in fuel_techs}
    before = len(df)
    df = df[df["fuel_tech"].astype(str).str.lower().isin(fuel_techs_lower)]
    log.info("Fuel-tech filter (%s): %d -> %d rows", sorted(fuel_techs_lower), before, len(df))

    before = len(df)
    df = df[df["capacity_registered_mw"] >= min_capacity_mw]
    log.info("Capacity filter (>= %.0f MW): %d -> %d rows", min_capacity_mw, before, len(df))

    # Title-case the fuel_tech display value to match the convention already
    # established by data/cache/nem/registry/nem_plant_registry.parquet ("Wind"/"Solar").
    df = df.copy()
    df["fuel_tech"] = (
        df["fuel_tech"]
        .astype(str)
        .str.strip()
        .str.lower()
        .map(FUEL_TECH_DISPLAY_MAP)
        .fillna(df["fuel_tech"].astype(str).str.strip().str.title())
    )

    before = len(df)
    df = df.dropna(subset=["lat", "lon"])
    dropped = before - len(df)
    if dropped:
        log.warning(
            "Dropped %d row(s) with null lat/lon (the registry schema requires no null "
            "coordinates) -- see module docstring for lat/lon field-name candidates to check "
            "if this count looks too high.",
            dropped,
        )

    return df.reset_index(drop=True)


def fetch_via_aemo_fallback(min_capacity_mw: float) -> pd.DataFrame:  # noqa: ARG001
    """
    STUB / TODO: fallback path when no OPENELECTRICITY_API_KEY is available.

    AEMO publishes a free, no-auth "NEM Registration and Exemption List" Excel
    workbook (search AEMO's site -- see AEMO_NEM_REGISTRATION_LIST_URL_HINT
    above; the exact download URL was not fetchable from this sandboxed
    planning session and should be located/confirmed live). That workbook
    gives DUID, station name, region, participant, and registered capacity --
    but NOT lat/lon coordinates.

    To fully implement this fallback:
      1. Download the workbook (`requests.get(<confirmed URL>)`).
      2. `pd.read_excel(..., sheet_name=<the "Generators and Scheduled Loads"
         sheet, name TBC>)` and select DUID/StationName/Region/
         RegisteredCapacity/FuelType-like columns (exact column headers TBC --
         inspect the real file).
      3. Filter to wind/solar fuel types and capacity >= min_capacity_mw, as
         `normalise_facilities()` does above.
      4. lat/lon are NOT in this workbook. For the (small, post-filter) set of
         qualifying plants, either maintain a small manually-curated CSV
         lookup (station name -> lat/lon, sourced by hand from public plant
         info) or geocode station names via a free geocoding API -- both are
         manual/semi-manual steps outside what this planning session could
         verify end-to-end, hence this function is a stub.

    Raises NotImplementedError until someone completes step 2 above against
    the real downloaded file.
    """
    raise NotImplementedError(
        "fetch_via_aemo_fallback() is a documented stub -- see its docstring. "
        "Get an OPENELECTRICITY_API_KEY instead if at all possible; it's free. "
        "If you must use this fallback, implement steps 1-4 in the docstring "
        "against the real AEMO Registration and Exemption List file."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build the NEM wind/solar plant registry via the OpenNEM/Open Electricity "
            "facilities API and cache it into data/cache/nem/registry/. Must be run with "
            "real network access -- see scripts/README.md."
        )
    )
    parser.add_argument("--min-capacity-mw", type=float, default=30.0)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--fuel-techs",
        type=str,
        default=DEFAULT_FUEL_TECHS,
        help=(
            "Comma-separated fueltech values to keep (default: "
            f"'{DEFAULT_FUEL_TECHS}'). VERIFY these strings against the live API's "
            "fueltech taxonomy -- see module docstring."
        ),
    )
    parser.add_argument(
        "--status",
        type=str,
        default=DEFAULT_STATUS,
        help=(
            f"Facility `status_id` value to keep (default: '{DEFAULT_STATUS}'). Excludes "
            "e.g. 'commissioning' facilities by default."
        ),
    )
    parser.add_argument(
        "--fallback-no-api-key",
        action="store_true",
        help=(
            "Use the AEMO no-auth fallback path instead of the OpenElectricity API "
            "(currently a documented stub -- see fetch_via_aemo_fallback())."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-write nem_plant_registry.parquet even if it already exists (default: refuse).",
    )
    args = parser.parse_args()

    fuel_techs = [ft.strip() for ft in args.fuel_techs.split(",") if ft.strip()]
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "nem_plant_registry.parquet"

    if out_file.exists() and not args.overwrite:
        log.error(
            "%s already exists and --overwrite was not passed -- refusing to clobber it "
            "(this file may contain real, hand-verified data). Pass --overwrite to replace it.",
            out_file,
        )
        return 1

    if args.fallback_no_api_key:
        df = fetch_via_aemo_fallback(args.min_capacity_mw)
    else:
        api_key = os.environ.get(API_KEY_ENV_VAR)
        if not api_key:
            log.error(
                "%s is not set. Get a free key from https://platform.openelectricity.org.au/ "
                "and `export %s=...`, or re-run with --fallback-no-api-key "
                "(note: that path is currently a documented stub, see fetch_via_aemo_fallback()).",
                API_KEY_ENV_VAR,
                API_KEY_ENV_VAR,
            )
            return 1

        log.info("=== Fetching facilities from OpenElectricity API ===")
        raw_records = fetch_facilities(api_key)
        log.info("Fetched %d raw facility records", len(raw_records))

        log.info(
            "=== Normalising + filtering (fuel_techs=%s, status=%r, min_capacity_mw=%.0f) ===",
            fuel_techs,
            args.status,
            args.min_capacity_mw,
        )
        df = normalise_facilities(raw_records, fuel_techs, args.min_capacity_mw, status=args.status)

    if df.empty:
        log.error("Resulting registry is empty -- not writing an output file. Check filters/API response shape.")
        return 2

    expected_cols = [
        "duid",
        "station_name",
        "region",
        "fuel_tech",
        "capacity_registered_mw",
        "lat",
        "lon",
        "status",
        "first_power_date",
    ]
    df = df[[c for c in expected_cols if c in df.columns]]
    df.to_parquet(out_file, index=False)

    missing_coords = df[df["lat"].isna() | df["lon"].isna()]

    print("\n" + "=" * 70)
    print("PLANT REGISTRY SUMMARY")
    print("=" * 70)
    print(f"Facilities/units written: {len(df)}")
    print(f"Output file: {out_file}")
    print(f"Regions covered: {sorted(df['region'].dropna().unique().tolist())}")
    print(f"Fuel techs covered: {sorted(df['fuel_tech'].dropna().unique().tolist())}")
    if len(missing_coords):
        print(f"WARNING: {len(missing_coords)} rows missing lat/lon: {missing_coords['duid'].tolist()}")
    else:
        print("All rows have lat/lon.")
    print("=" * 70)
    print(f"Next step: copy/commit '{out_file}' into this repo's data/cache/nem/registry/ directory.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
