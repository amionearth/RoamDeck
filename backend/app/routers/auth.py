import secrets
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from backend.app.db import get_db
from backend.app.models.models import User
from backend.app.security import hash_password, verify_password, create_user_session_token, get_current_user_optional

router = APIRouter(prefix="/auth", tags=["auth"])

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=30)
    display_name: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=4)
    home_city: str = "Kozhikode"

class LoginRequest(BaseModel):
    username: str
    password: str

@router.post("/register")
def register(req: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    req_username = req.username.lower().strip()
    if db.query(User).filter(User.username == req_username).first():
        raise HTTPException(status_code=400, detail="Username already taken")

    friend_code = f"FFH-{secrets.token_hex(3).upper()}"
    user = User(
        username=req_username,
        display_name=req.display_name.strip(),
        password_hash=hash_password(req.password),
        home_city=req.home_city,
        friend_code=friend_code,
        xp=0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_user_session_token(user.id)
    response.set_cookie(
        key="ffh_session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400 * 30,
    )

    return {
        "status": "ok",
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "friend_code": user.friend_code,
        },
        "token": token
    }

@router.post("/login")
def login(req: LoginRequest, response: Response, db: Session = Depends(get_db)):
    req_username = req.username.lower().strip()
    user = db.query(User).filter(User.username == req_username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_user_session_token(user.id)
    response.set_cookie(
        key="ffh_session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400 * 30,
    )

    return {
        "status": "ok",
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "friend_code": user.friend_code,
        },
        "token": token
    }

@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("ffh_session")
    return {"status": "logged_out"}
