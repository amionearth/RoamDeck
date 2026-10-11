import math
from typing import Tuple
from sqlalchemy.orm import Session
from backend.app.models.models import User, XPEvent

RARITY_MULTIPLIERS = {
    "common": 1,
    "rare": 2,
    "epic": 3,
    "legendary": 4,
}

def calculate_level_and_title(xp: int) -> Tuple[int, str]:
    if xp < 0:
        xp = 0
    level = 1 + int(math.floor(math.sqrt(xp / 60.0)))
    if level < 5:
        title = "Wanderer"
    elif level < 10:
        title = "Pathfinder"
    elif level < 20:
        title = "Trailblazer"
    elif level < 30:
        title = "Voyager"
    else:
        title = "Legend"
    return level, title

def award_xp(db: Session, user: User, kind: str, amount: int, ref: str = "") -> int:
    event = XPEvent(
        user_id=user.id,
        kind=kind,
        amount=amount,
        ref=ref,
    )
    db.add(event)
    user.xp = (user.xp or 0) + amount
    db.flush()
    return user.xp
