# V2 final review

## Release status

Approved for local Windows 11 paper-trading development and UI/API testing.

Not approved for public hosting, unattended live trading, or real-order execution.

## Controls present

- Live execution is blocked at configuration startup.
- API endpoints require a local authenticated session, except health and login/session checks.
- SQLite persists strategies and paper trades across application restarts.
- FYERS token updates are stored locally and never returned by the API.
- The frontend clearly identifies PAPER mode.
- No sample market prices are presented as live data.

## Known limitations

- No FYERS WebSocket market feed yet.
- No automatic expiry or valid strike discovery yet.
- Paper quantity currently uses the entered quantity/lots directly; exchange lot-size resolution is pending.
- No holiday calendar or NSE session scheduler in the V2 web service yet.
- No automated legacy strategy runner connected to the V2 database yet.
- No slippage, brokerage or statutory charge model yet.
- Local password is read from `.env`; production deployment requires hashed credentials and HTTPS.
- SQLite is suitable for one local user, not multi-instance cloud deployment.
