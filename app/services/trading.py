from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.brokers.base import OrderRequest
from app.brokers.paper import PaperBroker
from app.db.models import Strategy, Trade


class TradingService:
    def __init__(self, db: Session):
        self.db = db
        self.broker = PaperBroker()

    def execute_strategy(self, strategy: Strategy, market_price: float, instrument: str) -> Trade:
        if strategy.execution_mode != "PAPER":
            raise RuntimeError("Only PAPER execution is supported")
        if strategy.status not in {"PENDING", "READY"}:
            raise ValueError(f"Strategy status {strategy.status} is not executable")
        result = self.broker.place_order(OrderRequest(
            instrument=instrument,
            side="BUY",
            quantity=max(strategy.lots, 1),
            price=market_price,
            strategy_id=strategy.id,
            strategy_name=strategy.name,
            index_symbol=strategy.index_symbol,
        ))
        trade = Trade(
            strategy_id=strategy.id,
            strategy_name=strategy.name,
            index_symbol=strategy.index_symbol,
            instrument=instrument,
            mode="PAPER",
            status="OPEN",
            side="BUY",
            quantity=result.quantity,
            entry_price=result.fill_price,
        )
        strategy.status = "EXECUTED"
        self.db.add(trade)
        self.db.commit()
        self.db.refresh(trade)
        return trade

    def close_trade(self, trade: Trade, exit_price: float, reason: str) -> Trade:
        if trade.status != "OPEN":
            raise ValueError("Only open trades can be closed")
        multiplier = 1 if trade.side == "BUY" else -1
        trade.exit_price = round(exit_price, 2)
        trade.pnl = round((trade.exit_price - trade.entry_price) * trade.quantity * multiplier, 2)
        trade.exit_reason = reason
        trade.status = "CLOSED"
        trade.closed_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(trade)
        return trade
