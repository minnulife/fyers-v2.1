import asyncio
import csv, io, json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from queue import Queue
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session
from app.api.schemas import AutomatedUpdate, BulkAutomatedAction, CloseTradeRequest, ExecuteRequest, SettingsUpdate, StrategyCreate, StrategyOut, TokenAuthCode, TradeUpdateRequest
from app.core.config import get_settings
from app.db.database import get_db
from app.db.models import AuditLog, AutomatedStrategy, Setting, Strategy, Trade, WatchItem
from app.services.live_dashboard import dashboard_live_stream
from app.services.trading import TradingService
from accessToken.newtoken import generate_login_url, generate_and_store_token
from app.services.market_data import probe_fyers_connection
from app.services.market_data import DEFAULT_CORE_MARKETS, DEFAULT_MARKET_FALLBACKS
from app.services.symbol_master import refresh_symbol_master_cache, search_symbols

router=APIRouter(prefix='/api')

def audit(db,action,entity,entity_id='',details=None):
    db.add(AuditLog(action=action,entity=entity,entity_id=str(entity_id),details_json=json.dumps(details or {},default=str)))

def setting_map(db):
    return {x.key:x.value for x in db.scalars(select(Setting)).all() if not x.secret}

@router.get('/health')
def health():
    s=get_settings(); return {'status':'ok','mode':'PAPER','live_enabled':False,'version':'2.1.0','app':s.app_name}

@router.get('/dashboard')
def dashboard(db:Session=Depends(get_db)):
    now_utc = datetime.now(timezone.utc)
    pending=db.scalar(select(func.count()).select_from(Strategy).where(Strategy.status=='PENDING')) or 0
    opened=db.scalar(select(func.count()).select_from(Trade).where(Trade.status=='OPEN')) or 0
    realised=db.scalar(select(func.coalesce(func.sum(Trade.pnl),0)).where(Trade.status=='CLOSED')) or 0
    auto=db.scalars(select(AutomatedStrategy).order_by(AutomatedStrategy.key)).all()
    watches=db.scalars(select(WatchItem).where(WatchItem.enabled==True)).all()
    market_data=dashboard_live_stream.get_market_data(core_symbols=DEFAULT_CORE_MARKETS,fallback_map=DEFAULT_MARKET_FALLBACKS,watch_items=[w for w in watches if not w.is_core])
    return {'mode':'PAPER','pending_strategies':pending,'open_trades':opened,'realised_pnl':round(float(realised),2),
            'core_markets':market_data['core_markets'],
            'watchlist':market_data['watchlist'],
            'market_data_source':market_data['source'],
            'market_data_as_of':market_data['as_of'],
            'market_data_checked_at_ist':market_data['checked_at_ist'],
            'stream_state':dashboard_live_stream.current_state(),
            'broker':market_data['broker'],
            'server_time_utc': now_utc.isoformat(),
            'server_time_ist': now_utc.astimezone(ZoneInfo('Asia/Kolkata')).isoformat(),
            'automated':[serialize_auto(a) for a in auto],'broker_connected':market_data['broker']['connected']}

@router.post('/watchlist/{symbol}')
def add_watch(symbol:str,item_type:str='STOCK',db:Session=Depends(get_db)):
    symbol=symbol.upper().strip()
    if symbol in DEFAULT_CORE_MARKETS: raise HTTPException(409,'This is already a fixed core market')
    row=db.get(WatchItem,symbol)
    if row: row.enabled=True
    else: db.add(WatchItem(symbol=symbol,item_type=item_type.upper(),is_core=False))
    audit(db,'ADD','WATCHLIST',symbol); db.commit()
    watches=db.scalars(select(WatchItem).where(WatchItem.enabled==True)).all()
    dashboard_live_stream.refresh(core_symbols=DEFAULT_CORE_MARKETS,fallback_map=DEFAULT_MARKET_FALLBACKS,watch_items=[w for w in watches if not w.is_core],force=True)
    return {'status':'ok'}

@router.delete('/watchlist/{symbol}')
def remove_watch(symbol:str,db:Session=Depends(get_db)):
    row=db.get(WatchItem,symbol.upper())
    if not row: raise HTTPException(404,'Watch item not found')
    if row.is_core: raise HTTPException(409,'Core markets cannot be removed')
    db.delete(row); audit(db,'REMOVE','WATCHLIST',symbol); db.commit()
    watches=db.scalars(select(WatchItem).where(WatchItem.enabled==True)).all()
    dashboard_live_stream.refresh(core_symbols=DEFAULT_CORE_MARKETS,fallback_map=DEFAULT_MARKET_FALLBACKS,watch_items=[w for w in watches if not w.is_core],force=True)
    return {'status':'removed'}

@router.post('/strategies',response_model=StrategyOut,status_code=201)
def create_strategy(p:StrategyCreate,db:Session=Depends(get_db)):
    data=p.model_dump(exclude={'metadata'}); data['metadata_json']=json.dumps(p.metadata)
    st=Strategy(**data,source='CUSTOM',execution_mode='PAPER',status='PENDING')
    db.add(st); db.flush(); audit(db,'CREATE','STRATEGY',st.id,p.model_dump()); db.commit(); db.refresh(st); return st

@router.get('/strategies',response_model=list[StrategyOut])
def list_strategies(status:str|None=None,strategy_type:str|None=None,index_symbol:str|None=None,sort:str=Query('newest',pattern='^(newest|oldest)$'),db:Session=Depends(get_db)):
    q=select(Strategy)
    if status:q=q.where(Strategy.status==status.upper())
    if strategy_type:q=q.where(Strategy.strategy_type==strategy_type.upper())
    if index_symbol:q=q.where(Strategy.index_symbol==index_symbol.upper())
    return list(db.scalars(q.order_by(desc(Strategy.created_at) if sort=='newest' else asc(Strategy.created_at))).all())

@router.post('/strategies/{sid}/cancel')
def cancel_strategy(sid:int,db:Session=Depends(get_db)):
    st=db.get(Strategy,sid)
    if not st: raise HTTPException(404,'Strategy not found')
    if st.status not in {'PENDING','READY'}: raise HTTPException(409,f'Cannot cancel strategy in {st.status}')
    st.status='CANCELLED'; audit(db,'CANCEL','STRATEGY',sid); db.commit(); return {'status':'cancelled'}

@router.post('/strategies/{sid}/execute')
def execute_strategy(sid:int,p:ExecuteRequest,db:Session=Depends(get_db)):
    st=db.get(Strategy,sid)
    if not st: raise HTTPException(404,'Strategy not found')
    try:t=TradingService(db).execute_strategy(st,p.market_price,p.instrument)
    except (ValueError,RuntimeError) as e: raise HTTPException(409,str(e))
    t.source='CUSTOM'; t.target_price=st.target; t.stop_price=st.stop_loss; audit(db,'EXECUTE','STRATEGY',sid,{'trade_id':t.id}); db.commit()
    return {'trade_id':t.id,'status':t.status,'entry_price':t.entry_price}

def serialize_auto(a):
    return {'key':a.key,'display_name':a.display_name,'enabled':a.enabled,'paused_today':a.paused_today,'status':a.status,'mode':a.mode,
            'indices':json.loads(a.indices_json or '[]'),'config':json.loads(a.config_json or '{}'),'last_signal':a.last_signal,'last_trade':a.last_trade,
            'today_pnl':a.today_pnl,'trades_today':a.trades_today,'updated_at':a.updated_at}

@router.get('/automated')
def automated(db:Session=Depends(get_db)):
    return [serialize_auto(x) for x in db.scalars(select(AutomatedStrategy).order_by(AutomatedStrategy.key)).all()]

@router.put('/automated/{key}')
def update_automated(key:str,p:AutomatedUpdate,db:Session=Depends(get_db)):
    row=db.get(AutomatedStrategy,key.upper())
    if not row: raise HTTPException(404,'Automated strategy not found')
    before=serialize_auto(row)
    if p.enabled is not None: row.enabled=p.enabled
    if p.paused_today is not None: row.paused_today=p.paused_today
    if p.indices is not None: row.indices_json=json.dumps([x.upper() for x in p.indices])
    if p.config is not None:
        old=json.loads(row.config_json or '{}'); old.update(p.config); row.config_json=json.dumps(old)
    row.status='PAUSED' if row.paused_today else ('WAITING' if row.enabled else 'DISABLED')
    audit(db,'UPDATE','AUTOMATED_STRATEGY',row.key,{'before':before,'after':serialize_auto(row),'apply_mode':p.apply_mode})
    db.commit(); db.refresh(row); return serialize_auto(row)

@router.post('/automated/actions')
def auto_action(p:BulkAutomatedAction,db:Session=Depends(get_db)):
    action=p.action.upper(); rows=db.scalars(select(AutomatedStrategy)).all()
    for r in rows:
        if action=='START_ALL':r.enabled=True;r.paused_today=False;r.status='WAITING'
        elif action=='PAUSE_ALL':r.paused_today=True;r.status='PAUSED'
        elif action=='STOP_TODAY':r.paused_today=True;r.status='STOPPED_TODAY'
        else: raise HTTPException(400,'Unsupported action')
    audit(db,action,'AUTOMATED_STRATEGIES'); db.commit(); return {'status':'ok'}

@router.get('/trades')
def trades(status:str|None=None,mode:str|None=None,source:str|None=None,strategy:str|None=None,index_symbol:str|None=None,sort:str=Query('newest',pattern='^(newest|oldest|pnl_high|pnl_low)$'),db:Session=Depends(get_db)):
    q=select(Trade)
    if status:q=q.where(Trade.status==status.upper())
    if mode:q=q.where(Trade.mode==mode.upper())
    if source:q=q.where(Trade.source==source.upper())
    if strategy:q=q.where(Trade.strategy_name==strategy)
    if index_symbol:q=q.where(Trade.index_symbol==index_symbol.upper())
    om={'newest':desc(Trade.opened_at),'oldest':asc(Trade.opened_at),'pnl_high':desc(Trade.pnl),'pnl_low':asc(Trade.pnl)}
    return [trade_dict(t) for t in db.scalars(q.order_by(om[sort])).all()]

def trade_dict(t):
    return {'id':t.id,'strategy':t.strategy_name,'source':t.source,'index':t.index_symbol,'instrument':t.instrument,'mode':t.mode,'status':t.status,
            'side':t.side,'quantity':t.quantity,'entry_price':t.entry_price,'exit_price':t.exit_price,'target_price':t.target_price,'stop_price':t.stop_price,
            'pnl':t.pnl,'opened_at':t.opened_at,'closed_at':t.closed_at,'exit_reason':t.exit_reason}

@router.put('/trades/{tid}')
def update_trade(tid:int,p:TradeUpdateRequest,db:Session=Depends(get_db)):
    t=db.get(Trade,tid)
    if not t:raise HTTPException(404,'Trade not found')
    if t.status!='OPEN':raise HTTPException(409,'Only open trades can be updated')
    if p.target_price is not None:t.target_price=p.target_price
    if p.stop_price is not None:t.stop_price=p.stop_price
    if p.quantity is not None:t.quantity=p.quantity
    audit(db,'UPDATE','TRADE',tid,p.model_dump()); db.commit(); return trade_dict(t)

@router.post('/trades/{tid}/close')
def close_trade(tid:int,p:CloseTradeRequest,db:Session=Depends(get_db)):
    t=db.get(Trade,tid)
    if not t:raise HTTPException(404,'Trade not found')
    try:t=TradingService(db).close_trade(t,p.exit_price,p.reason)
    except ValueError as e:raise HTTPException(409,str(e))
    audit(db,'CLOSE','TRADE',tid,{'pnl':t.pnl}); db.commit(); return trade_dict(t)

@router.get('/reports/summary')
def report(mode:str|None=None,source:str|None=None,strategy:str|None=None,index_symbol:str|None=None,status:str|None=None,db:Session=Depends(get_db)):
    q=select(Trade)
    for col,val in ((Trade.mode,mode),(Trade.source,source),(Trade.strategy_name,strategy),(Trade.index_symbol,index_symbol),(Trade.status,status)):
        if val:q=q.where(col==val.upper() if col is not Trade.strategy_name else val)
    rows=list(db.scalars(q).all()); closed=[t for t in rows if t.status=='CLOSED']; wins=[t for t in closed if t.pnl>0]; losses=[t for t in closed if t.pnl<0]
    gp=sum(t.pnl for t in wins); gl=abs(sum(t.pnl for t in losses)); equity=0;peak=0;dd=0
    for t in sorted(closed,key=lambda x:x.closed_at or x.opened_at): equity+=t.pnl;peak=max(peak,equity);dd=max(dd,peak-equity)
    by={}
    for t in closed:
        d=by.setdefault(t.strategy_name,{'trades':0,'wins':0,'net_pnl':0});d['trades']+=1;d['wins']+=int(t.pnl>0);d['net_pnl']+=t.pnl
    return {'trades':len(rows),'closed':len(closed),'wins':len(wins),'losses':len(losses),'win_rate':round(len(wins)/len(closed)*100,2) if closed else 0,
            'gross_profit':round(gp,2),'gross_loss':round(gl,2),'net_pnl':round(sum(t.pnl for t in closed),2),'drawdown':round(dd,2),'profit_factor':round(gp/gl,2) if gl else None,
            'by_strategy':[{'strategy':k,**v,'win_rate':round(v['wins']/v['trades']*100,2) if v['trades'] else 0} for k,v in by.items()]}

@router.get('/reports/export.csv')
def export_csv(db:Session=Depends(get_db)):
    out=io.StringIO(); w=csv.writer(out); w.writerow(['ID','Strategy','Source','Index','Instrument','Mode','Status','Qty','Entry','Exit','PnL','Opened','Closed'])
    for t in db.scalars(select(Trade).order_by(desc(Trade.opened_at))).all():w.writerow([t.id,t.strategy_name,t.source,t.index_symbol,t.instrument,t.mode,t.status,t.quantity,t.entry_price,t.exit_price,t.pnl,t.opened_at,t.closed_at])
    return StreamingResponse(iter([out.getvalue()]),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=trade_report.csv'})

@router.get('/settings')
def get_settings_api(db:Session=Depends(get_db)):return setting_map(db)

@router.put('/settings')
def update_settings_api(p:SettingsUpdate,db:Session=Depends(get_db)):
    for k,v in p.values.items():
        if k=='trading_mode' and str(v).upper()!='PAPER': raise HTTPException(409,'V2.1 is locked to PAPER mode')
        row=db.get(Setting,k)
        if row:row.value=str(v)
        else:db.add(Setting(key=k,value=str(v)))
    audit(db,'UPDATE','SETTINGS',details=p.values);db.commit();return setting_map(db)

@router.get('/audit')
def audit_list(limit:int=100,db:Session=Depends(get_db)):
    rows=db.scalars(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(min(limit,500))).all()
    return [{'id':x.id,'action':x.action,'entity':x.entity,'entity_id':x.entity_id,'details':json.loads(x.details_json or '{}'),'created_at':x.created_at} for x in rows]

@router.get('/broker/token/login-url')
def token_url():
    s=get_settings()
    try:url=generate_login_url(client_id=s.fyers_client_id,secret_key=s.fyers_secret_key,redirect_uri=s.fyers_redirect_uri)
    except (ValueError,RuntimeError) as e:raise HTTPException(400,str(e))
    return {'login_url':url}

@router.post('/broker/token/exchange')
def token_exchange(p:TokenAuthCode):
    s=get_settings()
    try:path=generate_and_store_token(p.auth_code_or_url,client_id=s.fyers_client_id,secret_key=s.fyers_secret_key,redirect_uri=s.fyers_redirect_uri,token_path=s.fyers_token_path)
    except (ValueError,RuntimeError) as e:raise HTTPException(400,str(e))
    dashboard_live_stream.restart()
    return {'status':'ok','message':'FYERS token generated and stored successfully.','path':path.name}

@router.get('/broker/token/status')
def token_status():
    return probe_fyers_connection()


@router.get('/symbols/search')
def symbol_search(q: str = "", limit: int = 20):
    return {'items': search_symbols(q, limit=limit)}


@router.post('/symbols/refresh')
def symbol_refresh():
    return refresh_symbol_master_cache(force=False)


@router.websocket('/live')
async def live_dashboard_socket(websocket: WebSocket):
    await websocket.accept()
    client_queue: Queue = Queue(maxsize=4)
    dashboard_live_stream.subscribe_client(client_queue)
    try:
        while True:
            try:
                message = await asyncio.wait_for(asyncio.to_thread(client_queue.get), timeout=30)
            except asyncio.TimeoutError:
                await websocket.send_json({'type': 'heartbeat', 'ts': datetime.utcnow().isoformat()})
                continue
            await websocket.send_json(message)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        dashboard_live_stream.unsubscribe_client(client_queue)
