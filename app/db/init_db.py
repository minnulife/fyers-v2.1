import json
from sqlalchemy import select
from app.db.database import Base, engine, SessionLocal
from app.db import models

DEFAULT_AUTOMATED = {
    'ORB': {'display_name':'ORB Breakout','enabled':True,'indices':['NIFTY','BANKNIFTY'],'config':{'start_time':'09:15','end_time':'09:30','stop_time':'15:30','lots':1,'max_trades':2,'confirmation':'1M_CLOSE','entry_buffer_pct':0.05,'stop_loss_pct':20,'target_pct':25,'cooldown_sec':60,'rsi_long_min':55,'rsi_short_max':45}},
    'VWAP': {'display_name':'VWAP Reversion','enabled':True,'indices':['NIFTY'],'config':{'start_time':'09:15','stop_time':'15:30','lots':1,'max_trades':2,'confirmation':'1M_CLOSE','band_k':2.0,'lookback_min':120,'rsi_min':40,'rsi_max':60,'stop_loss_pct':8,'target_pct':8,'max_hold_min':15}},
    'SUPERTREND': {'display_name':'Supertrend Trend','enabled':False,'indices':['NIFTY'],'config':{'start_time':'09:15','stop_time':'15:30','lots':1,'max_trades':2,'confirmation':'5M_CLOSE','timeframe_min':5,'atr_period':10,'multiplier':3.0,'rsi_long_min':55,'rsi_short_max':45,'stop_loss_pct':12,'target_pct':18}},
    'BB': {'display_name':'Bollinger Band Scalp','enabled':True,'indices':['NIFTY'],'config':{'start_time':'09:15','stop_time':'15:30','lots':1,'max_trades':3,'confirmation':'1M_CLOSE','period':20,'std_dev':2.0,'rsi_min':45,'rsi_max':55,'stop_loss_pct':8,'target_pct':6.5,'max_hold_min':10,'cooldown_sec':120}},
}
DEFAULT_SETTINGS = {
    'trading_mode':'PAPER','automated_max_trades':'2','automated_daily_loss':'2000','custom_daily_loss':'0',
    'square_off_time':'15:30','paper_capital':'500000','slippage_pct':'0.10','brokerage_per_side':'20',
    'theme':'dark','notifications':'browser','market_reconnect_sec':'5','log_retention_days':'90',
}

def _normalize_session_window(db) -> None:
    for row in db.scalars(select(models.AutomatedStrategy)).all():
        config = json.loads(row.config_json or '{}')
        changed = False
        if config.get('start_time') in {None, '', '09:30'}:
            config['start_time'] = '09:15'
            changed = True
        if config.get('stop_time') != '15:30':
            config['stop_time'] = '15:30'
            changed = True
        if row.key == 'ORB' and config.get('end_time') in {None, ''}:
            config['end_time'] = '09:30'
            changed = True
        if changed:
            row.config_json = json.dumps(config)
    square_off = db.get(models.Setting, 'square_off_time')
    if square_off and square_off.value != '15:30':
        square_off.value = '15:30'

def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        for key, item in DEFAULT_AUTOMATED.items():
            if db.get(models.AutomatedStrategy, key) is None:
                db.add(models.AutomatedStrategy(key=key, display_name=item['display_name'], enabled=item['enabled'], status='WAITING' if item['enabled'] else 'DISABLED', indices_json=json.dumps(item['indices']), config_json=json.dumps(item['config'])))
        for key, value in DEFAULT_SETTINGS.items():
            if db.get(models.Setting, key) is None:
                db.add(models.Setting(key=key, value=value))
        for symbol in ('NIFTY','BANKNIFTY','SENSEX'):
            if db.get(models.WatchItem, symbol) is None:
                db.add(models.WatchItem(symbol=symbol, item_type='INDEX', is_core=True))
        _normalize_session_window(db)
        db.commit()
