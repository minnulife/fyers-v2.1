# strategy/supertrend_trend.py
import pandas as pd
from typing import Optional
from strategy.base import IStrategy
from data import utc_epoch_to_ist_dt
from config import ORB_START_IST, RSI_LONG_MIN, RSI_SHORT_MAX

def atr(df: pd.DataFrame, period=10):
    hl = df['h'] - df['l']
    hc = (df['h'] - df['c'].shift()).abs()
    lc = (df['l'] - df['c'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def supertrend(df: pd.DataFrame, period=10, multiplier=3.0):
    # expects columns: o,h,l,c, index TS
    _atr = atr(df, period)
    hl2 = (df['h'] + df['l']) / 2.0
    upper = hl2 + multiplier * _atr
    lower = hl2 - multiplier * _atr

    st = pd.Series(index=df.index, dtype=float)
    dir_up = True
    for i in range(len(df)):
        if i == 0:
            st.iloc[i] = upper.iloc[i]
            dir_up = df['c'].iloc[i] >= st.iloc[i]
            continue
        if dir_up:
            st.iloc[i] = min(upper.iloc[i], st.iloc[i-1])
            if df['c'].iloc[i] < st.iloc[i]:
                dir_up = False
                st.iloc[i] = lower.iloc[i]
        else:
            st.iloc[i] = max(lower.iloc[i], st.iloc[i-1])
            if df['c'].iloc[i] > st.iloc[i]:
                dir_up = True
                st.iloc[i] = upper.iloc[i]
    return st, dir_up  # last direction flag is not enough alone; use c vs st for signal

class SupertrendTrend(IStrategy):
    name = "supertrend_trend"

    def __init__(self, data_client, logger, index_symbol: str, period=10, multiplier=3.0, tf_min=5):
        self.dc = data_client
        self.log = logger
        self.symbol = index_symbol
        self.period = period
        self.multiplier = multiplier
        self.tf_min = tf_min

    def _df_5m(self) -> pd.DataFrame:
        c = self.dc.get_1m_today(self.symbol)
        if not c: return pd.DataFrame()
        rows = [{"ts": utc_epoch_to_ist_dt(ts), "o": o, "h": h, "l": l, "c": cl, "v": v} for ts,o,h,l,cl,v in c]
        df = pd.DataFrame(rows)
        df = df[df['ts'].dt.time >= ORB_START_IST]
        # 5m aggregate
        o = df['o'].resample(f'{self.tf_min}min', on='ts').first()
        h = df['h'].resample(f'{self.tf_min}min', on='ts').max()
        l = df['l'].resample(f'{self.tf_min}min', on='ts').min()
        cl = df['c'].resample(f'{self.tf_min}min', on='ts').last()
        out = pd.DataFrame({'o': o, 'h': h, 'l': l, 'c': cl}).dropna()
        return out

    def signal(self, idx_ltp: float, rsi_val: Optional[float]) -> Optional[str]:
        df5 = self._df_5m()
        if df5.empty or len(df5) < max(14, self.period+5):
            return None
        st, _ = supertrend(df5, period=self.period, multiplier=self.multiplier)
        last_st = st.iloc[-1]
        last_c = df5['c'].iloc[-1]
        # Trend-follow with RSI confirmation
        if rsi_val is None:
            return None
        if last_c > last_st and rsi_val > RSI_LONG_MIN:
            self.log("STRAT_SIG", reason=f"ST up: c>{last_st:.2f} RSI={rsi_val:.1f} -> CE")
            return "CE"
        if last_c < last_st and rsi_val < RSI_SHORT_MAX:
            self.log("STRAT_SIG", reason=f"ST down: c<{last_st:.2f} RSI={rsi_val:.1f} -> PE")
            return "PE"
        return None
