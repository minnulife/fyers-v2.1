from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from app.api.routes import router
from app.api.auth_routes import router as auth_router
from app.core.config import get_settings
from app.db.init_db import init_db
from app.services.live_dashboard import dashboard_live_stream

settings = get_settings()
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / 'static'

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    dashboard_live_stream.start()
    dashboard_live_stream.refresh_from_db(force=True)
    yield
    dashboard_live_stream.stop()


app = FastAPI(title=settings.app_name, version='2.1.0', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')
app.include_router(auth_router)
app.include_router(router)

PUBLIC_PATHS = {'/', '/app', '/api/health', '/api/auth/login', '/api/auth/session'}

@app.middleware('http')
async def require_local_login(request: Request, call_next):
    path = request.url.path
    if path.startswith('/static/') or path in PUBLIC_PATHS or path == '/docs' or path.startswith('/openapi'):
        return await call_next(request)
    if path.startswith('/api/') and not request.session.get('authenticated'):
        return JSONResponse({'detail': 'Authentication required'}, status_code=401)
    return await call_next(request)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie='fyers_v21_session',
    same_site='strict',
    https_only=False,  # Set true when deployed behind HTTPS.
    max_age=8 * 60 * 60,
)

@app.get('/', include_in_schema=False)
def root():
    return RedirectResponse('/app')

@app.get('/app', include_in_schema=False)
def web_app():
    return FileResponse(STATIC_DIR / 'index.html')

@app.get('/legacy-preview', include_in_schema=False)
def legacy_preview():
    return FileResponse(STATIC_DIR / 'legacy_dashboard.html')
