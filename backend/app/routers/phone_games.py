import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.db import get_db
from backend.app.models.models import Game, GamePlayer, GameTask, Photo, PhotoVote, Score, Trip
from backend.app.security import create_player_token, verify_signed_token, get_current_user_optional
from backend.app.services.games import parse_game_params, evaluate_photo_approval
from backend.app.services.images import process_and_save_image
from backend.app.realtime import manager

router = APIRouter(prefix="/g", tags=["phone_games"])

class JoinGameBody(BaseModel):
    player_id: Optional[str] = None
    display_name: Optional[str] = None

class VoteBody(BaseModel):
    photo_id: str
    vote: str # "approve" or "reject"

def get_verified_player(player_token: str, db: Session):
    """Resolve a player token, and refuse it the moment the session is closed.

    ``close_game_session`` clears every ``token_hash``, but the token itself is a
    signed blob that stays verifiable, so the game's status is checked here — that is
    what actually makes closing a session revoke its player links
    (brief 02 §C2, docs/SERVER_CONTRACT.md G-06).
    """
    data = verify_signed_token(player_token)
    if not data or not data.get("player_id") or not data.get("game_id"):
        raise HTTPException(status_code=401, detail="Invalid player token")
    player = db.query(GamePlayer).filter(GamePlayer.id == data["player_id"]).first()
    if not player:
        raise HTTPException(status_code=401, detail="Player not found")
    game = db.query(Game).filter(Game.id == player.game_id).first()
    if game is None:
        raise HTTPException(status_code=401, detail="Player not found")
    if game.status == "closed":
        raise HTTPException(
            status_code=409,
            detail={"error": "game_closed", "message": "This game has ended."},
        )
    return player

@router.get("/{code}/info")
def get_game_info(code: str, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.code == code.upper().strip()).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    params = parse_game_params(game)
    players = [
        {
            "id": p.id,
            "display_name": p.display_name,
            "color": p.color,
            "team": p.team,
            "claimed": bool(p.token_hash),
        }
        for p in game.players
    ]

    return {
        "game_id": game.id,
        "code": game.code,
        "type": game.type,
        "status": game.status,
        "params": params,
        "started_at": game.started_at.isoformat() if game.started_at else None,
        "ends_at": game.ends_at.isoformat() if game.ends_at else None,
        "players": players,
    }

@router.post("/{code}/start")
async def start_game_phone(code: str, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.code == code.upper().strip()).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    if game.status == "closed":
        raise HTTPException(status_code=400, detail="Cannot start a closed game")

    now = datetime.now(timezone.utc)
    params = parse_game_params(game)
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

@router.post("/{code}/join")
async def join_game(code: str, body: JoinGameBody, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.code == code.upper().strip()).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    if game.status == "closed":
        raise HTTPException(status_code=409, detail={"error": "game_closed", "message": "This game has ended."})

    target_player: Optional[GamePlayer] = None

    if body.player_id:
        target_player = db.query(GamePlayer).filter(GamePlayer.id == body.player_id, GamePlayer.game_id == game.id).first()
        if not target_player:
            raise HTTPException(status_code=404, detail="Selected player not found")
    elif body.display_name:
        name = body.display_name.strip()
        # Find if existing unclaimed or create new player
        target_player = db.query(GamePlayer).filter(GamePlayer.game_id == game.id, GamePlayer.display_name == name).first()
        if not target_player:
            colors = ["#1E7F85", "#1F4B8F", "#F2A33A", "#7B4FC2", "#2E7D32"]
            color = colors[len(game.players) % len(colors)]
            target_player = GamePlayer(
                game_id=game.id,
                display_name=name,
                color=color,
            )
            db.add(target_player)
            db.flush()
    else:
        raise HTTPException(status_code=400, detail="Must provide player_id or display_name")

    token = create_player_token(game.id, target_player.id, target_player.display_name)
    target_player.token_hash = "claimed"
    db.commit()

    # C2: WebSocket event game.player_joined ({player, joined_count})
    joined_count = sum(1 for p in game.players if bool(p.token_hash))
    await manager.broadcast(
        f"game:{game.id}",
        "game.player_joined",
        {
            "player": {
                "id": target_player.id,
                "display_name": target_player.display_name,
                "color": target_player.color,
                "team": target_player.team,
            },
            "joined_count": joined_count,
        }
    )

    return {
        "status": "joined",
        "player_id": target_player.id,
        "display_name": target_player.display_name,
        "color": target_player.color,
        "token": token,
    }

@router.get("/{code}/me")
def get_my_player_status(
    code: str,
    x_player_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    if not x_player_token:
        raise HTTPException(status_code=401, detail="Player token required")
    player = get_verified_player(x_player_token, db)
    game = db.query(Game).filter(Game.id == player.game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    tasks = db.query(GameTask).filter(
        GameTask.game_id == game.id,
        (GameTask.player_id == player.id) | (GameTask.player_id.is_(None))
    ).order_by(GameTask.idx.asc()).all()

    scores = db.query(Score).filter(Score.game_id == game.id, Score.player_id == player.id).all()
    my_points = sum(s.points for s in scores)

    return {
        "player": {
            "id": player.id,
            "display_name": player.display_name,
            "color": player.color,
            "score": my_points,
        },
        "game_status": game.status,
        "ends_at": game.ends_at.isoformat() if game.ends_at else None,
        "tasks": [
            {
                "id": t.id,
                "idx": t.idx,
                "text": t.text,
                "item_key": t.item_key,
                "points": t.points,
                "time_limit_s": t.time_limit_s,
                "status": t.status,
            }
            for t in tasks
        ]
    }

@router.post("/{code}/submissions")
async def submit_task_photo(
    code: str,
    task_id: str = Form(...),
    photo: UploadFile = File(...),
    caption: str = Form(""),
    x_player_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    if not x_player_token:
        raise HTTPException(status_code=401, detail="Player token required")
    player = get_verified_player(x_player_token, db)
    game = db.query(Game).filter(Game.id == player.game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    if game.status == "open":
        raise HTTPException(
            status_code=400,
            detail={"error": "game_not_started", "message": "Waiting for the host to start"}
        )

    if game.status == "closed":
        raise HTTPException(
            status_code=409,
            detail={"error": "game_closed", "message": "This game has ended. No further submissions accepted."}
        )

    task = db.query(GameTask).filter(GameTask.id == task_id, GameTask.game_id == game.id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    contents = await photo.read()
    photo_id = str(uuid.uuid4())
    trip_id = game.trip_id or "general"
    dest_path = settings.DATA_DIR / "trips" / trip_id / "photos" / f"{photo_id}.jpg"
    thumb_path = settings.DATA_DIR / "trips" / trip_id / "photos" / "thumbs" / f"{photo_id}.jpg"

    process_and_save_image(contents, dest_path, thumb_path)

    new_photo = Photo(
        id=photo_id,
        trip_id=game.trip_id or "default",
        game_id=game.id,
        task_id=task.id,
        author_player_id=player.id,
        author_name=player.display_name,
        author_user_id=player.user_id,
        file_path=str(dest_path),
        thumb_path=str(thumb_path),
        caption=caption,
        category="task",
        approval_status="pending",
    )
    db.add(new_photo)
    db.commit()
    db.refresh(new_photo)

    # Evaluate approval (auto vs peer_vote)
    await evaluate_photo_approval(db, new_photo, game)

    thumb_url = f"/api/trips/{new_photo.trip_id}/photos/{new_photo.id}/thumb" if new_photo.trip_id else ""

    # Broadcast realtime submission event (C2: include thumb_url)
    await manager.broadcast(
        f"game:{game.id}",
        "game.submission",
        {
            "photo_id": new_photo.id,
            "player_id": player.id,
            "player_name": player.display_name,
            "task_id": task.id,
            "thumb_url": thumb_url,
            "status": new_photo.approval_status,
        }
    )

    return {
        "status": "submitted",
        "photo_id": new_photo.id,
        "thumb_url": thumb_url,
        "approval_status": new_photo.approval_status,
    }

@router.get("/{code}/gallery")
def get_voting_gallery(
    code: str,
    x_player_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    if not x_player_token:
        raise HTTPException(status_code=401, detail="Player token required")
    player = get_verified_player(x_player_token, db)
    game = db.query(Game).filter(Game.id == player.game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    # Get submissions by OTHER players that are pending and this player hasn't voted on yet
    all_pending = db.query(Photo).filter(
        Photo.game_id == game.id,
        Photo.author_player_id != player.id,
        Photo.approval_status == "pending"
    ).all()

    voted_photo_ids = {
        pv.photo_id for pv in db.query(PhotoVote).filter(PhotoVote.voter_player_id == player.id).all()
    }

    out = []
    for ph in all_pending:
        if ph.id not in voted_photo_ids:
            task = db.query(GameTask).filter(GameTask.id == ph.task_id).first()
            out.append({
                "photo_id": ph.id,
                "player_name": ph.author_name,
                "task_text": task.text if task else "Photo task",
                "caption": ph.caption,
                "thumb_url": f"/api/trips/{ph.trip_id}/photos/{ph.id}/thumb" if ph.trip_id else "",
                "file_url": f"/api/trips/{ph.trip_id}/photos/{ph.id}/file" if ph.trip_id else "",
            })
    return out

@router.post("/{code}/vote")
async def vote_submission(
    code: str,
    body: VoteBody,
    x_player_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    if not x_player_token:
        raise HTTPException(status_code=401, detail="Player token required")
    player = get_verified_player(x_player_token, db)
    game = db.query(Game).filter(Game.id == player.game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    if game.status == "closed":
        raise HTTPException(
            status_code=409,
            detail={"error": "game_closed", "message": "This game has ended."}
        )

    photo = db.query(Photo).filter(Photo.id == body.photo_id, Photo.game_id == game.id).first()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    if photo.author_player_id == player.id:
        raise HTTPException(status_code=400, detail="Cannot vote on your own photo")

    existing_vote = db.query(PhotoVote).filter(
        PhotoVote.photo_id == photo.id,
        PhotoVote.voter_player_id == player.id
    ).first()

    if existing_vote:
        existing_vote.vote = body.vote
    else:
        pv = PhotoVote(
            photo_id=photo.id,
            voter_player_id=player.id,
            vote=body.vote,
        )
        db.add(pv)
    db.commit()

    # Re-evaluate approval
    await evaluate_photo_approval(db, photo, game)

    return {"status": "ok", "vote": body.vote}

@router.get("/{code}/scoreboard")
def get_live_scoreboard(code: str, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.code == code.upper().strip()).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    scores = db.query(Score).filter(Score.game_id == game.id).all()
    totals = {}
    for sc in scores:
        totals[sc.player_id] = totals.get(sc.player_id, 0) + sc.points

    results = []
    for p in game.players:
        results.append({
            "player_id": p.id,
            "display_name": p.display_name,
            "color": p.color,
            "points": totals.get(p.id, 0),
        })

    results.sort(key=lambda x: x["points"], reverse=True)
    for idx, r in enumerate(results):
        r["rank"] = idx + 1

    return {
        "status": game.status,
        "ends_at": game.ends_at.isoformat() if game.ends_at else None,
        "scoreboard": results,
    }
