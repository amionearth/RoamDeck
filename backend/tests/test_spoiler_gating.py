import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.app.db import Base
from backend.app.models.models import User, Trip, TripWaypoint, Photo, Friendship
from backend.app.services.spoiler import serialize_trip

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

def test_spoiler_gating_before_and_after_arrival(db_session):
    # 1. Create owner, friend, stranger
    owner = User(id="user-owner", username="owner", display_name="Owner User", password_hash="x")
    friend = User(id="user-friend", username="friend", display_name="Friend User", password_hash="x")
    stranger = User(id="user-stranger", username="stranger", display_name="Stranger User", password_hash="x")
    db_session.add_all([owner, friend, stranger])

    friendship = Friendship(requester_id=owner.id, addressee_id=friend.id, status="accepted")
    db_session.add(friendship)

    # 2. Create Trip with unrevealed destination
    trip = Trip(
        id="test-trip-1",
        owner_id=owner.id,
        title="Blind Mystery Trek",
        status="in_progress",
        destination_name="Secret Kappad Cove",
        destination_lat=11.3811,
        destination_lng=75.7227,
        destination_category="beach",
        destination_revealed=False,
        plan_json='{"destination": {"name": "Secret Kappad Cove", "lat": 11.3811, "lng": 75.7227}, "recipe": {"dish_name": "Egg Roll", "reveal_on": "arrival", "steps": ["Fry"]}}'
    )
    db_session.add(trip)

    # Waypoints: wp0 revealed, wp1 unrevealed
    wp0 = TripWaypoint(id="wp-0", trip_id=trip.id, idx=0, lat=11.26, lng=75.77, hint="Hint 0", revealed_at=datetime.now(timezone.utc))
    wp1 = TripWaypoint(id="wp-1", trip_id=trip.id, idx=1, lat=11.30, lng=75.75, hint="Secret Hint 1", revealed_at=None)
    db_session.add_all([wp0, wp1])

    # Photos: normal photo & destination photo
    p_norm = Photo(id="p-1", trip_id=trip.id, file_path="a.jpg", thumb_path="b.jpg", category="waypoint")
    p_dest = Photo(id="p-2", trip_id=trip.id, file_path="c.jpg", thumb_path="d.jpg", category="destination")
    db_session.add_all([p_norm, p_dest])
    db_session.commit()

    # --- BEFORE ARRIVAL ---
    for viewer in [owner, friend, stranger]:
        serialized = serialize_trip(trip, viewer)
        # Strict destination redactions
        assert serialized["destination_revealed"] is False
        assert serialized["destination_name"] == "???"
        assert serialized["destination_lat"] is None
        assert serialized["destination_lng"] is None
        assert serialized["destination_category"] is None
        
        # Plan redactions
        assert serialized["plan"]["destination"]["name"] == "???"
        assert "lat" not in serialized["plan"]["destination"]
        assert serialized["plan"]["recipe"]["dish_name"] == "Secret dish"

        # Waypoint 1 hidden
        waypoint_ids = [w["id"] for w in serialized["waypoints"]]
        assert "wp-0" in waypoint_ids
        assert "wp-1" not in waypoint_ids

        # Destination photo hidden
        photo_ids = [p["id"] for p in serialized["photos"]]
        assert "p-1" in photo_ids
        assert "p-2" not in photo_ids

    # --- AFTER ARRIVAL ---
    trip.destination_revealed = True
    trip.revealed_at = datetime.now(timezone.utc)
    trip.status = "arrived"
    db_session.commit()

    for viewer in [owner, friend, stranger]:
        serialized = serialize_trip(trip, viewer)
        assert serialized["destination_revealed"] is True
        assert serialized["destination_name"] == "Secret Kappad Cove"
        assert serialized["destination_lat"] == 11.3811
        assert serialized["destination_lng"] == 75.7227
        assert serialized["destination_category"] == "beach"

        # All waypoints visible
        waypoint_ids = [w["id"] for w in serialized["waypoints"]]
        assert "wp-0" in waypoint_ids
        assert "wp-1" in waypoint_ids

        # Destination photo revealed
        photo_ids = [p["id"] for p in serialized["photos"]]
        assert "p-1" in photo_ids
        assert "p-2" in photo_ids
