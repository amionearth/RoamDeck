import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.app.db import Base
from backend.app.models.models import Game, GamePlayer, GameTask, Photo, PhotoVote, Score, User
from backend.app.services.games import evaluate_photo_approval, close_game_session, check_bingo_win

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

def test_peer_vote_approval_logic(db_session):
    async def _run():
        # Create game with 4 players, approval=peer_vote
        # max(1, min(2, 4 - 1)) = 2 approvals required
        game = Game(
            id="game-1",
            type="photo_hunt",
            status="running",
            code="TEST1",
            params_json='{"approval": "peer_vote", "win_condition": "most_points"}'
        )
        db_session.add(game)

        p1 = GamePlayer(id="p-1", game_id=game.id, display_name="Asha")
        p2 = GamePlayer(id="p-2", game_id=game.id, display_name="Dev")
        p3 = GamePlayer(id="p-3", game_id=game.id, display_name="Tara")
        p4 = GamePlayer(id="p-4", game_id=game.id, display_name="Rohan")
        db_session.add_all([p1, p2, p3, p4])

        task = GameTask(id="task-1", game_id=game.id, text="Find blue tile", points=10)
        db_session.add(task)

        photo = Photo(
            id="ph-1",
            trip_id="trip-1",
            game_id=game.id,
            task_id=task.id,
            author_player_id=p1.id,
            author_name=p1.display_name,
            file_path="x.jpg",
            thumb_path="y.jpg",
            approval_status="pending"
        )
        db_session.add(photo)
        db_session.commit()

        # First vote: 1 approve -> still pending
        v1 = PhotoVote(photo_id=photo.id, voter_player_id=p2.id, vote="approve")
        db_session.add(v1)
        db_session.commit()
        await evaluate_photo_approval(db_session, photo, game)
        assert photo.approval_status == "pending"

        # Second vote: 2nd approve -> approved!
        v2 = PhotoVote(photo_id=photo.id, voter_player_id=p3.id, vote="approve")
        db_session.add(v2)
        db_session.commit()
        await evaluate_photo_approval(db_session, photo, game)
        assert photo.approval_status == "approved"
        assert task.status == "completed"

        # Check score added
        score = db_session.query(Score).filter(Score.game_id == game.id, Score.player_id == p1.id).first()
        assert score is not None
        assert score.points == 10

    asyncio.run(_run())

def test_bingo_win_condition(db_session):
    game = Game(id="game-bingo", type="bingo", status="running", code="BINGO")
    db_session.add(game)
    player = GamePlayer(id="p-bingo", game_id=game.id, display_name="Dev")
    db_session.add(player)

    # Complete a horizontal row: 0, 1, 2, 3, 4
    for idx in range(5):
        t = GameTask(
            id=f"t-{idx}",
            game_id=game.id,
            player_id=player.id,
            idx=idx,
            text=f"Tile {idx}",
            status="completed"
        )
        db_session.add(t)
    db_session.commit()

    assert check_bingo_win(db_session, game, player.id, 5) is True
