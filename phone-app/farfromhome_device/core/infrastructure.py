"""Process infrastructure: one object holding config, bus, storage, clock, hardware.

Why a bundle instead of globals: the brief wants four processes, and the demo runs
them in one. A bundle makes both possible and keeps tests from sharing state
between cases.

    infra = build_infrastructure(settings)
    infra.bus.subscribe("compass.update", on_compass)
    infra.storage.save_plan(plan)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .bus import Bus, make_bus
from .clock import Clock
from .config import Settings, get_settings
from .db import Storage, get_storage

logger = logging.getLogger("ffh.infra")


@dataclass
class Infrastructure:
    settings: Settings
    bus: Bus
    storage: Storage
    clock: Clock
    hardware: Any = None  # HardwareBundle; typed loosely to avoid a core->hal import
    transport: str = "local"

    # --- convenience --------------------------------------------------------
    def publish(self, topic: str, payload: Optional[Dict[str, Any]] = None, source: str = "app") -> None:
        self.bus.publish(topic, payload, source=source)

    def subscribe(self, topic: str, handler: Any) -> Any:
        return self.bus.subscribe(topic, handler)

    def close(self) -> None:
        if self.hardware is not None:
            try:
                self.hardware.close_all()
            except Exception:
                logger.exception("hardware close failed")
        self.bus.close()
        self.storage.close()

    def status(self) -> Dict[str, Any]:
        return {
            "settings": self.settings.as_public_dict(),
            "bus": self.bus.stats(),
            "storage": self.storage.stats(),
            "hardware": self.hardware.status() if self.hardware else None,
            "clock": self.clock.describe(),
        }


def build_infrastructure(
    settings: Optional[Settings] = None,
    *,
    hardware: Any = None,
    transport: str = "local",
    bus: Optional[Bus] = None,
) -> Infrastructure:
    """Wire the shared services together.

    ``transport`` is documented in ARCHITECTURE.md §Bus; ``local`` is the default
    and the only one shipped (see DECISIONS.md D-02).
    """
    settings = settings or get_settings()

    if bus is None:
        bus = make_bus(transport, name=transport)

    storage = get_storage(settings.outbox_db)

    infra = Infrastructure(
        settings=settings,
        bus=bus,
        storage=storage,
        clock=Clock(),
        hardware=hardware,
        transport=transport,
    )
    logger.debug("infrastructure ready: %s", settings.as_public_dict())
    return infra
