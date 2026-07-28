from typing import Optional
import pandas as pd

def compute_rsi_from_1m(candles_df: pd.DataFrame, period=14, tf_min=5) -> Optional[float]:
    if candles_df is None or candles_df.empty:
        return None
    df = candles_df.copy()
    if "ts" not in df.columns:
        return None
    df.set_index("ts", inplace=True)
    # aggregate to tf_min
    o = df['o'].resample(f'{tf_min}min').first()
    h = df['h'].resample(f'{tf_min}min').max()
    l = df['l'].resample(f'{tf_min}min').min()
    c = df['c'].resample(f'{tf_min}min').last()
    agg = pd.DataFrame({'o': o, 'h': h, 'l': l, 'c': c}).dropna()
    if len(agg) < period + 5:
        return None
    delta = agg['c'].diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    if pd.isna(avg_gain.iloc[-1]) or pd.isna(avg_loss.iloc[-1]):
        return None
    if avg_loss.iloc[-1] == 0:
        return 100.0
    rs = avg_gain.iloc[-1] / avg_loss.iloc[-1]
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return float(max(0.0, min(100.0, rsi)))
