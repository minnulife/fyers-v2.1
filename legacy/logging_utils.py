import os, csv, logging, datetime as dt
from config import LOG_DIR, IST

os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"orb_sim_{dt.datetime.now(IST).strftime('%Y%m%d')}.csv")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)

def ist_now():
    return dt.datetime.now(IST)

def init_csv():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["timestamp","event","symbol","side","price","qty","reason","pnl","day_pnl","extra"])

def logger_row(event, symbol="", side="", price=0.0, qty=0, reason="", pnl=0.0, day_pnl=0.0, extra=""):
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            ist_now().strftime("%Y-%m-%d %H:%M:%S"), event, symbol, side,
            f"{price:.2f}", qty, reason, f"{pnl:.2f}", f"{day_pnl:.2f}", extra
        ])
    logging.info(f"{event} | {symbol} {side} @ {price:.2f} | {reason} | PnL:{pnl:.2f} Day:{day_pnl:.2f} {extra}")
