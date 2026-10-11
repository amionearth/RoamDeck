import json
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, Header, Request, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.db import get_db
from backend.app.models.models import (
    Device, PairingCode, Trip, TripMember, TripWaypoint, TripPoint, Photo, PhotoVote, Game,
    GamePlayer, GameTask, Score, SOSEvent, User
)
from backend.app.security import (
    hash_token, get_current_device, get_current_user
)
from backend.app.services.images import process_and_save_image
from backend.app.services.stamps import award_stamp_for_trip
from backend.app.services.xp import award_xp
from backend.app.services.badges import check_user_badges
from backend.app.services.games import close_game_session, parse_game_params
from backend.app.realtime import manager

router = APIRouter(tags=["device"])

# --- Models ---
class RegisterDeviceRequest(BaseModel):
    name: str = "FarFromHome Pi 5"

class PairDeviceBody(BaseModel):
    code: str

class HeartbeatBody(BaseModel):
    battery_pct: int = 100

class GPSPointItem(BaseModel):
    lat: float
    lng: float
    t: Optional[str] = None
    acc: Optional[float] = 5.0

class ArriveBody(BaseModel):
    lat: float
    lng: float
    at: Optional[str] = None

class FinishBody(BaseModel):
    distance_m: Optional[float] = None
    summary: Optional[str] = ""

class CreateGamePlayerItem(BaseModel):
    name: str
    color: Optional[str] = "#1E7F85"
    team: Optional[str] = None

class CreateGameTaskItem(BaseModel):
    player_name: Optional[str] = None
    text: str
    item_key: Optional[str] = None
    points: int = 10
    time_limit_s: int = 300

class PointsBody(BaseModel):
    """Brief 03 §6.2 documents ``{"points": [...]}`` while the endpoint itself takes a
    bare array. Accepting both keeps every existing client working and lets the
    documented shape be used too. See docs/SERVER_CONTRACT.md G-02."""
    points: List[GPSPointItem] = []


class CreateDeviceGameBody(BaseModel):
    trip_id: Optional[str] = None
    type: str = "photo_hunt" # photo_hunt, bingo, color_hunt
    params: Optional[Dict[str, Any]] = None
    players: List[CreateGamePlayerItem] = []
    tasks: List[CreateGameTaskItem] = []
    duration_s: int = 600
    #: Honoured when the client supplies one (the handheld does), otherwise the
    #: server mints an id. Lets an outbox retry the create without duplicating the
    #: session. See docs/SERVER_CONTRACT.md G-01.
    game_id: Optional[str] = None

class SOSBody(BaseModel):
    lat: float
    lng: float
    trip_id: Optional[str] = None

# --- Pairing Flow (Section 7.2) ---
@router.post("/device/register")
def register_device_pairing(req: RegisterDeviceRequest, db: Session = Depends(get_db)):
    device_id = str(uuid.uuid4())
    poll_secret = secrets.token_hex(16)
    pairing_code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))

    dev = Device(
        id=device_id,
        name=req.name,
        online=True,
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(dev)

    pc = PairingCode(
        code=pairing_code,
        device_id=device_id,
        poll_secret_hash=hash_token(poll_secret),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db.add(pc)
    db.commit()

    return {
        "device_id": device_id,
        "pairing_code": pairing_code,
        "poll_secret": poll_secret,
    }

@router.post("/devices/pair")
def claim_device_code(req: PairDeviceBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    code_str = req.code.upper().strip()
    pc = db.query(PairingCode).filter(PairingCode.code == code_str).first()
    if not pc:
        raise HTTPException(status_code=404, detail="Invalid pairing code")
    if pc.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Pairing code has expired")

    pc.claimed_by_user_id = user.id
    dev = db.query(Device).filter(Device.id == pc.device_id).first()
    if dev:
        dev.owner_user_id = user.id

    db.commit()
    return {"status": "claimed", "device_name": dev.name if dev else "Device"}

@router.get("/device/pairing/{device_id}")
def poll_pairing_status(device_id: str, secret: str, db: Session = Depends(get_db)):
    dev = db.query(Device).filter(Device.id == device_id).first()
    if not dev:
        raise HTTPException(status_code=404, detail="Device not found")

    pc = db.query(PairingCode).filter(PairingCode.device_id == device_id).order_by(PairingCode.created_at.desc()).first()
    if not pc or hash_token(secret) != pc.poll_secret_hash:
        raise HTTPException(status_code=401, detail="Invalid secret")

    if pc.claimed_by_user_id:
        token = secrets.token_hex(24)
        dev.token_hash = hash_token(token)
        db.delete(pc)
        db.commit()
        return {
            "status": "paired",
            "token": token,
            "owner_user_id": dev.owner_user_id,
        }

    return {"status": "pending"}

@router.get("/devices")
def list_user_devices(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    devices = db.query(Device).filter(Device.owner_user_id == user.id).all()
    return [
        {
            "id": d.id,
            "name": d.name,
            "battery_pct": d.battery_pct,
            "online": d.online,
            "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
        }
        for d in devices
    ]

@router.delete("/devices/{id}")
def unpair_device(id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    dev = db.query(Device).filter(Device.id == id, Device.owner_user_id == user.id).first()
    if not dev:
        raise HTTPException(status_code=404, detail="Device not found")
    dev.owner_user_id = None
    dev.token_hash = None
    db.commit()
    return {"status": "unpaired"}

# --- Device Endpoints (Bearer token) ---
@router.post("/device/heartbeat")
async def device_heartbeat(body: HeartbeatBody, device: Device = Depends(get_current_device), db: Session = Depends(get_db)):
    device.battery_pct = body.battery_pct
    device.last_seen_at = datetime.now(timezone.utc)
    device.online = True
    db.commit()

    if device.owner_user_id:
        await manager.broadcast(
            f"user:{device.owner_user_id}",
            "device.status",
            {"device_id": device.id, "battery_pct": device.battery_pct, "online": True}
        )
    return {"status": "ok"}

@router.post("/device/trips")
def create_device_trip(plan: Dict[str, Any], device: Device = Depends(get_current_device), db: Session = Depends(get_db)):
    trip_id = plan.get("trip_id") or str(uuid.uuid4())
    existing = db.query(Trip).filter(Trip.id == trip_id).first()
    if existing:
        return {"status": "exists", "trip_id": existing.id}

    owner_id = device.owner_user_id
    if not owner_id:
        admin_user = db.query(User).first()
        owner_id = admin_user.id if admin_user else "unassigned"

    dest_data = plan.get("destination", {})
    dest_name = dest_data.get("name", "Mystery Destination")
    dest_lat = dest_data.get("lat", 11.2588)
    dest_lng = dest_data.get("lng", 75.7804)
    dest_cat = dest_data.get("category", "viewpoint")

    cal_time = None
    if plan.get("calendar_at"):
        try:
            cal_time = datetime.fromisoformat(plan["calendar_at"].replace("Z", "+00:00"))
        except Exception:
            pass

    trip = Trip(
        id=trip_id,
        owner_id=owner_id,
        device_id=device.id,
        title=plan.get("title", "Blind Exploration"),
        description=plan.get("description", ""),
        mood=plan.get("mood", "chill"),
        status="planned",
        visibility="public",
        destination_name=dest_name,
        destination_lat=dest_lat,
        destination_lng=dest_lng,
        destination_category=dest_cat,
        destination_revealed=False,
        plan_json=json.dumps(plan),
        calendar_at=cal_time,
    )
    db.add(trip)

    # Members
    for m in plan.get("members", []):
        name = m.get("name", "Explorer")
        member = TripMember(
            trip_id=trip_id,
            display_name=name,
            role="owner" if name.lower() == "owner" else "member",
            color=m.get("color", "#1E7F85"),
        )
        db.add(member)

    # Waypoints
    for wp in plan.get("waypoints", []):
        waypoint = TripWaypoint(
            trip_id=trip_id,
            idx=wp.get("idx", 0),
            lat=wp.get("lat", 0.0),
            lng=wp.get("lng", 0.0),
            radius_m=wp.get("radius_m", 40.0),
            hint=wp.get("hint", "Explore around this marker"),
            revealed_at=None,
        )
        db.add(waypoint)

    db.commit()
    return {"status": "created", "trip_id": trip.id}

def _point_key(value: datetime) -> Any:
    """A de-duplication key that ignores sub-millisecond noise.

    SQLite through SQLAlchemy hands back naive datetimes whatever went in, so
    comparing an aware value against a stored one silently matches nothing — which
    would make the de-duplication below useless. Normalising to UTC and truncating to
    the millisecond keeps a genuine resend identical while still allowing two points
    that share a second (a batched upload) to both be stored.
    """
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


@router.post("/device/trips/{id}/points")
async def add_gps_points(
    id: str,
    points: Union[List[GPSPointItem], PointsBody],
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Ingest a batch of GPS breadcrumbs.

    Accepts **both** a bare JSON array (what this server has always taken) and
    ``{"points": [...]}`` (what brief 03 §6.2 documents). The union type is deliberate:
    the handheld sends the array, and a browser client following the brief can send the
    wrapped form.
    """
    if isinstance(points, PointsBody):
        point_list = points.points
    else:
        point_list = points

    trip = db.query(Trip).filter(Trip.id == id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    if trip.status == "planned":
        trip.status = "in_progress"
        trip.start_at = datetime.now(timezone.utc)

    # Points are de-duplicated by timestamp so a resend is harmless (brief 03 §6.2).
    existing: set = set()
    if point_list:
        parsed_times = []
        for candidate in point_list:
            if not candidate.t:
                continue
            try:
                parsed_times.append(
                    _point_key(datetime.fromisoformat(str(candidate.t).replace("Z", "+00:00")))
                )
            except Exception:
                continue
        query = db.query(TripPoint.recorded_at).filter(TripPoint.trip_id == trip.id)
        if parsed_times:
            query = query.filter(
                TripPoint.recorded_at >= min(parsed_times) - timedelta(seconds=1)
            )
        existing = {_point_key(row[0]) for row in query.all() if row[0] is not None}

    latest_pt = None
    added = 0
    for p in point_list:
        t_val = datetime.now(timezone.utc)
        if p.t:
            try:
                t_val = datetime.fromisoformat(str(p.t).replace("Z", "+00:00"))
            except Exception:
                pass

        key = _point_key(t_val)
        if key in existing:
            continue
        existing.add(key)

        point = TripPoint(
            trip_id=trip.id,
            lat=p.lat,
            lng=p.lng,
            accuracy_m=p.acc or 5.0,
            recorded_at=t_val,
        )
        db.add(point)
        latest_pt = point
        added += 1

    db.commit()

    if latest_pt:
        # Broadcast position to trip channel
        await manager.broadcast(
            f"trip:{trip.id}",
            "trip.position",
            {
                "trip_id": trip.id,
                "lat": latest_pt.lat,
                "lng": latest_pt.lng,
                "recorded_at": latest_pt.recorded_at.isoformat(),
            }
        )

    return {"status": "ok", "points_added": added, "points_received": len(point_list)}

@router.post("/device/trips/{id}/waypoints/{idx}/reveal")
async def reveal_waypoint(id: str, idx: int, device: Device = Depends(get_current_device), db: Session = Depends(get_db)):
    wp = db.query(TripWaypoint).filter(TripWaypoint.trip_id == id, TripWaypoint.idx == idx).first()
    if not wp:
        raise HTTPException(status_code=404, detail="Waypoint not found")

    wp.revealed_at = datetime.now(timezone.utc)
    db.commit()

    await manager.broadcast(
        f"trip:{id}",
        "trip.waypoint_revealed",
        {
            "trip_id": id,
            "idx": wp.idx,
            "lat": wp.lat,
            "lng": wp.lng,
            "hint": wp.hint,
        }
    )
    return {"status": "revealed", "idx": idx}

@router.post("/device/trips/{id}/photos")
async def upload_device_trip_photo(
    id: str,
    file: UploadFile = File(...),
    photo_id: Optional[str] = Form(None),
    taken_at: Optional[str] = Form(None),
    lat: Optional[float] = Form(None),
    lng: Optional[float] = Form(None),
    task_id: Optional[str] = Form(None),
    game_id: Optional[str] = Form(None),
    caption: str = Form(""),
    category: str = Form("waypoint"),
    member_name: str = Form("Explorer"),
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    """Upload a photo from the handheld.

    Idempotent on ``photo_id``: re-sending an upload whose response was lost returns the
    photo that already exists instead of creating a second row, so an outbox retry can
    never duplicate. See docs/SERVER_CONTRACT.md G-05.
    """
    trip = db.query(Trip).filter(Trip.id == id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    if photo_id:
        existing = db.query(Photo).filter(Photo.id == photo_id).first()
        if existing:
            if existing.trip_id != trip.id:
                raise HTTPException(
                    status_code=409,
                    detail="That photo_id already belongs to another trip.",
                )
            return {
                "status": "duplicate",
                "photo_id": existing.id,
                "thumb_url": f"/api/trips/{existing.trip_id}/photos/{existing.id}/thumb",
            }

    contents = await file.read()
    new_photo_id = photo_id or str(uuid.uuid4())
    dest_path = settings.DATA_DIR / "trips" / trip.id / "photos" / f"{new_photo_id}.jpg"
    thumb_path = settings.DATA_DIR / "trips" / trip.id / "photos" / "thumbs" / f"{new_photo_id}.jpg"

    process_and_save_image(contents, dest_path, thumb_path)

    photo_time = datetime.now(timezone.utc)
    if taken_at:
        try:
            photo_time = datetime.fromisoformat(taken_at.replace("Z", "+00:00"))
        except Exception:
            pass

    photo = Photo(
        id=new_photo_id,
        trip_id=trip.id,
        game_id=game_id,
        task_id=task_id,
        author_name=member_name,
        file_path=str(dest_path),
        thumb_path=str(thumb_path),
        caption=caption,
        lat=lat,
        lng=lng,
        taken_at=photo_time,
        category=category,
        approval_status="approved",
    )
    db.add(photo)

    if not trip.cover_photo_id and category != "destination":
        trip.cover_photo_id = new_photo_id

    # Award owner photo XP
    if trip.owner:
        award_xp(db, trip.owner, "photo_uploaded", 5, ref=new_photo_id)

    db.commit()

    # Emit event (if destination, only emit if revealed)
    if trip.destination_revealed or category != "destination":
        await manager.broadcast(
            f"trip:{trip.id}",
            "trip.photo_added",
            {
                "photo_id": new_photo_id,
                "category": category,
                "author_name": member_name,
                "caption": caption,
            }
        )

    return {
        "status": "uploaded",
        "photo_id": new_photo_id,
        "thumb_url": f"/api/trips/{trip.id}/photos/{new_photo_id}/thumb",
    }

@router.post("/device/trips/{id}/arrive")
async def arrive_trip(id: str, body: ArriveBody, device: Device = Depends(get_current_device), db: Session = Depends(get_db)):
    trip = db.query(Trip).filter(Trip.id == id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    trip.destination_revealed = True
    trip.revealed_at = datetime.now(timezone.utc)
    trip.status = "arrived"

    # Award stamp to owner
    stamp = None
    if trip.owner:
        stamp = award_stamp_for_trip(db, trip, trip.owner)
        award_xp(db, trip.owner, "stamp_earned", 25, ref=stamp.id)
        check_user_badges(db, trip.owner)

    db.commit()

    # Realtime event: trip.arrived
    await manager.broadcast(
        f"trip:{trip.id}",
        "trip.arrived",
        {
            "trip_id": trip.id,
            "destination_name": trip.destination_name,
            "destination_lat": trip.destination_lat,
            "destination_lng": trip.destination_lng,
            "destination_category": trip.destination_category,
            "stamp": {
                "place_name": stamp.place_name,
                "rarity": stamp.rarity,
                "serial": stamp.serial,
            } if stamp else None,
        }
    )

    return {
        "status": "arrived",
        "destination_name": trip.destination_name,
        "stamp_serial": stamp.serial if stamp else None,
    }

@router.post("/device/trips/{id}/finish")
def finish_trip(id: str, body: FinishBody, device: Device = Depends(get_current_device), db: Session = Depends(get_db)):
    trip = db.query(Trip).filter(Trip.id == id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    trip.status = "finished"
    trip.end_at = datetime.now(timezone.utc)
    if body.distance_m is not None:
        trip.distance_m = body.distance_m
    if body.summary:
        trip.summary = body.summary

    if trip.owner:
        award_xp(db, trip.owner, "trip_finished", 100, ref=trip.id)
        check_user_badges(db, trip.owner)

    db.commit()
    return {"status": "finished"}

from backend.app.services.network import get_base_url
from fastapi import Request

# --- Device Games ---
@router.post("/device/games")
def create_device_game(
    body: CreateDeviceGameBody,
    request: Request,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    # A client-supplied id makes the create idempotent, so an outbox retry cannot
    # create a second session (docs/SERVER_CONTRACT.md G-01).
    game_id = (body.game_id or "").strip() or str(uuid.uuid4())
    existing = db.query(Game).filter(Game.id == game_id).first()
    if existing:
        base_url = get_base_url(request)
        return {
            "game_id": existing.id,
            "code": existing.code,
            "join_url": f"{base_url}/g/{existing.code}",
            "qr_png_url": f"/api/games/{existing.id}/qr.png",
            "status": existing.status,
            "ends_at": existing.ends_at.isoformat() if existing.ends_at else None,
        }

    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(5))

    # C2: Creating a session leaves it open (lobby). ends_at is calculated when started.
    params = body.params or {}
    params["duration_s"] = body.duration_s

    game = Game(
        id=game_id,
        trip_id=body.trip_id,
        type=body.type,
        status="open", # lobby
        code=code,
        params_json=json.dumps(params),
        started_at=None,
        ends_at=None,
        created_by_device_id=device.id,
    )
    db.add(game)

    # Players
    player_name_to_id = {}
    for p in body.players:
        player = GamePlayer(
            game_id=game_id,
            display_name=p.name,
            color=p.color or "#1E7F85",
            team=p.team,
        )
        db.add(player)
        db.flush()
        player_name_to_id[p.name] = player.id

    # Tasks
    for idx, t in enumerate(body.tasks):
        p_id = player_name_to_id.get(t.player_name) if t.player_name else None
        task = GameTask(
            game_id=game_id,
            player_id=p_id,
            player_name=t.player_name,
            idx=idx,
            text=t.text,
            item_key=t.item_key,
            points=t.points,
            time_limit_s=t.time_limit_s,
            status="pending",
        )
        db.add(task)

    db.commit()

    base_url = get_base_url(request)
    join_url = f"{base_url}/g/{code}"
    qr_png_url = f"/api/games/{game_id}/qr.png"

    return {
        "game_id": game_id,
        "code": code,
        "join_url": join_url,
        "qr_png_url": qr_png_url,
        "status": "open",
        "ends_at": None,
    }

@router.post("/device/games/{id}/start")
async def start_device_game(
    id: str,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db)
):
    game = db.query(Game).filter(Game.id == id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    if game.status == "closed":
        raise HTTPException(status_code=400, detail="Cannot start a closed game")

    now = datetime.now(timezone.utc)
    params = {}
    if game.params_json:
        try:
            params = json.loads(game.params_json)
        except Exception:
            pass
    duration_s = params.get("duration_s", 600)
    ends_at = now + timedelta(seconds=duration_s)

    game.status = "running"
    game.started_at = now
    game.ends_at = ends_at
    db.commit()

    # Realtime notification
    await manager.broadcast(
        f"game:{game.id}",
        "game.started",
        {
            "game_id": game.id,
            "status": "running",
            "started_at": game.started_at.isoformat(),
            "ends_at": game.ends_at.isoformat(),
        }
    )

    return {
        "status": "running",
        "started_at": game.started_at.isoformat(),
        "ends_at": game.ends_at.isoformat(),
    }

@router.post("/device/games/{id}/end")
async def end_device_game(id: str, device: Device = Depends(get_current_device), db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.id == id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    await close_game_session(db, game)
    return {"status": "ended"}

@router.get("/device/games/{id}/state")
def get_device_game_state(id: str, device: Device = Depends(get_current_device), db: Session = Depends(get_db)):
    """Everything the handheld's lobby and in-game screens need, in one call.

    Brief 03 §7.3 asks for a ``players`` array with ``joined``/``submissions``, plus
    ``pending_votes`` and ``winner``. The original response only carried
    ``scores`` keyed by player id, which is why the handheld had to poll and merge.
    All of it is returned now, and the older keys are kept so nothing that reads
    them breaks (docs/SERVER_CONTRACT.md G-07).
    """
    game = db.query(Game).filter(Game.id == id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    params = parse_game_params(game)

    score_rows = db.query(Score).filter(Score.game_id == game.id).all()
    totals: Dict[str, int] = {}
    score_reasons: Dict[str, list] = {}
    for row in score_rows:
        totals[row.player_id] = totals.get(row.player_id, 0) + row.points
        score_reasons.setdefault(row.player_id, []).append(
            {"points": row.points, "reason": row.reason}
        )

    submissions_count = db.query(Photo).filter(Photo.game_id == game.id).count()

    players_out = []
    for player in game.players:
        submissions = db.query(Photo).filter(
            Photo.game_id == game.id, Photo.author_player_id == player.id
        ).count()
        completed = db.query(GameTask).filter(
            GameTask.game_id == game.id,
            GameTask.player_id == player.id,
            GameTask.status == "completed",
        ).count()
        players_out.append(
            {
                "id": player.id,
                "name": player.display_name,
                "display_name": player.display_name,
                "color": player.color,
                "team": player.team,
                # token_hash is set when a phone claims the slot and cleared when the
                # session closes, which is exactly "is this player present".
                "joined": bool(player.token_hash),
                "score": totals.get(player.id, 0),
                "submissions": submissions,
                "completed_tasks": completed,
            }
        )
    players_out.sort(key=lambda entry: entry["score"], reverse=True)

    pending_votes = db.query(Photo).filter(
        Photo.game_id == game.id, Photo.approval_status == "pending"
    ).count()

    winner = None
    if players_out:
        top = players_out[0]
        tied = len([p for p in players_out if p["score"] == top["score"]]) > 1
        if top["score"] > 0 and not tied:
            winner = top["display_name"]

    return {
        "status": game.status,
        "ends_at": game.ends_at.isoformat() if game.ends_at else None,
        "started_at": game.started_at.isoformat() if game.started_at else None,
        "duration_s": params.get("duration_s", 600),
        "players": players_out,
        "pending_votes": pending_votes,
        "winner": winner,
        "type": game.type,
        "code": game.code,
        "penalty": params.get("penalty", "Loser buys a treat"),
        "params": params,
        # Kept for clients written against the earlier shape.
        "scores": totals,
        "submissions_count": submissions_count,
    }

# --- SOS ---
@router.post("/device/sos")
async def trigger_device_sos(body: SOSBody, device: Device = Depends(get_current_device), db: Session = Depends(get_db)):
    sos = SOSEvent(
        user_id=device.owner_user_id,
        device_id=device.id,
        trip_id=body.trip_id,
        lat=body.lat,
        lng=body.lng,
        status="active",
    )
    db.add(sos)
    db.commit()

    # Log trusted contact SMS alert
    owner = db.query(User).filter(User.id == device.owner_user_id).first() if device.owner_user_id else None
    contact_name = owner.trusted_contact_name if owner else "Emergency Contact"
    contact_phone = owner.trusted_contact_phone if owner else "Unset"
    
    print(f"[SOS ALERT] Device {device.id} triggered SOS at ({body.lat}, {body.lng}). Notifying {contact_name} ({contact_phone}).")

    if device.owner_user_id:
        await manager.broadcast(
            f"user:{device.owner_user_id}",
            "device.sos",
            {"sos_id": sos.id, "lat": body.lat, "lng": body.lng}
        )

    return {"status": "sos_logged", "sos_id": sos.id}
