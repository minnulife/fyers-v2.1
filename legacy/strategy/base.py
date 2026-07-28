# strategy/base.py
from typing import Optional

class IStrategy:
    name: str = "base"
    def signal(self, idx_ltp: float, rsi_val: Optional[float]) -> Optional[str]:
        """Return 'CE'/'PE'/None based on current state."""
        raise NotImplementedError
