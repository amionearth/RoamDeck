"""Clock helpers.

Two jobs:

1. Format every timestamp the server sees as ISO 8601 UTC with a ``Z``
   (brief 03 §4).
2. Report whether the clock is *synced enough* to be worth talking to the server.
   The server de-duplicates GPS points by ``t``, so a device with a clock stuck
   in 2019 will look like it is replaying an old trip. Without NTP on a Pi that
   has been off for a week, that is exactly what happens.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from .models import iso_z, parse_iso, utcnow

logger = logging.getLogger("ffh.clock")

#: The device considers its clock unsynced if UTC year is below this. A Pi with
#: no RTC and no network boots to its last known time; 2024 is a safe floor for
#: a build made in 2025+.
MIN_PLAUSIBLE_YEAR = 2024

#: Clock skew we tolerate before warning (seconds).
SKEW_WARN_S = 120.0


class Clock:
    """Wraps 'now' so tests can control time without patching ``datetime``."""

    def __init__(self, offset_s: float = 0.0) -> None:
        self._offset = timedelta(seconds=offset_s)
        self._server_time: Optional[datetime] = None
        self._measured_skew_s: float = 0.0
        self._synced = False

    def now(self) -> datetime:
        return utcnow() + self._offset

    def now_iso(self) -> str:
        return iso_z(self.now())

    def timestamp(self) -> float:
        return self.now().timestamp()

    # --- syncing against the server ----------------------------------------
    def apply_server_time(self, server_time: Optional[str]) -> float:
        """Align to ``GET /api/health``'s ``server_time``.

        Returns the measured skew in seconds. We correct only when the skew is
        large, so a jittery response does not make the clock jump backwards
        (GPS points de-duplicate by ``t``; going backwards would look like a
        replay and get points dropped).
        """
        parsed = parse_iso(server_time)
        if parsed is None:
            return 0.0
        local = utcnow()
        skew = (parsed - local).total_seconds()
        self._measured_skew_s = skew
        self._server_time = parsed

        if abs(skew) > SKEW_WARN_S:
            logger.warning(
                "Clock skew with server is %.0fs; correcting local clock offset. "
                "Check that NTP is running (timedatectl status).",
                skew,
            )
            self._offset = timedelta(seconds=skew)
            self._synced = True
        else:
            self._synced = True
        return skew

    @property
    def synced(self) -> bool:
        return self._synced

    @property
    def skew_s(self) -> float:
        return self._measured_skew_s

    def plausible(self) -> bool:
        """Is the local clock good enough to trust for trip timestamps?"""
        return self.now().year >= MIN_PLAUSIBLE_YEAR

    def describe(self) -> str:
        state = "synced" if self._synced else "unsynced"
        if not self.plausible():
            state = "IMPLAUSIBLE (no NTP / dead RTC)"
        return f"clock {state}, skew {self._measured_skew_s:+.0f}s, now {self.now_iso()}"


def format_duration(seconds: float) -> str:
    """``mm:ss`` for countdowns, ``h:mm:ss`` beyond an hour."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def seconds_until(iso_timestamp: Optional[str], clock: Optional[Clock] = None) -> Optional[float]:
    """Seconds from now until ``iso_timestamp``; ``None`` if unparseable."""
    target = parse_iso(iso_timestamp)
    if target is None:
        return None
    now = (clock or Clock()).now()
    return (target - now).total_seconds()


def humanize_ago(iso_timestamp: Optional[str], clock: Optional[Clock] = None) -> str:
    parsed = parse_iso(iso_timestamp)
    if parsed is None:
        return "never"
    delta = (clock or Clock()).now() - parsed
    seconds = delta.total_seconds()
    if seconds < 5:
        return "just now"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def stale(iso_timestamp: Optional[str], max_age_s: float, clock: Optional[Clock] = None) -> bool:
    parsed = parse_iso(iso_timestamp)
    if parsed is None:
        return True
    return ((clock or Clock()).now() - parsed).total_seconds() > max_age_s
