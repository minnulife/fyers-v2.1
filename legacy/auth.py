import os
import tempfile
from typing import Any, Dict

from fyers_apiv3 import fyersModel
from config import CLIENT_ID, TOKEN_PATH, INDEX_SYMBOL


def _validate_raw_v3_token(access_token: str) -> str:
    token = access_token.strip()
    if not token:
        raise ValueError("FYERS access token is empty.")
    if ":" in token:
        raise ValueError("Use the raw v3 JWT without an APP_ID prefix.")
    if token.count(".") < 2:
        raise ValueError("Token does not look like a JWT.")
    return token


def save_access_token(access_token: str) -> str:
    """Atomically save a validated token with owner-only permissions."""
    token = _validate_raw_v3_token(access_token)
    token_path = os.path.abspath(TOKEN_PATH)
    parent = os.path.dirname(token_path)
    os.makedirs(parent, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(prefix=".fyers-token-", dir=parent, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, token_path)
        try:
            os.chmod(token_path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
    return token_path


def get_fyers():
    token_path = os.path.abspath(TOKEN_PATH)
    with open(token_path, "r", encoding="utf-8") as f:
        access_token = _validate_raw_v3_token(f.read())
    return fyersModel.FyersModel(client_id=CLIENT_ID, token=access_token, log_path="")


def check_fyers_connection(access_token: str | None = None) -> Dict[str, Any]:
    """Verify both account/profile access and live NIFTY quote access."""
    if access_token is None:
        client = get_fyers()
    else:
        token = _validate_raw_v3_token(access_token)
        client = fyersModel.FyersModel(client_id=CLIENT_ID, token=token, log_path="")

    profile = client.get_profile()
    quotes = client.quotes({"symbols": INDEX_SYMBOL})
    profile_ok = isinstance(profile, dict) and profile.get("s") == "ok"
    quotes_ok = isinstance(quotes, dict) and quotes.get("s") == "ok"
    return {
        "ok": profile_ok and quotes_ok,
        "profile_ok": profile_ok,
        "quotes_ok": quotes_ok,
        "message": "FYERS connected" if profile_ok and quotes_ok else "FYERS validation failed",
    }
