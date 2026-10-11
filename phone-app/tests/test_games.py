"""Game tests: templates, the local scoreboard, and the game-host state machine.

The local scoreboard deliberately mirrors the rules in the server's
``backend/app/services/games.py``, so the assertions here are written against that
source rather than against my own implementation:

* peer-vote threshold ``max(1, min(2, players - 1))``;
* an approved photo completes its task exactly once;
* bingo wins on any row, column or diagonal of a 5x5 grid.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from farfromhome_device.core.models import GameHostState
from farfromhome_device.games.base import GameModule, LocalScoreboard, PhotoSubmission
from farfromhome_device.games.bingo import BingoGame
from farfromhome_device.games.host import GameHost, HostObservers
from farfromhome_device.games.photo_hunt import PhotoHuntGame
from farfromhome_device.games.template_loader import (
    TemplateError,
    load_templates,
    parse_template,
    slugify,
    template_summaries,
)


# --------------------------------------------------------------------------- #
# templates
# --------------------------------------------------------------------------- #
def test_slugify_makes_a_short_key() -> None:
    assert slugify("A Red Door!", "fallback") == "a_red_door"
    assert slugify("", "fallback") == "fallback"
    assert len(slugify("x" * 200, "fallback")) <= 32


def test_bundled_templates_all_load() -> None:
    templates = load_templates()
    assert templates, "the bundled games.yaml must contain templates"
    types = {template.type for template in templates}
    assert {"photo_hunt", "bingo", "color_hunt"} <= types
    for template in templates:
        assert template.items
        assert template.duration_choices()
        assert template.default_duration in template.duration_choices()


def test_template_summaries_have_what_the_configure_screen_needs() -> None:
    for summary in template_summaries():
        assert summary["title"]
        assert summary["players"]["min"] <= summary["players"]["max"]
        assert summary["item_count"] > 0
        assert summary["win_condition"] in ("most_points", "first_n_in_row")
        assert summary["approval"] in ("peer_vote", "auto", "host")


def test_a_template_with_no_items_is_rejected() -> None:
    with pytest.raises(TemplateError):
        parse_template({"type": "photo_hunt", "items": []})


def test_an_unknown_game_type_is_rejected() -> None:
    with pytest.raises(TemplateError):
        parse_template({"type": "space_invaders", "items": ["x"]})


def test_a_bad_approval_mode_is_rejected() -> None:
    with pytest.raises(TemplateError):
        parse_template({"type": "photo_hunt", "approval": "vibes", "items": ["take a photo"]})


def test_a_template_accepts_bare_strings_and_mappings() -> None:
    template = parse_template(
        {
            "type": "photo_hunt",
            "items": ["a red door", {"text": "a blue boat", "points": 25}],
        }
    )
    assert len(template.items) == 2
    assert template.items[0].key == "a_red_door"
    assert template.items[1].points == 25


def test_bingo_tasks_are_assigned_to_everyone() -> None:
    template = next(item for item in load_templates() if item.type == "bingo")
    tasks = template.build_tasks([{"name": "A"}, {"name": "B"}])
    assert len(tasks) == 25
    # Bingo is a shared grid, so no task is owned by one player.
    assert all(task["player_name"] is None for task in tasks)


def test_non_bingo_tasks_are_shared_round_robin() -> None:
    template = next(item for item in load_templates() if item.type == "photo_hunt")
    tasks = template.build_tasks([{"name": "A"}, {"name": "B"}, {"name": "C"}])
    owners = [task["player_name"] for task in tasks]
    assert set(owners) == {"A", "B", "C"}


def test_player_limits_are_enforced_with_a_readable_message() -> None:
    template = next(item for item in load_templates() if item.type == "bingo")
    assert template.players_ok(2) is True
    assert template.players_ok(1) is False
    assert template.players_ok(99) is False
    assert "at least" in template.players_error(1)
    assert "at most" in template.players_error(99)


def test_params_carry_the_win_condition() -> None:
    template = next(item for item in load_templates() if item.type == "bingo")
    params = template.to_params(900)
    assert params["win_condition"] == "first_n_in_row"
    assert params["n"] == 5
    assert params["duration_s"] == 900


# --------------------------------------------------------------------------- #
# the local scoreboard (the server's rules)
# --------------------------------------------------------------------------- #
def test_peer_vote_threshold_matches_the_server() -> None:
    assert LocalScoreboard(total_players=2).approval_threshold == 1
    assert LocalScoreboard(total_players=3).approval_threshold == 2
    assert LocalScoreboard(total_players=8).approval_threshold == 2
    assert LocalScoreboard(total_players=1).approval_threshold == 1


def test_auto_approval_scores_immediately() -> None:
    board = LocalScoreboard(approval_mode="auto", total_players=2)
    board.register_player("p1", "Asha")
    board.set_task_points({"t1": 15})
    submission = PhotoSubmission(photo_id="ph1", player_id="p1", player_name="Asha", task_id="t1")
    board.add_submission(submission, 15)
    assert submission.status == "approved"
    assert board.scores["p1"].points == 15


def test_peer_vote_needs_the_threshold() -> None:
    board = LocalScoreboard(approval_mode="peer_vote", total_players=3)
    board.register_player("p1", "Asha")
    board.set_task_points({"t1": 10})
    submission = PhotoSubmission(photo_id="ph1", player_id="p1", player_name="Asha", task_id="t1")
    board.add_submission(submission, 10)

    board.vote("ph1", "p2", True)
    assert submission.status == "pending", "one vote is not enough with three players"
    board.vote("ph1", "p3", True)
    assert submission.status == "approved"
    assert board.scores["p1"].points == 10


def test_a_rejected_photo_scores_nothing() -> None:
    board = LocalScoreboard(approval_mode="peer_vote", total_players=3)
    board.register_player("p1", "Asha")
    board.set_task_points({"t1": 10})
    submission = PhotoSubmission(photo_id="ph1", player_id="p1", player_name="Asha", task_id="t1")
    board.add_submission(submission, 10)
    board.vote("ph1", "p2", False)
    board.vote("ph1", "p3", False)
    assert submission.status == "rejected"
    assert board.scores["p1"].points == 0


def test_a_player_cannot_vote_for_themselves() -> None:
    board = LocalScoreboard(approval_mode="peer_vote", total_players=3)
    board.register_player("p1", "Asha")
    submission = PhotoSubmission(photo_id="ph1", player_id="p1", player_name="Asha", task_id="t1")
    board.add_submission(submission, 10)
    board.vote("ph1", "p1", True)
    assert submission.approvals == 0


def test_a_task_is_completed_only_once() -> None:
    board = LocalScoreboard(approval_mode="auto", total_players=3)
    board.register_player("p1", "Asha")
    board.register_player("p2", "Dev")
    board.set_task_points({"t1": 10})
    for index, player in enumerate(("p1", "p2")):
        submission = PhotoSubmission(
            photo_id=f"ph{index}", player_id=player, player_name=player, task_id="t1"
        )
        board.add_submission(submission, 10)
    assert board.scores["p1"].points == 10
    assert board.scores["p2"].points == 0, "the same task must not score twice"


def test_leaderboard_is_sorted_and_ranked() -> None:
    board = LocalScoreboard(approval_mode="auto", total_players=3)
    board.register_player("p1", "Asha")
    board.register_player("p2", "Dev")
    board.set_task_points({"t1": 30, "t2": 10})
    board.add_submission(PhotoSubmission(photo_id="a", player_id="p2", player_name="Dev", task_id="t2"), 10)
    board.add_submission(PhotoSubmission(photo_id="b", player_id="p1", player_name="Asha", task_id="t1"), 30)
    board = board
    ordered = board.leaderboard()
    assert [score.player_name for score in ordered] == ["Asha", "Dev"]
    assert [score.rank for score in ordered] == [1, 2]


def test_a_draw_has_no_winner() -> None:
    board = LocalScoreboard(approval_mode="auto", total_players=2)
    board.register_player("p1", "Asha")
    board.register_player("p2", "Dev")
    board.set_task_points({"t1": 10, "t2": 10})
    board.add_submission(
        PhotoSubmission(photo_id="a", player_id="p1", player_name="Asha", task_id="t1"), 10
    )
    board.add_submission(
        PhotoSubmission(photo_id="b", player_id="p2", player_name="Dev", task_id="t2"), 10
    )
    assert board.scores["p1"].points == board.scores["p2"].points == 10
    assert board.winner() is None


def test_pending_votes_are_counted() -> None:
    board = LocalScoreboard(approval_mode="peer_vote", total_players=4)
    board.register_player("p1", "Asha")
    board.add_submission(PhotoSubmission(photo_id="ph1", player_id="p1", player_name="Asha", task_id="t1"), 10)
    assert board.pending_votes() == 1
    board.vote("ph1", "p2", True)
    board.vote("ph1", "p3", True)
    assert board.pending_votes() == 0


# --------------------------------------------------------------------------- #
# photo hunt
# --------------------------------------------------------------------------- #
def make_module(module_cls: Any, players: List[str], tasks: List[Dict[str, Any]], **params: Any) -> GameModule:
    config = {"approval": "auto", "duration_s": 600, **params}
    return module_cls(config, [{"name": name} for name in players], tasks)


def test_photo_hunt_scores_an_auto_approved_photo() -> None:
    module = make_module(
        PhotoHuntGame,
        ["Asha"],
        [{"task_id": "t0", "text": "something red", "item_key": "red", "points": 15}],
    )
    events = module.on_photo(
        PhotoSubmission(photo_id="p1", player_id="p-0", player_name="Asha", task_id="t0")
    )
    assert any(event.kind == "score" for event in events)
    assert module.score()["players"][0]["score"] == 15


def test_photo_hunt_ends_when_every_task_is_done() -> None:
    module = make_module(
        PhotoHuntGame,
        ["Asha"],
        [
            {"task_id": "t0", "text": "a", "points": 5},
            {"task_id": "t1", "text": "b", "points": 5},
        ],
    )
    module.on_photo(PhotoSubmission(photo_id="p1", player_id="p-0", player_name="Asha", task_id="t0"))
    module.on_photo(PhotoSubmission(photo_id="p2", player_id="p-0", player_name="Asha", task_id="t1"))
    assert module.win_condition_met() is True


# --------------------------------------------------------------------------- #
# bingo
# --------------------------------------------------------------------------- #
def bingo_module(players: List[str]) -> BingoGame:
    tasks = [
        {"task_id": f"t{index}", "item_key": BingoGame.tile_key(index), "text": f"tile {index}", "points": 10}
        for index in range(25)
    ]
    return make_module(BingoGame, players, tasks, win_condition="first_n_in_row", n=5)


def test_tile_key_round_trip() -> None:
    assert BingoGame.tile_key(0) == "r0c0"
    assert BingoGame.tile_key(24) == "r4c4"
    assert BingoGame.index_from_key("r2c3") == 13
    assert BingoGame.index_from_key("nonsense") is None
    assert BingoGame.index_from_key("r9c9") is None


def test_a_full_row_wins() -> None:
    module = bingo_module(["Asha", "Dev"])
    player = "p-0"
    for index in range(5):
        module.scoreboard.completed_tasks[f"t{index}"] = player
    assert module.winning_lines(player) == ["row 1"]
    assert module.has_bingo(player) is True


def test_a_full_column_wins() -> None:
    module = bingo_module(["Asha", "Dev"])
    player = "p-0"
    for row in range(5):
        module.scoreboard.completed_tasks[f"t{row * 5 + 2}"] = player
    assert "column 3" in module.winning_lines(player)


def test_a_diagonal_wins() -> None:
    module = bingo_module(["Asha", "Dev"])
    player = "p-0"
    for index in range(5):
        module.scoreboard.completed_tasks[f"t{index * 5 + index}"] = player
    assert any("diagonal" in line for line in module.winning_lines(player))


def test_four_in_a_row_does_not_win() -> None:
    module = bingo_module(["Asha", "Dev"])
    player = "p-0"
    for index in range(4):
        module.scoreboard.completed_tasks[f"t{index}"] = player
    assert module.has_bingo(player) is False


def test_bingo_grid_is_25_cells() -> None:
    module = bingo_module(["Asha", "Dev"])
    assert len(module.grid("p-0")) == 25


def test_bingo_ends_the_game_on_a_win() -> None:
    module = bingo_module(["Asha", "Dev"])
    player = "p-0"
    for index in range(5):
        module.scoreboard.completed_tasks[f"t{index}"] = player
    assert module.win_condition_met() is True


# --------------------------------------------------------------------------- #
# the game host state machine (brief 02 §B2)
# --------------------------------------------------------------------------- #
class FakeClient:
    """Stands in for :class:`DeviceClient` with a scripted server behaviour."""

    def __init__(self, *, paired: bool = True, fail_create: bool = False,
                 join_url: str = "http://192.168.1.50:8000/g/QX7K2") -> None:
        self.paired = paired
        self.fail_create = fail_create
        self.join_url = join_url
        self.created: List[Any] = []
        self.started: List[str] = []
        self.ended: List[str] = []
        self.token = "fake"

    def create_game(self, request: Any) -> Any:
        from farfromhome_device.core.models import CreateGameResponse
        from farfromhome_device.net.client import ServerUnreachable

        if self.fail_create:
            raise ServerUnreachable("the laptop is not answering")
        self.created.append(request)
        return CreateGameResponse(
            game_id="server-game-1",
            code="QX7K2",
            join_url=self.join_url,
            qr_png_url="/api/games/server-game-1/qr.png",
            status="open",
        )

    def network_info(self) -> Any:
        from farfromhome_device.core.models import NetworkInfo

        return NetworkInfo(lan_ip="192.168.1.50", port=8000, base_url="http://192.168.1.50:8000")

    def start_game(self, game_id: str) -> Dict[str, Any]:
        self.started.append(game_id)
        return {"status": "running", "ends_at": "2030-01-01T00:10:00Z"}

    def end_game(self, game_id: str) -> Dict[str, Any]:
        self.ended.append(game_id)
        return {"status": "ended"}

    def game_state(self, game_id: str) -> Any:
        from farfromhome_device.core.models import GameStateResponse

        return GameStateResponse(status="running", ends_at="2030-01-01T00:10:00Z")


@pytest.fixture()
def host(settings: Any, storage: Any, bus: Any) -> GameHost:
    states: List[str] = []
    observers = HostObservers(on_state=lambda state, detail: states.append(state.value))
    instance = GameHost(settings, FakeClient(), storage=storage, bus=bus, observers=observers)
    instance.states_seen = states  # type: ignore[attr-defined]
    return instance


def test_host_starts_idle(host: GameHost) -> None:
    assert host.state == GameHostState.IDLE


def test_configure_rejects_an_unknown_template(host: GameHost) -> None:
    ok, message = host.configure("nope", [{"name": "A"}, {"name": "B"}])
    assert ok is False
    assert "template" in message.lower()


def test_configure_rejects_too_few_players(host: GameHost) -> None:
    ok, message = host.configure("bingo", [{"name": "A"}])
    assert ok is False
    assert "at least" in message


def test_configure_accepts_a_valid_roster(host: GameHost) -> None:
    ok, message = host.configure("photo_hunt", [{"name": "Asha"}, {"name": "Dev"}], duration_min=15)
    assert ok is True, message
    assert host.state == GameHostState.CONFIGURING
    assert host.config.duration_s() == 900
    assert len(host.players) == 2
    assert host.joined_count() == 0


def test_generate_falls_back_to_template_items(host: GameHost) -> None:
    host.configure("photo_hunt", [{"name": "Asha"}, {"name": "Dev"}])
    tasks = asyncio.run(host.generate(router=None))
    assert tasks
    assert host.module is not None
    assert all(task["text"] for task in tasks)


def test_create_session_reaches_the_lobby(host: GameHost) -> None:
    host.configure("photo_hunt", [{"name": "Asha"}, {"name": "Dev"}])
    asyncio.run(host.generate(router=None))
    ok, message = asyncio.run(host.create_session())
    assert ok is True, message
    assert host.state == GameHostState.LOBBY
    assert host.join is not None
    assert host.join.code == "QX7K2"
    assert host.session is not None


def test_create_session_rewrites_a_localhost_join_url(settings: Any, storage: Any) -> None:
    host = GameHost(
        settings,
        FakeClient(join_url="http://localhost:8000/g/QX7K2"),
        storage=storage,
    )
    host.configure("photo_hunt", [{"name": "Asha"}, {"name": "Dev"}])
    asyncio.run(host.generate(router=None))
    asyncio.run(host.create_session())
    assert host.join is not None
    assert "localhost" not in host.join.url
    assert host.join.rewrote_localhost is True


def test_create_session_failure_offers_offline_mode(host: GameHost) -> None:
    host.client.fail_create = True  # type: ignore[union-attr]
    host.configure("photo_hunt", [{"name": "Asha"}, {"name": "Dev"}])
    asyncio.run(host.generate(router=None))
    ok, message = asyncio.run(host.create_session())
    assert ok is False
    assert host.state == GameHostState.OFFLINE_FALLBACK
    assert host.offline is True
    assert "not answering" in message or "unreachable" in message.lower()


def test_offline_mode_marks_everyone_joined(host: GameHost) -> None:
    host.client.fail_create = True  # type: ignore[union-attr]
    host.configure("photo_hunt", [{"name": "Asha"}, {"name": "Dev"}])
    asyncio.run(host.generate(router=None))
    asyncio.run(host.create_session())
    asyncio.run(host.enable_offline_mode())
    assert host.joined_count() == 2


def test_the_offline_game_still_runs_and_ends(host: GameHost) -> None:
    host.client.fail_create = True  # type: ignore[union-attr]
    host.configure("photo_hunt", [{"name": "Asha"}, {"name": "Dev"}], duration_min=5)
    asyncio.run(host.generate(router=None))
    asyncio.run(host.create_session())
    asyncio.run(host.enable_offline_mode())
    ok, _ = asyncio.run(host.start())
    assert ok is True
    assert host.state == GameHostState.RUNNING
    results = asyncio.run(host.end(reason="host"))
    assert host.state == GameHostState.RESULTS
    assert results["offline"] is True
    assert results["penalty"]


def test_starting_a_game_tells_the_server(host: GameHost) -> None:
    host.configure("photo_hunt", [{"name": "Asha"}, {"name": "Dev"}])
    asyncio.run(host.generate(router=None))
    asyncio.run(host.create_session())
    asyncio.run(host.start())
    assert host.client.started == ["server-game-1"]  # type: ignore[union-attr]
    assert host.state == GameHostState.RUNNING


def test_a_server_event_adds_a_joined_player(host: GameHost) -> None:
    host.configure("photo_hunt", [{"name": "Asha"}, {"name": "Dev"}])
    asyncio.run(host.generate(router=None))
    asyncio.run(host.create_session())
    host.handle_server_event("game.player_joined", {"player": {"display_name": "Asha"}, "joined_count": 1})
    assert host.joined_count() == 1
    names = [player["name"] for player in host.players]
    assert "Asha" in names


def test_an_unknown_player_is_added_to_the_roster(host: GameHost) -> None:
    host.configure("photo_hunt", [{"name": "Asha"}, {"name": "Dev"}])
    asyncio.run(host.generate(router=None))
    asyncio.run(host.create_session())
    host.handle_server_event("game.player_joined", {"player": {"display_name": "Someone New"}})
    assert any(player["name"] == "Someone New" for player in host.players)


def test_a_submission_event_lands_in_the_ticker(host: GameHost) -> None:
    host.configure("photo_hunt", [{"name": "Asha"}, {"name": "Dev"}])
    asyncio.run(host.generate(router=None))
    asyncio.run(host.create_session())
    host.handle_server_event(
        "game.submission",
        {
            "photo_id": "ph1",
            "player_id": "p1",
            "player_name": "Asha",
            "task_id": "t0",
            "thumb_url": "/api/trips/t/photos/ph1/thumb",
            "status": "pending",
        },
    )
    assert host.ticker
    assert host.ticker[-1].kind == "submission"
    assert host.running_payload()["pending_uploads"] == 1


def test_the_countdown_comes_from_the_server_deadline(host: GameHost) -> None:
    host.configure("photo_hunt", [{"name": "Asha"}], duration_min=10)
    asyncio.run(host.generate(router=None))
    asyncio.run(host.create_session())
    asyncio.run(host.start())
    host.ends_at_iso = "2030-01-01T00:10:00Z"
    remaining = host.seconds_remaining()
    assert remaining is not None and remaining > 0
    assert host.countdown_label() != "--:--"


def test_the_game_ends_when_the_server_says_so(host: GameHost) -> None:
    host.configure("photo_hunt", [{"name": "Asha"}, {"name": "Dev"}])
    asyncio.run(host.generate(router=None))
    asyncio.run(host.create_session())
    asyncio.run(host.start())

    async def scenario() -> None:
        host.handle_server_event("game.ended", {"winner": "Asha", "scores": [], "penalty": "buy a treat"})
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert host.state == GameHostState.RESULTS
    assert host.winner == "Asha"


def test_ending_twice_is_harmless(host: GameHost) -> None:
    host.configure("photo_hunt", [{"name": "Asha"}, {"name": "Dev"}])
    asyncio.run(host.generate(router=None))
    asyncio.run(host.create_session())
    asyncio.run(host.start())
    first = asyncio.run(host.end(reason="host"))
    second = asyncio.run(host.end(reason="host"))
    assert first["state"] == second["state"]
    assert host.client.ended == ["server-game-1"]  # type: ignore[union-attr]


def test_closing_returns_to_the_trip_screen(host: GameHost) -> None:
    host.configure("photo_hunt", [{"name": "Asha"}])
    asyncio.run(host.generate(router=None))
    asyncio.run(host.create_session())
    host.close()
    assert host.state == GameHostState.CLOSED


def test_the_qr_is_only_drawn_when_online(host: GameHost) -> None:
    host.configure("photo_hunt", [{"name": "Asha"}, {"name": "Dev"}])
    asyncio.run(host.generate(router=None))
    asyncio.run(host.create_session())
    png = host.qr_png()
    assert png is not None and png[:4] == b"\x89PNG"

    host.offline = True
    assert host.qr_png() is None


def test_the_lobby_payload_has_everything_the_screen_shows(host: GameHost) -> None:
    host.configure("photo_hunt", [{"name": "Asha"}, {"name": "Dev"}])
    asyncio.run(host.generate(router=None))
    asyncio.run(host.create_session())
    payload = host.lobby_payload()
    for key in ("code", "join_url", "address", "players", "joined_count", "instructions"):
        assert key in payload
    assert payload["code"] == "QX7K2"


def test_the_game_is_persisted_for_the_sync_daemon(host: GameHost, storage: Any) -> None:
    host.configure("photo_hunt", [{"name": "Asha"}, {"name": "Dev"}])
    asyncio.run(host.generate(router=None))
    asyncio.run(host.create_session())
    sessions = storage.game_sessions()
    assert sessions
    assert sessions[0]["code"] == "QX7K2"
    assert sessions[0]["state"] == GameHostState.LOBBY.value


def test_states_seen_follow_the_brief_order(host: GameHost) -> None:
    host.configure("photo_hunt", [{"name": "Asha"}, {"name": "Dev"}])
    asyncio.run(host.generate(router=None))
    asyncio.run(host.create_session())
    asyncio.run(host.start())
    asyncio.run(host.end(reason="host"))
    seen = host.states_seen  # type: ignore[attr-defined]
    for expected in (
        GameHostState.CONFIGURING.value,
        GameHostState.GENERATING.value,
        GameHostState.CREATING_SESSION.value,
        GameHostState.LOBBY.value,
        GameHostState.RUNNING.value,
        GameHostState.ENDING.value,
        GameHostState.RESULTS.value,
    ):
        assert expected in seen, f"{expected} never happened (saw {seen})"
