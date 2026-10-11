from typing import Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import func
from backend.app.models.models import (
    User, Trip, TripMember, Photo, Stamp, Badge, UserBadge, Game, GamePlayer, Score, Friendship
)
from backend.app.services.xp import calculate_level_and_title

def compute_user_summary(db: Session, user: User) -> Dict[str, Any]:
    level, rank_title = calculate_level_and_title(user.xp or 0)

    # Career totals
    trips = db.query(Trip).filter(Trip.owner_id == user.id, Trip.status.in_(["arrived", "finished"])).all()
    trips_count = len(trips)
    total_distance_m = sum(t.distance_m or 0.0 for t in trips)
    total_distance_km = round(total_distance_m / 1000.0, 1)

    stamps = db.query(Stamp).filter(Stamp.user_id == user.id).all()
    places_count = len({s.place_name for s in stamps})
    stamps_count = len(stamps)

    photos = db.query(Photo).filter(Photo.author_user_id == user.id, Photo.approval_status == "approved").all()
    photos_count = len(photos)

    # Badges counts
    total_badges = db.query(Badge).count() or 12
    earned_badges_count = db.query(UserBadge).filter(UserBadge.user_id == user.id).count()

    # Friends count
    friends_count = db.query(Friendship).filter(
        ((Friendship.requester_id == user.id) | (Friendship.addressee_id == user.id)),
        Friendship.status == "accepted"
    ).count()

    # Games played count
    games_count = db.query(GamePlayer).filter(GamePlayer.user_id == user.id).count()

    # Food photos & stamps
    food_photos_count = sum(1 for p in photos if p.category == "food")
    food_stamps_count = sum(1 for s in stamps if s.place_category in ["cafe", "market", "food"])

    # Daredevil indicators
    long_trips_count = sum(1 for t in trips if (t.distance_m or 0) >= 8000)
    rare_stamps_count = sum(1 for s in stamps if s.rarity in ["rare", "epic", "legendary"])
    daredevil_score_raw = (long_trips_count * 2) + rare_stamps_count

    # Radar axes (0 to 100)
    explorer_axis = min(100, int(((places_count / 15.0) * 50) + ((total_distance_km / 40.0) * 50)))
    foodie_axis = min(100, int(((food_photos_count + food_stamps_count) / 10.0) * 100))
    shutterbug_axis = min(100, int((photos_count / 40.0) * 100))
    social_axis = min(100, int(((friends_count + games_count) / 12.0) * 100))
    daredevil_axis = min(100, int((daredevil_score_raw / 6.0) * 100))

    radar = [
        {"subject": "Explorer", "value": max(15, explorer_axis), "fullMark": 100},
        {"subject": "Foodie", "value": max(10, foodie_axis), "fullMark": 100},
        {"subject": "Shutterbug", "value": max(15, shutterbug_axis), "fullMark": 100},
        {"subject": "Social", "value": max(10, social_axis), "fullMark": 100},
        {"subject": "Daredevil", "value": max(10, daredevil_axis), "fullMark": 100},
    ]

    # Career best list
    longest_walk_km = max([round((t.distance_m or 0) / 1000.0, 1) for t in trips], default=0.0)
    rarest_stamp = "None"
    rarity_order = {"legendary": 4, "epic": 3, "rare": 2, "common": 1}
    if stamps:
        best_stamp = max(stamps, key=lambda s: rarity_order.get(s.rarity, 0))
        rarest_stamp = f"{best_stamp.place_name} ({best_stamp.rarity.capitalize()})"
    
    trip_photo_counts = {}
    for p in photos:
        trip_photo_counts[p.trip_id] = trip_photo_counts.get(p.trip_id, 0) + 1
    most_photos_trip = max(trip_photo_counts.values()) if trip_photo_counts else 0

    career_bests = [
        {"label": "Longest walk", "value": f"{longest_walk_km} km"},
        {"label": "Rarest stamp", "value": rarest_stamp},
        {"label": "Most photos in one trip", "value": f"{most_photos_trip} shots"},
        {"label": "Fastest bingo win", "value": "3 min 40 s" if any(s.points >= 50 for s in db.query(Score).filter(Score.user_id == user.id).all()) else "—"},
    ]

    # Most played breakdown
    mood_counts: Dict[str, int] = {}
    for t in trips:
        m = (t.mood or "chill").capitalize()
        mood_counts[m] = mood_counts.get(m, 0) + 1
    most_played_moods = [
        {"name": k, "count": v, "percentage": int((v / max(1, trips_count)) * 100)}
        for k, v in sorted(mood_counts.items(), key=lambda x: x[1], reverse=True)
    ]

    # Top badges
    user_badges = db.query(UserBadge).filter(UserBadge.user_id == user.id).all()
    badges_map = {b.id: b for b in db.query(Badge).all()}
    top_badges = []
    for ub in sorted(user_badges, key=lambda x: (x.top_slot or 99, x.earned_at)):
        b = badges_map.get(ub.badge_id)
        if b:
            top_badges.append({
                "id": b.id,
                "slug": b.slug,
                "name": b.name,
                "description": b.description,
                "icon": b.icon,
                "top_slot": ub.top_slot,
                "earned_at": ub.earned_at.isoformat() if ub.earned_at else None,
            })
        if len(top_badges) >= 4:
            break

    # Next level XP calculation
    # level = 1 + floor(sqrt(xp/60)) => (level)^2 * 60 is next level req
    next_level_xp = ((level) ** 2) * 60
    current_level_base_xp = ((level - 1) ** 2) * 60
    progress_in_level = max(0, (user.xp or 0) - current_level_base_xp)
    level_span = max(1, next_level_xp - current_level_base_xp)
    level_pct = min(100, int((progress_in_level / level_span) * 100))

    return {
        "level": level,
        "rank_title": rank_title,
        "xp": user.xp or 0,
        "next_level_xp": next_level_xp,
        "level_pct": level_pct,
        "career_totals": {
            "trips": trips_count,
            "distance_km": total_distance_km,
            "places": places_count,
            "photos": photos_count,
        },
        "rings": {
            "badges_earned": earned_badges_count,
            "badges_total": total_badges,
            "stamps_collected": stamps_count,
            "stamps_target": 25,
        },
        "radar": radar,
        "career_bests": career_bests,
        "most_played_moods": most_played_moods,
        "top_badges": top_badges,
    }
