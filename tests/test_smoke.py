import os
from pathlib import Path

os.environ.setdefault('ADMIN_USERNAME', 'admin')
os.environ.setdefault('ADMIN_PASSWORD', 'test-password')
os.environ.setdefault('SECRET_KEY', 'test-secret-key-that-is-long-enough')
os.environ.setdefault('DATABASE_URL', 'sqlite:///./data/test_trading_v2.db')
os.environ.setdefault('TRADING_MODE', 'PAPER')
os.environ.setdefault('LIVE_TRADING_ENABLED', 'false')

from fastapi.testclient import TestClient
from app.main import app


def test_health_and_authenticated_paper_flow():
    Path('data').mkdir(exist_ok=True)
    with TestClient(app) as client:
        health = client.get('/api/health')
        assert health.status_code == 200
        assert health.json()['mode'] == 'PAPER'

        blocked = client.get('/api/dashboard')
        assert blocked.status_code == 401

        login = client.post('/api/auth/login', json={'username': 'admin', 'password': 'test-password'})
        assert login.status_code == 200

        created = client.post('/api/strategies', json={
            'name': 'Smoke Strategy', 'strategy_type': 'OPTION_PREMIUM',
            'index_symbol': 'NIFTY', 'side': 'CE', 'confirmation': 'INSTANT',
            'lots': 1, 'entry': 100, 'target': 120, 'stop_loss': 90,
            'strike_mode': 'AUTO', 'trigger_description': 'Waiting for premium 100'
        })
        assert created.status_code == 201
        strategy_id = created.json()['id']

        executed = client.post(f'/api/strategies/{strategy_id}/execute', json={
            'market_price': 100, 'instrument': 'NIFTY-TEST-CE'
        })
        assert executed.status_code == 200
        trade_id = executed.json()['trade_id']

        closed = client.post(f'/api/trades/{trade_id}/close', json={
            'exit_price': 110, 'reason': 'TEST_EXIT'
        })
        assert closed.status_code == 200
        assert closed.json()['pnl'] == 10.0
