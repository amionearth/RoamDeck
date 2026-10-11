import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.config import settings
from backend.app.db import engine, Base, SessionLocal
from backend.app.models.models import Game
from backend.app.services.badges import seed_badges
from backend.app.services.games import close_game_session
from backend.app.realtime import manager

# Routers
from backend.app.routers.auth import router as auth_router
from backend.app.routers.users import router as users_router
from backend.app.routers.friends import router as friends_router
from backend.app.routers.trips import router as trips_router
from backend.app.routers.feed import router as feed_router
from backend.app.routers.passport import router as passport_router
from backend.app.routers.leaderboard import router as leaderboard_router
from backend.app.routers.games import router as games_router
from backend.app.routers.phone_games import router as phone_games_router
from backend.app.routers.device import router as device_router
from backend.app.routers.weather import router as weather_router
from backend.app.routers.demo import router as demo_router
from backend.app.routers.sos import router as sos_router
from backend.app.routers.llm import router as llm_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ffh.main")

# Background game expiry task (Section 9.2: background task checks ends_at every 5 seconds)
async def check_expired_games_loop():
    while True:
        try:
            await asyncio.sleep(5)
            db = SessionLocal()
            now = datetime.now(timezone.utc)
            running_games = db.query(Game).filter(
                Game.status.in_(["open", "running"]),
                Game.ends_at.isnot(None),
                Game.ends_at <= now
            ).all()

            for g in running_games:
                logger.info(f"Closing expired game session: {g.id} ({g.code})")
                await close_game_session(db, g)

            db.close()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error checking expired games: {e}")

from backend.app.services.network import lan_ip, get_ip_candidates, get_base_url, print_startup_banner

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create tables and seed badges
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    seed_badges(db)
    db.close()

    # C3: Print startup message with LAN address, QR code, and bind warning
    print_startup_banner()

    expiry_task = asyncio.create_task(check_expired_games_loop())
    yield
    expiry_task.cancel()
    try:
        await expiry_task
    except asyncio.CancelledError:
        pass

app = FastAPI(
    title=settings.APP_NAME,
    description="FarFromHome - Blind Trip Travel Device Backend & Web Platform",
    lifespan=lifespan,
)

# CORS.
#
# The wrapped mobile app is served from a non-web origin: Capacitor uses
# ``http://localhost`` on Android and ``capacitor://localhost`` on iOS, and neither is
# a normal HTTP origin, so a plain allowlist is not enough. ``allow_origin_regex``
# covers every local-scheme origin the native shells use, plus any LAN address, which
# is what makes "same WiFi, laptop server" work from a phone without configuration.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=settings.CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# C2: Health Endpoint (no auth)
@app.get("/api/health")
def health_check():
    return {
        "ok": True,
        "app": settings.APP_NAME,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }

# C2: Network Info Endpoint (no auth)
@app.get("/api/network-info")
def get_network_info(request: Request):
    ip = lan_ip()
    candidates = get_ip_candidates()
    base_url = get_base_url(request)
    return {
        "lan_ip": ip,
        "port": settings.PORT,
        "base_url": base_url,
        "candidates": candidates,
    }

# Config endpoint
@app.get("/api/config")
def get_app_config(request: Request):
    base_url = get_base_url(request)
    return {
        "appName": settings.APP_NAME,
        "demoMode": settings.DEMO_MODE,
        "publicBaseUrl": base_url,
    }

# Mount API Routers
app.include_router(auth_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(friends_router, prefix="/api")
app.include_router(trips_router, prefix="/api")
app.include_router(feed_router, prefix="/api")
app.include_router(passport_router, prefix="/api")
app.include_router(leaderboard_router, prefix="/api")
app.include_router(games_router, prefix="/api")
app.include_router(phone_games_router, prefix="/api")
app.include_router(device_router, prefix="/api")
app.include_router(weather_router, prefix="/api")
app.include_router(sos_router, prefix="/api")
# The LLM gateway the handheld calls: keys stay here, never on the device
# (brief 01 §5.1 "Production rule").
app.include_router(llm_router, prefix="/api")
if settings.DEMO_MODE:
    app.include_router(demo_router, prefix="/api")

# Realtime WebSocket (Section 7.4)
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            raw_msg = await websocket.receive_text()
            try:
                msg = json.loads(raw_msg)
                if "subscribe" in msg:
                    await manager.subscribe(websocket, msg["subscribe"])
                elif "unsubscribe" in msg:
                    await manager.unsubscribe(websocket, msg["unsubscribe"])
                elif msg.get("event") == "radio.transmission":
                    # Broadcast audio transmission to all channel listeners except sender
                    ch = msg.get("channel", "radio:ch1")
                    await manager.broadcast(ch, "radio.transmission", msg.get("data", {}))
                elif msg.get("event") == "radio.signal":
                    ch = msg.get("channel", "radio:ch1")
                    await manager.broadcast(ch, "radio.signal", msg.get("data", {}))
            except Exception:
                pass
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception:
        await manager.disconnect(websocket)

# SPA Static Serving (Single Port 8000 support)
frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str, request: Request):
        if full_path.startswith("api/") or full_path == "ws":
            return JSONResponse(status_code=404, content={"error": "Not found"})
        target_file = frontend_dist / full_path
        if target_file.is_file():
            return FileResponse(target_file)
        return FileResponse(frontend_dist / "index.html")
