"""Photo hunt and bingo modules.

Both are thin: the shared scoring lives in :class:`~.base.LocalScoreboard`, which
copies the server's rules. What differs between games is *how a photo is matched to
a task* and *how winning is decided*.

**Photo hunt** — any approved photo for a task completes that task.
**Bingo** — a 5x5 grid; a player wins the moment they complete a full row, column
or diagonal (``first_n_in_row``, n = 5), exactly as
``backend/app/services/games.py:check_bingo_win`` does it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Set

from .base import GameEvent, GameModule, PhotoSubmission

logger = logging.getLogger("ffh.games.modules")

GRID_SIZE = 5


class PhotoHuntGame(GameModule):
    """Pokemon-style photo hunt (brief §9 item 1)."""

    type = "photo_hunt"
    title = "Photo hunt"

    def on_photo(self, submission: PhotoSubmission) -> List[GameEvent]:
        events = super().on_photo(submission)
        task = self.task_for(submission.task_id)
        if task is not None and task.get("item_key"):
            events.append(
                GameEvent(
                    "submission",
                    player=submission.player_name,
                    photo_id=submission.photo_id,
                    message=f"{submission.player_name} found {task.get('item_key')}",
                )
            )
        if self.all_tasks_complete():
            events.extend(self.on_end())
        return events

    def all_tasks_complete(self) -> bool:
        if not self.tasks:
            return False
        return len(self.scoreboard.completed_tasks) >= len(self.tasks)

    def win_condition_met(self) -> bool:
        return self.all_tasks_complete()

    def rarest_find(self) -> Optional[Dict[str, Any]]:
        """The highest-point task anyone completed — used on the results screen."""
        best = None
        best_points = -1
        for task_id, player_id in self.scoreboard.completed_tasks.items():
            for index, task in enumerate(self.tasks):
                key = str(task.get("task_id") or task.get("id") or index)
                if key != str(task_id):
                    continue
                points = int(task.get("points", 10))
                if points > best_points:
                    best_points = points
                    best = {"task": task, "player_id": player_id, "points": points}
        return best


class BingoGame(GameModule):
    """Bingo. First to five in a row wins (brief §9 item 3, §8.2)."""

    type = "bingo"
    title = "Bingo"

    def __init__(self, config: Dict[str, Any], players: Sequence[Dict[str, Any]],
                 tasks: Sequence[Dict[str, Any]]) -> None:
        super().__init__(config, players, tasks)
        self.n = int(config.get("n", 5) or 5)

    # ------------------------------------------------------------------ #
    @staticmethod
    def tile_key(index: int) -> str:
        return f"r{index // GRID_SIZE}c{index % GRID_SIZE}"

    @staticmethod
    def index_from_key(key: str) -> Optional[int]:
        """``"r2c3"`` -> 13. Returns ``None`` for anything else."""
        if not key or len(key) < 4 or not key.startswith("r"):
            return None
        try:
            row_part, col_part = key[1:].split("c", 1)
            row, col = int(row_part), int(col_part)
        except (ValueError, IndexError):
            return None
        if 0 <= row < GRID_SIZE and 0 <= col < GRID_SIZE:
            return row * GRID_SIZE + col
        return None

    def completed_indices(self, player_id: str) -> Set[int]:
        """Completed tiles as grid indices, accepting either ordering scheme.

        The server's bingo check uses the task's ``idx`` (its position in the list
        it was sent). Our tasks carry ``item_key`` like ``r2c3``. We accept both so
        a plan generated here and a game created on the server agree.
        """
        completed: Set[int] = set()
        for task_id, owner in self.scoreboard.completed_tasks.items():
            if owner != player_id:
                continue
            for index, task in enumerate(self.tasks):
                key = str(task.get("task_id") or task.get("id") or index)
                if key != str(task_id):
                    continue
                from_key = self.index_from_key(str(task.get("item_key") or ""))
                completed.add(from_key if from_key is not None else index)
        return completed

    def winning_lines(self, player_id: str) -> List[str]:
        """Every complete row/column/diagonal, as human labels."""
        completed = self.completed_indices(player_id)
        wins: List[str] = []
        needed = self.n

        for row in range(GRID_SIZE):
            if all((row * GRID_SIZE + col) in completed for col in range(GRID_SIZE)):
                wins.append(f"row {row + 1}")
        for col in range(GRID_SIZE):
            if all((row * GRID_SIZE + col) in completed for row in range(GRID_SIZE)):
                wins.append(f"column {col + 1}")
        if all((index * GRID_SIZE + index) in completed for index in range(GRID_SIZE)):
            wins.append("the diagonal")
        if all((index * GRID_SIZE + (GRID_SIZE - 1 - index)) in completed for index in range(GRID_SIZE)):
            wins.append("the other diagonal")

        # If the config asks for fewer than five, honour it explicitly.
        if needed < GRID_SIZE and not wins:
            for row in range(GRID_SIZE):
                count = sum(1 for col in range(GRID_SIZE) if (row * GRID_SIZE + col) in completed)
                if count >= needed:
                    wins.append(f"row {row + 1}")
        return wins

    def has_bingo(self, player_id: str) -> bool:
        return bool(self.winning_lines(player_id))

    # ------------------------------------------------------------------ #
    def on_photo(self, submission: PhotoSubmission) -> List[GameEvent]:
        events = super().on_photo(submission)
        if submission.status != "approved":
            return events
        if self.has_bingo(submission.player_id):
            lines = ", ".join(self.winning_lines(submission.player_id))
            self.scoreboard.events.append(
                GameEvent("ended", player=submission.player_name,
                          message=f"Bingo! {submission.player_name} completed {lines}")
            )
            events.extend(self.on_end())
        return events

    def win_condition_met(self) -> bool:
        """Bingo is won by *any* grid line, for anyone on the grid.

        The check reads the completed-task map rather than the scoreboard, so a game
        where players were added after the module was built still detects a win.
        """
        owners = set(self.scoreboard.completed_tasks.values())
        owners |= set(self.scoreboard.scores)
        return any(self.has_bingo(player_id) for player_id in owners)

    def grid(self, player_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """The 5x5 grid for the player view: text, tile key, done flag."""
        completed = self.completed_indices(player_id) if player_id else set()
        cells: List[Dict[str, Any]] = []
        for index, task in enumerate(self.tasks[: GRID_SIZE * GRID_SIZE]):
            key = str(task.get("item_key") or self.tile_key(index))
            cells.append(
                {
                    "idx": index,
                    "key": key,
                    "text": task.get("text", ""),
                    "points": int(task.get("points", 10)),
                    "done": index in completed,
                }
            )
        while len(cells) < GRID_SIZE * GRID_SIZE:
            index = len(cells)
            cells.append({"idx": index, "key": self.tile_key(index), "text": "", "points": 10, "done": False})
        return cells

    def score(self) -> Dict[str, Any]:
        payload = super().score()
        payload["grid"] = self.grid()
        payload["winning_lines"] = {
            player_id: self.winning_lines(player_id)
            for player_id in self.scoreboard.scores
            if self.has_bingo(player_id)
        }
        return payload
