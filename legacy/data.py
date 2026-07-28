import os, csv, datetime as dt
import pandas as pd
from typing import Optional, Tuple, List
from config import IST, INDEX_SYMBOL
from config import USE_YDAY_WHEN_TODAY_EMPTY, EXPIRY_CODE
from config import LOT_SIZE, INIT_SL_PCT, COST_PER_SIDE_INR
from config import LOG_DIR

def ist_now():
    return dt.datetime.now(IST)

def utc_epoch_to_ist_dt(epoch: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc).astimezone(IST)

def nearest_50_strike(spot: float) -> int:
    return int(round(spot / 50.0) * 50)

class DataClient:
    def __init__(self, fyers, logger):
        self.fyers = fyers
        self.log = logger
        self._sym_cache = {}  # key: (expiry, strike, opt_type) -> symbol string

    # ---------- quotes / history ----------
    def quotes(self, symbol: str) -> dict:
        return self.fyers.quotes({"symbols": symbol})

    def get_ltp(self, symbol: str) -> float:
        resp = self.quotes(symbol)
        if resp.get("s") != "ok":
            raise RuntimeError(f"Quotes failed for {symbol}: {resp}")
        d = (resp.get("d") or [])
        if not d:
            raise RuntimeError(f"Quotes payload empty for {symbol}: {resp}")
        v = d[0].get("v") or {}
        if v.get("s") == "error" or v.get("errmsg"):
            raise RuntimeError(f"Invalid symbol per broker for {symbol}: {v}")
        price = v.get("lp") or v.get("last_price") or v.get("ltp") or v.get("open_price") or v.get("prev_close_price")
        if price is None:
            raise RuntimeError(f"LTP not available for {symbol}: {resp}")
        return float(price)

    def history(self, symbol: str, resolution: str, range_from: str, range_to: str) -> List[list]:
        payload = {
            "symbol": symbol,
            "resolution": resolution,   # "1" or "D"
            "date_format": "1",
            "range_from": range_from,
            "range_to": range_to,
            "cont_flag": "1"
        }
        resp = self.fyers.history(payload)
        if not isinstance(resp, dict) or resp.get("s") not in ("ok", "no_data"):
            self.log("HISTORY_ERR", symbol=symbol, reason=str(resp))
            return []
        if resp.get("s") == "no_data":
            self.log("HISTORY_ERR", symbol=symbol, reason=str(resp))
            return []
        return resp.get("candles") or []

    def get_prev_trading_close_strict(self, symbol: str) -> Tuple[Optional[str], Optional[float]]:
        today = ist_now().date()
        from_day = (today - dt.timedelta(days=15)).strftime("%Y-%m-%d")
        to_day   = (today - dt.timedelta(days=1)).strftime("%Y-%m-%d")
        daily = self.history(symbol, "D", from_day, to_day)
        for row in reversed(daily):
            if row and len(row) >= 6:
                ts_epoch, o, h, l, c, v = row
                d_ist = utc_epoch_to_ist_dt(ts_epoch).date().strftime("%Y-%m-%d")
                return d_ist, float(c)
        return None, None

    def get_1m_today(self, symbol: str) -> List[list]:
        day = ist_now().strftime("%Y-%m-%d")
        return self.history(symbol, "1", day, day)

    def get_1m_last_trading(self, symbol: str, lookback_days=7) -> List[list]:
        for i in range(1, lookback_days + 1):
            ds = (ist_now().date() - dt.timedelta(days=i)).strftime("%Y-%m-%d")
            d = self.history(symbol, "1", ds, ds)
            if d:
                return d
        return []

    # ---------- option symbol resolution ----------
    def _can_quote_symbol(self, symbol: str) -> bool:
        try:
            resp = self.quotes(symbol)
        except Exception:
            return False
        if resp.get("s") != "ok":
            return False
        d = (resp.get("d") or [])
        if not d: return False
        v = d[0].get("v") or {}
        if v.get("s") == "error" or v.get("errmsg"):
            return False
        price = v.get("lp") or v.get("last_price") or v.get("ltp") or v.get("open_price") or v.get("prev_close_price")
        return price is not None

    def resolve_option_symbol(self, expiry_code: str, strike: int, opt_type: str) -> str:
        key = (expiry_code, int(strike), opt_type.upper())
        if key in self._sym_cache:
            return self._sym_cache[key]

        # Prefer NSE first for your account (based on your logs), then NFO
        candidates = [
            f"NSE:NIFTY{expiry_code}{int(strike)}{opt_type.upper()}",
            f"NFO:NIFTY{expiry_code}{int(strike)}{opt_type.upper()}",
        ]

        # Also try nearby strikes if exact ATM isn’t quotable
        OFFSETS = [0, -50, +50, -100, +100, -150, +150]
        for off in OFFSETS:
            s = int(strike) + off
            for base in candidates:
                sym = base.replace(str(int(strike)), str(s), 1)
                if self._can_quote_symbol(sym):
                    if off != 0:
                        self.log("SYMBOL_FALLBACK", symbol=sym, reason=f"offset {off} from {strike}")
                    else:
                        self.log("SYMBOL_OK", symbol=sym, reason="Resolved option symbol")
                    self._sym_cache[key] = sym
                    return sym
        raise RuntimeError(f"Could not resolve option: {expiry_code} {strike} {opt_type}")

    def pick_atm_symbol(self, side: str) -> str:
        idx = self.get_ltp(INDEX_SYMBOL)
        strike = nearest_50_strike(idx)
        return self.resolve_option_symbol(EXPIRY_CODE, strike, side)
