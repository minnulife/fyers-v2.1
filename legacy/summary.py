from typing import List
def summarize(trades: List[dict]) -> dict:
    total = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    flats = [t for t in trades if abs(t["pnl"]) < 1e-6]
    total_pnl = sum(t["pnl"] for t in trades)
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    win_rate = (len(wins)/total*100.0) if total else 0.0
    avg_pnl = (total_pnl/total) if total else 0.0
    avg_win = (gross_win/len(wins)) if wins else 0.0
    avg_loss = (-gross_loss/len(losses)) if losses else 0.0
    profit_factor = (gross_win/gross_loss) if gross_loss > 0 else float('inf')
    best = max([t["pnl"] for t in trades], default=0.0)
    worst = min([t["pnl"] for t in trades], default=0.0)
    avg_hold = (sum(t["hold_min"] for t in trades)/total) if total else 0.0
    return {
        "total": total, "wins": len(wins), "losses": len(losses), "flats": len(flats),
        "win_rate": win_rate, "total_pnl": total_pnl, "avg_pnl": avg_pnl,
        "avg_win": avg_win, "avg_loss": avg_loss, "profit_factor": profit_factor,
        "best": best, "worst": worst, "avg_hold": avg_hold
    }
