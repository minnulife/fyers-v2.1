from __future__ import annotations

import copy
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.db.models import WatchItem
from app.services.market_data import (
    DEFAULT_CORE_MARKETS,
    DEFAULT_MARKET_FALLBACKS,
    _candidate_symbols,
    _resolve_future_symbol,
    build_dashboard_market_data,
)


class DashboardLiveStream:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket = None
        self._started = False
        self._connected = False
        self._last_error: str | None = None
        self._last_tick_at_utc: datetime | None = None
        self._last_symbol_updated: str | None = None
        self._market_data: dict[str, Any] | None = None
        self._context_signature: tuple[Any, ...] | None = None
        self._symbol_targets: dict[str, dict[str, str]] = {}
        self._subscribed_symbols: set[str] = set()
        self._subscribers: set[queue.Queue] = set()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="fyers-live-stream", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        socket = None
        thread = None
        with self._lock:
            socket = self._socket
            self._socket = None
            thread = self._thread
        if socket is not None:
            try:
                socket.close_connection()
            except Exception:
                pass
        if thread and thread.is_alive():
            thread.join(timeout=5)

    def restart(self) -> None:
        with self._lock:
            self._market_data = None
            self._context_signature = None
            self._symbol_targets = {}
            self._subscribed_symbols = set()
        self._connected = False
        self._last_error = None
        self._last_tick_at_utc = None
        self._last_symbol_updated = None
        self.stop()
        with self._lock:
            self._stop.clear()
            self._started = False
        self.start()
        self.refresh_from_db(force=True)

    def refresh_from_db(self, *, force: bool = False) -> dict[str, Any]:
        with SessionLocal() as db:
            watches = db.scalars(select(WatchItem).where(WatchItem.enabled == True)).all()  # noqa: E712
        return self.refresh(
            core_symbols=DEFAULT_CORE_MARKETS,
            fallback_map=DEFAULT_MARKET_FALLBACKS,
            watch_items=watches,
            force=force,
        )

    def refresh(
        self,
        *,
        core_symbols: list[str],
        fallback_map: dict[str, dict[str, Any]],
        watch_items: list[Any],
        force: bool = False,
    ) -> dict[str, Any]:
        signature = self._build_signature(core_symbols, watch_items)
        with self._lock:
            if not force and self._market_data is not None and self._context_signature == signature:
                return copy.deepcopy(self._market_data)

        market_data = build_dashboard_market_data(
            core_symbols=core_symbols,
            fallback_map=fallback_map,
            watch_items=watch_items,
        )

        symbol_targets = self._build_symbol_targets(core_symbols, watch_items, market_data)
        with self._lock:
            self._context_signature = signature
            self._market_data = copy.deepcopy(market_data)
            self._symbol_targets = symbol_targets
            self._subscribed_symbols = set(self._subscribed_symbols.intersection(symbol_targets))
            self._connected = self._connected or bool((market_data.get("broker") or {}).get("connected"))
            self._last_error = (market_data.get("broker") or {}).get("error")
        self._broadcast_snapshot()
        self._subscribe_missing_symbols()
        return copy.deepcopy(market_data)

    def get_market_data(
        self,
        *,
        core_symbols: list[str],
        fallback_map: dict[str, dict[str, Any]],
        watch_items: list[Any],
    ) -> dict[str, Any]:
        with self._lock:
            if self._market_data is not None and self._context_signature == self._build_signature(core_symbols, watch_items):
                return copy.deepcopy(self._market_data)
        return self.refresh(core_symbols=core_symbols, fallback_map=fallback_map, watch_items=watch_items)

    def subscribe_client(self, client_queue: queue.Queue) -> None:
        with self._lock:
            self._subscribers.add(client_queue)
            snapshot = copy.deepcopy(self._market_data) if self._market_data is not None else None
        if snapshot is not None:
            self._put_queue(client_queue, {"type": "market_data", "data": snapshot})

    def unsubscribe_client(self, client_queue: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(client_queue)

    def current_state(self) -> dict[str, Any]:
        with self._lock:
            snapshot = copy.deepcopy(self._market_data) if self._market_data is not None else None
            return {
                "connected": self._connected,
                "error": self._last_error,
                "last_tick_at_utc": self._last_tick_at_utc.isoformat() if self._last_tick_at_utc else None,
                "last_tick_at_ist": self._last_tick_at_utc.astimezone(ZoneInfo("Asia/Kolkata")).isoformat() if self._last_tick_at_utc else None,
                "last_symbol_updated": self._last_symbol_updated,
                "stale_seconds": round((datetime.now(timezone.utc) - self._last_tick_at_utc).total_seconds(), 1) if self._last_tick_at_utc else None,
                "market_data": snapshot,
            }

    def _build_signature(self, core_symbols: list[str], watch_items: list[Any]) -> tuple[Any, ...]:
        return (
            tuple(s.upper().strip() for s in core_symbols),
            tuple(
                (
                    getattr(item, "symbol", "").upper().strip(),
                    getattr(item, "item_type", "STOCK").upper().strip(),
                    bool(getattr(item, "enabled", True)),
                )
                for item in sorted(watch_items, key=lambda item: getattr(item, "symbol", "").upper().strip())
            ),
        )

    def _build_symbol_targets(
        self,
        core_symbols: list[str],
        watch_items: list[Any],
        market_data: dict[str, Any],
    ) -> dict[str, dict[str, str]]:
        targets: dict[str, dict[str, str]] = {}
        core_rows = {row.get("symbol"): row for row in market_data.get("core_markets", []) if isinstance(row, dict)}
        watch_rows = {row.get("symbol"): row for row in market_data.get("watchlist", []) if isinstance(row, dict)}

        for symbol in core_symbols:
            row = core_rows.get(symbol, {})
            live_symbol = row.get("fyers_symbol")
            candidates = [live_symbol] if live_symbol else _candidate_symbols(symbol, "INDEX")
            for candidate in candidates:
                if candidate:
                    targets[candidate] = {"row_symbol": symbol, "group": "core", "field": "spot"}
            future_symbol = row.get("future_symbol") or _resolve_future_symbol(symbol)
            if future_symbol:
                targets[future_symbol] = {"row_symbol": symbol, "group": "core", "field": "future"}

        for item in watch_items:
            if not getattr(item, "enabled", True):
                continue
            symbol = getattr(item, "symbol", "").upper().strip()
            if not symbol:
                continue
            row = watch_rows.get(symbol, {})
            live_symbol = row.get("fyers_symbol")
            candidates = [live_symbol] if live_symbol else _candidate_symbols(symbol, getattr(item, "item_type", "STOCK"))
            for candidate in candidates:
                if candidate:
                    targets[candidate] = {"row_symbol": symbol, "group": "watch", "field": "spot"}
        return targets

    def _create_socket(self):
        settings = get_settings()
        if not settings.fyers_client_id or not settings.fyers_token_path.exists():
            return None
        token = settings.fyers_token_path.read_text(encoding="utf-8").strip()
        if not token:
            return None
        try:
            from fyers_apiv3.FyersWebsocket.data_ws import FyersDataSocket
        except Exception as exc:
            self._set_error(f"FYERS websocket unavailable: {exc}")
            return None

        socket = FyersDataSocket(
            access_token=f"{settings.fyers_client_id}:{token}",
            log_path="",
            write_to_file=False,
            litemode=False,
            reconnect=True,
            on_message=self._on_message,
            on_error=self._on_error,
            on_connect=self._on_connect,
            on_close=self._on_close,
        )
        return socket

    def _run(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            socket = self._create_socket()
            if socket is None:
                self._set_connected(False)
                self._sleep(backoff)
                backoff = min(backoff + 2, 15)
                continue
            with self._lock:
                self._socket = socket
            try:
                socket.connect()
                backoff = 2
                while not self._stop.is_set() and socket.is_connected():
                    self._sleep(1)
            except Exception as exc:
                self._set_error(f"FYERS websocket failed: {exc}")
            finally:
                self._set_connected(False)
                with self._lock:
                    if self._socket is socket:
                        self._socket = None
                try:
                    socket.close_connection()
                except Exception:
                    pass
                self._sleep(backoff)
                backoff = min(backoff + 2, 15)

    def _on_connect(self) -> None:
        self._set_connected(True)
        self._last_error = None
        self._subscribe_missing_symbols()

    def _on_error(self, message: Any) -> None:
        self._set_error(self._stringify_error(message))

    def _on_close(self, message: Any) -> None:
        self._set_connected(False)
        self._set_error(self._stringify_error(message))

    def _on_message(self, message: Any) -> None:
        if not isinstance(message, dict):
            return
        symbol = str(message.get("symbol") or "").upper().strip()
        if not symbol:
            return
        with self._lock:
            if self._market_data is None:
                return
            target = self._symbol_targets.get(symbol)
            if not target:
                return
            row_symbol = target["row_symbol"]
            row = self._find_row(target["group"], row_symbol)
            if row is None:
                return
            self._update_row_from_message(row, target["field"], message)
            self._market_data["source"] = "LIVE"
            self._market_data["as_of"] = datetime.now(timezone.utc).isoformat()
            self._market_data["checked_at_ist"] = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).isoformat()
            self._last_tick_at_utc = datetime.now(timezone.utc)
            self._last_symbol_updated = row_symbol
            broker = dict(self._market_data.get("broker") or {})
            broker["connected"] = self._connected
            broker["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
            broker["checked_at_ist"] = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).isoformat()
            broker["error"] = self._last_error
            self._market_data["broker"] = broker
            snapshot = copy.deepcopy(self._market_data)
        self._broadcast({"type": "market_data", "data": snapshot})

    def _find_row(self, group: str, row_symbol: str) -> dict[str, Any] | None:
        rows = self._market_data.get("core_markets" if group == "core" else "watchlist", []) if self._market_data else []
        for row in rows:
            if isinstance(row, dict) and row.get("symbol") == row_symbol:
                return row
        return None

    def _update_row_from_message(self, row: dict[str, Any], field: str, message: dict[str, Any]) -> None:
        spot = self._first_float(message, "ltp", "last_price", "lp", "close_price")
        open_price = self._first_float(message, "open_price", "open", "o")
        prev_close = self._first_float(message, "prev_close_price", "prev_close", "pc")
        change = self._first_float(message, "ch", "change")
        pct = self._first_float(message, "chp", "pct", "percent_change")

        if spot is not None:
            if field == "future":
                row["future"] = round(float(spot), 2)
                row["future_symbol"] = message.get("symbol")
                row["future_chart_symbol"] = message.get("symbol")
            else:
                row["spot"] = round(float(spot), 2)
                row["fyers_symbol"] = message.get("symbol")
                row["data_source"] = "LIVE"
                if open_price is not None:
                    row["open"] = round(float(open_price), 2)
                if prev_close is not None:
                    row["prev_close"] = round(float(prev_close), 2)
                if change is None and prev_close not in (None, 0):
                    change = float(spot) - float(prev_close)
                if pct is None and prev_close not in (None, 0):
                    pct = (float(change) if change is not None else float(spot) - float(prev_close)) / float(prev_close) * 100
                row["change"] = round(float(change), 2) if change is not None else row.get("change", 0.0)
                row["pct"] = round(float(pct), 2) if pct is not None else row.get("pct", 0.0)
                row["mood"] = self._mood_from_change(row.get("change"))
                row["chart_symbol"] = row.get("chart_symbol") or message.get("symbol")

    def _subscribe_missing_symbols(self) -> None:
        with self._lock:
            socket = self._socket
            if socket is None or not self._symbol_targets:
                return
            symbols_to_subscribe = sorted(symbol for symbol in self._symbol_targets if symbol not in self._subscribed_symbols)
            if not symbols_to_subscribe:
                return
            self._subscribed_symbols.update(symbols_to_subscribe)
        try:
            socket.subscribe(symbols_to_subscribe, data_type="SymbolUpdate", channel=11)
        except Exception as exc:
            self._set_error(f"FYERS subscribe failed: {exc}")

    def _broadcast_snapshot(self) -> None:
        with self._lock:
            if self._market_data is None:
                return
            payload = {"type": "market_data", "data": copy.deepcopy(self._market_data)}
        self._broadcast(payload)

    def _broadcast(self, payload: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            self._put_queue(subscriber, payload)

    def _put_queue(self, subscriber: queue.Queue, payload: dict[str, Any]) -> None:
        try:
            subscriber.put_nowait(copy.deepcopy(payload))
        except queue.Full:
            try:
                subscriber.get_nowait()
                subscriber.put_nowait(copy.deepcopy(payload))
            except Exception:
                with self._lock:
                    self._subscribers.discard(subscriber)
        except Exception:
            with self._lock:
                self._subscribers.discard(subscriber)

    def _set_connected(self, connected: bool) -> None:
        with self._lock:
            self._connected = connected
            if self._market_data is not None:
                broker = dict(self._market_data.get("broker") or {})
                broker["connected"] = connected
                broker["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
                broker["checked_at_ist"] = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).isoformat()
                if not connected and self._last_error:
                    broker["error"] = self._last_error
                self._market_data["broker"] = broker

    def _set_error(self, error: str | None) -> None:
        with self._lock:
            self._last_error = error
            if self._market_data is not None and error:
                broker = dict(self._market_data.get("broker") or {})
                broker["error"] = error
                broker["connected"] = self._connected
                broker["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
                broker["checked_at_ist"] = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).isoformat()
                self._market_data["broker"] = broker

    def _sleep(self, seconds: int | float) -> None:
        self._stop.wait(timeout=seconds)

    def _stringify_error(self, message: Any) -> str | None:
        if message in (None, ""):
            return None
        if isinstance(message, dict):
            code = message.get("code")
            text = message.get("message") or message.get("msg") or message.get("error") or str(message)
            return f"FYERS websocket {code}: {text}" if code is not None else f"FYERS websocket: {text}"
        return str(message)

    def _first_float(self, message: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            value = message.get(key)
            if value in (None, "", "None"):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def _mood_from_change(self, change: Any) -> str:
        try:
            value = float(change)
        except (TypeError, ValueError):
            return "Neutral"
        if value > 0:
            return "Bullish"
        if value < 0:
            return "Bearish"
        return "Neutral"


dashboard_live_stream = DashboardLiveStream()
