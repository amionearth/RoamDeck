"""``fake_phone``: a browserless stand-in for a player's phone.

Brief §14 asks for a "fake-player script to join sessions and upload sample
photos", and M5's acceptance check is "the fake-player script plus one real phone
complete a session". This is that script, and it talks to the **server** exactly
the way the phone web page does (``backend/app/routers/phone_games.py``), so it
also proves the server's phone endpoints work without a browser:

    POST /api/g/{code}/join                          -> player token
    GET  /api/g/{code}/me                            -> my tasks
    POST /api/g/{code}/submissions    (multipart)     -> upload a photo
    GET  /api/g/{code}/gallery                        -> photos waiting for my vote
    POST /api/g/{code}/vote                           -> approve / reject
    GET  /api/g/{code}/scoreboard                     -> live scores

Usage::

    python tools/fake_phone.py --code QX7K2 --name Asha
    python tools/fake_phone.py --code QX7K2 --players 3 --rounds 2   # a whole session
"""

from __future__ import annotations

import argparse
import io
import json
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from PIL import Image, ImageDraw

SERVER_DEFAULT = "http://127.0.0.1:8888"

#: Colours the fake phone can produce, so a colour-hunt game has something real
#: to check. These are the same hex values the colour-hunt template uses.
COLOURS = {
    "red": (211, 47, 47),
    "blue": (21, 101, 192),
    "yellow": (249, 168, 37),
    "green": (46, 125, 50),
    "orange": (239, 108, 0),
    "purple": (106, 27, 154),
    "pink": (194, 24, 91),
    "white": (245, 245, 245),
}


def sample_photo(colour: str = "blue", label: str = "fake phone") -> bytes:
    """A JPEG dominated by one colour, so the server's colour check has a target."""
    rgb = COLOURS.get(colour.lower(), (30, 127, 133))
    image = Image.new("RGB", (900, 675), rgb)
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 20, 880, 655], outline=(255, 255, 255), width=4)
    draw.text((40, 40), f"{label}: {colour}", fill=(255, 255, 255))
    draw.text((40, 630), time.strftime("%H:%M:%S"), fill=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


class FakePhone:
    """One simulated player."""

    def __init__(self, server: str, code: str, name: Optional[str] = None,
                 player_id: Optional[str] = None, timeout: float = 10.0) -> None:
        self.server = server.rstrip("/")
        self.code = code.upper().strip()
        self.name = name
        self.player_id = player_id
        self.timeout = timeout
        self.session = requests.Session()
        self.token: Optional[str] = None

    # ------------------------------------------------------------------ #
    def _url(self, path: str) -> str:
        return f"{self.server}/api{path}"

    def _headers(self) -> Dict[str, str]:
        return {"X-Player-Token": self.token} if self.token else {}

    def info(self) -> Dict[str, Any]:
        response = self.session.get(self._url(f"/g/{self.code}/info"), timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def join(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if self.player_id:
            body["player_id"] = self.player_id
        elif self.name:
            body["display_name"] = self.name
        else:
            raise ValueError("a player name or id is required")
        response = self.session.post(
            self._url(f"/g/{self.code}/join"), json=body, timeout=self.timeout
        )
        if response.status_code == 409:
            payload = response.json()
            raise RuntimeError(f"the game has already ended: {payload.get('detail')}")
        response.raise_for_status()
        payload = response.json()
        self.token = payload["token"]
        self.name = payload.get("display_name") or self.name
        return payload

    def me(self) -> Dict[str, Any]:
        response = self.session.get(
            self._url(f"/g/{self.code}/me"), headers=self._headers(), timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def submit(self, task_id: str, colour: str = "blue", caption: str = "") -> Dict[str, Any]:
        image = sample_photo(colour, self.name or "player")
        response = self.session.post(
            self._url(f"/g/{self.code}/submissions"),
            headers=self._headers(),
            data={"task_id": task_id, "caption": caption or f"{colour} by {self.name}"},
            files={"photo": (f"{task_id}.jpg", image, "image/jpeg")},
            timeout=60.0,
        )
        return {"status": response.status_code, "body": _safe_json(response)}

    def gallery(self) -> List[Dict[str, Any]]:
        response = self.session.get(
            self._url(f"/g/{self.code}/gallery"), headers=self._headers(), timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def vote(self, photo_id: str, approve: bool = True) -> Dict[str, Any]:
        response = self.session.post(
            self._url(f"/g/{self.code}/vote"),
            headers=self._headers(),
            json={"photo_id": photo_id, "vote": "approve" if approve else "reject"},
            timeout=self.timeout,
        )
        return {"status": response.status_code, "body": _safe_json(response)}

    def scoreboard(self) -> Dict[str, Any]:
        response = self.session.get(self._url(f"/g/{self.code}/scoreboard"), timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def state(self) -> str:
        try:
            return str(self.info().get("status", "?"))
        except requests.RequestException:
            return "unreachable"


def _safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:200]


# --------------------------------------------------------------------------- #
# the whole-session simulation
# --------------------------------------------------------------------------- #
def run_session(
    server: str,
    code: str,
    player_count: int = 3,
    rounds: int = 2,
    approve: bool = True,
    wait_for_start: bool = True,
    verbose: bool = True,
) -> int:
    """Join, wait for the host to start, upload, vote, and print the scores."""
    def log(message: str) -> None:
        if verbose:
            print(message, flush=True)

    roster = [f"Phone{index + 1}" for index in range(player_count)]
    phones = [FakePhone(server, code, name=name) for name in roster]

    info = phones[0].info()
    log(f"game {code}: type={info.get('type')} status={info.get('status')} "
        f"players={[player.get('display_name') for player in info.get('players', [])]}")

    for phone in phones:
        try:
            joined = phone.join()
            log(f"  {joined.get('display_name')} joined (player id {joined.get('player_id', '')[:8]})")
        except Exception as exc:
            log(f"  {phone.name} could not join: {exc}")
            return 2

    if wait_for_start:
        deadline = time.time() + 120.0
        while phone_state(phones[0]) == "open" and time.time() < deadline:
            log("  waiting for the host to start...")
            time.sleep(3.0)
        if phone_state(phones[0]) == "open":
            log("  the host never started the game; uploading now would be refused")
            return 3

    for round_index in range(rounds):
        log(f"\nround {round_index + 1}")
        for phone in phones:
            try:
                status = phone.me()
            except requests.RequestException as exc:
                log(f"  {phone.name}: cannot read tasks ({exc})")
                continue
            tasks = [task for task in status.get("tasks", []) if task.get("status") != "completed"]
            if not tasks:
                log(f"  {phone.name}: no tasks left")
                continue
            task = tasks[0]
            colour = _guess_colour(task)
            result = phone.submit(str(task["id"]), colour=colour, caption=f"round {round_index + 1}")
            log(f"  {phone.name} uploaded for '{str(task.get('text'))[:40]}' -> "
                f"HTTP {result['status']} {result['body']}")

        # Peer votes: every phone approves every other phone's pending photos.
        for phone in phones:
            try:
                pending = phone.gallery()
            except requests.RequestException:
                continue
            for item in pending:
                result = phone.vote(str(item["photo_id"]), approve=approve)
                log(f"  {phone.name} voted {'approve' if approve else 'reject'} on "
                    f"{item.get('player_name')}'s photo -> HTTP {result['status']}")

        time.sleep(1.0)

    log("\nscoreboard")
    try:
        board = phones[0].scoreboard()
        for entry in board.get("scoreboard", []):
            log(f"  {entry.get('rank')}. {entry.get('display_name'):<10} {entry.get('points'):>4} pts")
        log(f"  status: {board.get('status')}")
    except requests.RequestException as exc:
        log(f"  could not read the scoreboard: {exc}")
        return 2
    return 0


def phone_state(phone: FakePhone) -> str:
    try:
        return str(phone.info().get("status", "?"))
    except requests.RequestException:
        return "unreachable"


def _guess_colour(task: Dict[str, Any]) -> str:
    """Pick the colour a colour-hunt task is asking for, from its item key or text."""
    key = str(task.get("item_key") or "").lower()
    if key in COLOURS:
        return key
    text = str(task.get("text") or "").lower()
    for name in COLOURS:
        if name in text:
            return name
    return random.choice(list(COLOURS))


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="A browserless fake player phone")
    parser.add_argument("--server", default=SERVER_DEFAULT, help="server base URL")
    parser.add_argument("--code", required=True, help="the join code shown on the handheld")
    parser.add_argument("--name", help="this player's name")
    parser.add_argument("--player-id", help="claim a specific roster slot")
    parser.add_argument("--players", type=int, default=1, help="simulate this many phones")
    parser.add_argument("--rounds", type=int, default=1, help="upload this many photos each")
    parser.add_argument("--reject", action="store_true", help="vote to reject instead of approve")
    parser.add_argument("--no-wait", action="store_true", help="do not wait for the host to start")
    parser.add_argument("--check", action="store_true", help="only check that the game exists")
    args = parser.parse_args()

    try:
        return _run(args)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status == 404:
            print(
                f"there is no game with the code {args.code.upper()} at {args.server}.\n"
                "Check the code on the handheld's lobby screen, and that SERVER_URL points at "
                "the machine running the server.",
                file=sys.stderr,
            )
        else:
            print(f"the server answered HTTP {status}: {exc}", file=sys.stderr)
        return 2
    except requests.RequestException as exc:
        print(f"cannot reach {args.server}: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 3


def _run(args: argparse.Namespace) -> int:
    if args.check:
        phone = FakePhone(args.server, args.code)
        info = phone.info()
        print(json.dumps(info, indent=2))
        return 0

    if args.players > 1:
        return run_session(
            args.server,
            args.code,
            player_count=args.players,
            rounds=args.rounds,
            approve=not args.reject,
            wait_for_start=not args.no_wait,
        )

    phone = FakePhone(args.server, args.code, name=args.name, player_id=args.player_id)
    print(f"game {args.code}: {phone.state()}")
    joined = phone.join()
    print(f"joined as {joined['display_name']} ({joined['player_id']})")

    if not args.no_wait:
        deadline = time.time() + 120.0
        while phone_state(phone) == "open" and time.time() < deadline:
            print("waiting for the host to start...")
            time.sleep(3.0)

    for _round in range(args.rounds):
        status = phone.me()
        tasks = [task for task in status.get("tasks", []) if task.get("status") != "completed"]
        if not tasks:
            print("no tasks left")
            break
        task = tasks[0]
        colour = _guess_colour(task)
        result = phone.submit(str(task["id"]), colour=colour)
        print(f"uploaded for '{task.get('text')}' -> HTTP {result['status']} {result['body']}")
        time.sleep(0.5)

    for item in phone.gallery():
        result = phone.vote(str(item["photo_id"]), approve=not args.reject)
        print(f"voted on {item.get('player_name')}'s photo -> HTTP {result['status']}")

    board = phone.scoreboard()
    print(json.dumps(board, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
