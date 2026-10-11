"""The game host: the handheld runs the game, the server is the filing cabinet.

This is brief 02 Part B in code. The whole idea in one line (§B1): *"The mobile app
runs the game. When the user starts a game on the mobile app, the mobile app asks
the server for a game session ... draws the QR code on its own screen, and other
people scan that QR from the mobile app to open the upload page on their phones."*

States (§B2): ``IDLE`` → ``CONFIGURING`` → ``GENERATING`` → ``CREATING_SESSION``
→ ``LOBBY`` → ``RUNNING`` → ``ENDING`` → ``RESULTS`` → ``CLOSED``, plus
``OFFLINE_FALLBACK``.

What this class owns:

* the state machine and the timer (the handheld keeps the clock while running,
  even with no server);
* the QR / join address, including the localhost rewrite (§B3);
* the lobby (who has joined, live);
* the live ticker and scoreboard (from the WebSocket, with REST polling fallback);
* ending the game from **any** of the four triggers (§B2 step 6): timer, host
  press, win condition, or the server telling us;
* the offline fallback (§B4): Local game mode with no QR, results queued in the
  outbox, and a re-sync when the connection returns.

Threading: every method is safe to call from the UI thread. Network calls are
pushed to a worker (:mod:`asyncio.to_thread` via :meth:`_call`), so a dead server
cannot freeze the screen.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..core.bus import Bus
from ..core.clock import format_duration, seconds_until
from ..core.config import Settings
from ..core.db import Storage
from ..core.models import (
    CreateGameRequest,
    CreateGameResponse,
    GameHostState,
    GamePlayerSpec,
    GameStateResponse,
    GameTaskSpec,
    iso_z,
    utcnow,
)
from ..net.client import DeviceClient, ServerError, ServerUnreachable
from ..net.diagnostics import Diagnostics, current_ssid, describe_for_screen, run_diagnostics
from ..net.pairing import NotPaired
from ..net.qr import JoinTarget, lobby_instructions, qr_png_bytes, resolve_join_url
from .base import GameEvent, GameModule, PhotoSubmission
from .photo_hunt import BingoGame, PhotoHuntGame
from .template_loader import GameTemplate, load_template

logger = logging.getLogger("ffh.games.host")

#: Which module handles which template ``game_types`` value.
MODULE_REGISTRY: Dict[str, type] = {
    "photo_hunt": PhotoHuntGame,
    "bingo": BingoGame,
    # These reuse the photo-hunt scoring: the difference is the content and the
    # approval mode, both carried by the template.
    "color_hunt": PhotoHuntGame,
    "funny_tasks": PhotoHuntGame,
    "secret_missions": PhotoHuntGame,
    "imposter": PhotoHuntGame,
    "generic": PhotoHuntGame,
}


@dataclass
class GameHostConfig:
    """What the configure screen collects (brief 02 §B2 step 1)."""

    template_type: str = "photo_hunt"
    players: List[Dict[str, Any]] = field(default_factory=list)
    duration_min: int = 30
    difficulty: str = ""
    theme: str = ""
    approval: str = ""
    penalty: str = ""
    trip_id: Optional[str] = None
    extra_params: Dict[str, Any] = field(default_factory=dict)

    def duration_s(self) -> int:
        return max(60, int(self.duration_min) * 60)

    def player_names(self) -> List[str]:
        return [
            str(player.get("name") or player.get("display_name") or f"Player {index + 1}")
            for index, player in enumerate(self.players)
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "template_type": self.template_type,
            "players": self.players,
            "duration_min": self.duration_min,
            "difficulty": self.difficulty,
            "theme": self.theme,
            "approval": self.approval,
            "penalty": self.penalty,
            "trip_id": self.trip_id,
        }


@dataclass
class HostObservers:
    """Callbacks the UI attaches to. All optional; all called on the calling thread."""

    on_state: Optional[Callable[[GameHostState, Dict[str, Any]], None]] = None
    on_qr: Optional[Callable[[JoinTarget], None]] = None
    on_lobby: Optional[Callable[[List[Dict[str, Any]]], None]] = None
    on_score: Optional[Callable[[List[Dict[str, Any]]], None]] = None
    on_ticker: Optional[Callable[[GameEvent], None]] = None
    on_results: Optional[Callable[[Dict[str, Any]], None]] = None
    on_error: Optional[Callable[[str, Dict[str, Any]], None]] = None

    def state(self, state: GameHostState, detail: Optional[Dict[str, Any]] = None) -> None:
        if self.on_state:
            self.on_state(state, detail or {})

    def error(self, message: str, detail: Optional[Dict[str, Any]] = None) -> None:
        if self.on_error:
            self.on_error(message, detail or {})


class GameHost:
    """Runs one game session on the handheld."""

    def __init__(
        self,
        settings: Settings,
        client: Optional[DeviceClient],
        storage: Optional[Storage] = None,
        bus: Optional[Bus] = None,
        observers: Optional[HostObservers] = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.storage = storage
        self.bus = bus
        self.observers = observers or HostObservers()

        self.state = GameHostState.IDLE
        self.config = GameHostConfig()
        self.template: Optional[GameTemplate] = None
        self.module: Optional[GameModule] = None
        self.tasks: List[Dict[str, Any]] = []
        self.session: Optional[CreateGameResponse] = None
        self.local_game_id = ""
        self.offline_game_id = ""
        self.join: Optional[JoinTarget] = None
        self.wifi_ssid: str = ""
        self.diagnostics: Optional[Diagnostics] = None
        self.offline = False

        self.players: List[Dict[str, Any]] = []
        self.ticker: List[GameEvent] = []
        self.submissions: List[PhotoSubmission] = []
        self.started_at_monotonic: Optional[float] = None
        self.ends_at_iso: Optional[str] = None
        self.ended_at_iso: Optional[str] = None
        self.winner: Optional[str] = None
        self.penalty: str = ""
        self.last_error: str = ""
        self.poll_task: Optional[asyncio.Task[Any]] = None
        self._stop = False
        self._ws_events_seen = 0
        self._last_state_poll = 0.0

    # ================================================================== #
    # 1. Configure and generate
    # ================================================================== #
    def configure(
        self,
        template_type: str,
        players: Sequence[Dict[str, Any]],
        duration_min: Optional[int] = None,
        trip_id: Optional[str] = None,
        **overrides: Any,
    ) -> Tuple[bool, str]:
        """``IDLE``/``CONFIGURING``: pick a template and its parameters."""
        self._set_state(GameHostState.CONFIGURING)
        template = load_template(template_type, reload=True)
        if template is None:
            message = f"There is no game template called {template_type!r}."
            self.last_error = message
            self.observers.error(message, {"available": [t.type for t in _available()]})
            return False, message

        roster = [dict(player) for player in players]
        if not template.players_ok(len(roster)):
            message = template.players_error(len(roster))
            self.last_error = message
            self.observers.error(message, {"min": template.min_players, "max": template.max_players})
            return False, message

        self.template = template
        self.config = GameHostConfig(
            template_type=template_type,
            players=roster,
            duration_min=int(duration_min or template.default_duration),
            difficulty=str(overrides.get("difficulty") or template.difficulty),
            theme=str(overrides.get("theme") or template.theme),
            approval=str(overrides.get("approval") or template.approval),
            penalty=str(overrides.get("penalty") or template.penalty),
            trip_id=trip_id,
            extra_params=dict(overrides.get("extra_params") or {}),
        )
        self.players = [
            {
                "name": name,
                "color": roster[index].get("color", "#1E7F85"),
                "team": roster[index].get("team"),
                "joined": False,
                "score": 0,
                "submissions": 0,
            }
            for index, name in enumerate(self.config.player_names())
        ]
        self.penalty = self.config.penalty
        logger.info(
            "game configured: %s, %d players, %d min",
            template.title, len(self.players), self.config.duration_min,
        )
        return True, ""

    async def generate(self, router: Any = None, offline: bool = False) -> List[Dict[str, Any]]:
        """``GENERATING``: fill the tasks. LLM first, template's built-in items second.

        Brief 02 §B2 step 2: *"The local LLM fills the tasks and items as JSON that
        is validated against a schema. Retry once on invalid output. If generation
        fails, use the template's built-in default items."*
        """
        self._set_state(GameHostState.GENERATING)
        if self.template is None:
            return []

        duration_s = self.config.duration_s()
        tasks = self.template.build_tasks(self.config.players, duration_s)
        source = "template"

        if router is not None and not offline:
            generated = await self._generate_with_llm(router, duration_s)
            if generated:
                tasks = generated
                source = "llm"

        self.tasks = tasks
        self.module = self._build_module(duration_s)
        logger.info("game tasks ready (%d items, source=%s)", len(tasks), source)
        if self.bus:
            self.bus.publish(
                "game.phase",
                {"phase": "generated", "source": source, "tasks": len(tasks)},
                source="game_host",
            )
        return tasks

    async def _generate_with_llm(self, router: Any, duration_s: int) -> List[Dict[str, Any]]:
        """One LLM call, schema-validated. Returns ``[]`` on any failure."""
        from ..agent.schemas import GameDraft, game_schema
        from ..agent.llm_router import ask_llm

        assert self.template is not None
        players = ", ".join(self.config.player_names())
        template_items = "\n".join(f"- {item.text} ({item.points} pts)" for item in self.template.items[:12])
        prompt = (
            f"Write the items for a {self.template.title} game.\n"
            f"- theme: {self.config.theme or self.template.theme}\n"
            f"- difficulty: {self.config.difficulty or self.template.difficulty}\n"
            f"- players: {players}\n"
            f"- it lasts {duration_s // 60} minutes\n"
            f"- the game uses the '{self.template.game_types}' format\n"
            f"- win condition: {self.template.win_condition}\n"
            + (f"- they will have {duration_s // 60} minutes\n")
            + "\nHere are the default items; write something better in the same spirit,\n"
            "or repeat them if they are already good:\n"
            f"{template_items}\n\n"
            "Rules: never name a place, a street or a business. Each task is one short\n"
            "sentence a person can act on immediately. Give every task an item_key: one\n"
            "lowercase word. Answer with JSON only."
        )
        result = await ask_llm(
            router,
            task="tasks",
            prompt=prompt,
            schema=game_schema(),
            model_cls=GameDraft,
            temperature=0.9,
            max_tokens=1200,
        )
        if result is None:
            return []
        try:
            draft = GameDraft.model_validate_json(result.text)
        except Exception as exc:
            logger.info("game generation draft was invalid (%s); using template items", exc)
            return []
        if not draft.tasks:
            return []

        duration_limit = min(duration_s, 3600)
        tasks: List[Dict[str, Any]] = []
        for index, task in enumerate(draft.tasks):
            tasks.append(
                {
                    "player_name": task.player_name if task.player_name in self.config.player_names() else None,
                    "text": task.text,
                    "item_key": task.item_key or f"item{index}",
                    "points": task.points,
                    "time_limit_s": min(duration_limit, task.time_limit_s),
                    "task_id": f"t{index}",
                }
            )
        # Bingo needs 25 tiles for a 5x5 grid; pad from the template if the model wrote fewer.
        if self.template.game_types == "bingo" and len(tasks) < 25:
            padding = self.template.build_tasks(self.config.players, duration_s)
            for extra in padding:
                if len(tasks) >= 25:
                    break
                extra["task_id"] = f"t{len(tasks)}"
                tasks.append(extra)
        return tasks

    def _build_module(self, duration_s: int) -> GameModule:
        assert self.template is not None
        module_cls = MODULE_REGISTRY.get(self.template.game_types, PhotoHuntGame)
        params = self.template.to_params(
            duration_s,
            {
                "approval": self.config.approval or self.template.approval,
                "penalty": self.config.penalty or self.template.penalty,
                "difficulty": self.config.difficulty or self.template.difficulty,
                "theme": self.config.theme or self.template.theme,
                **self.config.extra_params,
            },
        )
        module = module_cls(params, self.config.players, getattr(self, "tasks", []))
        return module

    # ================================================================== #
    # 2. Create the session on the server
    # ================================================================== #
    async def create_session(self) -> Tuple[bool, str]:
        """``CREATING_SESSION``: ask the server for a session and get the join URL.

        A direct call, not queued, because it needs an answer now (brief 02 §B5).
        On failure we do **not** give up: the caller offers Local game mode, and the
        reason comes from :meth:`diagnose`.
        """
        self._set_state(GameHostState.CREATING_SESSION)
        if self.template is None or self.module is None:
            return False, "the game has not been configured yet"

        self.wifi_ssid = current_ssid() or ""
        duration_s = self.config.duration_s()
        if not self.local_game_id:
            self.local_game_id = str(uuid.uuid4())

        request = CreateGameRequest(
            # Our own id: the server honours it now, so an outbox retry of the create
            # cannot mint a second session with a second join code
            # (docs/SERVER_CONTRACT.md G-01).
            game_id=self.local_game_id,
            trip_id=self.config.trip_id,
            type=self.template.game_types,
            duration_s=duration_s,
            params=self.template.to_params(
                duration_s,
                {
                    "approval": self.config.approval,
                    "penalty": self.config.penalty,
                    "difficulty": self.config.difficulty,
                    "theme": self.config.theme,
                    **self.config.extra_params,
                },
            ),
            players=[
                GamePlayerSpec(
                    name=player["name"],
                    color=player.get("color", "#1E7F85"),
                    team=player.get("team"),
                )
                for player in self.players
            ],
            tasks=[
                GameTaskSpec(
                    player_name=task.get("player_name"),
                    text=task["text"],
                    item_key=task.get("item_key"),
                    points=int(task.get("points", 10)),
                    time_limit_s=int(task.get("time_limit_s", 300)),
                )
                for task in getattr(self, "tasks", [])
            ],
        )

        if self.client is None or not self.client.paired:
            return self._fail_session("This handheld is not paired with the server yet.")

        try:
            response = await asyncio.to_thread(self.client.create_game, request)
        except NotPaired:
            return self._fail_session("The server rejected this handheld's token. Pair it again.")
        except ServerError as exc:
            return self._fail_session(f"The server refused the game: {exc.message or exc.status}")
        except ServerUnreachable as exc:
            return self._fail_session(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            return self._fail_session(f"Could not create the game: {exc}")

        self.session = response
        self.offline = False
        self.local_game_id = f"local-{int(time.time())}"
        self.ends_at_iso = response.ends_at

        # --- the QR (brief 02 §B3) -------------------------------------
        network_base = None
        info = None
        try:
            info = await asyncio.to_thread(self.client.network_info)
        except Exception:
            info = None
        if info is not None:
            network_base = info.base_url
        self.join = resolve_join_url(
            response.join_url,
            response.code,
            self.settings.server_url,
            network_base_url=network_base,
        )
        if self.join.rewrote_localhost:
            self.observers.error(
                "The server sent a localhost link; rebuilt it from this handheld's address.",
                {"url": self.join.url},
            )
        if self.observers.on_qr:
            self.observers.on_qr(self.join)

        self._set_state(GameHostState.LOBBY)
        # Persist after the state change, so a restart resumes in the lobby.
        self._persist_session()
        logger.info("game session %s in lobby with code %s", response.game_id, response.code)
        return True, ""

    def _fail_session(self, message: str) -> Tuple[bool, str]:
        self.offline = True
        self.last_error = message
        self.offline_game_id = f"offline-{int(time.time())}"
        logger.warning("game session creation failed: %s", message)
        self._set_state(GameHostState.OFFLINE_FALLBACK, {"reason": message})
        self.observers.error(message, {"offline_available": True})
        return False, message

    async def enable_offline_mode(self) -> None:
        """§B4: *"Offer Local game mode: the game runs on the mobile app alone."*

        No QR, no uploads from phones; the handheld shows the tasks and takes its
        own photos. Results stay in the outbox and re-sync later.
        """
        self.offline = True
        self.local_game_id = getattr(self, "offline_game_id", f"offline-{int(time.time())}")
        for player in self.players:
            player["joined"] = True  # everyone is on this device
        self._persist_session()
        self._set_state(GameHostState.OFFLINE_FALLBACK, {"reason": self.last_error})
        if self.bus:
            self.bus.publish(
                "game.phase",
                {"phase": "offline", "text": "Local game mode. No phones needed."},
                source="game_host",
            )

    # ================================================================== #
    # 3. Lobby
    # ================================================================== #
    def joined_count(self) -> int:
        return sum(1 for player in self.players if player.get("joined"))

    def lobby_payload(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "code": self.session.code if self.session else "",
            "join_url": self.join.url if self.join else "",
            "address": self.join.address if self.join else "",
            "qr_png_url": self.session.qr_png_url if self.session else "",
            "wifi_ssid": self.wifi_ssid,
            "offline": self.offline,
            "players": self.players,
            "joined_count": self.joined_count(),
            "instructions": lobby_instructions(self.join, self.wifi_ssid) if self.join else {},
            "template": self.template.title if self.template else "",
            "duration_min": self.config.duration_min,
        }

    def qr_png(self, target_px: int = 560) -> Optional[bytes]:
        """The QR image for the lobby, drawn locally (never an online service)."""
        if not self.join or self.offline:
            return None
        try:
            return qr_png_bytes(self.join.url, target_px=target_px)
        except Exception as exc:
            logger.error("could not render the QR code: %s", exc)
            self.observers.error(f"Could not draw the QR code: {exc}")
            return None

    async def start(self) -> Tuple[bool, str]:
        """``LOBBY`` → ``RUNNING``. Tells the server, then starts the local timer."""
        if self.offline:
            return self._begin_running(server_started=False)
        if self.session is None:
            return False, "there is no server session to start"
        if self.client is None:
            return False, "this handheld has no server client"

        try:
            payload = await asyncio.to_thread(self.client.start_game, self.session.game_id)
            self.ends_at_iso = payload.get("ends_at") or self.ends_at_iso
        except NotPaired:
            return self._fail_session("The server rejected this handheld's token.")
        except (ServerError, ServerUnreachable) as exc:
            # The brief is explicit: keep the game running locally and re-sync later.
            logger.warning("could not tell the server the game started (%s); running locally", exc)
            self.observers.error(
                "The server did not confirm the start. The game is running; scores will sync later.",
                {"detail": str(exc)},
            )
            self.offline = True
            return self._begin_running(server_started=False)

        return self._begin_running(server_started=True)

    def _begin_running(self, server_started: bool) -> Tuple[bool, str]:
        self.started_at_monotonic = time.monotonic()
        self._stop = False
        if self.module is not None:
            for event in self.module.on_start():
                self._add_event(event)
        if self.bus:
            self.bus.publish(
                "game.started",
                {
                    "game_id": self.session.game_id if self.session else self.local_game_id,
                    "type": self.template.game_types if self.template else "",
                    "ends_at": self.ends_at_iso,
                    "server_confirmed": server_started,
                    "offline": self.offline,
                },
                source="game_host",
            )
        self._persist_session()
        self._set_state(GameHostState.RUNNING, {"ends_at": self.ends_at_iso})
        return True, ""

    # ================================================================== #
    # 4. Running
    # ================================================================== #
    def seconds_remaining(self) -> Optional[float]:
        """From the server's ``ends_at`` when we have it, else from our own clock."""
        if self.ends_at_iso:
            remaining = seconds_until(self.ends_at_iso)
            if remaining is not None:
                return remaining
        if self.started_at_monotonic is None:
            return None
        elapsed = time.monotonic() - self.started_at_monotonic
        return max(0.0, self.config.duration_s() - elapsed)

    def countdown_label(self) -> str:
        remaining = self.seconds_remaining()
        return "--:--" if remaining is None else format_duration(remaining)

    def running_payload(self) -> Dict[str, Any]:
        remaining = self.seconds_remaining()
        board = self.module.score() if self.module else {"players": [], "pending_votes": 0}
        return {
            "state": self.state.value,
            "countdown": self.countdown_label(),
            "seconds_remaining": None if remaining is None else round(remaining, 1),
            "ends_at": self.ends_at_iso,
            "scoreboard": board.get("players", []),
            "pending_votes": board.get("pending_votes", 0),
            "pending_uploads": len(self.submissions),
            "ticker": [event.to_payload() for event in self.ticker[-8:]],
            "offline": self.offline,
            "players": self.players,
            "show_qr": bool(self.join and not self.offline),
        }

    def tick(self) -> bool:
        """Called by the UI timer. Returns True when the game just ended.

        Checks the four end conditions (brief 02 §B2 step 6), in the order they are
        cheapest to test: host action (handled elsewhere), win condition, timer,
        server message (handled by the WebSocket).
        """
        if self.state != GameHostState.RUNNING:
            return False
        if self.module and self.module.win_condition_met():
            logger.info("game ended by win condition")
            asyncio.ensure_future(self.end(reason="win_condition"))
            return True
        remaining = self.seconds_remaining()
        if remaining is not None and remaining <= 0:
            logger.info("game ended by timer")
            asyncio.ensure_future(self.end(reason="timer"))
            return True
        return False

    # ================================================================== #
    # 5. Events from the phones / the server
    # ================================================================== #
    def handle_server_event(self, event: str, data: Dict[str, Any]) -> None:
        """One WebSocket event (brief 03 §8 table)."""
        self._ws_events_seen += 1
        if event == "game.player_joined":
            player = data.get("player") or {}
            name = str(player.get("display_name") or player.get("name") or "")
            self._mark_joined(name)
            self._add_event(
                GameEvent("player_joined", player=name, message=f"{name} joined",
                          data={"joined_count": data.get("joined_count", self.joined_count())})
            )
            self._notify_lobby()

        elif event == "game.submission":
            submission = PhotoSubmission(
                photo_id=str(data.get("photo_id", "")),
                player_id=str(data.get("player_id", "")),
                player_name=str(data.get("player_name", "")),
                task_id=str(data.get("task_id", "")),
                thumb_url=str(data.get("thumb_url", "")),
                status=str(data.get("status", "pending")),
            )
            self._mark_joined(submission.player_name)
            self._record_submission(submission)
            self._add_event(
                GameEvent("submission", player=submission.player_name,
                          photo_id=submission.photo_id, task_id=submission.task_id,
                          thumb_url=submission.thumb_url,
                          message=f"{submission.player_name} uploaded a photo")
            )

        elif event in ("game.submission_status", "game.vote"):
            self._apply_submission_status(data)
            self._add_event(
                GameEvent("vote", photo_id=str(data.get("photo_id", "")),
                          message=f"a photo is now {data.get('status', 'pending')}")
            )

        elif event == "game.score":
            player = str(data.get("player", ""))
            points = int(data.get("points", 0) or 0)
            total = data.get("total")
            self._apply_score(player, points, total)

        elif event == "game.ended":
            logger.info("server ended the game: %s", data)
            asyncio.ensure_future(self._finish_from_server(data))

        elif event == "game.started":
            self.ends_at_iso = data.get("ends_at") or self.ends_at_iso
            if self.state == GameHostState.LOBBY:
                self._begin_running(server_started=True)

        self._notify_scoreboard()

    def _mark_joined(self, name: str) -> None:
        if not name:
            return
        for player in self.players:
            if player["name"].lower() == name.lower():
                player["joined"] = True
                return
        # A name we did not know about (someone typed their own): add them.
        self.players.append(
            {"name": name, "color": "#1E7F85", "team": None, "joined": True, "score": 0, "submissions": 0}
        )

    def _record_submission(self, submission: PhotoSubmission) -> None:
        self.submissions.append(submission)
        for player in self.players:
            if player["name"].lower() == submission.player_name.lower():
                player["submissions"] = int(player.get("submissions", 0)) + 1
                break
        if self.module is not None:
            for event in self.module.on_photo(submission):
                self._add_event(event)
        if self.bus:
            self.bus.publish("photo.captured", submission.to_payload(), source="game_host")

    def _apply_submission_status(self, data: Dict[str, Any]) -> None:
        photo_id = str(data.get("photo_id", ""))
        status = str(data.get("status", ""))
        for submission in self.submissions:
            if submission.photo_id == photo_id:
                submission.status = status or submission.status
                break
        if self.module is not None:
            for submission in self.module.scoreboard.submissions.values():
                if submission.photo_id == photo_id:
                    submission.status = status or submission.status

    def _apply_score(self, player_name: str, points: int, total: Optional[Any] = None) -> None:
        for player in self.players:
            if player["name"].lower() == player_name.lower():
                player["score"] = int(total) if total is not None else int(player.get("score", 0)) + points
                return

    def _add_event(self, event: GameEvent) -> None:
        self.ticker.append(event)
        del self.ticker[:-60]
        if self.observers.on_ticker:
            self.observers.on_ticker(event)
        if self.bus:
            self.bus.publish("game.event", event.to_payload(), source="game_host")

    def _notify_lobby(self) -> None:
        if self.observers.on_lobby:
            self.observers.on_lobby(self.players)

    def _notify_scoreboard(self) -> None:
        if self.observers.on_score:
            board = self.module.score().get("players", []) if self.module else self.players
            self.observers.on_score(board)

    # ------------------------------------------------------------------ #
    # polling fallback (brief 03 §7.3)
    # ------------------------------------------------------------------ #
    async def poll_state_once(self) -> Optional[GameStateResponse]:
        if self.offline or self.session is None or self.client is None:
            return None
        try:
            state = await asyncio.to_thread(self.client.game_state, self.session.game_id)
        except NotPaired:
            return None
        except (ServerError, ServerUnreachable) as exc:
            logger.debug("game state poll failed: %s", exc)
            return None
        self.apply_state(state)
        return state

    def apply_state(self, state: GameStateResponse) -> None:
        """Merge a REST state snapshot, keeping local scores when it has none."""
        self._last_state_poll = time.time()
        if state.ends_at:
            self.ends_at_iso = state.ends_at
        board = state.normalised()
        if board:
            for entry in board:
                for player in self.players:
                    if player["name"].lower() == str(entry.get("name", "")).lower():
                        player["score"] = max(int(player.get("score", 0)), int(entry.get("score", 0)))
                        if entry.get("joined"):
                            player["joined"] = True
                        player["submissions"] = max(
                            int(player.get("submissions", 0)), int(entry.get("submissions", 0))
                        )
        if state.status == "closed" and self.state == GameHostState.RUNNING:
            asyncio.ensure_future(self._finish_from_server({"status": "closed"}))
        self._notify_scoreboard()

    async def run_polling_loop(self, interval_s: float = 3.0) -> None:
        """Used when the WebSocket is unavailable."""
        while not self._stop:
            await asyncio.sleep(interval_s)
            if self.state not in (GameHostState.RUNNING, GameHostState.LOBBY):
                continue
            await self.poll_state_once()

    def start_polling(self, interval_s: float = 3.0) -> None:
        if self.poll_task is None or self.poll_task.done():
            self.poll_task = asyncio.ensure_future(self.run_polling_loop(interval_s))

    # ================================================================== #
    # 6. Ending
    # ================================================================== #
    async def end(self, reason: str = "host", winner: Optional[str] = None) -> Dict[str, Any]:
        """End from any trigger. Idempotent: calling it twice is harmless.

        ``winner`` lets the server's verdict win when it tells us the game is over;
        otherwise the local scoreboard decides.
        """
        if self.state in (GameHostState.CLOSED, GameHostState.RESULTS):
            return self.results_payload()
        self._set_state(GameHostState.ENDING, {"reason": reason})
        self._stop = True

        if self.module is not None:
            for event in self.module.on_end():
                self._add_event(event)

        self.ended_at_iso = iso_z(utcnow())
        if winner:
            self.winner = winner
        else:
            local = self.module.scoreboard.winner() if self.module else None
            self.winner = local.label if local else self.winner

        if not self.offline and self.session is not None and self.client is not None:
            try:
                await asyncio.to_thread(self.client.end_game, self.session.game_id)
            except NotPaired:
                self.observers.error("The server rejected this handheld's token while ending the game.")
            except (ServerError, ServerUnreachable) as exc:
                logger.warning("could not tell the server the game ended (%s); queuing is not possible", exc)
                self.observers.error("The server did not confirm the end. Results are kept locally.")

        self._persist_session()
        self._set_state(GameHostState.RESULTS)
        payload = self.results_payload()
        if self.observers.on_results:
            self.observers.on_results(payload)
        if self.bus:
            self.bus.publish("game.ended", payload, source="game_host")
        return payload

    async def _finish_from_server(self, data: Dict[str, Any]) -> None:
        """The server ended it (timer or win condition) — handle it like our own end."""
        if self.state in (GameHostState.CLOSED, GameHostState.RESULTS):
            return
        if "scores" in data and isinstance(data["scores"], list):
            for entry in data["scores"]:
                if isinstance(entry, dict):
                    self._apply_score(str(entry.get("player", "")), 0, entry.get("total"))
        winner = str(data.get("winner") or data.get("winner_player_id") or "") or None
        await self.end(reason="server", winner=winner)

    def results_payload(self) -> Dict[str, Any]:
        board = self.module.score().get("players", []) if self.module else []
        return {
            "state": self.state.value,
            "winner": self.winner,
            "scores": board,
            "penalty": self.penalty or "loser buys a treat",
            "offline": self.offline,
            "submissions": len(self.submissions),
            "started_at": None
            if self.started_at_monotonic is None
            else iso_z(utcnow()),
            "ended_at": self.ended_at_iso,
            "template": self.template.title if self.template else "",
            "photos_are_on_the_server": not self.offline and bool(self.session),
            "join_url": self.join.url if self.join else "",
        }

    def close(self) -> None:
        """``RESULTS`` → ``CLOSED``: back to the trip screen, QR no longer valid."""
        self._stop = True
        if self.poll_task and not self.poll_task.done():
            self.poll_task.cancel()
        self.poll_task = None
        self._set_state(GameHostState.CLOSED)
        logger.info("game session closed")

    # ================================================================== #
    # 7. Diagnostics and persistence
    # ================================================================== #
    def diagnose(self) -> Diagnostics:
        """Why the session could not be created, and what to do about it (brief 02 §B4)."""
        self.diagnostics = run_diagnostics(self.settings.server_url, client=self.client)
        return self.diagnostics

    def diagnostic_summary(self) -> Dict[str, Any]:
        if self.diagnostics is None:
            self.diagnose()
        assert self.diagnostics is not None
        return describe_for_screen(self.diagnostics)

    def _persist_session(self) -> None:
        if self.storage is None:
            return
        try:
            self.storage.save_game_session(
                {
                    "local_game_id": self.local_game_id or f"local-{int(time.time())}",
                    "server_game_id": self.session.game_id if self.session else None,
                    "trip_id": self.config.trip_id,
                    "game_type": self.template.game_types if self.template else "photo_hunt",
                    "template": self.template.type if self.template else None,
                    "state": self.state.value,
                    "code": self.session.code if self.session else None,
                    "join_url": self.join.url if self.join else None,
                    "params": {"duration_s": self.config.duration_s(), "penalty": self.penalty},
                    "players": self.players,
                    "tasks": getattr(self, "tasks", []),
                    "duration_s": self.config.duration_s(),
                    "started_at": iso_z(utcnow()) if self.started_at_monotonic else None,
                    "ends_at": self.ends_at_iso,
                    "ended_at": self.ended_at_iso,
                    "winner": self.winner,
                    "offline": self.offline,
                }
            )
        except Exception:
            logger.exception("could not persist the game session")

    def queue_results_if_offline(self) -> None:
        """§B5: everything except session creation goes through the outbox.

        An offline game has no server session, so there is nothing to upload except
        the local photos (which go through the normal photo queue) and a note that
        the game happened.
        """
        if self.storage is None:
            return
        self.storage.log_event(
            "game.offline_result",
            {
                "local_game_id": self.local_game_id,
                "winner": self.winner,
                "scores": self.module.score() if self.module else {},
            },
        )

    # ================================================================== #
    # plumbing
    # ================================================================== #
    def _set_state(self, state: GameHostState, detail: Optional[Dict[str, Any]] = None) -> None:
        if state != self.state:
            logger.debug("game host: %s -> %s", self.state.value, state.value)
        self.state = state
        payload = {"state": state.value, **(detail or {})}
        if self.bus:
            self.bus.publish("game.phase", payload, source="game_host")
        self.observers.state(state, detail or {})

    def status(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "offline": self.offline,
            "template": self.template.type if self.template else None,
            "code": self.session.code if self.session else None,
            "server_game_id": self.session.game_id if self.session else None,
            "local_game_id": self.local_game_id,
            "players": len(self.players),
            "joined": self.joined_count(),
            "submissions": len(self.submissions),
            "countdown": self.countdown_label(),
            "winner": self.winner,
            "ws_events": self._ws_events_seen,
            "last_error": self.last_error,
        }


def _available() -> List[GameTemplate]:
    from .template_loader import available_templates

    return available_templates()
