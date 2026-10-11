"""SQLite storage for the handheld. One database, WAL mode, commit per write.

Tables (brief §5.0 "Storage"):

    trips          one row per trip, plus the validated Plan JSON
    track_points   the downsampled track log for the wrap-up
    outbox         the offline queue (schema mirrors brief 03 §5/§11)
    photos         photo metadata + local file path + upload state
    game_sessions  locally tracked game sessions (host mode + offline fallback)
    events         append-only log of interesting things (reveals, SOS, errors)
    kv             tiny key/value store (last plan, demo flag, server URL cache)

Design notes
------------
* ``journal_mode=WAL`` and a commit after every insert: brief 03 §5 "Survive
  power loss".
* The outbox is deliberately one table read in ``id`` order. Strict ordering is
  the whole point (trip before its points), so there are no priority columns.
* ``check_same_thread=False`` with a lock, because the UI thread and the sync
  worker share the connection. SQLite in WAL mode handles one writer at a time;
  a process-wide lock keeps us from fighting over it.
* Photos are stored on disk; the DB keeps the path. Nothing large goes in rows.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from .models import Plan, iso_z, utcnow

logger = logging.getLogger("ffh.db")


SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trips (
    trip_id       TEXT PRIMARY KEY,
    state         TEXT NOT NULL DEFAULT 'planned',
    title         TEXT,
    plan_json     TEXT,
    spec_json     TEXT,
    destination_json TEXT,
    started_at    TEXT,
    arrived_at    TEXT,
    finished_at   TEXT,
    distance_m    REAL DEFAULT 0,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS track_points (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id     TEXT NOT NULL,
    lat         REAL NOT NULL,
    lng         REAL NOT NULL,
    accuracy_m  REAL,
    speed_mps   REAL,
    course_deg  REAL,
    heading_deg REAL,
    recorded_at TEXT NOT NULL,
    sent        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_track_trip ON track_points(trip_id, id);

CREATE TABLE IF NOT EXISTS outbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    method      TEXT NOT NULL,
    path        TEXT NOT NULL,
    body        TEXT,
    file_path   TEXT,
    form        TEXT,
    kind        TEXT,
    ref_id      TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    next_try    REAL NOT NULL DEFAULT 0,
    last_status INTEGER,
    last_error  TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbox_next ON outbox(next_try, id);

CREATE TABLE IF NOT EXISTS photos (
    photo_id    TEXT PRIMARY KEY,
    trip_id     TEXT,
    game_id     TEXT,
    task_id     TEXT,
    local_path  TEXT NOT NULL,
    member_name TEXT,
    category    TEXT DEFAULT 'waypoint',
    caption     TEXT DEFAULT '',
    lat         REAL,
    lng         REAL,
    taken_at    TEXT NOT NULL,
    uploaded    INTEGER NOT NULL DEFAULT 0,
    server_thumb_url TEXT,
    exif_stripped INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS game_sessions (
    local_game_id TEXT PRIMARY KEY,
    server_game_id TEXT,
    trip_id       TEXT,
    game_type     TEXT NOT NULL,
    template      TEXT,
    state         TEXT NOT NULL,
    code          TEXT,
    join_url      TEXT,
    params_json   TEXT,
    players_json  TEXT,
    tasks_json    TEXT,
    duration_s    INTEGER DEFAULT 600,
    started_at    TEXT,
    ends_at       TEXT,
    ended_at      TEXT,
    winner        TEXT,
    offline       INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_cache (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id    TEXT,
    topic      TEXT NOT NULL,
    payload    TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_trip ON events(trip_id, id);
"""


class Storage:
    """Thread-safe SQLite wrapper."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=15.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")  # WAL + NORMAL is the durable-enough default
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()
        logger.debug("storage ready at %s", self.path)

    # ------------------------------------------------------------------ #
    # plumbing
    # ------------------------------------------------------------------ #
    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ #
    # kv
    # ------------------------------------------------------------------ #
    def set_kv(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
            (key, json.dumps(value)),
        )

    def get_kv(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM meta WHERE key=?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return row["value"]

    # ------------------------------------------------------------------ #
    # trips
    # ------------------------------------------------------------------ #
    def save_plan(self, plan: Plan) -> None:
        self.execute(
            """
            INSERT INTO trips(trip_id, state, title, plan_json, spec_json, destination_json, created_at)
            VALUES(?, 'planned', ?, ?, ?, ?, ?)
            ON CONFLICT(trip_id) DO UPDATE SET
                title=excluded.title,
                plan_json=excluded.plan_json,
                spec_json=excluded.spec_json,
                destination_json=excluded.destination_json
            """,
            (
                plan.trip_id,
                plan.title,
                plan.model_dump_json(),
                plan.spec.model_dump_json(),
                plan.destination.model_dump_json(),
                iso_z(utcnow()),
            ),
        )

    def load_plan(self, trip_id: str) -> Optional[Plan]:
        row = self.query_one("SELECT plan_json FROM trips WHERE trip_id=?", (trip_id,))
        if not row or not row["plan_json"]:
            return None
        try:
            return Plan.model_validate_json(row["plan_json"])
        except Exception as exc:
            logger.error("stored plan for %s is unreadable: %s", trip_id, exc)
            return None

    def latest_plan(self) -> Optional[Plan]:
        row = self.query_one("SELECT plan_json FROM trips ORDER BY created_at DESC LIMIT 1")
        if not row or not row["plan_json"]:
            return None
        try:
            return Plan.model_validate_json(row["plan_json"])
        except Exception:
            return None

    def set_trip_state(self, trip_id: str, state: str, **fields: Any) -> None:
        allowed = {"started_at", "arrived_at", "finished_at", "distance_m", "title"}
        assignments = ["state=?"]
        params: List[Any] = [state]
        for key, value in fields.items():
            if key not in allowed:
                continue
            assignments.append(f"{key}=?")
            params.append(value)
        params.append(trip_id)
        self.execute(f"UPDATE trips SET {', '.join(assignments)} WHERE trip_id=?", params)

    def get_trip(self, trip_id: str) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM trips WHERE trip_id=?", (trip_id,))

    # ------------------------------------------------------------------ #
    # track log
    # ------------------------------------------------------------------ #
    def add_track_point(
        self,
        trip_id: str,
        lat: float,
        lng: float,
        accuracy_m: Optional[float] = None,
        speed_mps: Optional[float] = None,
        course_deg: Optional[float] = None,
        heading_deg: Optional[float] = None,
        recorded_at: Optional[str] = None,
    ) -> int:
        cursor = self.execute(
            """
            INSERT INTO track_points(trip_id, lat, lng, accuracy_m, speed_mps, course_deg, heading_deg, recorded_at)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                trip_id,
                lat,
                lng,
                accuracy_m,
                speed_mps,
                course_deg,
                heading_deg,
                recorded_at or iso_z(utcnow()),
            ),
        )
        return int(cursor.lastrowid or 0)

    def track_points(self, trip_id: str, limit: int = 5000) -> List[sqlite3.Row]:
        return self.query(
            "SELECT * FROM track_points WHERE trip_id=? ORDER BY id ASC LIMIT ?",
            (trip_id, limit),
        )

    def track_count(self, trip_id: str) -> int:
        row = self.query_one("SELECT COUNT(*) AS n FROM track_points WHERE trip_id=?", (trip_id,))
        return int(row["n"]) if row else 0

    def track_length_m(self, trip_id: str) -> float:
        """Great-circle length of the logged track. Used for the finish payload."""
        from ..engine.geo import haversine_m  # local import: avoids a core->engine cycle

        points = self.track_points(trip_id)
        total = 0.0
        for previous, current in zip(points, points[1:]):
            total += haversine_m(previous["lat"], previous["lng"], current["lat"], current["lng"])
        return total

    # ------------------------------------------------------------------ #
    # outbox
    # ------------------------------------------------------------------ #
    def outbox_add(
        self,
        method: str,
        path: str,
        body: Optional[Any] = None,
        file_path: Optional[str] = None,
        form: Optional[Dict[str, str]] = None,
        kind: str = "json",
        ref_id: Optional[str] = None,
    ) -> int:
        """Append a row. ``body`` may be a dict or a list (the GPS points endpoint
        takes a bare array), so it is stored as JSON either way."""
        cursor = self.execute(
            """
            INSERT INTO outbox(method, path, body, file_path, form, kind, ref_id, created_at)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                method.upper(),
                path,
                json.dumps(body) if body is not None else None,
                str(file_path) if file_path else None,
                json.dumps(form) if form else None,
                kind,
                ref_id,
                iso_z(utcnow()),
            ),
        )
        return int(cursor.lastrowid or 0)

    def outbox_head(self, now: Optional[float] = None) -> Optional[sqlite3.Row]:
        """The oldest row, if it is due. Strict ordering: never skip ahead.

        If the head is waiting for backoff, everything behind it waits. That is
        intentional (brief 03 §5: "One queue, strict order").
        """
        moment = time.time() if now is None else now
        row = self.query_one("SELECT * FROM outbox ORDER BY id LIMIT 1")
        if row is None:
            return None
        if row["next_try"] <= moment:
            return row
        return None

    def outbox_wait_seconds(self, now: Optional[float] = None) -> Optional[float]:
        moment = time.time() if now is None else now
        row = self.query_one("SELECT next_try FROM outbox ORDER BY id LIMIT 1")
        if row is None:
            return None
        return max(0.0, float(row["next_try"]) - moment)

    def outbox_peek(self, limit: int = 20) -> List[sqlite3.Row]:
        return self.query("SELECT * FROM outbox ORDER BY id ASC LIMIT ?", (limit,))

    def outbox_count(self) -> int:
        row = self.query_one("SELECT COUNT(*) AS n FROM outbox")
        return int(row["n"]) if row else 0

    def outbox_done(self, row_id: int) -> None:
        self.execute("DELETE FROM outbox WHERE id=?", (row_id,))

    def outbox_retry_later(self, row_id: int, attempts: int, status: Optional[int] = None,
                           error: str = "", delay: Optional[float] = None) -> float:
        """Backoff 2, 4, 8, 16, 32, then 60 s (brief 03 §5)."""
        wait = 2 ** (attempts + 1) if delay is None else delay
        wait = float(min(60.0, max(1.0, wait)))
        self.execute(
            "UPDATE outbox SET attempts=?, next_try=?, last_status=?, last_error=? WHERE id=?",
            (attempts + 1, time.time() + wait, status, error[:400], row_id),
        )
        return wait

    def outbox_force_now(self, row_id: Optional[int] = None) -> None:
        if row_id is None:
            self.execute("UPDATE outbox SET next_try=0")
        else:
            self.execute("UPDATE outbox SET next_try=0 WHERE id=?", (row_id,))

    def outbox_clear(self, path_like: Optional[str] = None) -> int:
        if path_like:
            cursor = self.execute("DELETE FROM outbox WHERE path LIKE ?", (f"%{path_like}%",))
        else:
            cursor = self.execute("DELETE FROM outbox")
        return cursor.rowcount or 0

    # ------------------------------------------------------------------ #
    # photos
    # ------------------------------------------------------------------ #
    def add_photo(
        self,
        photo_id: str,
        local_path: str,
        trip_id: Optional[str] = None,
        game_id: Optional[str] = None,
        task_id: Optional[str] = None,
        member_name: Optional[str] = None,
        category: str = "waypoint",
        caption: str = "",
        lat: Optional[float] = None,
        lng: Optional[float] = None,
        taken_at: Optional[str] = None,
        exif_stripped: bool = True,
    ) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO photos(
                photo_id, trip_id, game_id, task_id, local_path, member_name, category,
                caption, lat, lng, taken_at, uploaded, exif_stripped
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?)
            """,
            (
                photo_id,
                trip_id,
                game_id,
                task_id,
                str(local_path),
                member_name,
                category,
                caption,
                lat,
                lng,
                taken_at or iso_z(utcnow()),
                1 if exif_stripped else 0,
            ),
        )

    def mark_photo_uploaded(self, photo_id: str, thumb_url: str = "") -> None:
        self.execute(
            "UPDATE photos SET uploaded=1, server_thumb_url=? WHERE photo_id=?",
            (thumb_url, photo_id),
        )

    def photos_for_trip(self, trip_id: str, only_uploaded: bool = False) -> List[sqlite3.Row]:
        sql = "SELECT * FROM photos WHERE trip_id=?"
        if only_uploaded:
            sql += " AND uploaded=1"
        sql += " ORDER BY taken_at ASC"
        return self.query(sql, (trip_id,))

    def all_photos(self) -> List[sqlite3.Row]:
        return self.query("SELECT * FROM photos ORDER BY taken_at ASC")

    def photo_count(self, trip_id: Optional[str] = None) -> int:
        if trip_id:
            row = self.query_one("SELECT COUNT(*) AS n FROM photos WHERE trip_id=?", (trip_id,))
        else:
            row = self.query_one("SELECT COUNT(*) AS n FROM photos")
        return int(row["n"]) if row else 0

    def delete_uploaded_local_files(self, trip_id: str, keep_dir: bool = True) -> int:
        """Delete local photo files the server has confirmed, for a finished trip.

        Brief 03 §5: "Delete the local file only after the server confirms it and
        the trip is finished."
        """
        removed = 0
        for row in self.photos_for_trip(trip_id, only_uploaded=True):
            path = Path(row["local_path"])
            try:
                if path.is_file():
                    path.unlink()
                    removed += 1
            except OSError as exc:
                logger.warning("could not delete %s: %s", path, exc)
        return removed

    # ------------------------------------------------------------------ #
    # game sessions
    # ------------------------------------------------------------------ #
    def save_game_session(self, session: Dict[str, Any]) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO game_sessions(
                local_game_id, server_game_id, trip_id, game_type, template, state, code,
                join_url, params_json, players_json, tasks_json, duration_s,
                started_at, ends_at, ended_at, winner, offline, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                session["local_game_id"],
                session.get("server_game_id"),
                session.get("trip_id"),
                session["game_type"],
                session.get("template"),
                session["state"],
                session.get("code"),
                session.get("join_url"),
                json.dumps(session.get("params", {})),
                json.dumps(session.get("players", [])),
                json.dumps(session.get("tasks", [])),
                int(session.get("duration_s", 600)),
                session.get("started_at"),
                session.get("ends_at"),
                session.get("ended_at"),
                session.get("winner"),
                1 if session.get("offline") else 0,
                session.get("created_at") or iso_z(utcnow()),
            ),
        )

    def load_game_session(self, local_game_id: str) -> Optional[Dict[str, Any]]:
        row = self.query_one("SELECT * FROM game_sessions WHERE local_game_id=?", (local_game_id,))
        return self._game_row(row) if row else None

    def latest_game_session(self) -> Optional[Dict[str, Any]]:
        row = self.query_one("SELECT * FROM game_sessions ORDER BY created_at DESC LIMIT 1")
        return self._game_row(row) if row else None

    def game_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.query("SELECT * FROM game_sessions ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._game_row(row) for row in rows]

    @staticmethod
    def _game_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "local_game_id": row["local_game_id"],
            "server_game_id": row["server_game_id"],
            "trip_id": row["trip_id"],
            "game_type": row["game_type"],
            "template": row["template"],
            "state": row["state"],
            "code": row["code"],
            "join_url": row["join_url"],
            "params": json.loads(row["params_json"]) if row["params_json"] else {},
            "players": json.loads(row["players_json"]) if row["players_json"] else [],
            "tasks": json.loads(row["tasks_json"]) if row["tasks_json"] else [],
            "duration_s": row["duration_s"],
            "started_at": row["started_at"],
            "ends_at": row["ends_at"],
            "ended_at": row["ended_at"],
            "winner": row["winner"],
            "offline": bool(row["offline"]),
            "created_at": row["created_at"],
        }

    # ------------------------------------------------------------------ #
    # events
    # ------------------------------------------------------------------ #
    def log_event(self, topic: str, payload: Optional[Dict[str, Any]] = None, trip_id: Optional[str] = None) -> None:
        self.execute(
            "INSERT INTO events(trip_id, topic, payload, created_at) VALUES(?,?,?,?)",
            (trip_id, topic, json.dumps(payload or {}, default=str), iso_z(utcnow())),
        )

    def recent_events(self, limit: int = 200, trip_id: Optional[str] = None) -> List[sqlite3.Row]:
        if trip_id:
            return self.query(
                "SELECT * FROM events WHERE trip_id=? ORDER BY id DESC LIMIT ?", (trip_id, limit)
            )
        return self.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ #
    # housekeeping
    # ------------------------------------------------------------------ #
    def wipe(self) -> None:
        """Demo reset: drop trip data. Keeps the pairing token in ``meta``."""
        with self.tx() as conn:
            for table in ("track_points", "photos", "game_sessions", "events", "outbox", "trips"):
                conn.execute(f"DELETE FROM {table}")

    def stats(self) -> Dict[str, Any]:
        def count(table: str) -> int:
            row = self.query_one(f"SELECT COUNT(*) AS n FROM {table}")
            return int(row["n"]) if row else 0

        return {
            "path": str(self.path),
            "schema_version": self.get_kv("schema_version", SCHEMA_VERSION),
            "trips": count("trips"),
            "track_points": count("track_points"),
            "outbox": count("outbox"),
            "photos": count("photos"),
            "game_sessions": count("game_sessions"),
            "events": count("events"),
        }


_storage: Optional[Storage] = None


def get_storage(path: Path | str, reload: bool = False) -> Storage:
    """Process-wide storage singleton."""
    global _storage
    if _storage is None or reload or Path(path) != _storage.path:
        if _storage is not None:
            _storage.close()
        _storage = Storage(path)
    return _storage
