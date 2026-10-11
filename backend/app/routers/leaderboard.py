from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from backend.app.db import get_db
from backend.app.models.models import User, Friendship, Stamp, Photo, Score
from backend.app.security import get_current_user_optional
from backend.app.services.xp import calculate_level_and_title

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])

RARITY_STAMP_POINTS = {
    "common": 25,
    "rare": 50,
    "epic": 75,
    "legendary": 100,
}

@router.get("")
def get_leaderboard(
    scope: str = Query("global", regex="^(global|friends)$"),
    range: str = Query("all", regex="^(all|week)$"),
    metric: str = Query("points", regex="^(points|places|photos)$"),
    viewer: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    # Determine users in scope
    user_ids = None
    if scope == "friends":
        if not viewer:
            return []
        friendships = db.query(Friendship).filter(
            ((Friendship.requester_id == viewer.id) | (Friendship.addressee_id == viewer.id)),
            Friendship.status == "accepted"
        ).all()
        ids = {viewer.id}
        for f in friendships:
            ids.add(f.addressee_id if f.requester_id == viewer.id else f.requester_id)
        user_ids = list(ids)

    query = db.query(User)
    if user_ids is not None:
        query = query.filter(User.id.in_(user_ids))
    users = query.all()

    # Pre-fetch counts for efficiency
    stamp_map = {}
    for s in db.query(Stamp).all():
        stamp_map.setdefault(s.user_id, []).append(s)

    photo_map = {}
    for p in db.query(Photo).filter(Photo.approval_status == "approved", Photo.author_user_id.isnot(None)).all():
        photo_map.setdefault(p.author_user_id, []).append(p)

    score_map = {}
    for sc in db.query(Score).filter(Score.user_id.isnot(None)).all():
        score_map.setdefault(sc.user_id, []).append(sc)

    leaderboard = []
    for u in users:
        u_stamps = stamp_map.get(u.id, [])
        u_photos = photo_map.get(u.id, [])
        u_scores = score_map.get(u.id, [])

        if metric == "places":
            val = len({s.place_name for s in u_stamps})
        elif metric == "photos":
            val = len(u_photos)
        else: # points
            game_pts = sum(sc.points for sc in u_scores)
            stamp_pts = sum(RARITY_STAMP_POINTS.get(s.rarity, 25) for s in u_stamps)
            val = game_pts + stamp_pts + (u.xp or 0)

        level, rank_title = calculate_level_and_title(u.xp or 0)
        leaderboard.append({
            "user_id": u.id,
            "username": u.username,
            "display_name": u.display_name,
            "avatar_path": u.avatar_path,
            "level": level,
            "rank_title": rank_title,
            "value": val,
            "is_current_user": bool(viewer and viewer.id == u.id),
        })

    # Sort descending by value
    leaderboard.sort(key=lambda x: x["value"], reverse=True)

    # Assign ranks
    for idx, row in enumerate(leaderboard):
        row["rank"] = idx + 1

    return leaderboard
