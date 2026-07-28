import pandas as pd
from typing import Optional
from config import ORB_START_IST, ORB_END_IST, ENTRY_BUFFER_PCT
from config import USE_RSI, RSI_PERIOD, RSI_TIMEFRAME_MIN, RSI_LONG_MIN, RSI_SHORT_MAX
from data import utc_epoch_to_ist_dt
from indicators import compute_rsi_from_1m

class ORBStrategy:
    def __init__(self, data_client, logger):
        self.dc = data_client
        self.log = logger
        self.or_high: Optional[float] = None
        self.or_low: Optional[float] = None
        self.entry_hi_buf: Optional[float] = None
        self.entry_lo_buf: Optional[float] = None
        self.long_armed = True
        self.short_armed = True

    def compute_orb(self, one_min_candles: list) -> Optional[float]:
        rows = []
        for ts, o, h, l, cl, v in one_min_candles:
            t_ist = utc_epoch_to_ist_dt(ts)
            rows.append({"ts": t_ist, "o": o, "h": h, "l": l, "c": cl, "v": v})
        df = pd.DataFrame(rows)
        mask = (df['ts'].dt.time >= ORB_START_IST) & (df['ts'].dt.time < ORB_END_IST)
        or_df = df.loc[mask]
        if or_df.empty:
            raise RuntimeError("No ORB window candles found.")
        self.or_high = float(or_df['h'].max())
        self.or_low  = float(or_df['l'].min())
        self.entry_hi_buf = self.or_high * (1 + ENTRY_BUFFER_PCT/100.0)
        self.entry_lo_buf = self.or_low  * (1 - ENTRY_BUFFER_PCT/100.0)

        rsi_val = None
        if USE_RSI:
            post_open = df[df['ts'].dt.time >= ORB_START_IST]
            rsi_val = compute_rsi_from_1m(post_open, period=RSI_PERIOD, tf_min=RSI_TIMEFRAME_MIN)

        self.log("ORB_LEVELS", reason=f"ORH={self.or_high:.2f} ORL={self.or_low:.2f} RSI={rsi_val if rsi_val is not None else 'NA'}")
        return rsi_val

    def rsi_allows(self, direction: str, rsi_val: Optional[float]) -> bool:
        if rsi_val is None:
            return False  # block if RSI not ready
        if not USE_RSI or rsi_val is None:
            return True
        if direction == "UP":
            return rsi_val > RSI_LONG_MIN
        if direction == "DOWN":
            return rsi_val < RSI_SHORT_MAX
        return True
