from types import SimpleNamespace

from app.services import market_data


def test_dashboard_market_data_falls_back_to_demo(monkeypatch):
    monkeypatch.setattr(market_data, "_get_client", lambda: None)
    result = market_data.build_dashboard_market_data(
        core_symbols=["NIFTY"],
        fallback_map={"NIFTY": {"spot": 24854.35, "change": 126.4, "pct": 0.51, "open": 24740.2, "prev_close": 24727.95, "mood": "Bullish"}},
        watch_items=[],
    )

    assert result["source"] == "DEMO"
    assert result["core_markets"][0]["spot"] == 24854.35
    assert result["core_markets"][0]["data_source"] == "DEMO"


def test_dashboard_market_data_uses_live_quote(monkeypatch):
    class FakeClient:
        def depth(self, payload):
            assert payload == {"symbol": "NSE:NIFTY50-INDEX", "ohlcv_flag": 1}
            return {"s": "ok", "d": {"NSE:NIFTY50-INDEX": {"ltp": 25000.5, "o": 24950.0, "c": 24875.0, "ch": 125.5, "chp": 0.5}}}

    monkeypatch.setattr(market_data, "_get_client", lambda: FakeClient())
    monkeypatch.setattr(market_data, "_resolve_future_symbol", lambda symbol: None)
    monkeypatch.setattr(market_data, "_build_indicators", lambda client, symbol: {"rsi": 55.0, "vwap": 24990.0, "supertrend": "BUY", "ma20": 24980.0, "ma50": 24900.0, "ma100": 24800.0, "ma200": 24700.0})
    monkeypatch.setattr(
        market_data,
        "probe_fyers_connection",
        lambda: {
            "configured": True,
            "connected": True,
            "token_path": "token.txt",
            "checked_at_utc": "2026-07-28T20:10:00+00:00",
            "checked_at_ist": "2026-07-28T15:40:00+05:30",
            "error": None,
        },
    )
    result = market_data.build_dashboard_market_data(
        core_symbols=["NIFTY"],
        fallback_map={"NIFTY": {"spot": 24854.35, "change": 126.4, "pct": 0.51, "open": 24740.2, "prev_close": 24727.95, "mood": "Bullish"}},
        watch_items=[SimpleNamespace(symbol="RELIANCE", item_type="STOCK", enabled=True, is_core=False)],
    )

    assert result["source"] == "LIVE"
    assert result["core_markets"][0]["spot"] == 25000.5
    assert result["core_markets"][0]["data_source"] == "LIVE"
    assert result["core_markets"][0]["change"] == 125.5
    assert result["core_markets"][0]["prev_close"] == 24875.0
    assert result["core_markets"][0]["rsi"] == 55.0
    assert result["core_markets"][0]["chart_symbol"] == "NSE:NIFTY50-INDEX"


def test_dashboard_market_data_uses_live_future_symbol(monkeypatch):
    class FakeClient:
        def depth(self, payload):
            if payload == {"symbol": "NSE:NIFTY50-INDEX", "ohlcv_flag": 1}:
                return {"s": "ok", "d": {"NSE:NIFTY50-INDEX": {"ltp": 25000.5, "o": 24950.0, "c": 24875.0, "ch": 125.5, "chp": 0.5}}}
            if payload == {"symbol": "NSE:NIFTY26AUGFUT", "ohlcv_flag": 1}:
                return {"s": "ok", "d": {"NSE:NIFTY26AUGFUT": {"ltp": 25020.0, "o": 24980.0, "c": 24900.0, "ch": 120.0, "chp": 0.48}}}
            raise AssertionError(f"Unexpected payload: {payload}")

    monkeypatch.setattr(market_data, "_get_client", lambda: FakeClient())
    monkeypatch.setattr(market_data, "_resolve_future_symbol", lambda symbol: "NSE:NIFTY26AUGFUT")
    monkeypatch.setattr(market_data, "_build_indicators", lambda client, symbol: {})
    monkeypatch.setattr(
        market_data,
        "probe_fyers_connection",
        lambda: {
            "configured": True,
            "connected": True,
            "token_path": "token.txt",
            "checked_at_utc": "2026-07-28T20:10:00+00:00",
            "checked_at_ist": "2026-07-28T15:40:00+05:30",
            "error": None,
        },
    )

    result = market_data.build_dashboard_market_data(
        core_symbols=["NIFTY"],
        fallback_map={"NIFTY": {"spot": 24854.35, "future": 24912.1, "change": 126.4, "pct": 0.51, "open": 24740.2, "prev_close": 24727.95, "mood": "Bullish"}},
        watch_items=[],
    )

    row = result["core_markets"][0]
    assert row["future"] == 25020.0
    assert row["future_symbol"] == "NSE:NIFTY26AUGFUT"
    assert row["future_chart_symbol"] == "NSE:NIFTY26AUGFUT"
