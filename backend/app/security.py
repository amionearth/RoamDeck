import hashlib
import hmac
import secrets
import json
import base64
import time
from typing import Optional, Tuple
from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.db import get_db
from backend.app.models.models import User, Device, GamePlayer

def hash_password(password: str) -> str:
    # Use PBKDF2 with SHA256 and salt for secure, standard, dependency-free reliability
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}${key.hex()}"

def verify_password(password: str, hashed: str) -> bool:
    try:
        salt, key_hex = hashed.split("$", 1)
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
        return hmac.compare_digest(key.hex(), key_hex)
    except Exception:
        return False

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def create_signed_token(payload: dict) -> str:
    raw = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    sig = hmac.new(settings.SECRET_KEY.encode('utf-8'), raw, hashlib.sha256).digest()
    return f"{base64.urlsafe_b64encode(raw).decode('utf-8')}.{base64.urlsafe_b64encode(sig).decode('utf-8')}"

def verify_signed_token(token: str) -> Optional[dict]:
    try:
        raw_b64, sig_b64 = token.split(".", 1)
        raw = base64.urlsafe_b64decode(raw_b64)
        sig = base64.urlsafe_b64decode(sig_b64)
        expected_sig = hmac.new(settings.SECRET_KEY.encode('utf-8'), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        return json.loads(raw.decode('utf-8'))
    except Exception:
        return None

def create_user_session_token(user_id: str) -> str:
    return create_signed_token({"sub": user_id, "type": "session", "t": time.time()})

def create_player_token(game_id: str, player_id: str, display_name: str) -> str:
    return create_signed_token({"game_id": game_id, "player_id": player_id, "name": display_name, "t": time.time()})

def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    # Check httpOnly cookie first
    token = request.cookies.get("ffh_session")
    if not token:
        # Fallback to Authorization header Bearer token
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1].strip()

    if not token:
        return None

    data = verify_signed_token(token)
    if not data or data.get("type") != "session":
        return None

    user = db.query(User).filter(User.id == data.get("sub")).first()
    return user

def get_current_user(user: Optional[User] = Depends(get_current_user_optional)) -> User:
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user

def get_current_device(request: Request, db: Session = Depends(get_db)) -> Device:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Device bearer token required")
    
    token = auth_header.split(" ", 1)[1].strip()
    token_h = hash_token(token)

    device = db.query(Device).filter(Device.token_hash == token_h).first()
    if not device:
        raise HTTPException(status_code=401, detail="Invalid device token")
    return device

def get_current_player(request: Request, db: Session = Depends(get_db)) -> Tuple[dict, GamePlayer]:
    token = request.headers.get("X-Player-Token") or request.query_params.get("player_token")
    if not token:
        raise HTTPException(status_code=401, detail="Player token required")
    data = verify_signed_token(token)
    if not data or not data.get("player_id") or not data.get("game_id"):
        raise HTTPException(status_code=401, detail="Invalid player token")
    
    player = db.query(GamePlayer).filter(GamePlayer.id == data["player_id"]).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return data, player
