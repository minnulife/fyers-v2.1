# FYERS Trading Platform V2.1

Windows 11 local paper-trading web application. V2.1 is deliberately locked to PAPER mode; it contains no live order adapter.

## Included

- Dark login and multi-page dashboard
- Fixed NIFTY, BANKNIFTY and SENSEX core markets
- Add/remove watchlist instruments with LTP, absolute/percentage change, open and previous close
- Futures-level and option-premium custom strategies
- Automatic nearest-premium or exact-strike selection fields
- Candle confirmation, validity and trailing-stop fields
- Dedicated Automated Strategies page for ORB, VWAP, Supertrend and BB Scalp
- Enable, pause, stop-for-day and edit strategy parameters from the UI
- Separate CUSTOM and AUTOMATED trade pools
- Pending strategies with waiting condition and cancellation
- Ongoing trade target, stop, quantity and paper exit controls
- Trade History filters, sorting and CSV export
- Reports filters, strategy breakdown, drawdown and CSV export
- Single FYERS profile and token generation using `accessToken/newtoken.py`
- Global configuration stored in SQLite, with audit history
- Reviewed legacy bot preserved under `legacy/`

## Install on Windows 11

1. Extract the ZIP, for example to `C:\fyers-platform-v2.1`.
2. Double-click `scripts\setup_windows.bat`.
3. Copy `.env.example` to `.env` if setup did not do so.
4. Edit `.env` and set a strong local password plus FYERS credentials.
5. Double-click `scripts\start_windows.bat`.
6. Open `http://127.0.0.1:8000`.

## Required `.env` values

```env
SECRET_KEY=replace-with-a-long-random-secret
ADMIN_USERNAME=admin
ADMIN_PASSWORD=replace-with-a-strong-password
TRADING_MODE=PAPER
LIVE_TRADING_ENABLED=false
FYERS_CLIENT_ID=YOUR_APP_ID-100
FYERS_SECRET_KEY=YOUR_SECRET_KEY
FYERS_REDIRECT_URI=THE_EXACT_URI_REGISTERED_IN_FYERS
FYERS_TOKEN_PATH=./accessToken/token.txt
```

The client ID, secret key and redirect URI are never returned by the browser API. The generated token is stored locally at `accessToken\token.txt`.

## Token update

Dashboard -> Update Token -> Generate Login URL -> complete FYERS login -> paste the returned auth code, the complete redirect URL, or an existing FYERS access token -> Generate and Store Token.

Auth codes are short-lived and normally single-use. Generate a fresh login URL for each attempt.

## Tests

```bat
scripts\test_windows.bat
```

or:

```powershell
.\.venv\Scripts\python.exe smoketest.py
```

## Important boundaries

- Market values shown in the dashboard are demonstration values until the market-data service is connected.
- Automated strategy switches and configuration are persisted, but the legacy engine is not yet controlled as a background process by these records.
- SQLite persistence covers the V2.1 web application's strategies, trades, settings and audit history.
- Live Confirm and Live Auto remain locked until a separate live broker, order reconciliation, static-IP compliance and safety review are completed.
