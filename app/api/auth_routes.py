import secrets
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from app.core.config import get_settings

router = APIRouter(prefix="/api/auth")

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)

@router.post('/login')
def login(payload: LoginRequest, request: Request):
    settings = get_settings()
    valid_user = secrets.compare_digest(payload.username, settings.admin_username)
    valid_password = secrets.compare_digest(payload.password, settings.admin_password)
    if not (valid_user and valid_password):
        raise HTTPException(status_code=401, detail='Invalid username or password')
    request.session['authenticated'] = True
    request.session['username'] = settings.admin_username
    return {'status': 'ok', 'username': settings.admin_username, 'mode': settings.trading_mode}

@router.post('/logout')
def logout(request: Request):
    request.session.clear()
    return {'status': 'ok'}

@router.get('/session')
def session(request: Request):
    return {
        'authenticated': bool(request.session.get('authenticated')),
        'username': request.session.get('username'),
    }
