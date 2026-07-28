from functools import lru_cache
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "FYERS Trading Platform V2.1"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    secret_key: str = "development-only-change-me"
    admin_username: str = "admin"
    admin_password: str = "change-me-now"
    database_url: str = "sqlite:///./data/trading_v2.db"
    trading_mode: str = "PAPER"
    live_trading_enabled: bool = False
    fyers_client_id: str = ""
    fyers_secret_key: str = ""
    fyers_redirect_uri: str = ""
    fyers_token_path: Path = Path("./accessToken/token.txt")
    timezone: str = "Asia/Kolkata"
    max_automated_trades: int = Field(default=2, ge=1)
    max_daily_loss_inr: float = Field(default=2000.0, gt=0)

    @field_validator("trading_mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        mode = value.upper().strip()
        if mode not in {"PAPER", "LIVE_CONFIRM", "LIVE_AUTO"}:
            raise ValueError("TRADING_MODE must be PAPER, LIVE_CONFIRM, or LIVE_AUTO")
        return mode

    def validate_safety(self) -> None:
        if self.trading_mode != "PAPER":
            if not self.live_trading_enabled:
                raise RuntimeError("Live mode requested but LIVE_TRADING_ENABLED is false")
            raise RuntimeError("V2.1 contains no live broker adapter. Use TRADING_MODE=PAPER")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_safety()
    settings.fyers_token_path.parent.mkdir(parents=True, exist_ok=True)
    Path("data").mkdir(exist_ok=True)
    Path("logs").mkdir(exist_ok=True)
    return settings
