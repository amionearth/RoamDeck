import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Text, DateTime, ForeignKey, Index
)
from sqlalchemy.orm import relationship
from backend.app.db import Base

def gen_uuid() -> str:
    return str(uuid.uuid4())

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    username = Column(String(50), unique=True, nullable=False, index=True)
    display_name = Column(String(100), nullable=False)
    password_hash = Column(String(255), nullable=False)
    bio = Column(Text, default="")
    avatar_path = Column(String(255), nullable=True)
    home_city = Column(String(100), default="Kozhikode")
    is_private = Column(Boolean, default=False)
    xp = Column(Integer, default=0)
    friend_code = Column(String(12), unique=True, index=True)
    trusted_contact_name = Column(String(100), default="")
    trusted_contact_phone = Column(String(50), default="")
    playercard_json = Column(Text, default="{}") # {banner_photo_id, title, featured_stamp_id}
    created_at = Column(DateTime, default=utc_now)

    devices = relationship("Device", back_populates="owner")
    trips = relationship("Trip", back_populates="owner")
    stamps = relationship("Stamp", back_populates="user")
    user_badges = relationship("UserBadge", back_populates="user")

class Device(Base):
    __tablename__ = "devices"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    owner_user_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    name = Column(String(100), default="FarFromHome Pi 5")
    token_hash = Column(String(255), nullable=True)
    last_seen_at = Column(DateTime, default=utc_now)
    battery_pct = Column(Integer, default=100)
    online = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utc_now)

    owner = relationship("User", back_populates="devices")
    trips = relationship("Trip", back_populates="device")

class PairingCode(Base):
    __tablename__ = "pairing_codes"
    code = Column(String(12), primary_key=True, index=True)
    device_id = Column(String(36), ForeignKey("devices.id"), nullable=False)
    poll_secret_hash = Column(String(255), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    claimed_by_user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utc_now)

class Friendship(Base):
    __tablename__ = "friendships"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    requester_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    addressee_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(20), default="pending") # pending, accepted, blocked
    source = Column(String(20), default="search")  # search, code, game, meetup
    created_at = Column(DateTime, default=utc_now)

class Trip(Base):
    __tablename__ = "trips"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    device_id = Column(String(36), ForeignKey("devices.id"), nullable=True, index=True)
    title = Column(String(150), nullable=False)
    description = Column(Text, default="")
    mood = Column(String(50), default="chill") # chill, adventure, foodie, romantic, photo
    status = Column(String(20), default="planned") # planned, in_progress, arrived, finished
    visibility = Column(String(20), default="public") # public, friends, private
    destination_name = Column(String(150), nullable=False)
    destination_lat = Column(Float, nullable=False)
    destination_lng = Column(Float, nullable=False)
    destination_category = Column(String(50), default="viewpoint") # beach, park, cafe, market, temple, viewpoint
    destination_revealed = Column(Boolean, default=False)
    revealed_at = Column(DateTime, nullable=True)
    start_at = Column(DateTime, nullable=True)
    end_at = Column(DateTime, nullable=True)
    distance_m = Column(Float, default=0.0)
    summary = Column(Text, default="")
    cover_photo_id = Column(String(36), nullable=True)
    plan_json = Column(Text, default="{}")
    calendar_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)

    owner = relationship("User", back_populates="trips")
    device = relationship("Device", back_populates="trips")
    members = relationship("TripMember", back_populates="trip", cascade="all, delete-orphan")
    waypoints = relationship("TripWaypoint", back_populates="trip", cascade="all, delete-orphan")
    points = relationship("TripPoint", back_populates="trip", cascade="all, delete-orphan")
    photos = relationship("Photo", back_populates="trip", cascade="all, delete-orphan")
    games = relationship("Game", back_populates="trip", cascade="all, delete-orphan")
    likes = relationship("Like", back_populates="trip", cascade="all, delete-orphan")
    comments = relationship("Comment", back_populates="trip", cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="trip", cascade="all, delete-orphan")

class TripMember(Base):
    __tablename__ = "trip_members"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    trip_id = Column(String(36), ForeignKey("trips.id"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    display_name = Column(String(100), nullable=False)
    role = Column(String(20), default="member") # owner, member, guest
    color = Column(String(30), default="#1E7F85")
    created_at = Column(DateTime, default=utc_now)

    trip = relationship("Trip", back_populates="members")

class TripWaypoint(Base):
    __tablename__ = "trip_waypoints"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    trip_id = Column(String(36), ForeignKey("trips.id"), nullable=False, index=True)
    idx = Column(Integer, default=0)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    radius_m = Column(Float, default=40.0)
    hint = Column(Text, nullable=False)
    revealed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)

    trip = relationship("Trip", back_populates="waypoints")

class TripPoint(Base):
    __tablename__ = "trip_points"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    trip_id = Column(String(36), ForeignKey("trips.id"), nullable=False, index=True)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    recorded_at = Column(DateTime, default=utc_now)
    accuracy_m = Column(Float, default=5.0)
    created_at = Column(DateTime, default=utc_now)

    trip = relationship("Trip", back_populates="points")

class Photo(Base):
    __tablename__ = "photos"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    trip_id = Column(String(36), ForeignKey("trips.id"), nullable=False, index=True)
    game_id = Column(String(36), ForeignKey("games.id"), nullable=True, index=True)
    task_id = Column(String(36), nullable=True)
    author_user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    author_player_id = Column(String(36), nullable=True)
    author_name = Column(String(100), default="Explorer")
    file_path = Column(String(255), nullable=False)
    thumb_path = Column(String(255), nullable=False)
    caption = Column(Text, default="")
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    taken_at = Column(DateTime, default=utc_now)
    category = Column(String(30), default="waypoint") # waypoint, destination, task, group, food
    approval_status = Column(String(20), default="approved") # pending, approved, rejected
    created_at = Column(DateTime, default=utc_now)

    trip = relationship("Trip", back_populates="photos")
    votes = relationship("PhotoVote", back_populates="photo", cascade="all, delete-orphan")

class PhotoVote(Base):
    __tablename__ = "photo_votes"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    photo_id = Column(String(36), ForeignKey("photos.id"), nullable=False, index=True)
    voter_player_id = Column(String(36), nullable=False)
    vote = Column(String(10), nullable=False) # approve, reject
    created_at = Column(DateTime, default=utc_now)

    photo = relationship("Photo", back_populates="votes")

class Like(Base):
    __tablename__ = "likes"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    target_type = Column(String(20), default="trip") # trip
    target_id = Column(String(36), ForeignKey("trips.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now)

    trip = relationship("Trip", back_populates="likes")

class Comment(Base):
    __tablename__ = "comments"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    trip_id = Column(String(36), ForeignKey("trips.id"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    parent_id = Column(String(36), ForeignKey("comments.id"), nullable=True)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utc_now)

    trip = relationship("Trip", back_populates="comments")
    user = relationship("User")

class Review(Base):
    __tablename__ = "reviews"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    trip_id = Column(String(36), ForeignKey("trips.id"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    rating = Column(Integer, default=5) # 1 to 5
    body = Column(Text, default="")
    created_at = Column(DateTime, default=utc_now)

    trip = relationship("Trip", back_populates="reviews")
    user = relationship("User")

class Stamp(Base):
    __tablename__ = "stamps"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    trip_id = Column(String(36), ForeignKey("trips.id"), nullable=False, index=True)
    place_name = Column(String(150), nullable=False)
    place_category = Column(String(50), default="viewpoint")
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    rarity = Column(String(20), default="common") # common, rare, epic, legendary
    serial = Column(String(32), nullable=False, unique=True)
    earned_at = Column(DateTime, default=utc_now)
    created_at = Column(DateTime, default=utc_now)

    user = relationship("User", back_populates="stamps")

class Badge(Base):
    __tablename__ = "badges"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    slug = Column(String(50), unique=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=False)
    icon = Column(String(50), default="compass")
    criteria_key = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=utc_now)

class UserBadge(Base):
    __tablename__ = "user_badges"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    badge_id = Column(String(36), ForeignKey("badges.id"), nullable=False, index=True)
    earned_at = Column(DateTime, default=utc_now)
    top_slot = Column(Integer, nullable=True) # 1 to 4
    created_at = Column(DateTime, default=utc_now)

    user = relationship("User", back_populates="user_badges")
    badge = relationship("Badge")

class Game(Base):
    __tablename__ = "games"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    trip_id = Column(String(36), ForeignKey("trips.id"), nullable=True, index=True)
    type = Column(String(30), default="photo_hunt") # photo_hunt, bingo, color_hunt, trivia
    status = Column(String(20), default="open") # open, running, closed
    code = Column(String(8), unique=True, index=True) # e.g. "KOZ42"
    params_json = Column(Text, default="{}") # {approval: 'peer_vote', win_condition: 'most_points', penalty: '...'}
    started_at = Column(DateTime, nullable=True)
    ends_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    created_by_device_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=utc_now)

    trip = relationship("Trip", back_populates="games")
    players = relationship("GamePlayer", back_populates="game", cascade="all, delete-orphan")
    tasks = relationship("GameTask", back_populates="game", cascade="all, delete-orphan")
    scores = relationship("Score", back_populates="game", cascade="all, delete-orphan")

class GamePlayer(Base):
    __tablename__ = "game_players"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    game_id = Column(String(36), ForeignKey("games.id"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    display_name = Column(String(100), nullable=False)
    team = Column(String(50), nullable=True)
    color = Column(String(30), default="#1E7F85")
    token_hash = Column(String(255), nullable=True)
    joined_at = Column(DateTime, default=utc_now)
    created_at = Column(DateTime, default=utc_now)

    game = relationship("Game", back_populates="players")

class GameTask(Base):
    __tablename__ = "game_tasks"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    game_id = Column(String(36), ForeignKey("games.id"), nullable=False, index=True)
    player_id = Column(String(36), nullable=True)
    player_name = Column(String(100), nullable=True)
    idx = Column(Integer, default=0)
    text = Column(Text, nullable=False)
    item_key = Column(String(50), nullable=True)
    points = Column(Integer, default=10)
    time_limit_s = Column(Integer, default=300)
    status = Column(String(20), default="pending") # pending, completed
    created_at = Column(DateTime, default=utc_now)

    game = relationship("Game", back_populates="tasks")

class Score(Base):
    __tablename__ = "scores"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    game_id = Column(String(36), ForeignKey("games.id"), nullable=False, index=True)
    player_id = Column(String(36), nullable=False)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    points = Column(Integer, default=0)
    reason = Column(String(100), default="task_completed")
    created_at = Column(DateTime, default=utc_now)

    game = relationship("Game", back_populates="scores")

class XPEvent(Base):
    __tablename__ = "xp_events"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String(50), nullable=False)
    amount = Column(Integer, default=0)
    ref = Column(String(100), default="")
    created_at = Column(DateTime, default=utc_now)

class Notification(Base):
    __tablename__ = "notifications"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    type = Column(String(50), nullable=False)
    payload_json = Column(Text, default="{}")
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)

class SOSEvent(Base):
    __tablename__ = "sos_events"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    device_id = Column(String(36), ForeignKey("devices.id"), nullable=True)
    trip_id = Column(String(36), ForeignKey("trips.id"), nullable=True)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    status = Column(String(20), default="active") # active, resolved
    created_at = Column(DateTime, default=utc_now)

class Meetup(Base):
    __tablename__ = "meetups"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    trip_id = Column(String(36), ForeignKey("trips.id"), nullable=True)
    user_a_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    user_b_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    status = Column(String(20), default="invited") # invited, accepted, approaching, close, found, cancelled
    meeting_name = Column(String(100), default="Public Plaza")
    meeting_lat = Column(Float, nullable=False)
    meeting_lng = Column(Float, nullable=False)
    found_a = Column(Boolean, default=False)
    found_b = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utc_now)

class Capsule(Base):
    __tablename__ = "capsules"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    author_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    radius_m = Column(Float, default=30.0)
    text = Column(Text, nullable=False)
    visibility = Column(String(20), default="public")
    created_at = Column(DateTime, default=utc_now)
