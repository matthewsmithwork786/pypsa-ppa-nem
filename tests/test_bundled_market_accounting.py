"""Bundled purchases must pay for the same certificate credited on sales."""
import pandas as pd
import pytest

from ppa.network import build_network
from ppa.results import extract_results
from ppa.scenario import Scenario
from ppa.solver import solve


def test_market_trade_cannot_mint_free_certificates():
    s = Scenario(onsw_mw=10, pv_mw=0, ppaload_mw=10, include_bess=False,
                 enable_market_buy=True, market_buy_share=.1,
                 enable_shortfall=False, enable_penalty=False)
    ts = pd.DataFrame({"ts_PVGen": [0., 0.], "ts_WindGen": [1., 1.],
                       "ts_MktPrice": [50., 50.], "ppaload_mw": [10., 10.]},
                      index=pd.date_range("2025-01-01", periods=2, freq="h"))
    n = build_network(ts, s)
    status, condition = solve(n, s, ts)
    r = extract_results(n, s, ts, status, condition)
    assert r.summary.market_buy_to_ppa_mwh == pytest.approx(0., abs=1e-6)
    assert r.summary.sold_to_market_mwh == pytest.approx(0., abs=1e-6)


def test_sizing_respects_disabled_market_sales():
    s = Scenario(optimise_capacity=True, enable_market_sell=False)
    ts = pd.DataFrame({"ts_PVGen": [0.], "ts_WindGen": [.5],
                       "ts_MktPrice": [100.], "ppaload_mw": [10.]},
                      index=pd.date_range("2025-01-01", periods=1, freq="h"))
    n = build_network(ts, s)
    assert n.generators.static.at["Gen_SellToMarket", "p_nom"] == 0.
