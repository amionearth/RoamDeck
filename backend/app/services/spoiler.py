import json
from typing import Optional, Dict, Any, List
from backend.app.models.models import Trip, User, Photo

def serialize_trip(trip: Trip, viewer: Optional[User] = None) -> Dict[str, Any]:
    revealed = bool(trip.destination_revealed)

    # Base serialized dictionary
    data: Dict[str, Any] = {
        "id": trip.id,
        "owner_id": trip.owner_id,
        "device_id": trip.device_id,
        "title": trip.title,
        "description": trip.description,
        "mood": trip.mood,
        "status": trip.status,
        "visibility": trip.visibility,
        "destination_revealed": revealed,
        "revealed_at": trip.revealed_at.isoformat() if trip.revealed_at else None,
        "start_at": trip.start_at.isoformat() if trip.start_at else None,
        "end_at": trip.end_at.isoformat() if trip.end_at else None,
        "distance_m": trip.distance_m,
        "summary": trip.summary,
        "cover_photo_id": trip.cover_photo_id,
        "calendar_at": trip.calendar_at.isoformat() if trip.calendar_at else None,
        "created_at": trip.created_at.isoformat() if trip.created_at else None,
    }

    # Add owner summary if loaded
    if trip.owner:
        data["owner"] = {
            "id": trip.owner.id,
            "username": trip.owner.username,
            "display_name": trip.owner.display_name,
            "avatar_path": trip.owner.avatar_path,
        }

    # Destination gating
    if revealed:
        data["destination_name"] = trip.destination_name
        data["destination_lat"] = trip.destination_lat
        data["destination_lng"] = trip.destination_lng
        data["destination_category"] = trip.destination_category
    else:
        # Strictly redacted for ALL viewers including owner!
        data["destination_name"] = "???"
        data["destination_lat"] = None
        data["destination_lng"] = None
        data["destination_category"] = None

    # Parse and filter plan_json
    plan: Dict[str, Any] = {}
    if trip.plan_json:
        try:
            plan = json.loads(trip.plan_json)
        except Exception:
            plan = {}

    if not revealed:
        # Filter plan destination
        if "destination" in plan:
            plan["destination"] = {
                "name": "???",
                "secret": True
            }
        # Filter unrevealed recipe parts
        if "recipe" in plan and isinstance(plan["recipe"], dict):
            recipe = dict(plan["recipe"])
            if recipe.get("reveal_on") == "arrival":
                recipe["dish_name"] = "Secret dish"
                recipe["steps"] = ["Dish steps hidden until arrival"]
            plan["recipe"] = recipe
    data["plan"] = plan

    # Members
    data["members"] = [
        {
            "id": m.id,
            "user_id": m.user_id,
            "display_name": m.display_name,
            "role": m.role,
            "color": m.color,
        }
        for m in (trip.members or [])
    ]

    # Waypoints: filter unrevealed ones if trip is not revealed
    waypoints_out = []
    for wp in sorted(trip.waypoints or [], key=lambda w: w.idx):
        if revealed or wp.revealed_at is not None:
            waypoints_out.append({
                "id": wp.id,
                "idx": wp.idx,
                "lat": wp.lat,
                "lng": wp.lng,
                "radius_m": wp.radius_m,
                "hint": wp.hint,
                "revealed_at": wp.revealed_at.isoformat() if wp.revealed_at else None,
            })
        else:
            # Unrevealed waypoint is hidden
            pass
    data["waypoints"] = waypoints_out

    # Photos: filter category == 'destination' if not revealed
    photos_out = []
    dest_photo_ids = set()
    for ph in (trip.photos or []):
        if ph.category == "destination":
            dest_photo_ids.add(ph.id)
            if not revealed:
                continue # Strictly withheld!

        photos_out.append({
            "id": ph.id,
            "trip_id": ph.trip_id,
            "game_id": ph.game_id,
            "task_id": ph.task_id,
            "author_name": ph.author_name,
            "author_user_id": ph.author_user_id,
            "caption": ph.caption,
            "lat": ph.lat if (revealed or ph.category != "destination") else None,
            "lng": ph.lng if (revealed or ph.category != "destination") else None,
            "taken_at": ph.taken_at.isoformat() if ph.taken_at else None,
            "category": ph.category,
            "approval_status": ph.approval_status,
            "has_thumb": bool(ph.thumb_path),
        })
    data["photos"] = photos_out

    # If cover photo was taken at destination and trip is not revealed, mask cover photo
    if not revealed and trip.cover_photo_id in dest_photo_ids:
        data["cover_photo_id"] = None

    # Points (GPS trail)
    data["points"] = [
        {
            "lat": p.lat,
            "lng": p.lng,
            "recorded_at": p.recorded_at.isoformat() if p.recorded_at else None,
            "accuracy_m": p.accuracy_m,
        }
        for p in (trip.points or [])
    ]

    # Likes & comments counts
    data["likes_count"] = len(trip.likes or [])
    data["comments_count"] = len(trip.comments or [])
    if viewer:
        data["user_has_liked"] = any(l.user_id == viewer.id for l in (trip.likes or []))
    else:
        data["user_has_liked"] = False

    # Review average rating
    if trip.reviews:
        ratings = [r.rating for r in trip.reviews]
        data["rating"] = round(sum(ratings) / len(ratings), 1)
        data["reviews_count"] = len(ratings)
    else:
        data["rating"] = None
        data["reviews_count"] = 0

    return data
