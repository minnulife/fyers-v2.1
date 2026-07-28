from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo
import time
from urllib.request import urlopen

from app.core.config import get_settings

CORE_SYMBOL_CANDIDATES: dict[str, list[str]] = {
    "NIFTY": ["NSE:NIFTY50-INDEX", "NSE:NIFTY-INDEX", "NSE:NIFTY"],
    "BANKNIFTY": ["NSE:NIFTYBANK-INDEX", "NSE:BANKNIFTY-INDEX", "NSE:BANKNIFTY"],
    "SENSEX": ["BSE:SENSEX-INDEX", "BSE:SENSEX", "BSE:SENSEX50-INDEX"],
}

STOCK_SYMBOL_CANDIDATES = ("NSE:{symbol}-EQ", "BSE:{symbol}-EQ", "NSE:{symbol}", "BSE:{symbol}")
INDEX_SYMBOL_CANDIDATES = ("NSE:{symbol}-INDEX", "BSE:{symbol}-INDEX", "NSE:{symbol}", "BSE:{symbol}")
CORE_FUTURE_MASTER_URLS = {
    "NIFTY": "https://public.fyers.in/sym_details/NSE_FO.csv",
    "BANKNIFTY": "https://public.fyers.in/sym_details/NSE_FO.csv",
    "FINNIFTY": "https://public.fyers.in/sym_details/NSE_FO.csv",
    "MIDCPNIFTY": "https://public.fyers.in/sym_details/NSE_FO.csv",
    "SENSEX": "https://public.fyers.in/sym_details/BSE_FO.csv",
}
TRADINGVIEW_SYMBOLS = {
    "NIFTY": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
}
DEFAULT_CORE_MARKETS = ["NIFTY", "BANKNIFTY", "SENSEX"]
DEFAULT_MARKET_FALLBACKS: dict[str, dict[str, Any]] = {
    "NIFTY": {
        "spot": 24854.35,
        "future": 24912.10,
        "atm": 24850,
        "change": 126.40,
        "pct": 0.51,
        "open": 24740.20,
        "prev_close": 24727.95,
        "mood": "Bullish",
        "rsi": 61.4,
        "vwap": 24792.6,
        "supertrend": "BUY",
        "ma20": 24688.2,
        "ma50": 24495.4,
        "ma100": 23918.3,
        "ma200": 23182.5,
    },
    "BANKNIFTY": {
        "spot": 53642.10,
        "future": 53718.65,
        "atm": 53600,
        "change": -108.30,
        "pct": -0.20,
        "open": 53730.10,
        "prev_close": 53750.40,
        "mood": "Neutral",
        "rsi": 48.8,
        "vwap": 53681.7,
        "supertrend": "SELL",
        "ma20": 53210.1,
        "ma50": 52680.4,
        "ma100": 51180.7,
        "ma200": 49822.3,
    },
    "SENSEX": {
        "spot": 81284.90,
        "future": 81372.55,
        "atm": 81300,
        "change": 382.15,
        "pct": 0.47,
        "open": 80940.20,
        "prev_close": 80902.75,
        "mood": "Bullish",
        "rsi": 59.2,
        "vwap": 81122.4,
        "supertrend": "BUY",
        "ma20": 80690.5,
        "ma50": 79742.2,
        "ma100": 78102.6,
        "ma200": 75418.9,
    },
    "RELIANCE": {
        "spot": 3018.40,
        "change": 34.25,
        "pct": 1.15,
        "open": 2992.0,
        "prev_close": 2984.15,
        "mood": "Bullish",
    },
    "TCS": {
        "spot": 4286.60,
        "change": -21.10,
        "pct": -0.49,
        "open": 4315.0,
        "prev_close": 4307.70,
        "mood": "Neutral",
    },
    "FINNIFTY": {
        "spot": 24318.25,
        "change": 68.30,
        "pct": 0.28,
        "open": 24272.0,
        "prev_close": 24249.95,
        "mood": "Bullish",
    },
    "MIDCPNIFTY": {
        "spot": 12844.80,
        "change": -18.40,
        "pct": -0.14,
        "open": 12870.0,
        "prev_close": 12863.20,
        "mood": "Neutral",
    },
}
_PROBE_CACHE_TTL_SEC = 30
_PROBE_CACHE: dict[str, Any] | None = None
_SYMBOL_MASTER_CACHE_TTL_SEC = 3600
_SYMBOL_MASTER_CACHE: dict[str, Any] = {}
_INDICATOR_CACHE_TTL_SEC = 60
_INDICATOR_CACHE: dict[str, Any] = {}


def _get_client():
    settings = get_settings()
    token_path = settings.fyers_token_path
    if not settings.fyers_client_id or not token_path.exists():
        return None
    token = token_path.read_text(encoding="utf-8").strip()
    if not token:
        return None
    try:
        from fyers_apiv3 import fyersModel
    except ImportError:
        return None
    return fyersModel.FyersModel(client_id=settings.fyers_client_id, token=token, log_path="")


def _parse_client_error(response: Any) -> str:
    if not isinstance(response, dict):
        return f"Unexpected FYERS response: {type(response).__name__}"
    code = response.get("code")
    message = response.get("message") or response.get("msg") or response.get("error") or "Unknown FYERS error"
    if code is not None:
        return f"FYERS error {code}: {message}"
    return f"FYERS error: {message}"


def _quote_value(values: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = values.get(key)
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, "", "None"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_quote_response(symbol: str, response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict) or response.get("s") != "ok":
        return None
    rows = response.get("d") or []
    if not rows:
        return None
    values = rows[0].get("v") or {}
    spot = _quote_value(values, "lp", "last_price", "ltp", "close_price")
    if spot is None:
        return None
    open_price = _quote_value(values, "open_price", "open", "o")
    prev_close = _quote_value(values, "prev_close_price", "prev_close", "pc")
    change = _quote_value(values, "ch", "change")
    pct = _quote_value(values, "chp", "percent_change", "pct")
    if change is None and prev_close is not None:
        change = spot - prev_close
    if pct is None and prev_close not in (None, 0):
        pct = (change if change is not None else spot - prev_close) / prev_close * 100
    mood = "Bullish" if (change or 0) >= 0 else "Bearish"
    if prev_close is not None and change is not None and change == 0:
        mood = "Neutral"
    return {
        "symbol": symbol,
        "spot": round(float(spot), 2),
        "change": round(float(change), 2) if change is not None else 0.0,
        "pct": round(float(pct), 2) if pct is not None else 0.0,
        "open": round(float(open_price), 2) if open_price is not None else round(float(spot), 2),
        "prev_close": round(float(prev_close), 2) if prev_close is not None else round(float(spot - (change or 0.0)), 2),
        "mood": mood,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "data_source": "LIVE",
    }


def _parse_depth_response(symbol: str, response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict) or response.get("s") != "ok":
        return None
    rows = response.get("d") or {}
    if not isinstance(rows, dict):
        return None
    values = rows.get(symbol) or next(iter(rows.values()), None)
    if not isinstance(values, dict):
        return None

    spot = _quote_value(values, "ltp", "last_price", "lp", "close_price")
    if spot is None:
        return None
    open_price = _quote_value(values, "o", "open_price", "open")
    prev_close = _quote_value(values, "c", "prev_close_price", "prev_close", "pc")
    change = _quote_value(values, "ch", "change")
    pct = _quote_value(values, "chp", "percent_change", "pct")
    if change is None and prev_close is not None:
        change = spot - prev_close
    if pct is None and prev_close not in (None, 0):
        pct = (change if change is not None else spot - prev_close) / prev_close * 100
    mood = "Bullish" if (change or 0) >= 0 else "Bearish"
    if prev_close is not None and change is not None and change == 0:
        mood = "Neutral"
    return {
        "symbol": symbol,
        "spot": round(float(spot), 2),
        "change": round(float(change), 2) if change is not None else 0.0,
        "pct": round(float(pct), 2) if pct is not None else 0.0,
        "open": round(float(open_price), 2) if open_price is not None else round(float(spot), 2),
        "prev_close": round(float(prev_close), 2) if prev_close is not None else round(float(spot - (change or 0.0)), 2),
        "mood": mood,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "data_source": "LIVE",
    }


def _parse_history_candles(response: Any) -> list[list[float]]:
    if not isinstance(response, dict):
        return []
    candles = response.get("candles") or response.get("data", {}).get("candles") or []
    parsed: list[list[float]] = []
    for candle in candles:
        if not isinstance(candle, (list, tuple)) or len(candle) < 6:
            continue
        ts = _safe_float(candle[0])
        o = _safe_float(candle[1])
        h = _safe_float(candle[2])
        l = _safe_float(candle[3])
        c = _safe_float(candle[4])
        v = _safe_float(candle[5]) or 0.0
        if None in (ts, o, h, l, c):
            continue
        parsed.append([ts, o, h, l, c, v])
    return parsed


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for prev, curr in zip(values[:-1], values[1:]):
        change = curr - prev
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    window = values[-period:]
    return round(sum(window) / period, 2)


def _atr(candles: list[list[float]], period: int = 10) -> list[float]:
    trs: list[float] = []
    prev_close: float | None = None
    for _, _, high, low, close, *_ in candles:
        if prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        prev_close = close
    if len(trs) < period:
        return []
    atrs: list[float] = [sum(trs[:period]) / period]
    for tr in trs[period:]:
        atrs.append(((atrs[-1] * (period - 1)) + tr) / period)
    return atrs


def _supertrend(candles: list[list[float]], period: int = 10, multiplier: float = 3.0) -> str | None:
    if len(candles) < period + 2:
        return None
    atr_values = _atr(candles, period)
    if not atr_values:
        return None
    hl2: list[float] = [round((high + low) / 2, 4) for _, _, high, low, *_ in candles]
    upper_band: list[float] = []
    lower_band: list[float] = []
    for idx in range(len(candles)):
        atr = atr_values[min(max(idx - (period - 1), 0), len(atr_values) - 1)]
        upper_band.append(hl2[idx] + multiplier * atr)
        lower_band.append(hl2[idx] - multiplier * atr)
    final_upper = upper_band[:]
    final_lower = lower_band[:]
    trend = [True] * len(candles)
    for i in range(1, len(candles)):
        close = candles[i][4]
        prev_close = candles[i - 1][4]
        if upper_band[i] < final_upper[i - 1] or prev_close > final_upper[i - 1]:
            final_upper[i] = upper_band[i]
        else:
            final_upper[i] = final_upper[i - 1]
        if lower_band[i] > final_lower[i - 1] or prev_close < final_lower[i - 1]:
            final_lower[i] = lower_band[i]
        else:
            final_lower[i] = final_lower[i - 1]
        if close > final_upper[i - 1]:
            trend[i] = True
        elif close < final_lower[i - 1]:
            trend[i] = False
        else:
            trend[i] = trend[i - 1]
            if trend[i] and final_lower[i] < final_lower[i - 1]:
                final_lower[i] = final_lower[i - 1]
            if not trend[i] and final_upper[i] > final_upper[i - 1]:
                final_upper[i] = final_upper[i - 1]
    return "BUY" if trend[-1] else "SELL"


def _load_symbol_master(url: str) -> list[list[str]]:
    cached = _SYMBOL_MASTER_CACHE.get(url)
    now = time.monotonic()
    if cached and (now - cached["cached_at"]) < _SYMBOL_MASTER_CACHE_TTL_SEC:
        return cached["rows"]
    with urlopen(url, timeout=20) as response:
        content = response.read().decode("utf-8", errors="ignore")
    rows = list(csv.reader(io.StringIO(content)))
    _SYMBOL_MASTER_CACHE[url] = {"cached_at": now, "rows": rows}
    return rows


def _resolve_future_symbol(index_symbol: str) -> str | None:
    master_url = CORE_FUTURE_MASTER_URLS.get(index_symbol.upper())
    if not master_url:
        return None
    rows = _load_symbol_master(master_url)
    now_epoch = time.time()
    candidates: list[tuple[float, str]] = []
    for row in rows:
        if len(row) < 10:
            continue
        name = row[1].upper().strip()
        symbol = row[9].strip()
        if not symbol.endswith("FUT"):
            continue
        if not name.startswith(f"{index_symbol.upper()} "):
            continue
        expiry = _safe_float(row[8]) or 0.0
        if expiry and expiry >= now_epoch:
            candidates.append((expiry, symbol))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _fetch_history_candles(client: Any, symbol: str, *, days: int = 240) -> list[list[float]]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    try:
        response = client.history({
            "symbol": symbol,
            "resolution": "D",
            "date_format": "1",
            "range_from": start.isoformat(),
            "range_to": end.isoformat(),
        })
    except Exception:
        return []
    return _parse_history_candles(response)


def _build_indicators(client: Any, index_symbol: str) -> dict[str, Any]:
    cached = _INDICATOR_CACHE.get(index_symbol)
    now = time.monotonic()
    if cached and (now - cached["cached_at"]) < _INDICATOR_CACHE_TTL_SEC:
        return dict(cached["value"])
    candles = _fetch_history_candles(client, index_symbol)
    if not candles:
        return {}
    closes = [row[4] for row in candles]
    volumes = [row[5] for row in candles]
    highs = [row[2] for row in candles]
    lows = [row[3] for row in candles]
    rsi = _rsi(closes)
    ma20 = _sma(closes, 20)
    ma50 = _sma(closes, 50)
    ma100 = _sma(closes, 100)
    ma200 = _sma(closes, 200)
    typical_prices = [((highs[i] + lows[i] + closes[i]) / 3) for i in range(len(closes))]
    cumulative_volume = 0.0
    cumulative_price_volume = 0.0
    for tp, volume in zip(typical_prices, volumes):
        cumulative_volume += volume
        cumulative_price_volume += tp * volume
    vwap = round(cumulative_price_volume / cumulative_volume, 2) if cumulative_volume else None
    supertrend = _supertrend(candles)
    result = {
        "rsi": rsi,
        "vwap": vwap,
        "supertrend": supertrend,
        "ma20": ma20,
        "ma50": ma50,
        "ma100": ma100,
        "ma200": ma200,
    }
    _INDICATOR_CACHE[index_symbol] = {"cached_at": now, "value": result}
    return dict(result)


def _candidate_symbols(symbol: str, item_type: str | None = None) -> list[str]:
    key = symbol.upper().strip()
    if key in CORE_SYMBOL_CANDIDATES:
        return CORE_SYMBOL_CANDIDATES[key]
    if ":" in key:
        return [key]
    if (item_type or "").upper() == "INDEX":
        patterns = INDEX_SYMBOL_CANDIDATES
    else:
        patterns = STOCK_SYMBOL_CANDIDATES
    return [pattern.format(symbol=key) for pattern in patterns]


def _fetch_symbol_quote(client: Any, symbol: str, item_type: str | None = None) -> dict[str, Any] | None:
    for candidate in _candidate_symbols(symbol, item_type):
        try:
            response = client.depth({"symbol": candidate, "ohlcv_flag": 1})
        except Exception:
            continue
        parsed = _parse_depth_response(symbol, response)
        if parsed is None:
            parsed = _parse_quote_response(symbol, response)
        if parsed:
            parsed["fyers_symbol"] = candidate
            return parsed
    return None


def _probe_cache_key(settings) -> str:
    token_path = settings.fyers_token_path.expanduser()
    try:
        stat = token_path.stat()
    except OSError:
        return f"{settings.fyers_client_id or ''}|{token_path.resolve()}|missing"
    return f"{settings.fyers_client_id or ''}|{token_path.resolve()}|{stat.st_mtime_ns}|{stat.st_size}"


def probe_fyers_connection() -> dict[str, Any]:
    global _PROBE_CACHE
    settings = get_settings()
    token_path = settings.fyers_token_path
    cache_key = _probe_cache_key(settings)
    cached = _PROBE_CACHE
    if cached and cached.get("cache_key") == cache_key and (time.monotonic() - cached.get("cached_at", 0)) < _PROBE_CACHE_TTL_SEC:
        return dict(cached["value"])

    result: dict[str, Any] = {
        "configured": False,
        "connected": False,
        "token_path": str(token_path.expanduser().resolve()),
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "checked_at_ist": datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).isoformat(),
        "error": None,
    }
    if not settings.fyers_client_id:
        result["error"] = "FYERS_CLIENT_ID is not configured"
        return result
    if not token_path.exists():
        result["error"] = "Token file not found"
        return result
    token = token_path.read_text(encoding="utf-8").strip()
    if not token:
        result["error"] = "Token file is empty"
        return result
    result["configured"] = True
    client = _get_client()
    if client is None:
        result["error"] = "FYERS client could not be created"
        return result

    try:
        profile = client.get_profile()
    except Exception as exc:
        result["error"] = f"FYERS profile check failed: {exc}"
        return result
    if not isinstance(profile, dict) or profile.get("s") != "ok":
        result["error"] = _parse_client_error(profile)
        _PROBE_CACHE = {"cache_key": cache_key, "cached_at": time.monotonic(), "value": result}
        return result

    result["connected"] = True
    result["profile"] = profile
    _PROBE_CACHE = {"cache_key": cache_key, "cached_at": time.monotonic(), "value": result}
    return result


def build_dashboard_market_data(*, core_symbols: list[str], fallback_map: dict[str, dict[str, Any]], watch_items: list[Any]) -> dict[str, Any]:
    client = _get_client()
    any_live = False
    core_markets: list[dict[str, Any]] = []
    watchlist: list[dict[str, Any]] = []
    checked_at_utc = datetime.now(timezone.utc)
    checked_at_ist = checked_at_utc.astimezone(ZoneInfo("Asia/Kolkata"))
    probe = probe_fyers_connection()

    for symbol in core_symbols:
        fallback = fallback_map.get(symbol, {"spot": 0, "change": 0, "pct": 0, "open": 0, "prev_close": 0, "mood": "Neutral"})
        row = {"symbol": symbol, **fallback, "data_source": "DEMO", "chart_symbol": TRADINGVIEW_SYMBOLS.get(symbol, symbol)}
        if probe["connected"] and client is not None:
            live = _fetch_symbol_quote(client, symbol, "INDEX")
            if live:
                row.update(live)
                any_live = True
            future_symbol = _resolve_future_symbol(symbol)
            if future_symbol:
                future_live = _fetch_symbol_quote(client, future_symbol, "INDEX")
                if future_live:
                    row["future"] = future_live["spot"]
                    row["future_symbol"] = future_symbol
                    row["future_chart_symbol"] = future_symbol
            indicator_symbol = (live or {}).get("fyers_symbol") or CORE_SYMBOL_CANDIDATES.get(symbol, [symbol])[0]
            row.update(_build_indicators(client, indicator_symbol))
        core_markets.append(row)

    for item in watch_items:
        fallback = fallback_map.get(item.symbol, {"spot": 0, "change": 0, "pct": 0, "open": 0, "prev_close": 0, "mood": "Neutral"})
        row = {"symbol": item.symbol, **fallback, "data_source": "DEMO", "chart_symbol": item.symbol}
        if probe["connected"] and client is not None and item.enabled:
            live = _fetch_symbol_quote(client, item.symbol, item.item_type)
            if live:
                row.update(live)
                any_live = True
        watchlist.append(row)

    return {
        "source": "LIVE" if any_live else "DEMO",
        "as_of": checked_at_utc.isoformat() if any_live else None,
        "checked_at_ist": checked_at_ist.isoformat(),
        "broker": probe,
        "core_markets": core_markets,
        "watchlist": watchlist,
    }
