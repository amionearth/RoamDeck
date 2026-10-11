import io
import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response as RawResponse
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from backend.app.db import get_db
from backend.app.models.models import Friendship, User
from backend.app.security import get_current_user, get_current_user_optional
from backend.app.services.xp import award_xp
from backend.app.services.badges import award_badge_if_earned
from backend.app.services.network import get_base_url

router = APIRouter(prefix="/friends", tags=["friends"])

class FriendRequestBody(BaseModel):
    username_or_code: str
    source: Optional[str] = "search" # search, code, game, meetup

@router.get("")
def list_friends(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Accepted friendships
    accepted = db.query(Friendship).filter(
        ((Friendship.requester_id == user.id) | (Friendship.addressee_id == user.id)),
        Friendship.status == "accepted"
    ).all()

    # Pending incoming requests
    pending_incoming = db.query(Friendship).filter(
        Friendship.addressee_id == user.id,
        Friendship.status == "pending"
    ).all()

    # Pending outgoing requests
    pending_outgoing = db.query(Friendship).filter(
        Friendship.requester_id == user.id,
        Friendship.status == "pending"
    ).all()

    friends_list = []
    for f in accepted:
        friend_id = f.addressee_id if f.requester_id == user.id else f.requester_id
        friend_user = db.query(User).filter(User.id == friend_id).first()
        if friend_user:
            friends_list.append({
                "friendship_id": f.id,
                "user_id": friend_user.id,
                "username": friend_user.username,
                "display_name": friend_user.display_name,
                "avatar_path": friend_user.avatar_path,
                "source": f.source,
                "home_city": friend_user.home_city,
            })

    incoming_list = []
    for f in pending_incoming:
        sender = db.query(User).filter(User.id == f.requester_id).first()
        if sender:
            incoming_list.append({
                "friendship_id": f.id,
                "user_id": sender.id,
                "username": sender.username,
                "display_name": sender.display_name,
                "avatar_path": sender.avatar_path,
                "source": f.source,
            })

    base_url = get_base_url(request)
    friend_code = user.friend_code
    invite_url = f"{base_url}/friends?add={friend_code}"
    qr_url = f"/api/friends/qr.png"

    return {
        "friends": friends_list,
        "pending_requests": incoming_list,
        "outgoing_count": len(pending_outgoing),
        "friend_code": friend_code,
        "invite_url": invite_url,
        "qr_url": qr_url,
    }

@router.get("/qr.png")
def get_friend_qr_png(
    request: Request,
    code: Optional[str] = None,
    username: Optional[str] = None,
    user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    base_url = get_base_url(request)
    if username:
        invite_url = f"{base_url}/u/{username}"
    elif code:
        # Check if code corresponds to a user's friend_code
        u = db.query(User).filter(User.friend_code == code.strip().upper()).first()
        if u:
            invite_url = f"{base_url}/u/{u.username}?from_qr=1"
        else:
            invite_url = f"{base_url}/friends?add={code}"
    else:
        friend_code = user.friend_code if user else "FFH-EXPLORER"
        target_username = user.username if user else "explorer"
        invite_url = f"{base_url}/u/{target_username}?from_qr=1"

    qr_img = qrcode.make(invite_url)
    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    buf.seek(0)
    return RawResponse(content=buf.getvalue(), media_type="image/png")

@router.post("/request")
def request_friend(body: FriendRequestBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query_target = body.username_or_code.strip()
    target_user = db.query(User).filter(
        (User.username == query_target.lower()) | (User.friend_code == query_target.upper())
    ).first()

    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    if target_user.id == user.id:
        raise HTTPException(status_code=400, detail="Cannot send friend request to yourself")

    existing = db.query(Friendship).filter(
        ((Friendship.requester_id == user.id) & (Friendship.addressee_id == target_user.id)) |
        ((Friendship.requester_id == target_user.id) & (Friendship.addressee_id == user.id))
    ).first()

    if existing:
        if existing.status == "accepted":
            return {"status": "already_friends"}
        if existing.requester_id == target_user.id:
            # Auto-accept mutual request
            existing.status = "accepted"
            award_xp(db, user, "add_friend", 5, ref=target_user.id)
            award_xp(db, target_user, "add_friend", 5, ref=user.id)
            if existing.source in ["game", "meetup"]:
                award_badge_if_earned(db, user.id, "new_friend")
                award_badge_if_earned(db, target_user.id, "new_friend")
            db.commit()
            return {"status": "accepted"}
        return {"status": "request_already_pending"}

    fs = Friendship(
        requester_id=user.id,
        addressee_id=target_user.id,
        status="pending",
        source=body.source or "search",
    )
    db.add(fs)
    db.commit()
    return {"status": "sent", "friendship_id": fs.id}

@router.post("/{friendship_id}/accept")
def accept_friend(friendship_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    fs = db.query(Friendship).filter(Friendship.id == friendship_id).first()
    if not fs:
        raise HTTPException(status_code=404, detail="Request not found")
    if fs.addressee_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized to accept this request")

    fs.status = "accepted"
    other_user = db.query(User).filter(User.id == fs.requester_id).first()
    award_xp(db, user, "add_friend", 5, ref=fs.requester_id)
    if other_user:
        award_xp(db, other_user, "add_friend", 5, ref=user.id)
    if fs.source in ["game", "meetup"]:
        award_badge_if_earned(db, user.id, "new_friend")
        if other_user:
            award_badge_if_earned(db, other_user.id, "new_friend")

    db.commit()
    return {"status": "accepted"}

@router.delete("/{friendship_id}")
def remove_friend(friendship_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    fs = db.query(Friendship).filter(Friendship.id == friendship_id).first()
    if not fs:
        raise HTTPException(status_code=404, detail="Friendship not found")
    if fs.requester_id != user.id and fs.addressee_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    db.delete(fs)
    db.commit()
    return {"status": "removed"}
