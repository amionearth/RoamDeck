"""Everything visible on the panel.

The UI is a **view of the bus** (brief §5.3): screens subscribe to topics and
render payloads. That is what lets the four processes run separately — the UI
process has no engine objects, only bus messages — and it is why a screen cannot
accidentally peek at the destination: it only ever sees what the engine published.

Toolkit choice: **Qt via PySide6**, justified in ``docs/DECISIONS.md`` D-01.
:mod:`farfromhome_device.ui.headless` provides the same screens as plain text for
CI and for a Pi with no display attached, so the engine and games are testable
without a GUI toolkit at all.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("ffh.ui")

QT_AVAILABLE = False
QT_IMPORT_ERROR = ""
try:  # import guard: everything else in the package must run without Qt
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401

    QT_AVAILABLE = True
except Exception as exc:  # pragma: no cover - depends on the environment
    QT_IMPORT_ERROR = str(exc)
    logger.info("PySide6 is not available; the UI will run in headless mode (%s)", exc)

__all__ = ["QT_AVAILABLE", "QT_IMPORT_ERROR"]
