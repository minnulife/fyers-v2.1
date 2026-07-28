from uuid import uuid4
from app.brokers.base import Broker, OrderRequest, OrderResult


class PaperBroker(Broker):
    """Deterministic paper broker boundary. No external order API is called."""

    def place_order(self, order: OrderRequest) -> OrderResult:
        if order.quantity <= 0 or order.price <= 0:
            raise ValueError("Paper order requires positive quantity and price")
        side = order.side.upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        return OrderResult(
            order_id=f"PAPER-{uuid4().hex[:12].upper()}",
            status="FILLED",
            fill_price=round(order.price, 2),
            quantity=order.quantity,
        )
