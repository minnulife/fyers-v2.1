# engine.py
import time
import datetime as dt
from typing import Optional, List

import pandas as pd

from config import (
    # IDs / symbols / session
    INDEX_SYMBOL, IST,
    ORB_START_IST, ORB_END_IST, SQUARE_OFF_IST,
    START_IMMEDIATELY, USE_YDAY_WHEN_TODAY_EMPTY,

    # Trading & risk
    LOT_SIZE, COOLDOWN_SEC, MAX_CONCURRENT_POS,
    ALLOW_OPPOSITE_IF_SAFE, MAX_DAILY_LOSS_INR, COST_PER_SIDE_INR,
    INIT_SL_PCT, INIT_TP_PCT, TRAIL_STEPS, DD_HARD_DROP_PCT,
    TIME_BASED_EXIT_MIN, MOMENTUM_FAST_MIN, SLOW_PROFIT_PCT, REDUCED_TP_PCT,
    USE_PROJECTED_RISK_BLOCK,

    # RSI
    USE_RSI, RSI_PERIOD, RSI_TIMEFRAME_MIN, RSI_LONG_MIN, RSI_SHORT_MAX,

    # Re-entry guards
    PREVENT_DUPLICATE_SIDE, REARM_ON_PULLBACK, REARM_PULLBACK_PCT, REARM_USING_OR_BAND,

    # Snapshots/diagnostics
    SNAPSHOT_INTERVAL_SEC, ENABLE_DIAGNOSTICS, ENABLE_MOMENTUM_LOGS, RSI_HYSTERESIS,
)

# ---- optional config fallbacks (if not added to config.py yet) ----
try:
    from config import DIAG_INTERVAL_SEC
except Exception:
    DIAG_INTERVAL_SEC = 30
try:
    from config import DIAG_ONLY_ON_CHANGE
except Exception:
    DIAG_ONLY_ON_CHANGE = True
try:
    from config import MIN_PEAK_GAIN_BEFORE_DD_PCT
except Exception:
    MIN_PEAK_GAIN_BEFORE_DD_PCT = 5.0  # require +5% over entry before DD exit logic
try:
    from config import CORE_REARM_MIN_SECS
except Exception:
    CORE_REARM_MIN_SECS = 120  # optional time-based re-arm floor for core

try:
    from config import CORE_DD_HARD_DROP_PCT, SCALP_DD_HARD_DROP_PCT
except Exception:
    CORE_DD_HARD_DROP_PCT, SCALP_DD_HARD_DROP_PCT = 10.0, 8.0
try:
    from config import CORE_MIN_PEAK_GAIN_BEFORE_DD_PCT, SCALP_MIN_PEAK_GAIN_BEFORE_DD_PCT
except Exception:
    CORE_MIN_PEAK_GAIN_BEFORE_DD_PCT, SCALP_MIN_PEAK_GAIN_BEFORE_DD_PCT = 12.0, 6.0
try:
    from config import BREAKEVEN_AT_PROFIT_PCT, BREAKEVEN_OFFSET_PCT
except Exception:
    BREAKEVEN_AT_PROFIT_PCT, BREAKEVEN_OFFSET_PCT = 10.0, 0.5
try:
    from config import SCALP_MAX_OPEN, SCALP_MAX_PER_SIDE, SCALP_ENTRY_MIN_GAP_SEC
except Exception:
    SCALP_MAX_OPEN, SCALP_MAX_PER_SIDE, SCALP_ENTRY_MIN_GAP_SEC = 1, 1, 180



# BB Scalp knobs
from config import (
    SCALP_ENABLED, SCALP_TP_PCT, SCALP_SL_PCT, SCALP_MAX_HOLD_MIN, SCALP_COOLDOWN_SEC
)

from models import Position
from summary import summarize
from logging_utils import init_csv, logger_row as log, ist_now as now_ist
from data import DataClient, utc_epoch_to_ist_dt
from indicators import compute_rsi_from_1m
from strategy.orb import ORBStrategy
from strategy.bb_scalp import BBScalp
from strategy.supertrend_trend import SupertrendTrend
from strategy.vwap_reversion import VWAPReversion

class Engine:
    def __init__(self, fyers):
        init_csv()

        self.fyers = fyers
        self.dc = DataClient(fyers, log)
        self.orb = ORBStrategy(self.dc, log)

        self.positions: List[Position] = []
        self.realized_pnl = 0.0
        self.cooldown_until: Optional[dt.datetime] = None

        # EoD stats
        self.trades = []
        self.equity = 0.0
        self.equity_peak = 0.0
        self.max_drawdown = 0.0

        # Snapshot / diagnostics state
        self.last_snapshot_ts: Optional[dt.datetime] = None
        self._last_rsi_refresh_minute: Optional[dt.datetime] = None
        self.last_rsi_regime: Optional[str] = None  # 'bull' | 'bear' | 'neutral' | 'unknown'
        self.last_price_zone: Optional[str] = None  # 'above_hi' | 'inside_or' | 'below_lo'
        self._last_diag_ts: Optional[dt.datetime] = None
        self._last_diag_reasons = {"CE": None, "PE": None}

        # BB-Scalp strategy + its cooldown
        self.bb_scalp = BBScalp(self.dc, log, INDEX_SYMBOL)
        self.scalp_cooldown_until: Optional[dt.datetime] = None

        # Optional time-based re-arm tracker (in addition to your pullback/OR-band logic)
        self._last_core_entry_time = {"CE": None, "PE": None}

        # Track last scalp entry times
        self.last_scalp_entry_ts: Optional[dt.datetime] = None
        self.last_scalp_entry_ts_by_side = {"CE": None, "PE": None}

        self.strats = [
            SupertrendTrend(self.dc, log, INDEX_SYMBOL, period=10, multiplier=3.0, tf_min=5),
            VWAPReversion(self.dc, log, INDEX_SYMBOL, band_k=2.0, lookback_min=120),
        ]

        # ---- Auth check (tolerant) ----
        prof = {}
        prof_ok = False
        try:
            prof = self.fyers.get_profile()
            prof_ok = isinstance(prof, dict) and prof.get("s") == "ok"
        except Exception as e:
            prof = {"s": "error", "message": f"exception: {e}"}

        q = self.fyers.quotes({"symbols": INDEX_SYMBOL})
        quotes_ok = isinstance(q, dict) and q.get("s") == "ok"

        if quotes_ok:
            if prof_ok:
                log("AUTH_OK", reason="Profile & quotes succeeded", day_pnl=self.realized_pnl)
            else:
                log("AUTH_WARN", reason=f"Profile failed but quotes OK: {prof}", day_pnl=self.realized_pnl)
        else:
            raise RuntimeError(f"Auth/quotes failed: prof={prof} quotes={q}")

        # ---- Session header: previous close ----
        prev_date, prev_close = self.dc.get_prev_trading_close_strict(INDEX_SYMBOL)
        log(
            "SESSION_START",
            reason=f"Today={now_ist().date().isoformat()} PrevCloseDate={prev_date or 'NA'} "
                   f"PrevNIFTYClose={f'{prev_close:.2f}' if prev_close is not None else 'NA'}",
            day_pnl=self.realized_pnl
        )

    # ============ Helpers / position ops ============

    def log_pos_state(self, pos: Position, ltp: float, tag: str, extra: str = ""):
        snap = f"EP={pos.entry_price:.2f} CP={ltp:.2f} SL={pos.sl_price:.2f} TP={pos.tp_price:.2f}"
        if extra:
            snap += f" | {extra}"
        log(tag, symbol=pos.symbol, side=pos.side, price=ltp, qty=pos.qty, reason=snap, day_pnl=self.realized_pnl)

    def create_position(self, side: str, is_core=True, note=""):
        symbol = self.dc.pick_atm_symbol(side)
        ltp = self.dc.get_ltp(symbol)
        entry = ltp
        sl = entry * (1 - INIT_SL_PCT / 100.0)
        tp = entry * (1 + INIT_TP_PCT / 100.0)
        pos = Position(
            symbol=symbol, side=side, entry_time=now_ist(),
            entry_price=entry, qty=LOT_SIZE, sl_price=sl, tp_price=tp,
            peak_price=entry, is_core=is_core, notes=note
        )
        self.positions.append(pos)
        log("ENTER", symbol=symbol, side=side, price=entry, qty=LOT_SIZE,
            reason=f"New {'CORE' if is_core else 'SCALP'}", day_pnl=self.realized_pnl)
        self.log_pos_state(pos, ltp, tag="ENTER_STATE")
        if is_core:
            self._last_core_entry_time[side] = now_ist()

    def create_scalp_position(self, side: str):
        symbol = self.dc.pick_atm_symbol(side)
        ltp = self.dc.get_ltp(symbol)
        entry = ltp
        sl = entry * (1 - SCALP_SL_PCT / 100.0)
        tp = entry * (1 + SCALP_TP_PCT / 100.0)
        pos = Position(
            symbol=symbol, side=side, entry_time=now_ist(),
            entry_price=entry, qty=LOT_SIZE, sl_price=sl, tp_price=tp,
            peak_price=entry, is_core=False, notes="SCALP"
        )
        self.positions.append(pos)
        log("ENTER", symbol=symbol, side=side, price=entry, qty=LOT_SIZE,
            reason="New SCALP", day_pnl=self.realized_pnl)
        self.log_pos_state(pos, ltp, tag="ENTER_STATE")
        # stamp scalp entry times
        now = now_ist()
        self.last_scalp_entry_ts = now
        self.last_scalp_entry_ts_by_side[side] = now

    def exit_position(self, pos: Position, reason: str):
        exit_time = now_ist()
        ltp = self.dc.get_ltp(pos.symbol)
        self.log_pos_state(pos, ltp, tag="EXIT_STATE", extra=f"reason={reason}")

        pnl = (ltp - pos.entry_price) * pos.qty
        pnl -= 2 * COST_PER_SIDE_INR
        self.realized_pnl += pnl
        self.positions.remove(pos)

        # Cooldowns
        self.cooldown_until = exit_time + dt.timedelta(seconds=COOLDOWN_SEC)
        if not pos.is_core:
            self.scalp_cooldown_until = exit_time + dt.timedelta(seconds=SCALP_COOLDOWN_SEC)

        log("EXIT", symbol=pos.symbol, side=pos.side, price=ltp, qty=pos.qty,
            reason=reason, pnl=pnl, day_pnl=self.realized_pnl)

        # EoD tracking
        hold_min = (exit_time - pos.entry_time).total_seconds() / 60.0
        self.trades.append({
            "pnl": pnl, "side": pos.side, "core": pos.is_core, "reason": reason,
            "hold_min": hold_min, "entry_time": pos.entry_time, "exit_time": exit_time,
            "symbol": pos.symbol
        })
        self.equity += pnl
        if self.equity > self.equity_peak:
            self.equity_peak = self.equity
        dd = self.equity_peak - self.equity
        if dd > self.max_drawdown:
            self.max_drawdown = dd

    def has_open_core_side(self, side: str) -> bool:
        return any(p.side == side and p.is_core for p in self.positions)

    def first_position_safe(self) -> bool:
        return any(p.sl_price >= p.entry_price for p in self.positions)

    # ---- risk gate using provided sl% (core vs scalp) ----
    def can_new_entry_with_sl(self, est_entry_price: Optional[float], sl_pct: float) -> bool:
        # Never allow an entry when the option quote used for risk estimation failed.
        if est_entry_price is None or est_entry_price <= 0:
            return False
        if self.realized_pnl <= -MAX_DAILY_LOSS_INR:
            return False
        if len(self.positions) >= MAX_CONCURRENT_POS:
            return False
        if self.cooldown_until and now_ist() < self.cooldown_until:
            return False
        if USE_PROJECTED_RISK_BLOCK:
            sl_price = est_entry_price * (1 - sl_pct / 100.0)
            risk = (est_entry_price - sl_price) * LOT_SIZE + 2 * COST_PER_SIDE_INR
            projected = self.realized_pnl - risk
            if projected <= -MAX_DAILY_LOSS_INR:
                return False
        return True

    # Backward-compatible wrapper
    def can_new_entry(self, est_entry_price: Optional[float]) -> bool:
        return self.can_new_entry_with_sl(est_entry_price, INIT_SL_PCT)

    # Add a guard: can we open a scalp right now
    def can_open_scalp(self, side: str) -> bool:
        # cap total open scalps
        open_scalps = [p for p in self.positions if not p.is_core]
        if len(open_scalps) >= SCALP_MAX_OPEN:
            return False
        # cap per side
        if SCALP_MAX_PER_SIDE > 0 and any((p.side == side and not p.is_core) for p in open_scalps):
            return False
        # min gap between any two scalp entries
        now = now_ist()
        if self.last_scalp_entry_ts and (now - self.last_scalp_entry_ts).total_seconds() < SCALP_ENTRY_MIN_GAP_SEC:
            return False
        # min gap per side
        last_side_ts = self.last_scalp_entry_ts_by_side.get(side)
        if last_side_ts and (now - last_side_ts).total_seconds() < SCALP_ENTRY_MIN_GAP_SEC:
            return False
        return True

    # =========== simple regime detection + router ===========
    def detect_regime(self, idx_ltp: float, rsi_val: Optional[float]) -> str:
        # use ORB buffers + RSI to classify
        if rsi_val is None:
            return "unknown"
        # trend if far outside OR buffers
        hi_buf = getattr(self.orb, "entry_hi_buf", None)
        lo_buf = getattr(self.orb, "entry_lo_buf", None)
        if hi_buf and idx_ltp > hi_buf and rsi_val > 55:
            return "trend_up"
        if lo_buf and idx_ltp < lo_buf and rsi_val < 45:
            return "trend_down"
        return "range"

    def pick_secondary_signal(self, idx_ltp: float, rsi_val: Optional[float]) -> Optional[str]:
        regime = self.detect_regime(idx_ltp, rsi_val)
        # route: trend → Supertrend; range → VWAP reversion
        ordered = []
        if regime == "trend_up" or regime == "trend_down":
            ordered = [s for s in self.strats if s.name == "supertrend_trend"] + [s for s in self.strats if
                                                                                  s.name != "supertrend_trend"]
        else:
            ordered = [s for s in self.strats if s.name == "vwap_reversion"] + [s for s in self.strats if
                                                                                s.name != "vwap_reversion"]

        for s in ordered:
            sig = s.signal(idx_ltp, rsi_val)
            if sig:
                self.maybe_log_momentum_price_changes(idx_ltp, rsi_val)  # context
                self.log_signal_diagnostics(idx_ltp, rsi_val, force=True)  # explain rejections too
                return sig
        return None

    # ============ Position management ============

    def trail_sl(self, pos: Position, ltp: float):
        profit_pct = (ltp - pos.entry_price) * 100.0 / pos.entry_price

        # 1) Move SL to (near) breakeven once we have cushion
        if BREAKEVEN_AT_PROFIT_PCT is not None and profit_pct >= BREAKEVEN_AT_PROFIT_PCT:
            be = pos.entry_price * (1 + BREAKEVEN_OFFSET_PCT / 100.0)
            if pos.sl_price < be:
                old = pos.sl_price
                pos.sl_price = be
                log("SL_TO_BE", symbol=pos.symbol, side=pos.side, price=ltp,
                    reason=f"Profit {profit_pct:.1f}% -> SL {old:.2f} -> {pos.sl_price:.2f}",
                    day_pnl=self.realized_pnl)
                self.log_pos_state(pos, ltp, tag="SL_UPDATE", extra="breakeven")

        # 2) Then apply step trailing (as before)
        for level, sl_from_entry_pct in sorted(TRAIL_STEPS, key=lambda x: x[0]):
            if profit_pct >= level and pos.last_trail_level_hit < level:
                new_sl = pos.entry_price * (1 + sl_from_entry_pct / 100.0)
                if new_sl > pos.sl_price:
                    old = pos.sl_price
                    pos.sl_price = new_sl
                    pos.last_trail_level_hit = level
                    log("TRAIL_SL", symbol=pos.symbol, side=pos.side, price=ltp,
                        reason=f"Profit {profit_pct:.1f}% -> SL {old:.2f} -> {pos.sl_price:.2f}",
                        day_pnl=self.realized_pnl)
                    self.log_pos_state(pos, ltp, tag="SL_UPDATE", extra=f"level={level}")

    def dd_exit(self, pos: Position, ltp: float) -> bool:
        # separate cushions/thresholds
        min_gain = CORE_MIN_PEAK_GAIN_BEFORE_DD_PCT if pos.is_core else SCALP_MIN_PEAK_GAIN_BEFORE_DD_PCT
        dd_thr = CORE_DD_HARD_DROP_PCT if pos.is_core else SCALP_DD_HARD_DROP_PCT

        # require peak > entry by min_gain before activating DD logic
        if pos.peak_price < pos.entry_price * (1 + min_gain / 100.0):
            return False

        dd_pct = (pos.peak_price - ltp) * 100.0 / pos.peak_price
        if dd_pct >= dd_thr:
            self.exit_position(pos, reason=f"Hard DD {dd_pct:.1f}% from peak")
            return True
        return False

    def dynamic_tp(self, pos: Position, ltp: float):
        held_min = (now_ist() - pos.entry_time).total_seconds() / 60.0
        profit_pct = (ltp - pos.entry_price) * 100.0 / pos.entry_price
        if held_min <= MOMENTUM_FAST_MIN:
            return
        if profit_pct >= SLOW_PROFIT_PCT and held_min >= TIME_BASED_EXIT_MIN:
            reduced_tp = pos.entry_price * (1 + REDUCED_TP_PCT / 100.0)
            if reduced_tp < pos.tp_price:
                old = pos.tp_price
                pos.tp_price = reduced_tp
                log("ADJUST_TP", symbol=pos.symbol, side=pos.side, price=ltp,
                    reason=f"TP {old:.2f} -> {pos.tp_price:.2f} (held {held_min:.1f}m, profit {profit_pct:.1f}%)",
                    day_pnl=self.realized_pnl)
                self.log_pos_state(pos, ltp, tag="TP_UPDATE",
                                   extra=f"held={held_min:.1f}m profit={profit_pct:.1f}%")

    # ============ RSI refresh / snapshots / momentum logs ============

    def refresh_rsi_minutely(self, current_rsi: Optional[float]) -> Optional[float]:
        if not USE_RSI:
            return current_rsi
        now = now_ist()
        minute_key = now.replace(second=0, microsecond=0)
        if self._last_rsi_refresh_minute == minute_key:
            return current_rsi
        self._last_rsi_refresh_minute = minute_key
        try:
            c = self.dc.get_1m_today(INDEX_SYMBOL)
            if not c:
                return current_rsi
            rows = []
            for ts, o, h, l, cl, v in c:
                rows.append({"ts": utc_epoch_to_ist_dt(ts), "o": o, "h": h, "l": l, "c": cl, "v": v})
            df = pd.DataFrame(rows)
            post_open = df[df['ts'].dt.time >= ORB_START_IST]
            new_rsi = compute_rsi_from_1m(post_open, period=RSI_PERIOD, tf_min=RSI_TIMEFRAME_MIN)
            return new_rsi if new_rsi is not None else current_rsi
        except Exception:
            return current_rsi

    def snapshot_market(self, idx_ltp: float, rsi_val: Optional[float]):
        orh = getattr(self.orb, "or_high", None)
        orl = getattr(self.orb, "or_low", None)
        hi_buf = getattr(self.orb, "entry_hi_buf", None)
        lo_buf = getattr(self.orb, "entry_lo_buf", None)

        cd_rem = 0
        if self.cooldown_until:
            cd_rem = max(0, int((self.cooldown_until - now_ist()).total_seconds()))

        extra = ""
        if orh is not None and orl is not None:
            extra += f"IDX={idx_ltp:.2f} ORH={orh:.2f} ORL={orl:.2f} "
        else:
            extra += f"IDX={idx_ltp:.2f} ORH=NA ORL=NA "
        if hi_buf is not None and lo_buf is not None:
            extra += f"HI_BUF={hi_buf:.2f} LO_BUF={lo_buf:.2f} "
        else:
            extra += "HI_BUF=NA LO_BUF=NA "
        extra += (
            f"RSI={f'{rsi_val:.2f}' if rsi_val is not None else 'NA'} "
            f"Armed(L/S)={self.orb.long_armed}/{self.orb.short_armed} "
            f"CD={cd_rem}s OpenPos={len(self.positions)}"
        )
        log("SNAPSHOT", reason=extra, day_pnl=self.realized_pnl)

        if self.positions:
            for p in self.positions:
                try:
                    cp = self.dc.get_ltp(p.symbol)
                except Exception:
                    cp = float('nan')
                line = (
                    f"{'CORE' if p.is_core else 'SCALP'} {p.side} [{p.symbol}] "
                    f"EP={p.entry_price:.2f} CP={cp:.2f} SL={p.sl_price:.2f} TP={p.tp_price:.2f}"
                )
                log("SNAPSHOT_POS", reason=line, day_pnl=self.realized_pnl)
        else:
            log("SNAPSHOT_POS", reason="None", day_pnl=self.realized_pnl)

    def _rsi_regime(self, rsi_val: Optional[float]) -> str:
        if rsi_val is None:
            return "unknown"
        up = RSI_LONG_MIN
        dn = RSI_SHORT_MAX
        if self.last_rsi_regime == "bull":
            dn = RSI_SHORT_MAX - RSI_HYSTERESIS
        elif self.last_rsi_regime == "bear":
            up = RSI_LONG_MIN + RSI_HYSTERESIS
        if rsi_val > up:
            return "bull"
        if rsi_val < dn:
            return "bear"
        return "neutral"

    def _price_zone(self, idx_ltp: float) -> str:
        hi_buf = getattr(self.orb, "entry_hi_buf", None)
        lo_buf = getattr(self.orb, "entry_lo_buf", None)
        if hi_buf and idx_ltp > hi_buf:
            return "above_hi"
        if lo_buf and idx_ltp < lo_buf:
            return "below_lo"
        return "inside_or"

    def maybe_log_momentum_price_changes(self, idx_ltp: float, rsi_val: Optional[float]):
        if not ENABLE_MOMENTUM_LOGS:
            return
        regime = self._rsi_regime(rsi_val)
        if regime != self.last_rsi_regime:
            log("MOMENTUM_SHIFT",
                reason=f"RSI regime {self.last_rsi_regime or 'NA'} -> {regime} (RSI={rsi_val if rsi_val is not None else 'NA'})",
                day_pnl=self.realized_pnl)
            self.last_rsi_regime = regime

        zone = self._price_zone(idx_ltp)
        if zone != self.last_price_zone:
            log("PRICE_STATE",
                reason=f"Zone {self.last_price_zone or 'NA'} -> {zone} (IDX={idx_ltp:.2f})",
                day_pnl=self.realized_pnl)
            self.last_price_zone = zone

    # ============ Diagnostics (throttled & only on change) ============

    def log_signal_diagnostics(self, idx_ltp: float, rsi_val: Optional[float], force: bool = False):
        if not ENABLE_DIAGNOSTICS:
            return

        now_ts = now_ist()
        if not force and self._last_diag_ts is not None:
            if (now_ts - self._last_diag_ts).total_seconds() < DIAG_INTERVAL_SEC:
                return

        def build_reasons(side: str) -> str:
            reasons = []
            if side == "CE":
                raw = (self.orb.entry_hi_buf is not None) and (idx_ltp > self.orb.entry_hi_buf)
                if not raw:
                    reasons.append("no_breakout_above_buffer")
                if raw and not self.orb.rsi_allows("UP", rsi_val):
                    reasons.append("rsi_block")
                if PREVENT_DUPLICATE_SIDE and self.has_open_core_side('CE'):
                    reasons.append("duplicate_core")
                if REARM_ON_PULLBACK and not self.orb.long_armed:
                    reasons.append("not_armed")
            else:
                raw = (self.orb.entry_lo_buf is not None) and (idx_ltp < self.orb.entry_lo_buf)
                if not raw:
                    reasons.append("no_breakdown_below_buffer")
                if raw and not self.orb.rsi_allows("DOWN", rsi_val):
                    reasons.append("rsi_block")
                if PREVENT_DUPLICATE_SIDE and self.has_open_core_side('PE'):
                    reasons.append("duplicate_core")
                if REARM_ON_PULLBACK and not self.orb.short_armed:
                    reasons.append("not_armed")

            if self.realized_pnl <= -MAX_DAILY_LOSS_INR:
                reasons.append("daily_loss_hit")
            if self.cooldown_until and now_ist() < self.cooldown_until:
                reasons.append("cooldown")
            if len(self.positions) >= MAX_CONCURRENT_POS:
                reasons.append("max_concurrent")

            if raw:
                try:
                    sym = self.dc.pick_atm_symbol(side)
                    est = self.dc.get_ltp(sym)
                    if USE_PROJECTED_RISK_BLOCK:
                        sl_price = est * (1 - INIT_SL_PCT / 100.0)
                        risk = (est - sl_price) * LOT_SIZE + 2 * COST_PER_SIDE_INR
                        projected = self.realized_pnl - risk
                        if projected <= -MAX_DAILY_LOSS_INR:
                            reasons.append("projected_risk_breach")
                except Exception as e:
                    reasons.append(f"est_entry_failed:{str(e)[:80]}")

            return ", ".join(reasons) if reasons else "ok"

        ce_reasons = build_reasons("CE")
        pe_reasons = build_reasons("PE")

        if DIAG_ONLY_ON_CHANGE:
            if ce_reasons == (self._last_diag_reasons.get("CE") or "") and \
               pe_reasons == (self._last_diag_reasons.get("PE") or "") and \
               not force:
                return

        log("DIAG_NO_ENTRY",
            reason=f"CE blocked: {ce_reasons} | IDX={idx_ltp:.2f} RSI={rsi_val if rsi_val is not None else 'NA'}",
            day_pnl=self.realized_pnl)
        log("DIAG_NO_ENTRY",
            reason=f"PE blocked: {pe_reasons} | IDX={idx_ltp:.2f} RSI={rsi_val if rsi_val is not None else 'NA'}",
            day_pnl=self.realized_pnl)

        self._last_diag_ts = now_ts
        self._last_diag_reasons["CE"] = ce_reasons
        self._last_diag_reasons["PE"] = pe_reasons

    # ============ Main loop ============

    def run(self):
        # Refuse to trade on weekends or after the configured square-off time.
        session_now = now_ist()
        if session_now.weekday() >= 5:
            log("SESSION_SKIP", reason="Weekend; trading engine not started", day_pnl=self.realized_pnl)
            return
        if session_now.time() >= SQUARE_OFF_IST:
            log("SESSION_SKIP", reason="Started after square-off; no trading session", day_pnl=self.realized_pnl)
            return

        # 0) Respect START_IMMEDIATELY: optionally wait till 09:30 IST
        if not START_IMMEDIATELY and now_ist().time() < ORB_END_IST:
            log("INFO", reason="Waiting for ORB end (09:30 IST)", day_pnl=self.realized_pnl)
            tgt = IST.localize(dt.datetime.combine(now_ist().date(), ORB_END_IST))
            while now_ist() < tgt:
                time.sleep(1)

        # 1) Build ORB levels (with off-hours fallback if enabled)
        c = self.dc.get_1m_today(INDEX_SYMBOL)
        if (not c) and USE_YDAY_WHEN_TODAY_EMPTY:
            log("INFO", reason="No 1m data for today yet; using last trading day for TESTING", day_pnl=self.realized_pnl)
            c = self.dc.get_1m_last_trading(INDEX_SYMBOL)
        if not c:
            raise RuntimeError("History failed (1m).")

        rsi_val = self.orb.compute_orb(c)

        try:
            while True:
                # Square-off
                if now_ist().time() >= SQUARE_OFF_IST:
                    for p in list(self.positions):
                        self.exit_position(p, reason="Square-off")
                    log("SESSION_END", reason="Square-off reached", day_pnl=self.realized_pnl)
                    break

                # Index LTP
                try:
                    idx = self.dc.get_ltp(INDEX_SYMBOL)
                except Exception:
                    time.sleep(1.0)
                    continue

                # Momentum / price-zone logs
                self.maybe_log_momentum_price_changes(idx, rsi_val)

                # 15-min snapshot
                now_ts = now_ist()
                if self.last_snapshot_ts is None or (now_ts - self.last_snapshot_ts).total_seconds() >= SNAPSHOT_INTERVAL_SEC:
                    self.snapshot_market(idx, rsi_val)
                    self.last_snapshot_ts = now_ts

                # Optional: refresh RSI once per minute
                rsi_val = self.refresh_rsi_minutely(rsi_val)

                # ---- Optional time-based re-arm (in addition to your pullback/OR band rules) ----
                for side in ("CE", "PE"):
                    armed = self.orb.long_armed if side == "CE" else self.orb.short_armed
                    if not armed and self._last_core_entry_time.get(side):
                        if (now_ist() - self._last_core_entry_time[side]).total_seconds() >= CORE_REARM_MIN_SECS:
                            if side == "CE":
                                self.orb.long_armed = True
                            else:
                                self.orb.short_armed = True
                            log("REARM", reason=f"{side} timed re-arm after {CORE_REARM_MIN_SECS}s", day_pnl=self.realized_pnl)

                # ---- Manage positions ----
                for p in list(self.positions):
                    try:
                        cp = self.dc.get_ltp(p.symbol)
                    except Exception:
                        continue

                    p.record(now_ist(), cp)

                    # Trailing SL steps
                    self.trail_sl(p, cp)

                    # Adaptive DD exit
                    if self.dd_exit(p, cp):
                        continue

                    # Dynamic TP (time decay control)
                    self.dynamic_tp(p, cp)

                    # Hard SL/TP
                    if cp <= p.sl_price:
                        self.exit_position(p, reason="Stop-Loss")
                        continue
                    if cp >= p.tp_price:
                        self.exit_position(p, reason="Take-Profit")
                        continue

                    # Scalp max holding time exit
                    held_min = (now_ist() - p.entry_time).total_seconds() / 60.0
                    if not p.is_core and held_min >= SCALP_MAX_HOLD_MIN:
                        self.exit_position(p, reason=f"Scalp time exit {held_min:.1f}m")
                        continue

                # Daily loss hard gate for new entries
                if self.realized_pnl <= -MAX_DAILY_LOSS_INR:
                    time.sleep(0.8)
                    continue

                # ---- Core ORB signals ----
                try_long = bool(getattr(self.orb, "entry_hi_buf", None)) and (idx > self.orb.entry_hi_buf) and self.orb.rsi_allows("UP", rsi_val)
                try_short = bool(getattr(self.orb, "entry_lo_buf", None)) and (idx < self.orb.entry_lo_buf) and self.orb.rsi_allows("DOWN", rsi_val)

                if REARM_ON_PULLBACK:
                    try_long = try_long and self.orb.long_armed
                    try_short = try_short and self.orb.short_armed

                entered_this_tick = False
                side, is_core, note = None, True, "CORE"

                if try_long or try_short:
                    side = 'CE' if try_long else 'PE'
                    # prevent duplicate same-side core
                    if PREVENT_DUPLICATE_SIDE and self.has_open_core_side(side):
                        side = None
                    # opposite scalp if core blocked and first pos safe
                    if side is None and ALLOW_OPPOSITE_IF_SAFE:
                        opp = 'PE' if try_long else 'CE'
                        if self.first_position_safe() and len(self.positions) < MAX_CONCURRENT_POS:
                            side = opp
                            is_core = False
                            note = "SCALP"

                if side:
                    try:
                        est_sym = self.dc.pick_atm_symbol(side)
                        est_entry = self.dc.get_ltp(est_sym)
                    except Exception:
                        est_entry = None

                    if is_core:
                        if self.can_new_entry_with_sl(est_entry, INIT_SL_PCT):
                            self.create_position(side=side, is_core=True, note=note)
                            entered_this_tick = True
                            # Disarm this side for core until pullback / OR-band / timed re-arm
                            if side == 'CE':
                                self.orb.long_armed = False
                            else:
                                self.orb.short_armed = False
                    else:
                        if self.can_new_entry_with_sl(est_entry, SCALP_SL_PCT):
                            self.create_scalp_position(side)
                            entered_this_tick = True

                # ---- BB Range Scalp (if no core entered this tick) ----
                if SCALP_ENABLED and not entered_this_tick:
                    can_scalp = (
                            (self.scalp_cooldown_until is None or now_ist() >= self.scalp_cooldown_until)
                            and self.realized_pnl > -MAX_DAILY_LOSS_INR
                            and len(self.positions) < MAX_CONCURRENT_POS
                    )
                    if can_scalp:
                        scalp_side = self.bb_scalp.signal()  # 'CE'/'PE'/None
                        if scalp_side and self.can_open_scalp(scalp_side):
                            try:
                                est_sym = self.dc.pick_atm_symbol(scalp_side)
                                est_entry = self.dc.get_ltp(est_sym)
                            except Exception:
                                est_entry = None

                            if self.can_new_entry_with_sl(est_entry, SCALP_SL_PCT):
                                self.create_scalp_position(scalp_side)
                                entered_this_tick = True
                # ---- Secondary strategies (if no core entered this tick) ----
                if not entered_this_tick:
                    try:
                        sec_side = self.pick_secondary_signal(idx, rsi_val)  # 'CE'/'PE'/None
                    except Exception as e:
                        log("STRAT_ERR", reason=f"secondary signal error: {e}", day_pnl=self.realized_pnl)
                        sec_side = None
                    if sec_side:
                        try:
                            est_sym = self.dc.pick_atm_symbol(sec_side)
                            est_entry = self.dc.get_ltp(est_sym)
                        except Exception:
                            est_entry = None

                        # use scalp risk for secondaries by default (quick targets)
                        if self.can_new_entry_with_sl(est_entry, SCALP_SL_PCT):
                            self.create_scalp_position(sec_side)
                            entered_this_tick = True

                # If nothing entered, log diagnostics (throttled & on-change)
                if not entered_this_tick:
                    self.log_signal_diagnostics(idx, rsi_val)

                time.sleep(0.8)

        finally:
            # ---- EoD summary (even on exceptions) ----
            stats = summarize(self.trades)

            def srow(name, value):
                try:
                    num = float(value)
                except Exception:
                    num = 0.0
                log("SUMMARY", reason=name, pnl=num, extra=str(value), day_pnl=self.realized_pnl)

            for k in [
                "total", "wins", "losses", "flats", "win_rate",
                "total_pnl", "avg_pnl", "avg_win", "avg_loss",
                "profit_factor", "avg_hold", "best", "worst"
            ]:
                srow(k, stats[k])
            srow("max_drawdown", self.max_drawdown)

            # Console
            print("\n========== EOD SUMMARY ==========")
            for k, v in stats.items():
                print(f"{k:>12}: {v}")
            print(f"{'max_drawdown':>12}: {self.max_drawdown:.2f}")
            print("=================================\n")
