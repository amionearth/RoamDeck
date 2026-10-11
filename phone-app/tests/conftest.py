"""Shared test fixtures.

Every test runs against a temporary data directory, so no test can touch a real
device token, outbox or trip photo. That matters more than usual here: the
handheld's whole job is to hold someone's location history.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Iterator

import pytest

os.environ.setdefault("FFH_FORCE_SIM", "1")

from farfromhome_device.core.bus import LocalBus
from farfromhome_device.core.config import Settings, load_settings
from farfromhome_device.core.db import Storage
from farfromhome_device.core.infrastructure import Infrastructure
from farfromhome_device.hal.sim import (
    build_sim_bundle,
)


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    """Settings pointed at an empty temp directory, in simulator mode."""
    config = load_settings(
        env_file=tmp_path / "does-not-exist.env",
        overrides={
            "data_dir": tmp_path / "data",
            "device_mode": "sim",
            "server_url": "http://127.0.0.1:9",  # deliberately unroutable
            "speech_enabled": False,
            "headless": True,
        },
    )
    config.ensure_dirs()
    return config


@pytest.fixture()
def storage(settings: Settings) -> Iterator[Storage]:
    store = Storage(settings.outbox_db)
    yield store
    store.close()


@pytest.fixture()
def bus() -> Iterator[LocalBus]:
    instance = LocalBus(name="test")
    yield instance
    instance.close()


@pytest.fixture()
def hardware() -> Iterator[Any]:
    bundle = build_sim_bundle(start_lat=11.2588, start_lng=75.7804, speed_mps=1.4)
    bundle.open_all()
    yield bundle
    bundle.close_all()


@pytest.fixture()
def infra(settings: Settings, hardware: Any) -> Iterator[Infrastructure]:
    bus = LocalBus(name="test")
    store = Storage(settings.outbox_db)
    instance = Infrastructure(settings=settings, bus=bus, storage=store, clock=_TestClock(), hardware=hardware)
    yield instance
    instance.close()


class _TestClock:
    """A clock the tests can move, so time-based rules are deterministic."""

    def __init__(self, start: Any = None) -> None:
        from datetime import datetime, timezone

        self._now = start or datetime(2026, 3, 14, 9, 0, 0, tzinfo=timezone.utc)
        self.offset = 0.0

    def now(self) -> Any:
        from datetime import timedelta

        return self._now + timedelta(seconds=self.offset)

    def now_iso(self) -> str:
        from farfromhome_device.core.models import iso_z

        return iso_z(self.now())

    def timestamp(self) -> float:
        return self.now().timestamp()

    def advance(self, seconds: float) -> None:
        self.offset += seconds

    def apply_server_time(self, server_time: Any) -> float:
        return 0.0

    def describe(self) -> str:
        return f"test clock at {self.now_iso()}"

    @property
    def synced(self) -> bool:
        return True


@pytest.fixture()
def clock() -> _TestClock:
    return _TestClock()


# --------------------------------------------------------------------------- #
# async
# --------------------------------------------------------------------------- #
@pytest.fixture()
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    """A fresh event loop per test.

    pytest-asyncio is not a dependency on purpose: pulling in another plugin for
    this would be one more thing for a new developer to install.
    """
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


def run_async(coro: Any) -> Any:
    """Run a coroutine to completion on a private loop."""
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# fixtures for the planner
# --------------------------------------------------------------------------- #
@pytest.fixture()
def offline_planner(settings: Settings) -> Any:
    from farfromhome_device.agent.planner import Planner
    from farfromhome_device.agent.tools import OfflinePlaces, StraightLineRouting

    return Planner(settings, bus=None, router=None, places=OfflinePlaces(), routing=StraightLineRouting())


@pytest.fixture()
def sample_plan(offline_planner: Any) -> Any:
    from farfromhome_device.agent.planner import PlanRequest

    outcome = asyncio.run(
        offline_planner.plan(
            PlanRequest(
                text="a chill walk by the water for two, about an hour",
                start_lat=11.2588,
                start_lng=75.7804,
                offline=True,
            )
        )
    )
    assert outcome.plan is not None, "the offline planner must always produce a plan"
    return outcome.plan
