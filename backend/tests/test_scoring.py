import pytest
from datetime import datetime, timezone
from backend.app.services.stamps import calculate_rarity
from backend.app.services.xp import calculate_level_and_title

def test_rarity_calculation():
    # Beach (1.0) + short walk (1km * 0.3 = 0.3) + midday (0 bonus) = 1.3 -> common
    t_midday = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
    assert calculate_rarity("beach", 1000.0, t_midday) == "common"

    # Temple (3.0) + 5km (1.5) + midday = 4.5 -> epic
    assert calculate_rarity("temple", 5000.0, t_midday) == "epic"

    # Viewpoint (3.0) + 10km (3.0) + sunset bonus (1.0) = 7.0 -> legendary
    t_sunset = datetime(2026, 10, 11, 18, 15, 0, tzinfo=timezone.utc)
    assert calculate_rarity("viewpoint", 10000.0, t_sunset) == "legendary"

def test_level_and_title_calculation():
    # 0 XP -> Level 1, Wanderer
    lvl, title = calculate_level_and_title(0)
    assert lvl == 1
    assert title == "Wanderer"

    # Level = 1 + floor(sqrt(xp/60))
    # 60 XP -> sqrt(1) = 1 -> Level 2
    lvl, title = calculate_level_and_title(60)
    assert lvl == 2
    assert title == "Wanderer"

    # 1000 XP -> sqrt(16.66) = 4 -> Level 5, Pathfinder
    lvl, title = calculate_level_and_title(1000)
    assert lvl == 5
    assert title == "Pathfinder"

    # 5000 XP -> sqrt(83.33) = 9 -> Level 10, Trailblazer
    lvl, title = calculate_level_and_title(5000)
    assert lvl == 10
    assert title == "Trailblazer"
