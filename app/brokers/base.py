from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class OrderRequest:
    instrument: str
    side: str
    quantity: int
    price: float
    strategy_id: int | None = None
    strategy_name: str = "Manual"
    index_symbol: str = "NIFTY"


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    status: str
    fill_price: float
    quantity: int


class Broker(ABC):
    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError
