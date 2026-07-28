# strategy/vwap_reversion.py
import pandas as pd
import numpy as np
from typing import Optional
from strategy.base import IStrategy
from data import utc_epoch_to_ist_dt, ist_now
from config import ORB_START_IST

class VWAPReversion(IStrategy):
    name = "vwap_reversion"

    def __init__(self, data_client, logger, index_symbol: str, band_k=2.0, lookback_min=120):
        self.dc = data_client
        self.log = logger
        self.symbol = index_symbol
        self.k = band_k
        self.lookback_min = lookback_min

    def _df_1m(self) -> pd.DataFrame:
        c = self.dc.get_1m_today(self.symbol)
        if not c:
            return pd.DataFrame()
        rows = [{"ts": utc_epoch_to_ist_dt(ts), "o": o, "h": h, "l": l, "c": cl, "v": v}
                for ts, o, h, l, cl, v in c]
        df = pd.DataFrame(rows)
        # enforce numeric dtypes (avoid object/NAType later)
        for col in ["o", "h", "l", "c", "v"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df[df['ts'].dt.time >= ORB_START_IST]
        cutoff = ist_now() - pd.Timedelta(minutes=self.lookback_min)
        df = df[df['ts'] >= cutoff]
        return df.dropna(subset=["o", "h", "l", "c", "v"])

    def _vwap_bands(self, df: pd.DataFrame):
        # Typical price
        tp = (df['h'] + df['l'] + df['c']) / 3.0
        # Cumulative price*vol and vol
        pv = (tp * df['v']).cumsum().astype(float)
        vv = df['v'].cumsum().astype(float)
        # Avoid division by zero using np.where -> float dtype
        with np.errstate(invalid="ignore", divide="ignore"):
            vwap = np.where(vv > 0, pv / vv, np.nan)
        vwap = pd.Series(vwap, index=df.index, dtype="float64").ffill()
        # Deviation of close from vwap (float)
        diff = (df['c'].astype(float) - vwap.astype(float))
        dev = diff.rolling(window=20, min_periods=10).std()
        upper = vwap + self.k * dev
        lower = vwap - self.k * dev
        return vwap, upper, lower

    def signal(self, idx_ltp: float, rsi_val: Optional[float]) -> Optional[str]:
        df = self._df_1m()
        if df.empty or len(df) < 40:
            return None
        vwap, ub, lb = self._vwap_bands(df)
        # ensure last values are numeric
        if any(pd.isna(x) for x in (ub.iloc[-1], lb.iloc[-1], vwap.iloc[-1])):
            return None

        last_c = float(df['c'].iloc[-1])
        last_ub = float(ub.iloc[-1])
        last_lb = float(lb.iloc[-1])

        # live LTP (fallback to last close)
        try:
            ltp = float(self.dc.get_ltp(self.symbol))
        except Exception:
            ltp = last_c

        # prefer neutral RSI for reversion
        if rsi_val is not None and (rsi_val < 40 or rsi_val > 60):
            return None

        # tag + reject logic using previous close vs band and current LTP back inside
        prev_close = float(df['c'].iloc[-2]) if len(df) >= 2 else last_c

        if prev_close <= last_lb and ltp > last_lb:
            self.log("STRAT_SIG", reason=f"VWAPR CE: prev<=LB {last_lb:.2f} & LTP {ltp:.2f}>LB")
            return "CE"

        if prev_close >= last_ub and ltp < last_ub:
            self.log("STRAT_SIG", reason=f"VWAPR PE: prev>=UB {last_ub:.2f} & LTP {ltp:.2f}<UB")
            return "PE"

        return None
