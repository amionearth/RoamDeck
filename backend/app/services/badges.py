from datetime import datetime, timezone
from sqlalchemy.orm import Session
from backend.app.models.models import Badge, UserBadge, User, Trip, Photo, Stamp, Friendship, Game

INITIAL_BADGES = [
    {
        "slug": "first_steps",
        "name": "First Steps",
        "description": "Finish your very first blind trip.",
        "icon": "footprints",
        "criteria_key": "trips_1",
    },
    {
        "slug": "blind_faith",
        "name": "Blind Faith",
        "description": "Complete 5 blind trips without peeking.",
        "icon": "compass",
        "criteria_key": "trips_5",
    },
    {
        "slug": "shutterbug",
        "name": "Shutterbug",
        "description": "Capture 50 approved travel photos.",
        "icon": "camera",
        "criteria_key": "photos_50",
    },
    {
        "slug": "rare_find",
        "name": "Rare Find",
        "description": "Collect a Rare or better stamp in your passport.",
        "icon": "sparkles",
        "criteria_key": "stamp_rare",
    },
    {
        "slug": "collector",
        "name": "Collector",
        "description": "Collect 10 distinct stamps.",
        "icon": "stamp",
        "criteria_key": "stamps_10",
    },
    {
        "slug": "bingo_champion",
        "name": "Bingo Champion",
        "description": "Win a blind trip photo bingo game.",
        "icon": "trophy",
        "criteria_key": "bingo_win",
    },
    {
        "slug": "color_master",
        "name": "Color Master",
        "description": "Win a color hunt challenge.",
        "icon": "palette",
        "criteria_key": "color_win",
    },
    {
        "slug": "social_butterfly",
        "name": "Social Butterfly",
        "description": "Connect with 5 explorer friends.",
        "icon": "users",
        "criteria_key": "friends_5",
    },
    {
        "slug": "new_friend",
        "name": "New Friend",
        "description": "Add a friend met on a trip or game.",
        "icon": "user-plus",
        "criteria_key": "friend_trip",
    },
    {
        "slug": "early_bird",
        "name": "Early Bird",
        "description": "Embark on a trip starting before 7:00 AM.",
        "icon": "sunrise",
        "criteria_key": "start_early",
    },
    {
        "slug": "night_owl",
        "name": "Night Owl",
        "description": "Complete a blind journey after 9:00 PM.",
        "icon": "moon",
        "criteria_key": "finish_late",
    },
    {
        "slug": "marathoner",
        "name": "Marathoner",
        "description": "Trek more than 10 kilometers on a single trip.",
        "icon": "map-pin",
        "criteria_key": "distance_10k",
    },
]

def seed_badges(db: Session):
    for b in INITIAL_BADGES:
        existing = db.query(Badge).filter(Badge.slug == b["slug"]).first()
        if not existing:
            badge = Badge(
                slug=b["slug"],
                name=b["name"],
                description=b["description"],
                icon=b["icon"],
                criteria_key=b["criteria_key"],
            )
            db.add(badge)
    db.commit()

def award_badge_if_earned(db: Session, user_id: str, badge_slug: str):
    badge = db.query(Badge).filter(Badge.slug == badge_slug).first()
    if not badge:
        return
    existing = db.query(UserBadge).filter(UserBadge.user_id == user_id, UserBadge.badge_id == badge.id).first()
    if not existing:
        user_badge = UserBadge(
            user_id=user_id,
            badge_id=badge.id,
            earned_at=datetime.now(timezone.utc),
        )
        db.add(user_badge)
        db.flush()

def check_user_badges(db: Session, user: User):
    # Trips count
    trips_count = db.query(Trip).filter(Trip.owner_id == user.id, Trip.status.in_(["arrived", "finished"])).count()
    if trips_count >= 1:
        award_badge_if_earned(db, user.id, "first_steps")
    if trips_count >= 5:
        award_badge_if_earned(db, user.id, "blind_faith")

    # Photos count
    photos_count = db.query(Photo).filter(Photo.author_user_id == user.id, Photo.approval_status == "approved").count()
    if photos_count >= 50:
        award_badge_if_earned(db, user.id, "shutterbug")

    # Stamps count and rarity
    stamps = db.query(Stamp).filter(Stamp.user_id == user.id).all()
    if len(stamps) >= 10:
        award_badge_if_earned(db, user.id, "collector")
    if any(s.rarity in ["rare", "epic", "legendary"] for s in stamps):
        award_badge_if_earned(db, user.id, "rare_find")

    # Friends count
    friends_count = db.query(Friendship).filter(
        (Friendship.requester_id == user.id) | (Friendship.addressee_id == user.id),
        Friendship.status == "accepted"
    ).count()
    if friends_count >= 5:
        award_badge_if_earned(db, user.id, "social_butterfly")

    # Friend from game/trip
    meet_friend = db.query(Friendship).filter(
        (Friendship.requester_id == user.id) | (Friendship.addressee_id == user.id),
        Friendship.status == "accepted",
        Friendship.source.in_(["game", "meetup"])
    ).first()
    if meet_friend:
        award_badge_if_earned(db, user.id, "new_friend")
