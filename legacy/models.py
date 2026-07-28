from dataclasses import dataclass, field
import datetime as dt
from typing import List, Tuple

@dataclass
class Position:
    symbol: str
    side: str                 # 'CE' or 'PE'
    entry_time: dt.datetime
    entry_price: float
    qty: int
    sl_price: float
    tp_price: float
    peak_price: float
    last_trail_level_hit: float = 0.0
    is_core: bool = True
    notes: str = ""
    history: List[Tuple[dt.datetime, float]] = field(default_factory=list)

    def record(self, ts: dt.datetime, ltp: float):
        self.history.append((ts, ltp))
        if ltp > self.peak_price:
            self.peak_price = ltp
