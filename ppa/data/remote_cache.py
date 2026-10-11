"""Fetches NEM timeseries from the pinned Zenodo record into the runtime cache.

This is the ONLY runtime module in the package permitted to make network calls.
ppa/data/nem_data.py stays cache-only and network-free; callers must invoke
`ensure_plant_years` BEFORE asking nem_data to read anything, never the other
way round. Do not add a lazy download hook inside nem_data.

Files are pinned by `ppa/data/zenodo_manifest.json`, which names the record and
gives one entry per remote file: the remote path, the year it covers, the kind
of data (``availability`` or ``price``), the DUID/region columns it holds, and
an optional md5 fingerprint verified on full downloads. Reads use parquet
column pruning (fsspec HTTP range requests + ``pyarrow.ParquetFile.read``) so
asking for a handful of plants never downloads a whole year-wide file.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import Callable, Sequence

import fsspec
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ppa.data import nem_data

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "zenodo_manifest.json"
# Read-ahead block size for the fsspec HTTP handle. Small enough that a
# column-pruned read only transfers the chunks it touches; large enough not to
# issue one range request per 5-min row.
HTTP_BLOCK_SIZE = 1 << 20


class RemoteFetchError(RuntimeError):
    """Raised when a pinned Zenodo file cannot be fetched or verified."""


def _manifest_path() -> Path:
    """Manifest location, overridable via PPA_ZENODO_MANIFEST for tests."""
    override = os.environ.get("PPA_ZENODO_MANIFEST")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / MANIFEST_FILENAME


def manifest() -> dict:
    """The pinned record manifest.

    Returns the parsed JSON: ``record_id``/``doi``/``base_url`` plus a ``files``
    dict mapping remote path to an entry with ``year``, ``kind``, ``columns``
    and an optional ``md5``.
    """
    path = _manifest_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise RemoteFetchError(f"Could not read Zenodo manifest at {path}: {exc}") from exc
    if not isinstance(data, dict) or "files" not in data or "base_url" not in data:
        raise RemoteFetchError(
            f"Zenodo manifest at {path} must contain 'base_url' and 'files'."
        )
    return data


def available_years() -> list[int]:
    """Sorted distinct years with at least one file on the pinned record."""
    years = {
        int(entry["year"])
        for entry in manifest()["files"].values()
        if entry.get("year") is not None
    }
    return sorted(years)


def _effective_cache_dir(cache_dir) -> Path:
    return Path(cache_dir) if cache_dir is not None else nem_data.RUNTIME_CACHE_DIR


def _already_present(relative: Path, cache_dir) -> bool:
    """True when the file exists in the effective cache dir, or, when the
    default runtime cache is in play, in the packaged cache too."""
    if (_effective_cache_dir(cache_dir) / relative).exists():
        return True
    if cache_dir is None and (nem_data.NEM_CACHE_DIR / relative).exists():
        return True
    return False


def missing_plant_years(duids, years, cache_dir=None) -> list[tuple[str, int]]:
    """(duid, year) pairs not already present in packaged or runtime cache."""
    missing: list[tuple[str, int]] = []
    for duid in duids:
        duid = str(duid).strip().upper()
        for year in years:
            year = int(year)
            relative = Path("availability") / f"{duid}_{year}.parquet"
            if not _already_present(relative, cache_dir):
                missing.append((duid, year))
    return missing


def _url_for(path: str) -> str:
    base = str(manifest()["base_url"]).rstrip("/")
    return f"{base}/{str(path).lstrip('/')}"


def _entry(manifest_data: dict, kind: str, year: int) -> "dict | None":
    for path, entry in manifest_data["files"].items():
        if entry.get("kind") == kind and int(entry.get("year", -1)) == year:
            return {**entry, "path": str(path)}
    return None


def _supports_ranges(url: str) -> bool:
    """True when the server advertises byte-range support (Accept-Ranges).

    A HEAD that succeeds but omits Accept-Ranges means the pruned read would
    silently pull the whole file, so the caller falls back to a full download.
    A HEAD that fails is treated as range-capable so the read path can still
    report the underlying error properly.
    """
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.headers.get("Accept-Ranges", "").lower() == "bytes"
    except Exception:
        return True


def _download_full(url: str, expected_md5: "str | None", record_id: str) -> bytes:
    """Whole-file download with an optional md5 check against the manifest."""
    try:
        fs = fsspec.filesystem(url.split("://", 1)[0])
        raw = fs.cat_file(url)
    except Exception as exc:
        raise RemoteFetchError(
            f"Full download of {url} from record {record_id} failed: {exc}"
        ) from exc
    if expected_md5:
        actual = hashlib.md5(raw).hexdigest()
        if actual != expected_md5:
            raise RemoteFetchError(
                f"md5 mismatch for {url} from record {record_id}: "
                f"expected {expected_md5}, got {actual}"
            )
    return raw


def _read_table(url, columns, block_size, expected_md5=None, record_id="unknown"):
    """Read only the wanted columns of a remote parquet file.

    Prefers a column-pruned read over a seekable handle (range requests); falls
    back to a full download (with an md5 check) when the server does not honour
    byte ranges. Every network failure is wrapped in RemoteFetchError.
    """
    scheme = url.split("://", 1)[0]
    if scheme in ("http", "https") and not _supports_ranges(url):
        logger.warning(
            "Server at %s does not advertise byte-range support; falling back "
            "to a full download of the whole file.", url,
        )
        raw = _download_full(url, expected_md5, record_id)
        return pq.read_table(BytesIO(raw), columns=list(columns))
    try:
        fs = fsspec.filesystem(scheme)
        with fs.open(url, block_size=block_size) as fh:
            pf = pq.ParquetFile(fh)
            return pf.read(columns=list(columns))
    except RemoteFetchError:
        raise
    except Exception as exc:
        raise RemoteFetchError(
            f"Failed to fetch {url} from record {record_id}: {exc}"
        ) from exc


def _verify_table(table, year: int, path: str, record_id: str) -> None:
    """Integrity checks for a column-pruned read: exact row count and dtype."""
    expected = nem_data.expected_intervals(year)
    if table.num_rows != expected:
        raise RemoteFetchError(
            f"{path} from record {record_id} has {table.num_rows} rows; "
            f"expected {expected} for {year}."
        )
    for column in table.column_names:
        if table.column(column).type != pa.float32():
            raise RemoteFetchError(
                f"Column '{column}' in {path} from record {record_id} has type "
                f"{table.column(column).type}; expected float32."
            )


def _capacity_mw_map(cache_dir: Path) -> dict:
    """Registered capacity per DUID.

    Prefers the per-year ``capacity_registered_mw_used`` from the plant-years
    manifest (when present) over the registry's single capacity column, so
    scaling Zenodo CF values to MW uses the same capacity the model runs with.
    """
    capacities: dict[str, float] = {}
    try:
        registry = nem_data.load_plant_registry(cache_dir)
        capacities = {
            str(row["duid"]): float(row["capacity_registered_mw"])
            for _, row in registry.iterrows()
        }
    except Exception as exc:
        raise RemoteFetchError(
            f"Cannot scale Zenodo CF values to MW: the plant registry is "
            f"unreadable ({exc}). Point cache_dir at a cache that has one."
        ) from exc
    try:
        plant_years = nem_data.load_plant_years(cache_dir)
        if "capacity_registered_mw_used" in plant_years.columns:
            for _, row in plant_years.iterrows():
                used = row["capacity_registered_mw_used"]
                if used is not None and not pd.isna(used):
                    capacities[str(row["duid"])] = float(used)
    except Exception:
        pass
    return capacities


def _atomic_write_parquet(frame: pd.DataFrame, out: Path, index: bool) -> None:
    """Write to a sibling `.tmp` then os.replace, so a killed process never
    leaves a truncated parquet that later reads back as valid."""
    tmp = out.with_name(f".{out.name}.tmp")
    frame.to_parquet(tmp, compression="zstd", index=index)
    os.replace(tmp, out)


def ensure_plant_years(
    duids: "Sequence[str]",
    years: "Sequence[int]",
    progress: "Callable[[float, str], None] | None" = None,
    cache_dir: "Path | None" = None,
) -> list[Path]:
    """Fetch every missing (duid, year) CF column and materialise it as
    <runtime_cache>/availability/<DUID>_<year>.parquet in the compact
    values-only format nem_data._read_5min_values expects (RangeIndex,
    single float32 'availability' column, one row per
    nem_data.canonical_5min_index(year) position -- see
    ppa/data/nem_data.py, already merged, for the exact reader).

    The stored dataset holds CF in [0, 1]; each column is multiplied by the
    plant's registered capacity (from the plant-years manifest's
    ``capacity_registered_mw_used`` or the registry's
    ``capacity_registered_mw``) so ``nem_data.load_availability`` keeps
    returning MW. Idempotent: already-present files are skipped. Raises
    RemoteFetchError with an actionable message on network failure -- never a
    bare requests exception.
    """
    cache_dir = _effective_cache_dir(cache_dir)
    duids = [str(d).strip().upper() for d in duids]
    years = [int(y) for y in years]
    missing = missing_plant_years(duids, years, cache_dir)
    total = len(missing)
    if total == 0:
        return []

    man = manifest()
    record_id = str(man.get("record_id", "unknown"))
    capacities = _capacity_mw_map(cache_dir)
    out_paths: list[Path] = []
    done = 0
    for year in sorted({y for _, y in missing}):
        entry = _entry(man, "availability", year)
        if entry is None:
            raise RemoteFetchError(
                f"Record {record_id} has no availability file for {year}."
            )
        wanted = [d for (d, y) in missing if y == year and d in entry["columns"]]
        absent = [d for (d, y) in missing if y == year and d not in entry["columns"]]
        if absent:
            raise RemoteFetchError(
                f"Record {record_id} has no availability column for "
                f"{', '.join(sorted(absent))} in {year}."
            )
        url = _url_for(entry["path"])
        table = _read_table(url, wanted, HTTP_BLOCK_SIZE, entry.get("md5"), record_id)
        _verify_table(table, year, entry["path"], record_id)
        for duid in wanted:
            if duid not in capacities:
                raise RemoteFetchError(
                    f"DUID '{duid}' is not in the plant registry for cache "
                    f"{cache_dir}, so its CF column cannot be scaled to MW."
                )
            cf = table.column(duid).to_numpy()
            mw = (cf * capacities[duid]).astype(np.float32)
            out = cache_dir / "availability" / f"{duid}_{year}.parquet"
            out.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_parquet(pd.DataFrame({"availability": mw}), out, index=False)
            out_paths.append(out)
            done += 1
            if progress is not None:
                progress(done / total, f"availability {duid} {year}")
    return out_paths


def ensure_price_years(
    regions: "Sequence[str]",
    years: "Sequence[int]",
    progress: "Callable[[float, str], None] | None" = None,
    cache_dir: "Path | None" = None,
) -> list[Path]:
    """Fetch every missing (region, year) RRP column and materialise it as
    <runtime_cache>/price/rrp_<REGION>_<year>.parquet -- a DatetimeIndex on the
    canonical 5-minute grid with a single float32 'rrp' column, the format
    nem_data._load_5min_series reads. Idempotent; raises RemoteFetchError with
    an actionable message on network failure.
    """
    cache_dir = _effective_cache_dir(cache_dir)
    regions = [str(r).strip().upper() for r in regions]
    years = [int(y) for y in years]
    missing: list[tuple[str, int]] = []
    for region in regions:
        for year in years:
            relative = Path("price") / f"rrp_{region}_{year}.parquet"
            if not _already_present(relative, cache_dir):
                missing.append((region, year))
    total = len(missing)
    if total == 0:
        return []

    man = manifest()
    record_id = str(man.get("record_id", "unknown"))
    out_paths: list[Path] = []
    done = 0
    for year in sorted({y for _, y in missing}):
        entry = _entry(man, "price", year)
        if entry is None:
            raise RemoteFetchError(
                f"Record {record_id} has no price file for {year}."
            )
        wanted = [r for (r, y) in missing if y == year and r in entry["columns"]]
        absent = [r for (r, y) in missing if y == year and r not in entry["columns"]]
        if absent:
            raise RemoteFetchError(
                f"Record {record_id} has no price column for "
                f"{', '.join(sorted(absent))} in {year}."
            )
        url = _url_for(entry["path"])
        table = _read_table(url, wanted, HTTP_BLOCK_SIZE, entry.get("md5"), record_id)
        _verify_table(table, year, entry["path"], record_id)
        idx = nem_data.canonical_5min_index(year)
        for region in wanted:
            out = cache_dir / "price" / f"rrp_{region}_{year}.parquet"
            out.parent.mkdir(parents=True, exist_ok=True)
            frame = pd.DataFrame({"rrp": table.column(region).to_numpy()}, index=idx)
            frame.index.name = "settlementdate"
            _atomic_write_parquet(frame, out, index=True)
            out_paths.append(out)
            done += 1
            if progress is not None:
                progress(done / total, f"price {region} {year}")
    return out_paths
