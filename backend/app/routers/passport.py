from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.app.db import get_db
from backend.app.models.models import Stamp, Badge, UserBadge, User, Trip
from backend.app.security import get_current_user, get_current_user_optional

router = APIRouter(tags=["passport"])

@router.get("/passport")
def get_passport(user: Optional[User] = Depends(get_current_user_optional), db: Session = Depends(get_db)):
    if not user:
        # Fallback to default explorer user for smooth demonstration
        user = db.query(User).filter(User.username == "explorer").first()
    if not user:
        return {"stamps": [], "total_stamps": 0, "categories_collected": 0, "categories_total": 8, "rarities": {"common": 0, "rare": 0, "epic": 0, "legendary": 0}}

    stamps = db.query(Stamp).filter(Stamp.user_id == user.id).order_by(Stamp.earned_at.desc()).all()
    
    categories = {"beach", "park", "cafe", "market", "temple", "heritage", "viewpoint", "other"}
    collected_categories = {s.place_category for s in stamps}

    stamps_list = []
    for s in stamps:
        trip = db.query(Trip).filter(Trip.id == s.trip_id).first()
        img_url = f"/api/trips/{trip.id}/cover" if (trip and trip.cover_photo_id) else "/seed/photos/images.jpg"
        stamps_list.append({
            "id": s.id,
            "place_name": s.place_name,
            "place_category": s.place_category,
            "lat": s.lat,
            "lng": s.lng,
            "rarity": s.rarity,
            "serial": s.serial,
            "earned_at": s.earned_at.isoformat() if s.earned_at else None,
            "trip_id": s.trip_id,
            "image_url": img_url,
            "cover_url": img_url,
        })

    return {
        "stamps": stamps_list,
        "total_stamps": len(stamps_list),
        "categories_collected": len(collected_categories),
        "categories_total": len(categories),
        "rarities": {
            "common": sum(1 for s in stamps if s.rarity == "common"),
            "rare": sum(1 for s in stamps if s.rarity == "rare"),
            "epic": sum(1 for s in stamps if s.rarity == "epic"),
            "legendary": sum(1 for s in stamps if s.rarity == "legendary"),
        }
    }

@router.get("/badges")
def get_badges(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    all_badges = db.query(Badge).all()
    user_badges = {ub.badge_id: ub for ub in db.query(UserBadge).filter(UserBadge.user_id == user.id).all()}

    out = []
    for b in all_badges:
        ub = user_badges.get(b.id)
        out.append({
            "id": b.id,
            "slug": b.slug,
            "name": b.name,
            "description": b.description,
            "icon": b.icon,
            "earned": ub is not None,
            "earned_at": ub.earned_at.isoformat() if ub and ub.earned_at else None,
            "top_slot": ub.top_slot if ub else None,
        })
    return out
