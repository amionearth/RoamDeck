import secrets
from datetime import datetime, timezone
from backend.app.models.models import Stamp, Trip, User
from sqlalchemy.orm import Session

CATEGORY_WEIGHTS = {
    "beach": 1.0,
    "park": 1.0,
    "cafe": 1.0,
    "market": 2.0,
    "temple": 3.0,
    "heritage": 3.0,
    "viewpoint": 3.0,
    "other": 2.0,
}

def calculate_rarity(category: str, distance_m: float, arrival_time: datetime) -> str:
    cat = (category or "").lower().strip()
    cat_weight = CATEGORY_WEIGHTS.get(cat, 2.0)
    distance_km = (distance_m or 0.0) / 1000.0
    dist_bonus = min(distance_km, 10.0) * 0.3

    # Check if within 1 hour of sunrise (around 06:15) or sunset (around 18:30)
    hour = arrival_time.hour + (arrival_time.minute / 60.0)
    sun_bonus = 0.0
    if (5.25 <= hour <= 7.25) or (17.5 <= hour <= 19.5):
        sun_bonus = 1.0

    score = cat_weight + dist_bonus + sun_bonus
    if score < 2.5:
        return "common"
    elif score < 4.0:
        return "rare"
    elif score < 5.5:
        return "epic"
    else:
        return "legendary"

def generate_stamp_serial(place_name: str) -> str:
    prefix = "".join(c for c in place_name.upper() if c.isalnum())[:4] or "TRIP"
    token = secrets.token_hex(4).upper()
    return f"{prefix}-{token}"

def award_stamp_for_trip(db: Session, trip: Trip, user: User) -> Stamp:
    existing = db.query(Stamp).filter(Stamp.trip_id == trip.id, Stamp.user_id == user.id).first()
    if existing:
        return existing

    arrival_time = trip.revealed_at or datetime.now(timezone.utc)
    rarity = calculate_rarity(trip.destination_category, trip.distance_m, arrival_time)
    serial = generate_stamp_serial(trip.destination_name)

    stamp = Stamp(
        user_id=user.id,
        trip_id=trip.id,
        place_name=trip.destination_name,
        place_category=trip.destination_category or "viewpoint",
        lat=trip.destination_lat,
        lng=trip.destination_lng,
        rarity=rarity,
        serial=serial,
        earned_at=arrival_time,
    )
    db.add(stamp)
    db.flush()
    return stamp
