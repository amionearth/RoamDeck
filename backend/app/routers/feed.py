from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from backend.app.db import get_db
from backend.app.models.models import Trip, User, Friendship
from backend.app.security import get_current_user_optional
from backend.app.services.spoiler import serialize_trip

router = APIRouter(prefix="/feed", tags=["feed"])

@router.get("")
def get_feed(
    cursor: Optional[str] = None,
    limit: int = Query(10, le=50),
    viewer: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    query = db.query(Trip).order_by(Trip.created_at.desc())

    if cursor:
        query = query.filter(Trip.id < cursor)

    # Get allowed trips: public trips, own trips, or friends' trips
    trips = query.limit(limit * 2).all()

    friend_ids = set()
    if viewer:
        friendships = db.query(Friendship).filter(
            ((Friendship.requester_id == viewer.id) | (Friendship.addressee_id == viewer.id)),
            Friendship.status == "accepted"
        ).all()
        for f in friendships:
            friend_ids.add(f.addressee_id if f.requester_id == viewer.id else f.requester_id)

    visible_trips = []
    for t in trips:
        if t.visibility == "public":
            visible_trips.append(t)
        elif viewer and t.owner_id == viewer.id:
            visible_trips.append(t)
        elif viewer and t.visibility == "friends" and t.owner_id in friend_ids:
            visible_trips.append(t)

        if len(visible_trips) == limit:
            break

    next_cursor = visible_trips[-1].id if visible_trips and len(visible_trips) == limit else None

    items = [serialize_trip(t, viewer) for t in visible_trips]

    return {
        "items": items,
        "next_cursor": next_cursor,
    }
