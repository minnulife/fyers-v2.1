"""FYERS OAuth helper used by the V2 web application.

This module deliberately contains no web-framework code. It generates the FYERS
login URL, exchanges an auth code for an access token, and stores the token
atomically. The API layer calls these functions after local authentication.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
import html
import json
import re



def _required(value: str, name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} is not configured")
    return cleaned


def extract_auth_code(value: str) -> str:
    """Accept either a raw auth code or the complete redirect URL."""
    raw = html.unescape(value).strip().strip('"\'')
    if not raw:
        raise ValueError("Auth code is empty")

    # Accept a complete redirect URL, a query string, or a raw auth code.
    if "://" in raw or "?" in raw or raw.startswith("auth_code=") or raw.startswith("code="):
        parsed = urlparse(raw if "://" in raw else "http://local/?" + raw.lstrip("?"))
        params = parse_qs(parsed.query)
        if parsed.fragment:
            fragment = parse_qs(parsed.fragment)
            params.update(fragment)
        code = (params.get("auth_code") or params.get("code") or [""])[0].strip()
        if not code:
            raise ValueError("No auth_code was found. Paste the complete redirect URL or only the auth_code value.")
        return unquote(code).strip()
    return unquote(raw)


_JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


def looks_like_access_token(value: str) -> bool:
    """Heuristically detect a raw FYERS JWT access token."""
    raw = html.unescape(value).strip().strip('"\'')
    if not raw or "://" in raw or "?" in raw or "=" in raw:
        return False
    return bool(_JWT_RE.fullmatch(raw))


def build_session(*, client_id: str, secret_key: str, redirect_uri: str) -> Any:
    try:
        from fyers_apiv3 import fyersModel
    except ImportError as exc:
        raise RuntimeError("fyers-apiv3 is not installed. Run setup_windows.bat first.") from exc
    return fyersModel.SessionModel(
        client_id=_required(client_id, "FYERS_CLIENT_ID"),
        secret_key=_required(secret_key, "FYERS_SECRET_KEY"),
        redirect_uri=_required(redirect_uri, "FYERS_REDIRECT_URI"),
        response_type="code",
        grant_type="authorization_code",
    )


def generate_login_url(*, client_id: str, secret_key: str, redirect_uri: str) -> str:
    session = build_session(
        client_id=client_id,
        secret_key=secret_key,
        redirect_uri=redirect_uri,
    )
    url = session.generate_authcode()
    if not isinstance(url, str) or not url.startswith("http"):
        raise RuntimeError("FYERS did not return a valid login URL")
    return url


def _validate_token_response(response: Any) -> str:
    if not isinstance(response, dict):
        raise RuntimeError(f"Unexpected response from FYERS token API: {type(response).__name__}")
    token = str(response.get("access_token") or (response.get("data") or {}).get("access_token") or "").strip()
    if not token:
        message = response.get("message") or response.get("msg") or response.get("error") or "FYERS token generation failed"
        safe = {k: v for k, v in response.items() if k not in {"access_token", "refresh_token"}}
        raise RuntimeError(f"{message}. FYERS response: {json.dumps(safe, default=str)[:1000]}")
    if any(ch.isspace() for ch in token):
        raise RuntimeError("FYERS returned an invalid access token")
    return token


def exchange_auth_code(
    auth_code_or_url: str,
    *,
    client_id: str,
    secret_key: str,
    redirect_uri: str,
) -> str:
    session = build_session(
        client_id=client_id,
        secret_key=secret_key,
        redirect_uri=redirect_uri,
    )
    session.set_token(extract_auth_code(auth_code_or_url))
    return _validate_token_response(session.generate_token())


def save_access_token(token: str, token_path: Path) -> Path:
    """Atomically save a token and use owner-only permissions where supported."""
    clean = token.strip()
    if not clean or any(ch.isspace() for ch in clean):
        raise ValueError("Access token is invalid")

    destination = token_path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".fyers-token-", dir=destination.parent, text=True)
    try:
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(clean)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        try:
            os.chmod(destination, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return destination


def generate_and_store_token(
    auth_code_or_url: str,
    *,
    client_id: str,
    secret_key: str,
    redirect_uri: str,
    token_path: Path,
) -> Path:
    raw = html.unescape(auth_code_or_url).strip().strip('"\'')
    if looks_like_access_token(raw):
        token = raw
    else:
        token = exchange_auth_code(
            raw,
            client_id=client_id,
            secret_key=secret_key,
            redirect_uri=redirect_uri,
        )
    return save_access_token(token, token_path)
