import os
import shutil
import uuid
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.db import get_db, engine, Base
from backend.app.models.models import (
    User, Device, Trip, TripMember, TripWaypoint, TripPoint, Photo, PhotoVote, Game,
    GamePlayer, GameTask, Score, Stamp, Badge, UserBadge, Friendship, Like, Comment, Review
)
from backend.app.security import hash_password, hash_token
from backend.app.services.badges import seed_badges, check_user_badges, award_badge_if_earned
from backend.app.services.stamps import award_stamp_for_trip
from backend.app.services.xp import award_xp
from backend.app.services.games import close_game_session
from backend.app.realtime import manager
from PIL import Image

router = APIRouter(prefix="/demo", tags=["demo"])

class FakeTripRequest(BaseModel):
    title: Optional[str] = "Sunset Wander along Malabar Coast"
    destination_name: Optional[str] = "Kappad Beach Rock View"
    destination_category: Optional[str] = "beach"

class FakeGameRequest(BaseModel):
    players_count: int = 3
    type: str = "photo_hunt"

@router.post("/reset")
def reset_database(db: Session = Depends(get_db)):
    if not settings.DEMO_MODE:
        raise HTTPException(status_code=403, detail="Demo mode disabled")

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    trips_dir = settings.DATA_DIR / "trips"
    users_dir = settings.DATA_DIR / "users"
    if trips_dir.exists():
        shutil.rmtree(trips_dir, ignore_errors=True)
    if users_dir.exists():
        shutil.rmtree(users_dir, ignore_errors=True)

    trips_dir.mkdir(parents=True, exist_ok=True)
    users_dir.mkdir(parents=True, exist_ok=True)

    seed_badges(db)
    return {"status": "reset_completed"}

@router.post("/seed")
def seed_demo_data(db: Session = Depends(get_db)):
    if not settings.DEMO_MODE:
        raise HTTPException(status_code=403, detail="Demo mode disabled")

    seed_badges(db)

    # 1. Six users with rich travel bios
    user_specs = [
        ("explorer", "Alex Explorer", "Passionate blind traveler & sunset hunter along the Malabar Coast.", "Kozhikode", 580),
        ("nila", "Nila Nair", "Architecture enthusiast, looking for quiet heritage corners and old tiles.", "Kozhikode", 420),
        ("kabir", "Kabir Sen", "Street food detective. Follow the aroma of spices and fresh tea.", "Kochi", 350),
        ("meera", "Meera Rao", "Always with a lens ready. Chasing sea breezes and light.", "Bangalore", 290),
        ("tarun", "Tarun Das", "Fast walker, bingo runner, early morning trekker.", "Kannur", 210),
        ("zoya", "Zoya Khan", "Curious flaneur exploring hidden alleys and port markets.", "Mumbai", 180),
    ]

    users = {}
    seed_photos_dir = Path(settings.DATA_DIR).resolve().parent / "frontend" / "public" / "seed" / "photos"

    for idx, (uname, dname, bio, city, user_xp) in enumerate(user_specs):
        existing = db.query(User).filter(User.username == uname).first()
        if not existing:
            u = User(
                username=uname,
                display_name=dname,
                password_hash=hash_password(uname if uname == "explorer" else "secret"),
                bio=bio,
                home_city=city,
                friend_code=f"FFH-{uname[:3].upper()}{idx+10}",
                xp=user_xp,
            )
            db.add(u)
            db.flush()
            users[uname] = u
        else:
            existing.xp = user_xp
            users[uname] = existing

    explorer_user = users["explorer"]

    # Copy realistic user avatars
    for uname, u in users.items():
        av_path = settings.DATA_DIR / "users" / u.id / "avatar.jpg"
        av_thumb = settings.DATA_DIR / "users" / u.id / "avatar_thumb.jpg"
        av_path.parent.mkdir(parents=True, exist_ok=True)
        # Select scenic photo to represent user
        src_photo = seed_photos_dir / f"{list(seed_photos_dir.glob('*.jpg'))[len(uname) % 10].name}" if seed_photos_dir.exists() and list(seed_photos_dir.glob('*.jpg')) else None
        if src_photo and src_photo.exists():
            shutil.copyfile(src_photo, av_path)
            shutil.copyfile(src_photo, av_thumb)
        u.avatar_path = f"/api/users/{uname}/avatar"

    # 5 Friends for explorer (3 accepted, 2 pending requests) (Section 7)
    db.query(Friendship).filter((Friendship.requester_id == explorer_user.id) | (Friendship.addressee_id == explorer_user.id)).delete()
    db.add(Friendship(requester_id=explorer_user.id, addressee_id=users["nila"].id, status="accepted", source="code"))
    db.add(Friendship(requester_id=explorer_user.id, addressee_id=users["kabir"].id, status="accepted", source="game"))
    db.add(Friendship(requester_id=explorer_user.id, addressee_id=users["meera"].id, status="accepted", source="search"))
    db.add(Friendship(requester_id=users["tarun"].id, addressee_id=explorer_user.id, status="pending", source="search"))
    db.add(Friendship(requester_id=users["zoya"].id, addressee_id=explorer_user.id, status="pending", source="meetup"))

    # Demo device paired to explorer
    dev = db.query(Device).filter(Device.owner_user_id == explorer_user.id).first()
    if not dev:
        dev = Device(
            name="FarFromHome Pi 5 (Demo)",
            owner_user_id=explorer_user.id,
            token_hash=hash_token("demo-device-token"),
            online=True,
            battery_pct=92,
            last_seen_at=datetime.now(timezone.utc),
        )
        db.add(dev)
        db.flush()

    # 8 Finished trips around Kozhikode with 6 photos each, ratings, reviews, likes, comments
    trip_data = [
        {
            "title": "Malabar Coast Sunset Odyssey",
            "desc": "A gentle coastal stroll along the Arabian Sea culminating at ancient shoreline boulders.",
            "mood": "chill",
            "dest_name": "Kappad Beach Historic Shore",
            "lat": 11.3811, "lng": 75.7227, "cat": "beach",
            "dist": 8200.0, "status": "finished", "rev": True,
            "owner": explorer_user, "rating": 5, "review": "Finding this cove at golden hour without knowing the map was pure magic."
        },
        {
            "title": "Old Town Aroma Trail",
            "desc": "Following spice wafts from Sweet Meat Street to the historic freshwater reservoir.",
            "mood": "foodie",
            "dest_name": "Mananchira Heritage Square",
            "lat": 11.2512, "lng": 75.7794, "cat": "heritage",
            "dist": 4300.0, "status": "finished", "rev": True,
            "owner": explorer_user, "rating": 5, "review": "The bakery aromas led us straight across the banyan courtyards."
        },
        {
            "title": "Timber Docks & Sea Bridges",
            "desc": "Ancient river mouth whispers where ocean-going dhow ships have been built for centuries.",
            "mood": "adventure",
            "dest_name": "Beypore Port Pier",
            "lat": 11.1648, "lng": 75.8083, "cat": "viewpoint",
            "dist": 11200.0, "status": "finished", "rev": True,
            "owner": explorer_user, "rating": 4, "review": "Long brisk trek. Reaching the stone walkway jutting into the sea was worth every step."
        },
        {
            "title": "Mangrove Forest Sanctuary",
            "desc": "Quiet water channels, kingfishers, and coastal train bridges between tidal islands.",
            "mood": "photo",
            "dest_name": "Kadalundi Bird Sanctuary",
            "lat": 11.1278, "lng": 75.8306, "cat": "park",
            "dist": 6500.0, "status": "finished", "rev": True,
            "owner": users["nila"], "rating": 5, "review": "Flocks of terns at sunset over the estuary."
        },
        {
            "title": "Secret Beachside Chai Run",
            "desc": "Quick blind dash towards the salty horizon for hot Sulaimani tea and fried banana chips.",
            "mood": "foodie",
            "dest_name": "Kozhikode South Beach Pier",
            "lat": 11.2467, "lng": 75.7689, "cat": "cafe",
            "dist": 3100.0, "status": "finished", "rev": True,
            "owner": users["kabir"], "rating": 4, "review": "Perfect evening tea spot right on the sea wall."
        },
        {
            "title": "Lighthouse Shadows",
            "desc": "Chasing the maritime beacon beam across seaside promenades.",
            "mood": "romantic",
            "dest_name": "Kozhikode Lighthouse Beacon",
            "lat": 11.2612, "lng": 75.7675, "cat": "viewpoint",
            "dist": 5400.0, "status": "finished", "rev": True,
            "owner": users["meera"], "rating": 5, "review": "Watched the beacon turn on as twilight settled."
        },
        {
            "title": "Spices and Pottery Alleys",
            "desc": "A puzzle of narrow shaded streets lined with ancient terracotta tile roofs.",
            "mood": "adventure",
            "dest_name": "Valayanad Temple Grove",
            "lat": 11.2389, "lng": 75.8056, "cat": "temple",
            "dist": 7200.0, "status": "finished", "rev": True,
            "owner": users["tarun"], "rating": 4, "review": "Peaceful shaded sanctuary amidst old trees."
        },
        {
            "title": "Dawn Fish Market Rumble",
            "desc": "The electric energy of morning catch being hauled under swaying palm trees.",
            "mood": "foodie",
            "dest_name": "Puthiyappa Harbor Market",
            "lat": 11.3056, "lng": 75.7489, "cat": "market",
            "dist": 4800.0, "status": "finished", "rev": True,
            "owner": users["zoya"], "rating": 5, "review": "Unforgettable colors and early morning boat lights."
        },
    ]

    all_seed_photos = list(seed_photos_dir.glob("*.jpg")) if seed_photos_dir.exists() else []

    for idx, td in enumerate(trip_data):
        t_id = f"demo-trip-{idx+1}"
        trip = db.query(Trip).filter(Trip.id == t_id).first()
        if not trip:
            trip = Trip(
                id=t_id,
                owner_id=td["owner"].id,
                device_id=dev.id,
                title=td["title"],
                description=td["desc"],
                mood=td["mood"],
                status=td["status"],
                visibility="public",
                destination_name=td["dest_name"],
                destination_lat=td["lat"],
                destination_lng=td["lng"],
                destination_category=td["cat"],
                destination_revealed=td["rev"],
                revealed_at=datetime.now(timezone.utc) - timedelta(days=idx+1),
                start_at=datetime.now(timezone.utc) - timedelta(days=idx+1, hours=3),
                end_at=datetime.now(timezone.utc) - timedelta(days=idx+1),
                distance_m=td["dist"],
                summary=f"Completed {td['title']}. An unforgettable blind expedition.",
                plan_json=f'{{"destination": {{"name": "{td["dest_name"]}", "lat": {td["lat"]}, "lng": {td["lng"]}}}, "recipe": {{"dish_name": "Malabar Biryani", "steps": ["Layer fragrant rice", "Slow cook with spices"]}}}}'
            )
            db.add(trip)
            db.flush()

            # GPS breadcrumbs
            base_lat, base_lng = 11.2588, 75.7804
            steps = 8
            for s in range(steps + 1):
                lat_s = base_lat + (td["lat"] - base_lat) * (s / steps)
                lng_s = base_lng + (td["lng"] - base_lng) * (s / steps)
                pt = TripPoint(trip_id=t_id, lat=lat_s, lng=lng_s, accuracy_m=4.5)
                db.add(pt)

            # Waypoint
            wp = TripWaypoint(
                trip_id=t_id,
                idx=0,
                lat=base_lat + (td["lat"] - base_lat) * 0.5,
                lng=base_lng + (td["lng"] - base_lng) * 0.5,
                hint="Pass the old archway where spices fragrance the breeze.",
                revealed_at=datetime.now(timezone.utc) - timedelta(days=idx+1, hours=1),
            )
            db.add(wp)

            # Members
            db.add(TripMember(trip_id=t_id, display_name=td["owner"].display_name, role="owner"))
            db.add(TripMember(trip_id=t_id, display_name="Asha", role="member"))

            # 6 Photos per trip using realistic scenic files
            trip_photos_dir = settings.DATA_DIR / "trips" / t_id / "photos"
            trip_thumbs_dir = trip_photos_dir / "thumbs"
            trip_photos_dir.mkdir(parents=True, exist_ok=True)
            trip_thumbs_dir.mkdir(parents=True, exist_ok=True)

            # Check for user-uploaded photos in photos/
            user_photos_dir = Path("photos")
            user_photos = sorted(list(user_photos_dir.glob("*.jpg"))) if user_photos_dir.exists() else []

            for p_idx in range(6):
                p_id = f"demo-photo-{idx+1}-{p_idx+1}"
                p_file = trip_photos_dir / f"{p_id}.jpg"
                p_thumb = trip_thumbs_dir / f"{p_id}.jpg"

                if idx == 0 and user_photos:
                    # Alex's primary trip demo-trip-1 gets the user-provided photos!
                    src_p = user_photos[p_idx % len(user_photos)]
                    shutil.copyfile(src_p, p_file)
                    shutil.copyfile(src_p, p_thumb)
                elif all_seed_photos:
                    src_p = all_seed_photos[(idx * 2 + p_idx) % len(all_seed_photos)]
                    shutil.copyfile(src_p, p_file)
                    shutil.copyfile(src_p, p_thumb)

                cat = "food" if p_idx == 2 else ("destination" if p_idx == 5 else "waypoint")
                ph = Photo(
                    id=p_id,
                    trip_id=t_id,
                    author_name=td["owner"].display_name if p_idx % 2 == 0 else "Asha",
                    author_user_id=td["owner"].id if p_idx % 2 == 0 else None,
                    file_path=str(p_file),
                    thumb_path=str(p_thumb),
                    caption=f"{td['title']} viewpoint #{p_idx+1}",
                    category=cat,
                    approval_status="approved",
                )
                db.add(ph)
                if p_idx == 0:
                    trip.cover_photo_id = p_id

            # Review & comments
            rev = Review(trip_id=t_id, user_id=td["owner"].id, rating=td["rating"], body=td["review"])
            db.add(rev)

            db.add(Comment(trip_id=t_id, user_id=users["nila"].id, body="Loved the photos! That view looks incredible."))
            db.add(Like(user_id=users["nila"].id, target_id=t_id, target_type="trip"))
            db.add(Like(user_id=users["kabir"].id, target_id=t_id, target_type="trip"))

            # Stamps
            if td["rev"]:
                award_stamp_for_trip(db, trip, td["owner"])

    # 1 Live Trip currently in progress for explorer
    live_trip_id = "demo-live-trip"
    live_trip = db.query(Trip).filter(Trip.id == live_trip_id).first()
    if not live_trip:
        live_trip = Trip(
            id=live_trip_id,
            owner_id=explorer_user.id,
            device_id=dev.id,
            title="Mysterious North Trail",
            description="Heading north towards secret coastal rocks and evening ocean breezes.",
            mood="adventure",
            status="in_progress",
            visibility="public",
            destination_name="Kappad Point Sanctuary",
            destination_lat=11.3850,
            destination_lng=75.7200,
            destination_category="viewpoint",
            destination_revealed=False, # STRICTLY UNREVEALED
            start_at=datetime.now(timezone.utc) - timedelta(minutes=45),
            distance_m=2840.0,
            plan_json='{"destination": {"name": "Kappad Point Sanctuary", "lat": 11.3850, "lng": 75.7200, "secret": true}}'
        )
        db.add(live_trip)
        db.flush()
        db.add(TripPoint(trip_id=live_trip_id, lat=11.2650, lng=75.7720))
        db.add(TripPoint(trip_id=live_trip_id, lat=11.2800, lng=75.7650))
        db.add(TripPoint(trip_id=live_trip_id, lat=11.3000, lng=75.7500))

        # Give live trip a real cover photo
        if all_seed_photos:
            lp_dir = settings.DATA_DIR / "trips" / live_trip_id / "photos"
            lp_thumbs = lp_dir / "thumbs"
            lp_dir.mkdir(parents=True, exist_ok=True)
            lp_thumbs.mkdir(parents=True, exist_ok=True)
            lp_file = lp_dir / "live-cover.jpg"
            lp_thumb = lp_thumbs / "live-cover.jpg"
            shutil.copyfile(all_seed_photos[0], lp_file)
            shutil.copyfile(all_seed_photos[0], lp_thumb)
            ph = Photo(id="live-cover", trip_id=live_trip_id, author_name="Alex", author_user_id=explorer_user.id, file_path=str(lp_file), thumb_path=str(lp_thumb), category="waypoint")
            db.add(ph)
            live_trip.cover_photo_id = "live-cover"

    # Seed 8 stamps for explorer across all four rarities (common, rare, epic, legendary)
    db.query(Stamp).filter(Stamp.user_id == explorer_user.id).delete()
    stamp_specs = [
        ("Kappad Historic Beacon", "viewpoint", 11.3811, 75.7227, "legendary", "KAPP-L9901"),
        ("Mananchira Water Courtyard", "heritage", 11.2512, 75.7794, "epic", "MANA-E4412"),
        ("Beypore Ancient Dhow Port", "viewpoint", 11.1648, 75.8083, "legendary", "BEYP-L8823"),
        ("Kozhikode South Beach Pier", "cafe", 11.2467, 75.7689, "common", "KOZH-C1104"),
        ("Kadalundi Estuary Islets", "park", 11.1278, 75.8306, "rare", "KADA-R5532"),
        ("Puthiyappa Harbor Nets", "market", 11.3056, 75.7489, "epic", "PUTH-E7741"),
        ("Valayanad Sacred Grove", "temple", 11.2389, 75.8056, "epic", "VALA-E3389"),
        ("Chaliyam Coastal Rocks", "beach", 11.1550, 75.8020, "common", "CHAL-C2290"),
    ]
    for s_name, s_cat, s_lat, s_lng, s_rarity, s_ser in stamp_specs:
        s = Stamp(
            user_id=explorer_user.id,
            trip_id="demo-trip-1",
            place_name=s_name,
            place_category=s_cat,
            lat=s_lat,
            lng=s_lng,
            rarity=s_rarity,
            serial=s_ser,
            earned_at=datetime.now(timezone.utc) - timedelta(days=len(s_name)),
        )
        db.add(s)

    # Earn 6 Badges for explorer
    db.query(UserBadge).filter(UserBadge.user_id == explorer_user.id).delete()
    for b_slug, slot in [("first_steps", 1), ("blind_faith", 2), ("shutterbug", 3), ("rare_find", 4), ("collector", None), ("social_butterfly", None)]:
        award_badge_if_earned(db, explorer_user.id, b_slug)
        ub = db.query(UserBadge).join(Badge).filter(UserBadge.user_id == explorer_user.id, Badge.slug == b_slug).first()
        if ub and slot:
            ub.top_slot = slot

    # Set featured playercard for explorer
    explorer_user.playercard_json = '{"title": "Coastal Compass Voyager", "featured_stamp_id": "KAPP-L9901"}'

    # Two finished games and one running game (Section 7)
    db.query(PhotoVote).delete()
    db.query(Score).delete()
    db.query(GameTask).delete()
    db.query(Photo).filter(Photo.game_id.isnot(None)).delete()
    db.query(GamePlayer).delete()
    db.query(Game).delete()
    # Game 1 (Running)
    g_run = Game(
        id="game-running-1",
        type="photo_hunt",
        status="running",
        code="KOZ88",
        params_json='{"approval": "auto", "win_condition": "most_points", "penalty": "Loser buys tender coconuts for the crew"}',
        started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        ends_at=datetime.now(timezone.utc) + timedelta(minutes=20),
    )
    db.add(g_run)
    db.flush()
    p_ex = GamePlayer(game_id=g_run.id, user_id=explorer_user.id, display_name="Alex Explorer", color="#1E7F85")
    p_ni = GamePlayer(game_id=g_run.id, user_id=users["nila"].id, display_name="Nila Nair", color="#F2A33A")
    p_ka = GamePlayer(game_id=g_run.id, user_id=users["kabir"].id, display_name="Kabir Sen", color="#7B4FC2")
    db.add_all([p_ex, p_ni, p_ka])
    db.flush()
    db.add(Score(game_id=g_run.id, player_id=p_ex.id, user_id=explorer_user.id, points=25, reason="Spotted fishing boat"))
    db.add(Score(game_id=g_run.id, player_id=p_ni.id, user_id=users["nila"].id, points=20, reason="Spotted blue archway"))
    db.add(Score(game_id=g_run.id, player_id=p_ka.id, user_id=users["kabir"].id, points=10, reason="Spotted spice sack"))

    # Game 2 (Finished)
    g_fin = Game(
        id="game-finished-1",
        type="bingo",
        status="closed",
        code="BINGO4",
        params_json='{"approval": "auto", "win_condition": "first_n_in_row", "penalty": "Loser carries the camera bag"}',
        started_at=datetime.now(timezone.utc) - timedelta(days=2),
        ends_at=datetime.now(timezone.utc) - timedelta(days=2, minutes=-15),
        ended_at=datetime.now(timezone.utc) - timedelta(days=2, minutes=-12),
    )
    db.add(g_fin)
    db.flush()
    p_ex2 = GamePlayer(game_id=g_fin.id, user_id=explorer_user.id, display_name="Alex Explorer", color="#1E7F85")
    p_me2 = GamePlayer(game_id=g_fin.id, user_id=users["meera"].id, display_name="Meera Rao", color="#1F4B8F")
    db.add_all([p_ex2, p_me2])
    db.flush()
    db.add(Score(game_id=g_fin.id, player_id=p_ex2.id, user_id=explorer_user.id, points=50, reason="Completed 5-in-a-row bingo"))
    db.add(Score(game_id=g_fin.id, player_id=p_me2.id, user_id=users["meera"].id, points=30, reason="4 tiles completed"))

    db.commit()

    return {
        "status": "seeded",
        "users_count": len(users),
        "trips_count": len(trip_data),
        "stamps_count": len(stamp_specs),
        "badges_count": 6,
    }

@router.post("/start-fake-trip")
async def start_fake_trip(req: FakeTripRequest, db: Session = Depends(get_db)):
    explorer = db.query(User).filter(User.username == "explorer").first()
    if not explorer:
        raise HTTPException(status_code=400, detail="Demo explorer user not found, seed first.")

    dev = db.query(Device).filter(Device.owner_user_id == explorer.id).first()

    t_id = f"fake-trip-{uuid.uuid4().hex[:6]}"
    trip = Trip(
        id=t_id,
        owner_id=explorer.id,
        device_id=dev.id if dev else None,
        title=req.title or "Spontaneous Blind Drift",
        description="Compass needle spinning, following the coastline.",
        mood="adventure",
        status="in_progress",
        visibility="public",
        destination_name=req.destination_name or "Kappad Beach Historic Shore",
        destination_lat=11.3811,
        destination_lng=75.7227,
        destination_category=req.destination_category or "beach",
        destination_revealed=False,
        start_at=datetime.now(timezone.utc),
        distance_m=1200.0,
    )
    db.add(trip)
    db.add(TripPoint(trip_id=t_id, lat=11.2588, lng=75.7804))
    db.add(TripPoint(trip_id=t_id, lat=11.2750, lng=75.7700))
    db.commit()

    await manager.broadcast("feed", "trip.started", {"trip_id": t_id, "title": trip.title})
    return {"status": "started", "trip_id": t_id}

@router.post("/arrive-trip")
async def arrive_trip_demo(trip_id: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Trip).filter(Trip.status == "in_progress", Trip.destination_revealed == False)
    if trip_id:
        trip = query.filter(Trip.id == trip_id).first()
    else:
        trip = query.order_by(Trip.created_at.desc()).first()

    if not trip:
        raise HTTPException(status_code=404, detail="No in-progress unrevealed trip found")

    trip.destination_revealed = True
    trip.revealed_at = datetime.now(timezone.utc)
    trip.status = "arrived"
    trip.distance_m = (trip.distance_m or 1500.0) + 1200.0

    stamp = None
    if trip.owner:
        stamp = award_stamp_for_trip(db, trip, trip.owner)
        award_xp(db, trip.owner, "stamp_earned", 25, ref=stamp.id)
        check_user_badges(db, trip.owner)

    db.commit()

    await manager.broadcast(
        f"trip:{trip.id}",
        "trip.arrived",
        {
            "trip_id": trip.id,
            "destination_name": trip.destination_name,
            "destination_lat": trip.destination_lat,
            "destination_lng": trip.destination_lng,
            "stamp": {"place_name": stamp.place_name, "rarity": stamp.rarity, "serial": stamp.serial} if stamp else None,
        }
    )

    return {"status": "arrived", "trip_id": trip.id, "destination_name": trip.destination_name}

@router.post("/start-fake-game")
def start_fake_game(req: FakeGameRequest, db: Session = Depends(get_db)):
    game_id = f"fake-game-{uuid.uuid4().hex[:6]}"
    code = f"GM{secrets.choice('ABCDEF')}{secrets.randbelow(89)+10}"

    game = Game(
        id=game_id,
        type=req.type,
        status="running",
        code=code,
        params_json='{"approval": "auto", "win_condition": "most_points", "penalty": "Loser buys tender coconuts for the crew"}',
        started_at=datetime.now(timezone.utc),
        ends_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db.add(game)

    names = ["Asha", "Dev", "Tara", "Rohan", "Leila"]
    for i in range(min(req.players_count, len(names))):
        p = GamePlayer(game_id=game_id, display_name=names[i], color="#1E7F85" if i % 2 == 0 else "#F2A33A")
        db.add(p)
        db.flush()
        task = GameTask(
            game_id=game_id,
            player_id=p.id,
            player_name=p.display_name,
            idx=0,
            text="Find a yellow fishing boat or yellow sign",
            points=15,
            status="pending",
        )
        db.add(task)

    db.commit()
    return {"status": "started", "game_id": game_id, "code": code, "join_url": f"{settings.PUBLIC_BASE_URL}/g/{code}"}

@router.post("/end-game")
async def end_active_game(db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.status == "running").order_by(Game.created_at.desc()).first()
    if not game:
        raise HTTPException(status_code=404, detail="No running game found")

    await close_game_session(db, game)
    return {"status": "ended", "game_id": game.id}
