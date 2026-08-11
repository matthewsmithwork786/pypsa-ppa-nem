### WP4 — `ppa/data/remote_cache.py`: Zenodo fetch with parquet column pruning

**Owns:** new `ppa/data/remote_cache.py`, new
`ppa/data/zenodo_manifest.json`, `requirements.txt`,
`tests/test_remote_cache.py`, `tests/test_nem_data.py` (one addition only, see
§4.4).

**No real Zenodo record exists yet** (the dataset publish step is deferred until
Hanan sets up the account/upload). Put a clearly-fake placeholder in
`ppa/data/zenodo_manifest.json`:
```json
{
  "schema_version": 1,
  "record_id": "PLACEHOLDER_NOT_YET_PUBLISHED",
  "doi": "PLACEHOLDER",
  "base_url": "https://zenodo.org/records/PLACEHOLDER_NOT_YET_PUBLISHED/files/",
  "files": {}
}
```
All your tests must run entirely offline against a small local HTTP fixture (a
`pytest-httpserver`-style local server, or Python's `http.server` in a fixture,
or `fsspec`'s `file://` scheme) — never against the real `zenodo.org`. Do NOT
attempt to hit the real network in this session.

#### 4.2 Module API

```python
"""Fetches NEM timeseries from the pinned Zenodo record into the runtime cache.

This is the ONLY runtime module in the package permitted to make network calls.
ppa/data/nem_data.py stays cache-only and network-free; callers must invoke
`ensure_plant_years` BEFORE asking nem_data to read anything, never the other
way round. Do not add a lazy download hook inside nem_data.
"""

class RemoteFetchError(RuntimeError): ...

def manifest() -> dict: ...

def available_years() -> list[int]: ...

def missing_plant_years(duids, years, cache_dir=None) -> list[tuple[str, int]]:
    """(duid, year) pairs not already present in packaged or runtime cache."""

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

    Idempotent: already-present files are skipped. Raises RemoteFetchError with
    an actionable message on network failure — never a bare requests exception.
    """

def ensure_price_years(regions, years, progress=None, cache_dir=None) -> list[Path]: ...
```

`cache_dir` here means `ppa.data.nem_data.RUNTIME_CACHE_DIR` (already merged —
import it from there, do not redefine it).

#### 4.3 The fetch itself

```python
import fsspec
import pyarrow.parquet as pq

fs = fsspec.filesystem("http")
with fs.open(url, block_size=1 << 20) as fh:
    pf = pq.ParquetFile(fh)
    table = pf.read(columns=list(wanted_duids))
```

`fsspec`'s HTTP filesystem issues HTTP range requests; `pq.ParquetFile.read(columns=...)`
reads the footer, then only the column chunks it needs. Reading 2 columns from a 50 MB
year file should transfer well under 1 MB. For your local test fixture this is still
worth asserting (bytes transferred < 10% of file size) — write a small file-serving
fixture and wrap the handle in a byte counter.

If the server does not honour range requests (`Accept-Ranges` absent), fall back to a
full download with a warning logged — but do not fail.

Materialisation, mirroring `scripts/compact_availability_cache.py` (read it for the
exact pattern):

```python
out = pd.DataFrame({"availability": cf_values.astype("float32") * capacity_mw})
out.to_parquet(path, compression="zstd", index=False)
```

Two things to get right here:
- The stored dataset holds **CF** (capacity factor, [0,1]); `nem_data.load_availability`
  returns **MW** and `capacity_factor_series` divides by capacity. So multiply back by
  the plant's registered capacity (from `nem_data.load_plant_registry()`'s
  `capacity_registered_mw` column, or from `nem_data.load_plant_years()`'s
  `capacity_registered_mw_used` if present) on write. Do not change `nem_data`'s
  contract. Assert round-trip in a test.
- Row count must equal `nem_data.expected_intervals(year)` exactly, or
  `nem_data._read_5min_values` silently returns `None` and falls through to the slow
  path.

Add integrity checks: md5 the downloaded bytes against the manifest when a full file was
downloaded; on a column-pruned read, assert row count and dtype. Write to a `.tmp` file
and `os.replace` so a killed process never leaves a truncated parquet that later reads
as valid.

#### 4.4 Dependencies and discipline

- Add `fsspec` and `aiohttp` (fsspec's HTTP backend) to `requirements.txt`, pinned to
  whatever recent stable versions you find compatible; do not touch `pixi.toml` or
  `pixi.lock`.
- Add ONE new positive assertion to `tests/test_nem_data.py`:
  `test_nem_data_does_not_import_remote_cache`, asserting `"remote_cache"` does not
  appear as a substring in `ppa/data/nem_data.py`'s source. Do not touch anything else
  in that file.

#### 4.5 Tests — `tests/test_remote_cache.py`

All offline — no test may hit the real network.

- Build a small wide parquet (a few DUID columns) in a temp dir, serve it via a local
  fixture (pick whichever of `http.server` in a background thread, or `fsspec`'s
  `file://` scheme, is simplest given what's already installed in the pixi env — check
  with `PYTHONPATH=. .pixi/envs/default/bin/python3 -c "import pytest_httpserver"` first;
  if unavailable, use stdlib `http.server`).
- `ensure_plant_years` writes a file readable by `nem_data.load_availability` and
  round-tripping to the source CF within float32 tolerance.
- Idempotency: a second call performs zero reads (mock/count the open calls).
- Bytes-transferred assertion (§4.3).
- `RemoteFetchError` is raised, with a message naming the record ID and the file, when
  the URL 404s.

**Done when:** all tests pass offline (`MPLCONFIGDIR=/tmp/mplcache
.pixi/envs/default/bin/python3 -m pytest -q -p no:cacheprovider tests/test_remote_cache.py`),
the full existing suite is still green, and `pip install -r requirements.txt` (in a
scratch venv, not the pixi env — do not touch pixi) does not error.

---
Do not modify files outside the "Files owned" list above. If you believe you need
to, stop and report why.
