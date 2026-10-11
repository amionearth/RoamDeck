import os
from pathlib import Path
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.db import get_db
from backend.app.models.models import Trip, Photo, Like, Comment, Review, User, Friendship
from backend.app.security import get_current_user, get_current_user_optional
from backend.app.services.spoiler import serialize_trip

router = APIRouter(prefix="/trips", tags=["trips"])

class CommentCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=1000)
    parent_id: Optional[str] = None

class ReviewCreate(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    body: Optional[str] = ""

def check_trip_access(trip: Trip, viewer: Optional[User], db: Session) -> bool:
    if trip.visibility == "public":
        return True
    if not viewer:
        return False
    if trip.owner_id == viewer.id:
        return True
    if trip.visibility == "friends":
        friendship = db.query(Friendship).filter(
            ((Friendship.requester_id == viewer.id) & (Friendship.addressee_id == trip.owner_id)) |
            ((Friendship.requester_id == trip.owner_id) & (Friendship.addressee_id == viewer.id)),
            Friendship.status == "accepted"
        ).first()
        return bool(friendship)
    return False

@router.get("/{id}")
def get_trip(id: str, viewer: Optional[User] = Depends(get_current_user_optional), db: Session = Depends(get_db)):
    trip = db.query(Trip).filter(Trip.id == id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    if not check_trip_access(trip, viewer, db):
        raise HTTPException(status_code=403, detail="Access denied")

    return serialize_trip(trip, viewer)

@router.get("/{id}/photos")
def get_trip_photos(id: str, member: Optional[str] = None, viewer: Optional[User] = Depends(get_current_user_optional), db: Session = Depends(get_db)):
    trip = db.query(Trip).filter(Trip.id == id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    if not check_trip_access(trip, viewer, db):
        raise HTTPException(status_code=403, detail="Access denied")

    revealed = bool(trip.destination_revealed)
    query = db.query(Photo).filter(Photo.trip_id == trip.id)
    if not revealed:
        # Strictly hide destination photos before arrival
        query = query.filter(Photo.category != "destination")

    if member:
        query = query.filter(Photo.author_name.ilike(f"%{member}%"))

    photos = query.order_by(Photo.taken_at.asc()).all()
    return [
        {
            "id": p.id,
            "author_name": p.author_name,
            "author_user_id": p.author_user_id,
            "caption": p.caption,
            "taken_at": p.taken_at.isoformat() if p.taken_at else None,
            "category": p.category,
            "approval_status": p.approval_status,
            "task_id": p.task_id,
            "url": f"/api/trips/{trip.id}/photos/{p.id}/file",
            "thumb_url": f"/api/trips/{trip.id}/photos/{p.id}/thumb",
        }
        for p in photos
    ]

@router.get("/{id}/photos/{photo_id}/file")
def get_trip_photo_file(id: str, photo_id: str, viewer: Optional[User] = Depends(get_current_user_optional), db: Session = Depends(get_db)):
    trip = db.query(Trip).filter(Trip.id == id).first()
    if not trip or not check_trip_access(trip, viewer, db):
        raise HTTPException(status_code=404, detail="Not found")

    photo = db.query(Photo).filter(Photo.id == photo_id, Photo.trip_id == trip.id).first()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    if not trip.destination_revealed and photo.category == "destination":
        raise HTTPException(status_code=404, detail="Photo not yet available")

    file_p = Path(photo.file_path)
    if not file_p.exists():
        raise HTTPException(status_code=404, detail="Image file missing")

    return FileResponse(file_p, media_type="image/jpeg")

@router.get("/{id}/photos/{photo_id}/thumb")
def get_trip_photo_thumb(id: str, photo_id: str, viewer: Optional[User] = Depends(get_current_user_optional), db: Session = Depends(get_db)):
    trip = db.query(Trip).filter(Trip.id == id).first()
    if not trip or not check_trip_access(trip, viewer, db):
        raise HTTPException(status_code=404, detail="Not found")

    photo = db.query(Photo).filter(Photo.id == photo_id, Photo.trip_id == trip.id).first()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    if not trip.destination_revealed and photo.category == "destination":
        raise HTTPException(status_code=404, detail="Photo not yet available")

    thumb_p = Path(photo.thumb_path)
    if not thumb_p.exists():
        thumb_p = Path(photo.file_path)

    if not thumb_p.exists():
        raise HTTPException(status_code=404, detail="Image file missing")

    return FileResponse(thumb_p, media_type="image/jpeg")

@router.post("/{id}/like")
def toggle_like(id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    trip = db.query(Trip).filter(Trip.id == id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    existing = db.query(Like).filter(Like.user_id == user.id, Like.target_id == trip.id, Like.target_type == "trip").first()
    if existing:
        db.delete(existing)
        db.commit()
        liked = False
    else:
        like = Like(user_id=user.id, target_id=trip.id, target_type="trip")
        db.add(like)
        db.commit()
        liked = True

    count = db.query(Like).filter(Like.target_id == trip.id, Like.target_type == "trip").count()
    return {"liked": liked, "likes_count": count}

@router.get("/{id}/comments")
def get_comments(id: str, db: Session = Depends(get_db)):
    comments = db.query(Comment).filter(Comment.trip_id == id).order_by(Comment.created_at.asc()).all()
    out = []
    for c in comments:
        out.append({
            "id": c.id,
            "user_id": c.user_id,
            "username": c.user.username if c.user else "Explorer",
            "display_name": c.user.display_name if c.user else "Explorer",
            "avatar_path": c.user.avatar_path if c.user else None,
            "parent_id": c.parent_id,
            "body": c.body,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return out

@router.post("/{id}/comments")
def add_comment(id: str, body: CommentCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    trip = db.query(Trip).filter(Trip.id == id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    comment = Comment(
        trip_id=trip.id,
        user_id=user.id,
        parent_id=body.parent_id,
        body=body.body.strip(),
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)

    return {
        "id": comment.id,
        "user_id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "parent_id": comment.parent_id,
        "body": comment.body,
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
    }

@router.post("/{id}/review")
def add_review(id: str, req: ReviewCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    trip = db.query(Trip).filter(Trip.id == id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    if trip.status not in ["arrived", "finished"]:
        raise HTTPException(status_code=400, detail="Cannot review an unfinished trip.")

    existing = db.query(Review).filter(Review.trip_id == trip.id, Review.user_id == user.id).first()
    if existing:
        existing.rating = req.rating
        existing.body = (req.body or "").strip()
    else:
        rev = Review(
            trip_id=trip.id,
            user_id=user.id,
            rating=req.rating,
            body=(req.body or "").strip(),
        )
        db.add(rev)

    db.commit()
    return {"status": "ok"}
