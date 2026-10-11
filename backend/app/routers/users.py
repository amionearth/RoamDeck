import json
from pathlib import Path
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.db import get_db
from backend.app.models.models import User, Friendship, UserBadge
from backend.app.security import get_current_user, get_current_user_optional
from backend.app.services.images import process_and_save_image
from backend.app.services.scoring import compute_user_summary
from backend.app.services.xp import calculate_level_and_title

router = APIRouter(tags=["users"])

class UpdateMeRequest(BaseModel):
    display_name: Optional[str] = None
    bio: Optional[str] = None
    home_city: Optional[str] = None
    is_private: Optional[bool] = None
    trusted_contact_name: Optional[str] = None
    trusted_contact_phone: Optional[str] = None

class UpdatePlayercardRequest(BaseModel):
    banner_photo_id: Optional[str] = None
    title: Optional[str] = None
    featured_stamp_id: Optional[str] = None
    top_badge_ids: Optional[List[str]] = None

@router.get("/me")
def get_me(user: User = Depends(get_current_user)):
    level, rank_title = calculate_level_and_title(user.xp or 0)
    playercard = {}
    if user.playercard_json:
        try:
            playercard = json.loads(user.playercard_json)
        except Exception:
            pass

    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "bio": user.bio,
        "avatar_path": user.avatar_path,
        "home_city": user.home_city,
        "is_private": user.is_private,
        "xp": user.xp,
        "level": level,
        "rank_title": rank_title,
        "friend_code": user.friend_code,
        "trusted_contact_name": user.trusted_contact_name,
        "trusted_contact_phone": user.trusted_contact_phone,
        "playercard": playercard,
    }

@router.patch("/me")
def update_me(req: UpdateMeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if req.display_name is not None:
        user.display_name = req.display_name.strip()
    if req.bio is not None:
        user.bio = req.bio.strip()
    if req.home_city is not None:
        user.home_city = req.home_city.strip()
    if req.is_private is not None:
        user.is_private = req.is_private
    if req.trusted_contact_name is not None:
        user.trusted_contact_name = req.trusted_contact_name.strip()
    if req.trusted_contact_phone is not None:
        user.trusted_contact_phone = req.trusted_contact_phone.strip()

    db.commit()
    return {"status": "ok", "user": {"id": user.id, "display_name": user.display_name}}

@router.post("/me/avatar")
async def upload_avatar(file: UploadFile = File(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    contents = await file.read()
    dest = settings.DATA_DIR / "users" / user.id / "avatar.jpg"
    thumb = settings.DATA_DIR / "users" / user.id / "avatar_thumb.jpg"
    
    process_and_save_image(contents, dest, thumb, max_size=(600, 600), thumb_size=(150, 150))
    user.avatar_path = f"/api/users/{user.username}/avatar"
    db.commit()
    return {"status": "ok", "avatar_path": user.avatar_path}

@router.patch("/me/playercard")
def update_playercard(req: UpdatePlayercardRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    card_data = {}
    if user.playercard_json:
        try:
            card_data = json.loads(user.playercard_json)
        except Exception:
            pass

    if req.banner_photo_id is not None:
        card_data["banner_photo_id"] = req.banner_photo_id
    if req.title is not None:
        card_data["title"] = req.title
    if req.featured_stamp_id is not None:
        card_data["featured_stamp_id"] = req.featured_stamp_id

    user.playercard_json = json.dumps(card_data)

    if req.top_badge_ids is not None:
        # Reset current top slots
        for ub in user.user_badges:
            ub.top_slot = None
        for idx, badge_id in enumerate(req.top_badge_ids[:4]):
            ub = db.query(UserBadge).filter(UserBadge.user_id == user.id, UserBadge.badge_id == badge_id).first()
            if ub:
                ub.top_slot = idx + 1

    db.commit()
    return {"status": "ok", "playercard": card_data}

@router.get("/users/search")
def search_users(q: str = "", current_user: Optional[User] = Depends(get_current_user_optional), db: Session = Depends(get_db)):
    query_str = q.strip().lower()
    if not query_str:
        return []

    users = db.query(User).filter(
        (User.username.contains(query_str)) | (User.display_name.ilike(f"%{query_str}%")) | (User.friend_code == query_str.upper())
    ).limit(20).all()

    out = []
    for u in users:
        level, rank_title = calculate_level_and_title(u.xp or 0)
        out.append({
            "id": u.id,
            "username": u.username,
            "display_name": u.display_name,
            "avatar_path": u.avatar_path,
            "level": level,
            "rank_title": rank_title,
            "friend_code": u.friend_code,
        })
    return out

@router.get("/users/{username}")
def get_user_profile(username: str, viewer: Optional[User] = Depends(get_current_user_optional), db: Session = Depends(get_db)):
    target_user = db.query(User).filter(User.username == username.lower().strip()).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    is_self = viewer and viewer.id == target_user.id
    is_friend = False
    friendship_status = "none" # "accepted", "pending_incoming", "pending_outgoing", "none"
    if viewer and not is_self:
        fs = db.query(Friendship).filter(
            ((Friendship.requester_id == viewer.id) & (Friendship.addressee_id == target_user.id)) |
            ((Friendship.requester_id == target_user.id) & (Friendship.addressee_id == viewer.id))
        ).first()
        if fs:
            if fs.status == "accepted":
                is_friend = True
                friendship_status = "accepted"
            elif fs.requester_id == viewer.id:
                friendship_status = "pending_outgoing"
            else:
                friendship_status = "pending_incoming"

    level, rank_title = calculate_level_and_title(target_user.xp or 0)

    # Privacy check (Section 9.8: Private profiles show only the name, avatar and rank to non-friends)
    if target_user.is_private and not is_self and not is_friend:
        return {
            "id": target_user.id,
            "username": target_user.username,
            "display_name": target_user.display_name,
            "avatar_path": target_user.avatar_path,
            "level": level,
            "rank_title": rank_title,
            "is_private": True,
            "is_friend": False,
            "is_self": False,
            "friendship_status": friendship_status,
            "friend_code": target_user.friend_code,
            "bio": "",
        }

    playercard = {}
    if target_user.playercard_json:
        try:
            playercard = json.loads(target_user.playercard_json)
        except Exception:
            pass

    return {
        "id": target_user.id,
        "username": target_user.username,
        "display_name": target_user.display_name,
        "bio": target_user.bio,
        "avatar_path": target_user.avatar_path,
        "home_city": target_user.home_city,
        "is_private": target_user.is_private,
        "level": level,
        "rank_title": rank_title,
        "xp": target_user.xp or 0,
        "is_friend": is_friend,
        "is_self": bool(is_self),
        "friendship_status": friendship_status,
        "friend_code": target_user.friend_code,
        "playercard": playercard,
    }

@router.get("/users/{username}/summary")
def get_user_summary(username: str, viewer: Optional[User] = Depends(get_current_user_optional), db: Session = Depends(get_db)):
    target_user = db.query(User).filter(User.username == username.lower().strip()).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    is_self = viewer and viewer.id == target_user.id
    is_friend = False
    if viewer and not is_self:
        friendship = db.query(Friendship).filter(
            ((Friendship.requester_id == viewer.id) & (Friendship.addressee_id == target_user.id)) |
            ((Friendship.requester_id == target_user.id) & (Friendship.addressee_id == viewer.id)),
            Friendship.status == "accepted"
        ).first()
        is_friend = bool(friendship)

    if target_user.is_private and not is_self and not is_friend:
        raise HTTPException(status_code=403, detail="This profile is private.")

    return compute_user_summary(db, target_user)
