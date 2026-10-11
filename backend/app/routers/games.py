import io
import json
import qrcode
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import Response as RawResponse
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.db import get_db
from backend.app.models.models import Game, GamePlayer, GameTask, Photo, Score, Trip
from backend.app.services.games import parse_game_params

router = APIRouter(prefix="/games", tags=["games"])

@router.get("")
def list_games(db: Session = Depends(get_db)):
    games = db.query(Game).order_by(Game.created_at.desc()).limit(30).all()
    out = []
    for g in games:
        params = parse_game_params(g)
        # Winner
        scores = db.query(Score).filter(Score.game_id == g.id).all()
        totals = {}
        for s in scores:
            totals[s.player_id] = totals.get(s.player_id, 0) + s.points
        winner_id = max(totals, key=totals.get) if totals else None
        winner_name = None
        if winner_id:
            p = db.query(GamePlayer).filter(GamePlayer.id == winner_id).first()
            if p:
                winner_name = p.display_name

        out.append({
            "id": g.id,
            "type": g.type,
            "status": g.status,
            "code": g.code,
            "started_at": g.started_at.isoformat() if g.started_at else None,
            "ends_at": g.ends_at.isoformat() if g.ends_at else None,
            "ended_at": g.ended_at.isoformat() if g.ended_at else None,
            "winner_name": winner_name,
            "penalty": params.get("penalty", "Loser buys a treat"),
            "players_count": len(g.players),
            "trip_id": g.trip_id,
            "qr_url": f"/api/games/{g.id}/qr.png",
        })
    return out

from fastapi import Request
from backend.app.services.network import get_base_url

@router.get("/{id}")
def get_game_detail(id: str, request: Request, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.id == id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    params = parse_game_params(game)

    # Scoreboard
    scores = db.query(Score).filter(Score.game_id == game.id).all()
    points_by_player = {}
    for s in scores:
        points_by_player[s.player_id] = points_by_player.get(s.player_id, 0) + s.points

    players_out = []
    for p in game.players:
        pts = points_by_player.get(p.id, 0)
        players_out.append({
            "id": p.id,
            "display_name": p.display_name,
            "color": p.color,
            "team": p.team,
            "points": pts,
        })
    players_out.sort(key=lambda x: x["points"], reverse=True)

    # Submissions
    photos = db.query(Photo).filter(Photo.game_id == game.id).all()
    submissions = []
    for ph in photos:
        task = db.query(GameTask).filter(GameTask.id == ph.task_id).first() if ph.task_id else None
        approvals = sum(1 for v in ph.votes if v.vote == "approve")
        rejections = sum(1 for v in ph.votes if v.vote == "reject")

        submissions.append({
            "photo_id": ph.id,
            "player_id": ph.author_player_id,
            "player_name": ph.author_name,
            "task_id": ph.task_id,
            "task_text": task.text if task else "Photo task",
            "approval_status": ph.approval_status,
            "approvals": approvals,
            "rejections": rejections,
            "url": f"/api/trips/{ph.trip_id}/photos/{ph.id}/file" if ph.trip_id else "",
            "thumb_url": f"/api/trips/{ph.trip_id}/photos/{ph.id}/thumb" if ph.trip_id else "",
            "taken_at": ph.taken_at.isoformat() if ph.taken_at else None,
        })

    tasks_out = [
        {
            "id": t.id,
            "player_id": t.player_id,
            "player_name": t.player_name,
            "idx": t.idx,
            "text": t.text,
            "points": t.points,
            "status": t.status,
            "item_key": t.item_key,
        }
        for t in sorted(game.tasks, key=lambda x: x.idx)
    ]

    base_url = get_base_url(request)
    join_url = f"{base_url}/g/{game.code}"

    return {
        "id": game.id,
        "type": game.type,
        "status": game.status,
        "code": game.code,
        "params": params,
        "started_at": game.started_at.isoformat() if game.started_at else None,
        "ends_at": game.ends_at.isoformat() if game.ends_at else None,
        "ended_at": game.ended_at.isoformat() if game.ended_at else None,
        "penalty": params.get("penalty", "Loser buys a treat"),
        "players": players_out,
        "tasks": tasks_out,
        "submissions": submissions,
        "join_url": join_url,
        "qr_url": f"/api/games/{game.id}/qr.png",
    }

@router.get("/{id}/qr.png")
def get_game_qr_png(id: str, request: Request, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.id == id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    base_url = get_base_url(request)
    join_url = f"{base_url}/g/{game.code}"
    qr_img = qrcode.make(join_url)
    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    buf.seek(0)
    return RawResponse(content=buf.getvalue(), media_type="image/png")
