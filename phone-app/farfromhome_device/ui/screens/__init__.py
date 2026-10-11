"""Screens and the Qt application shell.

Imported lazily: ``farfromhome_device.ui.screens.theme`` is safe to import with no
Qt available, while ``farfromhome_device.ui.app`` needs PySide6. That split is what
lets ``--headless`` run on a machine with no GUI toolkit.
"""

from .theme import PALETTE, apply_theme, colour, stylesheet

__all__ = ["PALETTE", "apply_theme", "colour", "stylesheet"]
