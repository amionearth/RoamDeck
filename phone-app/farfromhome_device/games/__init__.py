"""Game modules: templates (data), the state machine, and scoring.

The brief calls games "plugins with a common interface: ``on_start``,
``on_event``, ``on_photo``, ``score``, ``on_end``" (§5.3). That interface is
:class:`farfromhome_device.games.base.GameModule`, and the two shipped modules are
:class:`PhotoHuntGame` and :class:`BingoGame`. Adding a game must not require new
code (brief 02 §B2 step 1), so the *content* lives in YAML under
``games/templates/games.yaml`` and is loaded by
:mod:`farfromhome_device.games.template_loader`.
"""

from .base import GameEvent, GameModule, GameScore, LocalScoreboard, PhotoSubmission
from .bingo import BingoGame
from .host import GameHost, GameHostConfig, HostObservers
from .photo_hunt import PhotoHuntGame
from .template_loader import (
    GameTemplate,
    TemplateError,
    available_templates,
    load_template,
    load_templates,
    template_summaries,
)

__all__ = [
    "BingoGame",
    "GameEvent",
    "GameHost",
    "GameHostConfig",
    "GameModule",
    "GameScore",
    "GameTemplate",
    "HostObservers",
    "LocalScoreboard",
    "PhotoHuntGame",
    "PhotoSubmission",
    "TemplateError",
    "available_templates",
    "load_template",
    "load_templates",
    "template_summaries",
]
