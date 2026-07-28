from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field, field_validator

class StrategyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    strategy_type: str
    index_symbol: str
    side: str
    confirmation: str = 'INSTANT'
    validity: str = 'TODAY'
    trailing_mode: str = 'NONE'
    lots: int = Field(default=1, ge=1, le=100)
    entry: float | None = Field(default=None, gt=0)
    target: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    strike_mode: str | None = None
    strike_price: float | None = Field(default=None, gt=0)
    premium_tolerance: float | None = Field(default=None, ge=0)
    trigger_description: str = 'Waiting for trigger'
    metadata: dict[str, Any] = Field(default_factory=dict)
    @field_validator('strategy_type')
    @classmethod
    def validate_type(cls, value: str) -> str:
        value=value.upper()
        if value not in {'FUTURES','OPTION_PREMIUM'}: raise ValueError('Unsupported custom strategy type')
        return value
    @field_validator('side')
    @classmethod
    def validate_side(cls, value: str) -> str:
        value=value.upper()
        if value not in {'CE','PE','LONG','SHORT'}: raise ValueError('Unsupported side')
        return value

class StrategyOut(BaseModel):
    id:int; name:str; strategy_type:str; source:str; index_symbol:str; side:str; execution_mode:str; status:str
    trigger_description:str; confirmation:str; validity:str; trailing_mode:str; lots:int
    entry:float|None; target:float|None; stop_loss:float|None; strike_mode:str|None; strike_price:float|None; premium_tolerance:float|None; created_at:datetime
    model_config={'from_attributes':True}

class ExecuteRequest(BaseModel):
    market_price: float = Field(gt=0)
    instrument: str = Field(min_length=3,max_length=80)
class CloseTradeRequest(BaseModel):
    exit_price: float = Field(gt=0)
    reason: str = Field(default='MANUAL_EXIT',min_length=2,max_length=80)
class TradeUpdateRequest(BaseModel):
    target_price: float|None = Field(default=None,gt=0)
    stop_price: float|None = Field(default=None,gt=0)
    quantity: int|None = Field(default=None,ge=1)
class TokenAuthCode(BaseModel):
    auth_code_or_url: str = Field(min_length=3,max_length=4096)
class AutomatedUpdate(BaseModel):
    enabled: bool|None=None
    paused_today: bool|None=None
    indices: list[str]|None=None
    config: dict[str,Any]|None=None
    apply_mode: str='IMMEDIATE'
class BulkAutomatedAction(BaseModel):
    action: str
class SettingsUpdate(BaseModel):
    values: dict[str,Any]
