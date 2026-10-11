"""The game module interface, and local scoring that mirrors the server's.

Brief §5.3: games are plugins with ``on_start``, ``on_event``, ``on_photo``,
``score``, ``on_end``. Brief 02 §B4 adds the requirement that a game must be able
to run **locally** when the server is unreachable, which is what
:class:`LocalScoreboard` is for: it applies the same rules the server applies
(``backend/app/services/games.py``), so a game that starts offline produces the
same winner as one that starts online.

The rules copied from the server, deliberately:

* approval threshold for ``peer_vote`` is ``max(1, min(2, players - 1))``;
* a photo that is approved completes its task **once** and scores the task's points;
* bingo wins on ``first_n_in_row`` with n = 5, checked on a 5x5 grid by index;
* ending a game freezes scores and revokes players.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("ffh.games")


@dataclass
class GameEvent:
    """Something interesting happened. Mirrors the server's WebSocket events."""

    kind: str  # "player_joined" | "submission" | "vote" | "score" | "ended" | "started"
    player: str = ""
    task_id: str = ""
    photo_id: str = ""
    points: int = 0
    message: str = ""
    thumb_url: str = ""
    at: float = field(default_factory=time.time)
    data: Dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "player": self.player,
            "task_id": self.task_id,
            "photo_id": self.photo_id,
            "points": self.points,
            "message": self.message,
            "thumb_url": self.thumb_url,
            "data": self.data,
        }


@dataclass
class PhotoSubmission:
    """A photo sent in against a task, plus its local approval state."""

    photo_id: str
    player_id: str
    player_name: str
    task_id: str
    file_path: str = ""
    thumb_url: str = ""
    caption: str = ""
    status: str = "pending"  # pending | approved | rejected
    approvals: int = 0
    rejections: int = 0
    points_awarded: int = 0
    taken_at: float = field(default_factory=time.time)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "photo_id": self.photo_id,
            "player_id": self.player_id,
            "player_name": self.player_name,
            "task_id": self.task_id,
            "file_path": self.file_path,
            "thumb_url": self.thumb_url,
            "caption": self.caption,
            "status": self.status,
            "approvals": self.approvals,
            "rejections": self.rejections,
            "points_awarded": self.points_awarded,
        }


@dataclass
class GameScore:
    player_id: str
    player_name: str
    color: str = "#1E7F85"
    points: int = 0
    submissions: int = 0
    completed_tasks: int = 0
    rank: int = 0

    @property
    def label(self) -> str:
        return self.player_name or "Player"

    def to_payload(self) -> Dict[str, Any]:
        return {
            "player_id": self.player_id,
            "name": self.player_name,
            "score": self.points,
            "submissions": self.submissions,
            "completed_tasks": self.completed_tasks,
            "color": self.color,
            "rank": self.rank,
        }


class LocalScoreboard:
    """The offline scoreboard. Applies the server's rules exactly."""

    def __init__(self, approval_mode: str = "peer_vote", total_players: int = 2) -> None:
        self.approval_mode = approval_mode
        self.total_players = max(1, total_players)
        self.scores: Dict[str, GameScore] = {}
        self.submissions: Dict[str, PhotoSubmission] = {}
        self.votes: Dict[str, Dict[str, str]] = {}  # photo_id -> {voter: approve|reject}
        self.completed_tasks: Dict[str, str] = {}  # task_id -> player_id
        self.events: List[GameEvent] = []

    # ------------------------------------------------------------------ #
    @property
    def approval_threshold(self) -> int:
        """``max(1, min(2, players - 1))`` — the server's rule."""
        return max(1, min(2, self.total_players - 1))

    def register_player(self, player_id: str, name: str, color: str = "#1E7F85") -> GameScore:
        score = self.scores.get(player_id) or GameScore(player_id=player_id, player_name=name, color=color)
        self.scores[player_id] = score
        return score

    def add_submission(self, submission: PhotoSubmission, task_points: int = 10) -> PhotoSubmission:
        self.submissions[submission.photo_id] = submission
        score = self.scores.get(submission.player_id)
        if score is None:
            score = self.register_player(submission.player_id, submission.player_name)
        score.submissions += 1
        if self.approval_mode == "auto":
            self._approve(submission, task_points)
        return submission

    def vote(self, photo_id: str, voter_id: str, approve: bool) -> Optional[PhotoSubmission]:
        submission = self.submissions.get(photo_id)
        if submission is None:
            return None
        if submission.player_id == voter_id:
            logger.debug("ignoring a self-vote on %s", photo_id)
            return submission
        bucket = self.votes.setdefault(photo_id, {})
        bucket[voter_id] = "approve" if approve else "reject"
        submission.approvals = sum(1 for value in bucket.values() if value == "approve")
        submission.rejections = sum(1 for value in bucket.values() if value == "reject")

        threshold = self.approval_threshold
        if submission.status == "pending":
            if submission.approvals >= threshold:
                self._approve(submission, self._task_points(submission.task_id))
            elif submission.rejections >= threshold:
                submission.status = "rejected"
                self.events.append(
                    GameEvent("vote", player=submission.player_name, photo_id=photo_id,
                              message=f"{submission.player_name}'s photo was not accepted")
                )
        return submission

    def _task_points(self, task_id: str) -> int:
        return self._task_points_map.get(task_id, 10)

    #: Set by the module so approvals can score the right number of points.
    _task_points_map: Dict[str, int] = {}

    def set_task_points(self, mapping: Dict[str, int]) -> None:
        self._task_points_map = dict(mapping)

    def _approve(self, submission: PhotoSubmission, points: int) -> None:
        if submission.status == "approved":
            return
        submission.status = "approved"
        already = self.completed_tasks.get(submission.task_id)
        if already is None:
            self.completed_tasks[submission.task_id] = submission.player_id
            submission.points_awarded = points
            score = self.scores.get(submission.player_id)
            if score is not None:
                score.points += points
                score.completed_tasks += 1
            self.events.append(
                GameEvent(
                    "score",
                    player=submission.player_name,
                    task_id=submission.task_id,
                    photo_id=submission.photo_id,
                    points=points,
                    message=f"+{points} for {submission.player_name}",
                )
            )

    # ------------------------------------------------------------------ #
    def pending_votes(self) -> int:
        return sum(1 for submission in self.submissions.values() if submission.status == "pending")

    def leaderboard(self) -> List[GameScore]:
        ordered = sorted(
            self.scores.values(), key=lambda score: (-score.points, score.player_name.lower())
        )
        for index, score in enumerate(ordered, start=1):
            score.rank = index
        return ordered

    def winner(self) -> Optional[GameScore]:
        board = self.leaderboard()
        if not board:
            return None
        if len(board) > 1 and board[0].points == board[1].points:
            return None  # a genuine draw; the penalty does not apply
        return board[0] if board[0].points > 0 else None

    def to_payload(self) -> Dict[str, Any]:
        board = self.leaderboard()
        return {
            "players": [score.to_payload() for score in board],
            "pending_votes": self.pending_votes(),
            "winner": self.winner().label if self.winner() else None,
            "approval_mode": self.approval_mode,
            "approval_threshold": self.approval_threshold,
            "submissions": len(self.submissions),
        }


class GameModule:
    """Base class for game plugins. Subclasses override what they need."""

    #: Matches ``Game.type`` on the server.
    type: str = "generic"
    title: str = "Game"

    def __init__(self, config: Dict[str, Any], players: Sequence[Dict[str, Any]],
                 tasks: Sequence[Dict[str, Any]]) -> None:
        self.config = dict(config)
        self.players = [dict(player) for player in players]
        self.tasks = [dict(task) for task in tasks]
        self.scoreboard = LocalScoreboard(
            approval_mode=str(config.get("approval", "peer_vote")),
            total_players=len(self.players),
        )
        self.scoreboard.set_task_points(
            {str(task.get("task_id") or task.get("id") or index): int(task.get("points", 10))
             for index, task in enumerate(self.tasks)}
        )
        for index, player in enumerate(self.players):
            self.scoreboard.register_player(
                str(player.get("player_id") or player.get("id") or f"p{index}"),
                str(player.get("name") or player.get("display_name") or f"Player {index + 1}"),
                str(player.get("color") or "#1E7F85"),
            )
        self.started_at: Optional[float] = None
        self.ended_at: Optional[float] = None
        self.ended = False

    # --- interface -----------------------------------------------------
    def on_start(self) -> List[GameEvent]:
        self.started_at = time.time()
        return [GameEvent("started", message=f"{self.title} has started")]

    def on_event(self, event: GameEvent) -> List[GameEvent]:
        """React to anything (a phone joining, a timer, a host action)."""
        return []

    def on_photo(self, submission: PhotoSubmission) -> List[GameEvent]:
        task = self.task_for(submission.task_id)
        points = int(task.get("points", 10)) if task else 10
        self.scoreboard.add_submission(submission, points)
        events = [
            GameEvent(
                "submission",
                player=submission.player_name,
                task_id=submission.task_id,
                photo_id=submission.photo_id,
                thumb_url=submission.thumb_url,
                message=f"{submission.player_name} sent a photo",
            )
        ]
        if submission.status == "approved":
            events.append(
                GameEvent("score", player=submission.player_name, points=submission.points_awarded)
            )
        return events

    def on_vote(self, photo_id: str, voter_id: str, approve: bool) -> List[GameEvent]:
        submission = self.scoreboard.vote(photo_id, voter_id, approve)
        if submission is None:
            return []
        return [
            GameEvent(
                "vote",
                player=submission.player_name,
                photo_id=photo_id,
                message=f"{submission.player_name}'s photo is now {submission.status}",
            )
        ]

    def score(self) -> Dict[str, Any]:
        return self.scoreboard.to_payload()

    def on_end(self) -> List[GameEvent]:
        self.ended = True
        self.ended_at = time.time()
        board = self.scoreboard.leaderboard()
        winner = self.scoreboard.winner()
        return [
            GameEvent(
                "ended",
                message=f"{winner.label} wins" if winner else "Game over - it is a draw",
                data={"scores": [score.to_payload() for score in board]},
            )
        ]

    # --- helpers -------------------------------------------------------
    def task_for(self, task_id: str) -> Optional[Dict[str, Any]]:
        for index, task in enumerate(self.tasks):
            if str(task.get("task_id") or task.get("id") or index) == str(task_id):
                return task
            if str(task.get("item_key") or "") == str(task_id):
                return task
        return None

    def tasks_for_player(self, player_name: str) -> List[Dict[str, Any]]:
        mine = [task for task in self.tasks if task.get("player_name") in (None, "", player_name)]
        return mine or list(self.tasks)

    def win_condition_met(self) -> bool:
        return False

    def duration_elapsed(self, now: Optional[float] = None) -> bool:
        if self.started_at is None:
            return False
        moment = now or time.time()
        return (moment - self.started_at) >= float(self.config.get("duration_s", 600))

    # --- bingo support -------------------------------------------------
    def completed_task_indices(self, player_id: str) -> set[int]:
        """Indices of tasks this player has completed, by position in ``self.tasks``."""
        completed: set[int] = set()
        for task_id, owner in self.scoreboard.completed_tasks.items():
            if owner != player_id:
                continue
            for index, task in enumerate(self.tasks):
                if str(task.get("task_id") or task.get("id") or index) == str(task_id):
                    completed.add(index)
        return completed
