import os
os.environ.setdefault('ADMIN_USERNAME','admin')
os.environ.setdefault('ADMIN_PASSWORD','test-password')
os.environ.setdefault('SECRET_KEY','test-secret-key-long')
os.environ.setdefault('DATABASE_URL','sqlite:///./data/test_v21.db')
from fastapi.testclient import TestClient
from app.main import app


def login(client):
    r=client.post('/api/auth/login',json={'username':'admin','password':'test-password'})
    assert r.status_code==200, r.text


def test_v21_feature_surface():
    with TestClient(app) as c:
        login(c)
        h=c.get('/api/health'); assert h.status_code==200 and h.json()['version']=='2.1.0'
        d=c.get('/api/dashboard'); assert d.status_code==200
        assert [x['symbol'] for x in d.json()['core_markets']]==['NIFTY','BANKNIFTY','SENSEX']
        a=c.get('/api/automated'); assert a.status_code==200 and len(a.json())==4
        u=c.put('/api/automated/ORB',json={'enabled':True,'indices':['NIFTY','BANKNIFTY'],'config':{'lots':2},'apply_mode':'NEXT_SESSION'})
        assert u.status_code==200 and u.json()['config']['lots']==2
        s=c.post('/api/strategies',json={'name':'Premium 160','strategy_type':'OPTION_PREMIUM','index_symbol':'NIFTY','side':'CE','confirmation':'1M_CLOSE','validity':'TODAY','trailing_mode':'STEP','lots':1,'entry':160,'target':190,'stop_loss':145,'strike_mode':'AUTO_NEAREST_PREMIUM','premium_tolerance':5,'trigger_description':'Waiting for nearest CE premium around 160'})
        assert s.status_code==201, s.text
        sid=s.json()['id']
        e=c.post(f'/api/strategies/{sid}/execute',json={'market_price':160,'instrument':'NIFTY-OPTION'})
        assert e.status_code==200, e.text
        tid=e.json()['trade_id']
        assert c.put(f'/api/trades/{tid}',json={'target_price':195,'stop_price':148}).status_code==200
        assert c.post(f'/api/trades/{tid}/close',json={'exit_price':180,'reason':'TEST'}).status_code==200
        assert c.get('/api/reports/summary?mode=PAPER').status_code==200
        assert c.put('/api/settings',json={'values':{'trading_mode':'PAPER','automated_max_trades':'3'}}).status_code==200
        assert c.get('/api/audit').status_code==200
