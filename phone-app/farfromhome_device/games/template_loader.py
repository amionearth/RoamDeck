"""YAML game templates: the content half of "adding a game needs no new code".

One file (``templates/games.yaml``) holds every game. A template is loaded,
validated and turned into a task list. Items may be written either as a mapping
(``{key: red, text: ..., points: 10}``) or as a bare string (``"a red door"``),
because forcing a key onto 25 bingo tiles is exactly the kind of ceremony that
stops people from adding games.

Validation is strict on purpose: a typo in a template should fail loudly at load
time with a file and line, not silently produce a game with no tasks.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("ffh.games.templates")

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
DEFAULT_TEMPLATE_FILE = TEMPLATE_DIR / "games.yaml"

#: Kept in step with ``core.models.GameType``; a template naming anything else is
#: rejected rather than silently downgraded to photo_hunt.
KNOWN_TYPES = (
    "photo_hunt", "bingo", "color_hunt", "funny_tasks", "secret_missions", "imposter", "generic",
)
KNOWN_APPROVALS = ("peer_vote", "auto", "host")
KNOWN_WIN_CONDITIONS = ("most_points", "first_n_in_row")
DIFFICULTIES = ("gentle", "normal", "spicy")


class TemplateError(ValueError):
    """A template is malformed. The message names the offending entry."""


@dataclass
class TemplateItem:
    key: str
    text: str
    points: int = 10
    time_limit_s: int = 300

    def to_task(self, player_name: Optional[str] = None, index: int = 0) -> Dict[str, Any]:
        return {
            "player_name": player_name,
            "text": self.text,
            "item_key": self.key,
            "points": self.points,
            "time_limit_s": self.time_limit_s,
            "task_id": f"t{index}",
        }

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "text": self.text, "points": self.points,
                "time_limit_s": self.time_limit_s}


def slugify(text: str, fallback: str) -> str:
    """A short, stable, lowercase item key."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if not cleaned:
        return fallback
    return cleaned[:32]


@dataclass
class GameTemplate:
    type: str
    title: str
    game_types: str = "generic"
    approval: str = "peer_vote"
    win_condition: str = "most_points"
    n: int = 5
    min_players: int = 1
    max_players: int = 8
    duration_options: List[int] = field(default_factory=lambda: [15, 30, 45])
    default_duration: int = 30
    difficulty: str = "normal"
    theme: str = ""
    penalty: str = "loser buys a treat"
    requires_players: int = 1
    items: List[TemplateItem] = field(default_factory=list)
    source: str = ""

    # --- conveniences used by the host and the UI ----------------------
    def duration_choices(self) -> List[int]:
        choices = sorted({int(value) for value in self.duration_options if int(value) > 0})
        return choices or [self.default_duration]

    def players_ok(self, count: int) -> bool:
        return self.min_players <= count <= self.max_players and count >= self.requires_players

    def players_error(self, count: int) -> str:
        """Why this player count is not allowed, in words, for the UI."""
        if count < self.requires_players:
            return f"{self.title} needs at least {self.requires_players} players."
        if count < self.min_players:
            return f"{self.title} needs at least {self.min_players} players."
        if count > self.max_players:
            return f"{self.title} supports at most {self.max_players} players."
        return ""

    def build_tasks(self, players: Sequence[Dict[str, Any]], duration_s: Optional[int] = None) -> List[Dict[str, Any]]:
        """Assign the template's items to players, round-robin.

        Bingo assigns every tile to every player (the grid is shared), so
        ``player_name`` is ``None`` there. Everything else is per-player, because
        the brief wants each member to get a different silly task.
        """
        names = [str(player.get("name") or player.get("display_name") or f"Player {i + 1}")
                 for i, player in enumerate(players)] or ["Explorer"]
        tasks: List[Dict[str, Any]] = []
        per_player = self.game_types != "bingo" and len(names) > 1

        for index, item in enumerate(self.items):
            task = item.to_task(
                player_name=names[index % len(names)] if per_player else None,
                index=index,
            )
            if duration_s:
                task["time_limit_s"] = min(int(duration_s), task["time_limit_s"])
            tasks.append(task)
        return tasks

    def to_params(self, duration_s: int, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "approval": self.approval,
            "win_condition": self.win_condition,
            "penalty": self.penalty,
            "template": self.type,
            "difficulty": self.difficulty,
            "theme": self.theme,
            "duration_s": int(duration_s),
        }
        if self.win_condition == "first_n_in_row":
            params["n"] = self.n
        if extra:
            params.update(extra)
        return params

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "title": self.title,
            "game_types": self.game_types,
            "approval": self.approval,
            "win_condition": self.win_condition,
            "n": self.n,
            "players": {"min": self.min_players, "max": self.max_players},
            "duration_options": self.duration_choices(),
            "default_duration": self.default_duration,
            "difficulty": self.difficulty,
            "theme": self.theme,
            "penalty": self.penalty,
            "requires_players": self.requires_players,
            "item_count": len(self.items),
            "items": [item.to_dict() for item in self.items],
        }


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def parse_item(raw: Any, index: int, template_type: str) -> TemplateItem:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise TemplateError(f"{template_type}: item {index} is empty")
        return TemplateItem(key=slugify(text, f"item{index}")[:32], text=text)
    if isinstance(raw, dict):
        text = str(raw.get("text") or raw.get("label") or "").strip()
        if not text:
            raise TemplateError(f"{template_type}: item {index} has no 'text'")
        key = str(raw.get("key") or raw.get("item_key") or slugify(text, f"item{index}"))
        return TemplateItem(
            key=key[:32],
            text=text,
            points=int(raw.get("points", 10)),
            time_limit_s=int(raw.get("time_limit_s", 300)),
        )
    raise TemplateError(f"{template_type}: item {index} must be a string or a mapping")


def parse_template(raw: Dict[str, Any], source: str = "") -> GameTemplate:
    if not isinstance(raw, dict):
        raise TemplateError(f"{source}: each template must be a mapping")

    template_type = str(raw.get("type") or "").strip()
    if not template_type:
        raise TemplateError(f"{source}: a template is missing 'type'")
    if template_type not in KNOWN_TYPES:
        raise TemplateError(f"{source}: unknown game type {template_type!r}")

    approval = str(raw.get("approval", "peer_vote")).strip()
    if approval not in KNOWN_APPROVALS:
        raise TemplateError(f"{template_type}: approval must be one of {KNOWN_APPROVALS}")

    win_condition = str(raw.get("win_condition", "most_points")).strip()
    if win_condition not in KNOWN_WIN_CONDITIONS:
        raise TemplateError(f"{template_type}: win_condition must be one of {KNOWN_WIN_CONDITIONS}")

    difficulty = str(raw.get("difficulty", "normal")).strip()
    if difficulty not in DIFFICULTIES:
        raise TemplateError(f"{template_type}: difficulty must be one of {DIFFICULTIES}")

    players = raw.get("players") or {}
    if not isinstance(players, dict):
        raise TemplateError(f"{template_type}: 'players' must be a mapping with min/max")

    raw_items = raw.get("items") or []
    if not isinstance(raw_items, list):
        raise TemplateError(f"{template_type}: 'items' must be a list")

    template = GameTemplate(
        type=template_type,
        title=str(raw.get("title") or template_type.replace("_", " ").title()),
        game_types=str(raw.get("game_types") or template_type),
        approval=approval,
        win_condition=win_condition,
        n=int(raw.get("n", 5) or 5),
        min_players=int(players.get("min", 1)),
        max_players=int(players.get("max", 8)),
        duration_options=[int(value) for value in (raw.get("duration_options") or [15, 30, 45])],
        default_duration=int(raw.get("default_duration", 30)),
        difficulty=difficulty,
        theme=str(raw.get("theme", "")),
        penalty=str(raw.get("penalty", "loser buys a treat")),
        requires_players=int(raw.get("requires_players", 1)),
        items=[parse_item(item, index, template_type) for index, item in enumerate(raw_items)],
        source=source,
    )

    if not template.items:
        raise TemplateError(f"{template_type}: a game with no items cannot be generated")
    if template.game_types == "bingo" and len(template.items) < 25:
        logger.warning(
            "%s is a bingo template with only %d tiles; the grid will be padded",
            template_type, len(template.items),
        )
    if template.min_players > template.max_players:
        raise TemplateError(f"{template_type}: min_players is greater than max_players")
    if not template.duration_choices():
        raise TemplateError(f"{template_type}: no usable durations")
    if template.default_duration not in template.duration_choices():
        template.default_duration = template.duration_choices()[0]
    return template


def load_templates(path: Optional[Path | str] = None) -> List[GameTemplate]:
    """Load every template from a YAML file (default: the bundled one)."""
    import yaml

    file_path = Path(path) if path else DEFAULT_TEMPLATE_FILE
    if not file_path.is_file():
        logger.warning("no game template file at %s; only built-in content is available", file_path)
        return []
    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise TemplateError(f"{file_path}: could not parse YAML ({exc})") from exc

    if isinstance(raw, dict) and "templates" in raw:
        raw = raw["templates"]
    if not isinstance(raw, list):
        raise TemplateError(f"{file_path}: the file must contain a list of templates")

    templates: List[GameTemplate] = []
    for index, entry in enumerate(raw):
        templates.append(parse_template(entry, source=f"{file_path.name}[{index}]"))
    logger.info("loaded %d game templates from %s", len(templates), file_path)
    return templates


_cache: Optional[List[GameTemplate]] = None


def cached_templates(reload: bool = False, path: Optional[Path | str] = None) -> List[GameTemplate]:
    global _cache
    if _cache is None or reload:
        try:
            _cache = load_templates(path)
        except TemplateError as exc:
            logger.error("game templates failed to load: %s", exc)
            _cache = []
    return _cache


def available_templates(reload: bool = False) -> List[GameTemplate]:
    return cached_templates(reload=reload)


def load_template(game_type: str, reload: bool = False) -> Optional[GameTemplate]:
    for template in cached_templates(reload=reload):
        if template.type == game_type:
            return template
    return None


def template_summaries() -> List[Dict[str, Any]]:
    """What the configure screen renders."""
    return [template.to_dict() for template in available_templates()]
