import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from backend.app.models.models import Game, GamePlayer, GameTask, Photo, PhotoVote, Score, User
from backend.app.services.xp import award_xp
from backend.app.services.badges import award_badge_if_earned
from backend.app.realtime import manager

def parse_game_params(game: Game) -> dict:
    try:
        return json.loads(game.params_json) if game.params_json else {}
    except Exception:
        return {}

def check_bingo_win(db: Session, game: Game, player_id: str, n: int = 5) -> bool:
    """Check 5x5 bingo grid for player. Tasks have idx 0..24."""
    # Find all completed tasks for this player
    tasks = db.query(GameTask).filter(
        GameTask.game_id == game.id,
        GameTask.player_id == player_id,
        GameTask.status == "completed"
    ).all()
    completed_indices = {t.idx for t in tasks}

    grid_size = 5
    # Rows
    for r in range(grid_size):
        if all((r * grid_size + c) in completed_indices for c in range(grid_size)):
            return True

    # Columns
    for c in range(grid_size):
        if all((r * grid_size + c) in completed_indices for r in range(grid_size)):
            return True

    # Diagonals
    if all((i * grid_size + i) in completed_indices for i in range(grid_size)):
        return True
    if all((i * grid_size + (grid_size - 1 - i)) in completed_indices for i in range(grid_size)):
        return True

    return False

async def close_game_session(db: Session, game: Game):
    if game.status == "closed":
        return

    game.status = "closed"
    game.ended_at = datetime.now(timezone.utc)

    # Calculate winners and losers
    # Tally scores per player
    scores = db.query(Score).filter(Score.game_id == game.id).all()
    player_points: dict[str, int] = {}
    for sc in scores:
        player_points[sc.player_id] = player_points.get(sc.player_id, 0) + sc.points

    winner_id = None
    max_pts = -1
    for p_id, pts in player_points.items():
        if pts > max_pts:
            max_pts = pts
            winner_id = p_id

    # Revoke every player token (C2: revoke every player token on game end)
    for player in game.players:
        player.token_hash = None
        if player.user_id:
            user = db.query(User).filter(User.id == player.user_id).first()
            if user:
                # XP for participating
                award_xp(db, user, "game_played", 20, ref=game.id)
                if player.id == winner_id:
                    award_xp(db, user, "game_win", 50, ref=game.id)
                    if game.type == "bingo":
                        award_badge_if_earned(db, user.id, "bingo_champion")
                    elif game.type == "color_hunt":
                        award_badge_if_earned(db, user.id, "color_master")

    db.commit()

    # Emit game.ended over WebSocket
    await manager.broadcast(
        f"game:{game.id}",
        "game.ended",
        {
            "game_id": game.id,
            "status": "closed",
            "winner_player_id": winner_id,
            "ended_at": game.ended_at.isoformat(),
        }
    )

async def evaluate_photo_approval(db: Session, photo: Photo, game: Game):
    params = parse_game_params(game)
    approval_mode = params.get("approval", "peer_vote")

    if approval_mode == "auto":
        photo.approval_status = "approved"
    elif approval_mode == "peer_vote":
        total_players = len(game.players)
        # max(1, min(2, players - 1))
        required = max(1, min(2, total_players - 1))

        approvals = sum(1 for v in photo.votes if v.vote == "approve")
        rejections = sum(1 for v in photo.votes if v.vote == "reject")

        if approvals >= required:
            photo.approval_status = "approved"
        elif rejections >= required:
            photo.approval_status = "rejected"
        else:
            photo.approval_status = "pending"

    if photo.approval_status == "approved" and photo.task_id:
        task = db.query(GameTask).filter(GameTask.id == photo.task_id).first()
        if task and task.status != "completed":
            task.status = "completed"
            # Add score
            score = Score(
                game_id=game.id,
                player_id=photo.author_player_id,
                user_id=photo.author_user_id,
                points=task.points,
                reason=f"Completed {task.text[:30]}",
            )
            db.add(score)
            db.flush()

            # Award XP if associated with registered user
            if photo.author_user_id:
                user = db.query(User).filter(User.id == photo.author_user_id).first()
                if user:
                    award_xp(db, user, "photo_task", 10 + task.points, ref=photo.id)

            # Check win condition
            win_condition = params.get("win_condition", "most_points")
            if win_condition == "first_n_in_row" and game.type == "bingo":
                if check_bingo_win(db, game, photo.author_player_id, 5):
                    await close_game_session(db, game)
                    return

    db.commit()

    # Broadcast vote & score updates
    await manager.broadcast(
        f"game:{game.id}",
        "game.submission_status",
        {
            "photo_id": photo.id,
            "status": photo.approval_status,
            "task_id": photo.task_id,
        }
    )
