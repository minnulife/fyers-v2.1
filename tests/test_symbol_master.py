from app.services import symbol_master


def test_search_symbols_uses_cached_items(monkeypatch):
    monkeypatch.setattr(
        symbol_master,
        "_ensure_cache_current",
        lambda: [
            {
                "symbol": "NSE:TCS-EQ",
                "name": "TATA CONSULTANCY SERVICES LTD",
                "exchange": "NSE",
                "segment": "CM",
                "item_type": "STOCK",
                "short_sym": "TCS",
                "lot": 1,
                "tick": 0.05,
                "expiry": None,
                "strike": None,
                "opt": "",
                "source": "NSE_CM",
                "search_blob": "NSE:TCS-EQ TATA CONSULTANCY SERVICES LTD TCS NSE CM STOCK",
            },
            {
                "symbol": "NSE:NIFTY26AUGFUT",
                "name": "NIFTY 25 AUG 26 FUT",
                "exchange": "NSE",
                "segment": "FO",
                "item_type": "FUTURES",
                "short_sym": "NIFTY",
                "lot": 65,
                "tick": 0.05,
                "expiry": 1787652000,
                "strike": None,
                "opt": "XX",
                "source": "NSE_FO",
                "search_blob": "NSE:NIFTY26AUGFUT NIFTY 25 AUG 26 FUT NIFTY NSE FO FUTURES",
            },
        ],
    )

    items = symbol_master.search_symbols("tcs")
    assert len(items) == 1
    assert items[0]["symbol"] == "NSE:TCS-EQ"

    futures = symbol_master.search_symbols("nifty fut")
    assert futures[0]["symbol"] == "NSE:NIFTY26AUGFUT"
    assert futures[0]["item_type"] == "FUTURES"

