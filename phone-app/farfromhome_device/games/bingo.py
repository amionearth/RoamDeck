"""Bingo re-export.

``BingoGame`` lives in :mod:`farfromhome_device.games.photo_hunt` next to
``PhotoHuntGame`` because they share the tile/approval logic and are 40 lines each.
This module exists so ``from farfromhome_device.games.bingo import BingoGame``
works for anyone expecting a file per game.
"""

from .photo_hunt import GRID_SIZE, BingoGame

__all__ = ["BingoGame", "GRID_SIZE"]
