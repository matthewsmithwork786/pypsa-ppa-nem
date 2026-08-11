"""Offline tests for ppa/data/remote_cache.py.

Every test runs against a local range-capable HTTP server (stdlib
``http.server`` in a background thread) serving synthetic wide parquet files --
never the real zenodo.org. The handler counts requests and body bytes so the
column-pruning and idempotency claims are asserted, not assumed.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import socketserver
import threading
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ppa.data import nem_data
from ppa.data import remote_cache
from ppa.data.remote_cache import RemoteFetchError
from tests.fixtures.nem_fixtures import build_nem_fixture_cache

RECORD_ID = "TEST-RECORD"
HEADER_BLOCK_SIZE = 1 << 20


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    """Serves files from `root`, honouring byte ranges when `range_capable`.

    Subclasses can flip `range_capable` off (each class keeps its own request
    and byte counters). `root` is shared across instances via the fixtures.
    """

    root: Path = None
    range_capable = True
    requests = 0
    body_bytes = 0

    def log_message(self, *args):
        pass

    def _serve_path(self) -> Path:
        return type(self).root / self.path.lstrip("/")

    def do_HEAD(self):
        path = self._serve_path()
        if not path.is_file():
            self.send_error(404, "Not Found")
            return
        self.send_response(200)
        if type(self).range_capable:
            self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()

    def do_GET(self):
        type(self).requests += 1
        path = self._serve_path()
        if not path.is_file():
            self.send_error(404, "Not Found")
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        range_header = self.headers.get("Range")
        if type(self).range_capable and range_header and range_header.startswith("bytes="):
            spec = range_header[6:]
            a, b = spec.split("-", 1)
            start = int(a) if a else 0
            end = min(int(b) if b else size - 1, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        if type(self).range_capable:
            self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = fh.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                type(self).body_bytes += len(chunk)
                remaining -= len(chunk)


class _NoRangeHandler(_RangeHandler):
    """Pretends ranges do not exist: 200 with the full body for every GET."""

    range_capable = False


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


# ── Fixture builders ─────────────────────────────────────────────────────────

def _write_wide_cf(path: Path, year: int, duids, n_extra: int = 0, seed: int = 7):
    """Wide parquet of float32 CF columns on the canonical grid for `year`.

    `n_extra` adds synthetic DUID columns so the pruned-read test has a file
    much larger than the two columns actually requested.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = nem_data.expected_intervals(year)
    rng = np.random.default_rng(seed)
    data = {duid: rng.random(rows, dtype=np.float32) for duid in duids}
    for i in range(n_extra):
        data[f"SYNTH{i:03d}"] = rng.random(rows, dtype=np.float32)
    pd.DataFrame(data).to_parquet(path, compression="NONE", row_group_size=rows, index=False)


def _write_wide_price(path: Path, year: int, regions, seed: int = 3):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = nem_data.expected_intervals(year)
    rng = np.random.default_rng(seed)
    data = {region: (40.0 + 30.0 * rng.random(rows)).astype(np.float32) for region in regions}
    pd.DataFrame(data).to_parquet(path, compression="NONE", row_group_size=rows, index=False)


def _file_entry(path: Path, kind: str, year: int, columns) -> dict:
    return {
        "year": year,
        "kind": kind,
        "columns": list(columns),
        "md5": hashlib.md5(path.read_bytes()).hexdigest(),
    }


def _write_manifest(root: Path, base_url: str, files: dict) -> Path:
    path = root / "manifest.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "record_id": RECORD_ID,
        "doi": f"10.5072/{RECORD_ID}",
        "base_url": base_url,
        "files": files,
    }))
    return path


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def served_root(tmp_path_factory) -> Path:
    """Directory of synthetic wide parquet files, built once per session."""
    root = tmp_path_factory.mktemp("zenodo_fixture")
    _write_wide_cf(root / "availability" / "2025.parquet", 2025, ["FULLWF1", "GAPSF1"])
    _write_wide_cf(root / "availability" / "2024.parquet", 2024, ["FULLWF1", "GAPSF1"], n_extra=94)
    _write_wide_price(root / "price" / "2025.parquet", 2025, ["NSW1", "QLD1"])
    return root


@pytest.fixture()
def http_server(served_root):
    """Range-capable local server with fresh per-test request/byte counters."""
    _RangeHandler.root = served_root
    _RangeHandler.requests = 0
    _RangeHandler.body_bytes = 0
    server = _ThreadedHTTPServer(("127.0.0.1", 0), _RangeHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def fixture_cache(tmp_path) -> Path:
    """Synthetic NEM cache tree with a plant registry but no availability."""
    return build_nem_fixture_cache(tmp_path / "nem_cache")


def _set_manifest(monkeypatch, root: Path, base_url: str, files: dict) -> None:
    monkeypatch.setenv("PPA_ZENODO_MANIFEST", str(_write_manifest(root, base_url, files)))


# ── Tests ────────────────────────────────────────────────────────────────────

def test_available_years_from_fixture_manifest(served_root, http_server, monkeypatch):
    _set_manifest(monkeypatch, served_root, http_server, {
        "availability/2024.parquet": _file_entry(
            served_root / "availability" / "2024.parquet", "availability", 2024,
            ["FULLWF1", "GAPSF1"]),
        "availability/2025.parquet": _file_entry(
            served_root / "availability" / "2025.parquet", "availability", 2025,
            ["FULLWF1", "GAPSF1"]),
    })
    assert remote_cache.available_years() == [2024, 2025]


def test_ensure_plant_years_round_trips_through_nem_data(
    served_root, http_server, fixture_cache, monkeypatch
):
    _set_manifest(monkeypatch, served_root, http_server, {
        "availability/2025.parquet": _file_entry(
            served_root / "availability" / "2025.parquet", "availability", 2025,
            ["FULLWF1", "GAPSF1"]),
    })

    progress_calls: list[tuple[float, str]] = []
    written = remote_cache.ensure_plant_years(
        ["fullwf1", "GAPSF1"], [2025],
        progress=lambda frac, msg: progress_calls.append((frac, msg)),
        cache_dir=fixture_cache,
    )
    assert sorted(p.name for p in written) == ["FULLWF1_2025.parquet", "GAPSF1_2025.parquet"]
    assert len(progress_calls) == 2
    assert progress_calls[-1][0] == 1.0

    compact = pd.read_parquet(fixture_cache / "availability" / "FULLWF1_2025.parquet")
    assert isinstance(compact.index, pd.RangeIndex)
    assert list(compact.columns) == ["availability"]
    assert compact["availability"].dtype == np.float32
    assert len(compact) == nem_data.expected_intervals(2025)

    source = pd.read_parquet(served_root / "availability" / "2025.parquet",
                             columns=["FULLWF1", "GAPSF1"])
    capacities = {"FULLWF1": 100.0, "GAPSF1": 200.0}
    for duid, capacity_mw in capacities.items():
        availability = nem_data.load_availability(duid, 2025, fixture_cache)
        assert len(availability) == nem_data.expected_intervals(2025)
        round_tripped = availability.to_numpy() / capacity_mw
        assert np.allclose(round_tripped, source[duid].to_numpy(),
                           rtol=1e-5, atol=1e-6, equal_nan=True)


def test_ensure_plant_years_idempotent_second_call_does_no_network(
    served_root, http_server, fixture_cache, monkeypatch
):
    _set_manifest(monkeypatch, served_root, http_server, {
        "availability/2025.parquet": _file_entry(
            served_root / "availability" / "2025.parquet", "availability", 2025,
            ["FULLWF1", "GAPSF1"]),
    })
    first = remote_cache.ensure_plant_years(["FULLWF1", "GAPSF1"], [2025], cache_dir=fixture_cache)
    assert len(first) == 2
    requests_after_first = _RangeHandler.requests
    body_after_first = _RangeHandler.body_bytes
    assert requests_after_first > 0

    second = remote_cache.ensure_plant_years(["FULLWF1", "GAPSF1"], [2025], cache_dir=fixture_cache)
    assert second == []
    assert _RangeHandler.requests == requests_after_first
    assert _RangeHandler.body_bytes == body_after_first


def test_pruned_read_transfers_under_ten_percent(
    served_root, http_server, fixture_cache, monkeypatch
):
    """Reading 2 DUIDs from a 96-column year file must transfer < 10% of it."""
    _set_manifest(monkeypatch, served_root, http_server, {
        "availability/2024.parquet": _file_entry(
            served_root / "availability" / "2024.parquet", "availability", 2024,
            ["FULLWF1", "GAPSF1"]),
    })
    remote_cache.ensure_plant_years(["FULLWF1", "GAPSF1"], [2024], cache_dir=fixture_cache)
    file_size = (served_root / "availability" / "2024.parquet").stat().st_size
    assert _RangeHandler.body_bytes < 0.10 * file_size


def test_remote_fetch_error_names_record_id_and_file(
    http_server, served_root, fixture_cache, monkeypatch
):
    """A 404 must surface as RemoteFetchError naming the record and the file."""
    _set_manifest(monkeypatch, served_root, http_server, {
        "availability/9999.parquet": {
            "year": 9999,
            "kind": "availability",
            "columns": ["FULLWF1"],
            "md5": "0" * 32,
        },
    })
    with pytest.raises(RemoteFetchError) as excinfo:
        remote_cache.ensure_plant_years(["FULLWF1"], [9999], cache_dir=fixture_cache)
    message = str(excinfo.value)
    assert RECORD_ID in message
    assert "availability/9999.parquet" in message


def test_full_download_fallback_when_server_ignores_ranges(
    served_root, fixture_cache, monkeypatch
):
    """No Accept-Ranges -> whole-file download with md5 verification, no fail."""
    _NoRangeHandler.root = served_root
    _NoRangeHandler.requests = 0
    _NoRangeHandler.body_bytes = 0
    server = _ThreadedHTTPServer(("127.0.0.1", 0), _NoRangeHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        files = {
            "availability/2025.parquet": _file_entry(
                served_root / "availability" / "2025.parquet", "availability", 2025,
                ["FULLWF1", "GAPSF1"]),
        }
        _set_manifest(monkeypatch, served_root, base_url, files)
        written = remote_cache.ensure_plant_years(["FULLWF1"], [2025], cache_dir=fixture_cache)
        assert [p.name for p in written] == ["FULLWF1_2025.parquet"]
        availability = nem_data.load_availability("FULLWF1", 2025, fixture_cache)
        assert len(availability) == nem_data.expected_intervals(2025)
        file_size = (served_root / "availability" / "2025.parquet").stat().st_size
        assert _NoRangeHandler.body_bytes >= file_size

        bad_files = {
            "availability/2025.parquet": {
                "year": 2025, "kind": "availability",
                "columns": ["GAPSF1"], "md5": "0" * 32,
            },
        }
        _set_manifest(monkeypatch, served_root, base_url, bad_files)
        with pytest.raises(RemoteFetchError, match="md5"):
            remote_cache.ensure_plant_years(["GAPSF1"], [2025], cache_dir=fixture_cache)
    finally:
        server.shutdown()
        server.server_close()


def test_ensure_price_years_round_trips_through_nem_data(
    served_root, http_server, fixture_cache, monkeypatch
):
    # QLD1 is deliberately absent from the fixture cache (only NSW1 is), so the
    # fetch has something to materialise.
    _set_manifest(monkeypatch, served_root, http_server, {
        "price/2025.parquet": _file_entry(
            served_root / "price" / "2025.parquet", "price", 2025, ["NSW1", "QLD1"]),
    })
    written = remote_cache.ensure_price_years(["QLD1"], [2025], cache_dir=fixture_cache)
    assert [p.name for p in written] == ["rrp_QLD1_2025.parquet"]

    prices = nem_data.load_regional_price("QLD1", 2025, fixture_cache)
    assert len(prices) == nem_data.expected_intervals(2025)
    source = pd.read_parquet(served_root / "price" / "2025.parquet", columns=["QLD1"])["QLD1"]
    assert np.allclose(prices.to_numpy(), source.to_numpy(), rtol=1e-5, atol=1e-6)
