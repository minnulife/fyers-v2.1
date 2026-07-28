from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen

SOURCE_URLS = (
    ("NSE_CM", "https://public.fyers.in/sym_details/NSE_CM.csv"),
    ("BSE_CM", "https://public.fyers.in/sym_details/BSE_CM.csv"),
    ("NSE_FO", "https://public.fyers.in/sym_details/NSE_FO.csv"),
    ("BSE_FO", "https://public.fyers.in/sym_details/BSE_FO.csv"),
)
TTL_SECONDS = 7 * 24 * 60 * 60

CACHE_DIR = Path("data") / "symbol_master"
CACHE_FILE = CACHE_DIR / "symbols.json"
META_FILE = CACHE_DIR / "meta.json"


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, "", "None"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, "", "None"):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").upper().strip().split())


def _row_to_item(source: str, row: list[str]) -> dict[str, Any] | None:
    if len(row) < 10:
        return None
    symbol = (row[9] if len(row) > 9 else "").strip()
    name = (row[1] if len(row) > 1 else "").strip()
    if not symbol and not name:
        return None
    item = {
        "source": source,
        "symbol": symbol,
        "name": name,
        "instrument_type": row[2].strip() if len(row) > 2 else "",
        "lot": _safe_int(row[3] if len(row) > 3 else None),
        "tick": _safe_float(row[4] if len(row) > 4 else None),
        "isin": row[5].strip() if len(row) > 5 else "",
        "trad_ses": row[6].strip() if len(row) > 6 else "",
        "last_upd": row[7].strip() if len(row) > 7 else "",
        "expiry": _safe_int(row[8] if len(row) > 8 else None),
        "exchange": row[10].strip() if len(row) > 10 else "",
        "segment": row[11].strip() if len(row) > 11 else "",
        "script_code": row[12].strip() if len(row) > 12 else "",
        "short_sym": row[13].strip() if len(row) > 13 else "",
        "strike": _safe_float(row[14] if len(row) > 14 else None),
        "opt": row[15].strip() if len(row) > 15 else "",
    }
    item["item_type"] = "INDEX" if item["segment"] == "10" and item["symbol"].endswith("-INDEX") else (
        "FUTURES" if item["symbol"].endswith("FUT") or item["opt"] == "XX" and item["strike"] in (None, -1.0) else (
            "OPTION" if item["opt"] in {"CE", "PE"} else "STOCK"
        )
    )
    item["search_blob"] = _normalize_text(
        " ".join(
            str(part)
            for part in (
                item["symbol"],
                item["name"],
                item["short_sym"],
                item["exchange"],
                item["segment"],
                item["item_type"],
            )
            if part
        )
    )
    return item


def _load_remote_source(source: str, url: str) -> list[dict[str, Any]]:
    with urlopen(url, timeout=30) as response:
        text = response.read().decode("utf-8", errors="ignore")
    rows = csv.reader(io.StringIO(text))
    items: list[dict[str, Any]] = []
    for row in rows:
        item = _row_to_item(source, row)
        if item:
            items.append(item)
    return items


def _read_meta() -> dict[str, Any] | None:
    if not META_FILE.exists():
        return None
    try:
        return json.loads(META_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_cache() -> list[dict[str, Any]]:
    if not CACHE_FILE.exists():
        return []
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write_atomic(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def refresh_symbol_master_cache(*, force: bool = False) -> dict[str, Any]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    meta = _read_meta() or {}
    cache_age = time.time() - float(meta.get("updated_at_epoch", 0) or 0)
    if not force and CACHE_FILE.exists() and cache_age < TTL_SECONDS:
        cached = _read_cache()
        return {
            "source": "cache",
            "updated_at": meta.get("updated_at"),
            "count": len(cached),
            "stale": False,
        }

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for source, url in SOURCE_URLS:
        try:
            items.extend(_load_remote_source(source, url))
        except Exception as exc:
            errors.append(f"{source}: {exc}")

    if items:
        updated_at = datetime.now(timezone.utc).isoformat()
        _write_atomic(CACHE_FILE, json.dumps(items, ensure_ascii=False))
        _write_atomic(META_FILE, json.dumps({"updated_at": updated_at, "updated_at_epoch": time.time(), "count": len(items)}, ensure_ascii=False))
        return {"source": "remote", "updated_at": updated_at, "count": len(items), "errors": errors, "stale": False}

    cached = _read_cache()
    return {
        "source": "cache",
        "updated_at": meta.get("updated_at"),
        "count": len(cached),
        "errors": errors or ["No remote sources were available"],
        "stale": True,
    }


def _ensure_cache_current() -> list[dict[str, Any]]:
    meta = _read_meta() or {}
    cache_age = time.time() - float(meta.get("updated_at_epoch", 0) or 0)
    if CACHE_FILE.exists() and cache_age < TTL_SECONDS:
        return _read_cache()
    refresh_symbol_master_cache(force=True)
    return _read_cache()


def search_symbols(query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    q = _normalize_text(query)
    if not q:
        return []
    tokens = [token for token in q.split(" ") if token]
    items = _ensure_cache_current()
    matches: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for item in items:
        blob = item.get("search_blob", "")
        if any(token not in blob for token in tokens):
            continue
        symbol = item.get("symbol", "")
        name = item.get("name", "")
        exact = int(q == _normalize_text(symbol))
        starts = int(_normalize_text(symbol).startswith(q) or _normalize_text(name).startswith(q))
        score = (
            exact,
            starts,
            -min((blob.find(token) for token in tokens if token in blob), default=9999),
        )
        matches.append((score, item))
    matches.sort(key=lambda pair: pair[0], reverse=True)
    output: list[dict[str, Any]] = []
    for _, item in matches[: max(limit, 1)]:
        output.append(
            {
                "symbol": item.get("symbol"),
                "name": item.get("name"),
                "exchange": item.get("exchange"),
                "segment": item.get("segment"),
                "item_type": item.get("item_type"),
                "short_sym": item.get("short_sym"),
                "lot": item.get("lot"),
                "tick": item.get("tick"),
                "expiry": item.get("expiry"),
                "strike": item.get("strike"),
                "opt": item.get("opt"),
                "source": item.get("source"),
            }
        )
    return output
