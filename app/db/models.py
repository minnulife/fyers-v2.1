from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Strategy(Base):
    __tablename__ = 'strategies'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), index=True)
    strategy_type: Mapped[str] = mapped_column(String(40), index=True)
    source: Mapped[str] = mapped_column(String(20), default='CUSTOM', index=True)
    index_symbol: Mapped[str] = mapped_column(String(40), index=True)
    side: Mapped[str] = mapped_column(String(8))
    execution_mode: Mapped[str] = mapped_column(String(20), default='PAPER')
    status: Mapped[str] = mapped_column(String(20), default='PENDING', index=True)
    trigger_description: Mapped[str] = mapped_column(String(255), default='Waiting for trigger')
    confirmation: Mapped[str] = mapped_column(String(30), default='INSTANT')
    validity: Mapped[str] = mapped_column(String(40), default='TODAY')
    trailing_mode: Mapped[str] = mapped_column(String(40), default='NONE')
    lots: Mapped[int] = mapped_column(Integer, default=1)
    entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    strike_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    strike_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    premium_tolerance: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Trade(Base):
    __tablename__ = 'trades'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    strategy_name: Mapped[str] = mapped_column(String(80), index=True)
    source: Mapped[str] = mapped_column(String(20), default='CUSTOM', index=True)
    index_symbol: Mapped[str] = mapped_column(String(40), index=True)
    instrument: Mapped[str] = mapped_column(String(80), index=True)
    mode: Mapped[str] = mapped_column(String(20), default='PAPER', index=True)
    status: Mapped[str] = mapped_column(String(20), default='OPEN', index=True)
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    exit_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AutomatedStrategy(Base):
    __tablename__ = 'automated_strategies'
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    paused_today: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default='DISABLED')
    mode: Mapped[str] = mapped_column(String(20), default='PAPER')
    indices_json: Mapped[str] = mapped_column(Text, default='["NIFTY"]')
    config_json: Mapped[str] = mapped_column(Text, default='{}')
    last_signal: Mapped[str] = mapped_column(String(255), default='No signal yet')
    last_trade: Mapped[str] = mapped_column(String(255), default='No trade yet')
    today_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    trades_today: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Setting(Base):
    __tablename__ = 'settings'
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    secret: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WatchItem(Base):
    __tablename__ = 'watch_items'
    symbol: Mapped[str] = mapped_column(String(40), primary_key=True)
    item_type: Mapped[str] = mapped_column(String(20), default='STOCK')
    is_core: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity: Mapped[str] = mapped_column(String(80), index=True)
    entity_id: Mapped[str] = mapped_column(String(80), default='')
    details_json: Mapped[str] = mapped_column(Text, default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
