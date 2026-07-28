import pandas as pd
from typing import Optional, Tuple
from config import (SCALP_BB_PERIOD, SCALP_BB_STD, SCALP_RSI_MIN, SCALP_RSI_MAX,
                    SCALP_LOOKBACK_MIN, ORB_START_IST)
from data import utc_epoch_to_ist_dt, ist_now
from indicators import compute_rsi_from_1m

class BBScalp:
    """
    Bollinger Band mean-reversion scalper on INDEX 1m data.
    Buys CE when price rejects lower band in RSI range regime.
    Buys PE when price rejects upper band in RSI range regime.
    """
    def __init__(self, data_client, logger, index_symbol: str):
        self.dc = data_client
        self.log = logger
        self.index_symbol = index_symbol

    def _build_today_df(self) -> pd.DataFrame:
        c = self.dc.get_1m_today(self.index_symbol)
        rows = []
        for ts, o, h, l, cl, v in c:
            rows.append({"ts": utc_epoch_to_ist_dt(ts), "o": o, "h": h, "l": l, "c": cl, "v": v})
        df = pd.DataFrame(rows)
        return df

    def _recent_df(self) -> pd.DataFrame:
        df = self._build_today_df()
        if df.empty:
            return df
        # only post-open data
        df = df[df['ts'].dt.time >= ORB_START_IST]
        # limit to lookback window
        cutoff = ist_now() - pd.Timedelta(minutes=SCALP_LOOKBACK_MIN)
        df = df[df['ts'] >= cutoff]
        return df

    def _compute_bb(self, closes: pd.Series) -> Tuple[pd.Series, pd.Series, pd.Series]:
        ma = closes.rolling(SCALP_BB_PERIOD).mean()
        sd = closes.rolling(SCALP_BB_PERIOD).std()
        upper = ma + SCALP_BB_STD * sd
        lower = ma - SCALP_BB_STD * sd
        return ma, upper, lower

    def signal(self) -> Optional[str]:
        """
        Returns 'CE' / 'PE' / None based on last two candles:
        - CE: last closed candle <= lower band AND current price back above lower band, RSI in [RSI_MIN, RSI_MAX]
        - PE: last closed candle >= upper band AND current price back below upper band, RSI in [RSI_MIN, RSI_MAX]
        """
        df = self._recent_df()
        if df.empty or len(df) < SCALP_BB_PERIOD + 5:
            return None

        # Compute RSI on 1m (no aggregation for faster responsiveness)
        rsi = compute_rsi_from_1m(df[['ts','o','h','l','c','v']].copy(), period=14, tf_min=1)
        if rsi is None or not (SCALP_RSI_MIN <= rsi <= SCALP_RSI_MAX):
            return None  # avoid trending conditions

        closes = df['c']
        ma, upper, lower = self._compute_bb(closes)

        # Use last closed bar and current live price (ltp)
        last_idx = closes.index[-1]
        prev_idx = closes.index[-2] if len(closes) >= 2 else None
        if prev_idx is None:
            return None

        last_close = float(closes.loc[last_idx])
        prev_close = float(closes.loc[prev_idx])
        last_upper = float(upper.loc[last_idx]) if pd.notna(upper.loc[last_idx]) else None
        last_lower = float(lower.loc[last_idx]) if pd.notna(lower.loc[last_idx]) else None

        if last_upper is None or last_lower is None:
            return None

        # Live index LTP for 'rejection' confirmation
        try:
            ltp = self.dc.get_ltp(self.index_symbol)
        except Exception:
            return None

        # Mean-reversion “tag + reject”
        # Long scalp (CE): touched/closed at/below lower band, then ltp back above lower band
        if prev_close <= last_lower and ltp > last_lower:
            self.log("SCALP_SIG", reason=f"CE: prev_close={prev_close:.2f} <= LB={last_lower:.2f} & LTP {ltp:.2f} > LB; RSI={rsi:.1f}")
            return "CE"

        # Short scalp (PE): touched/closed at/above upper band, then ltp back below upper band
        if prev_close >= last_upper and ltp < last_upper:
            self.log("SCALP_SIG", reason=f"PE: prev_close={prev_close:.2f} >= UB={last_upper:.2f} & LTP {ltp:.2f} < UB; RSI={rsi:.1f}")
            return "PE"

        return None
