"""``/api/llm/*`` — the LLM gateway the handheld calls.

Brief 01 §5.1 and §6.4: *"LLM gateway (``POST /llm/*``): holds provider keys,
rate-limits, caches, logs usage."*

Two endpoints:

* ``POST /api/llm/text`` — free-form text, optionally JSON-schema-constrained. This is
  what the handheld's ``ServerGatewayProvider`` calls.
* ``POST /api/llm/plan`` — a convenience alias that asks for a full plan's creative
  content in one shot, with the planning schema described in the prompt.

Auth is the **device bearer token**: the gateway is for paired handhelds, not for the
open internet. The rate limit is per device and global, and it is what stops one
handheld from spending the day's free-tier quota.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.db import get_db
from backend.app.models.models import Device
from backend.app.security import get_current_device
from backend.app.services.llm_gateway import (
    DEFAULT_SYSTEM,
    GatewayUnavailable,
    RateLimited,
    gateway,
)

logger = logging.getLogger("ffh.routers.llm")

router = APIRouter(prefix="/llm", tags=["llm"])


class LlmTextRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)
    system: str = Field(default="", max_length=4000)
    schema_: Optional[Dict[str, Any]] = Field(default=None, alias="schema")
    task: str = Field(default="text", max_length=40)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1200, ge=16, le=4096)
    cache: bool = True

    model_config = {"populate_by_name": True}


class LlmTextResponse(BaseModel):
    text: str
    provider: str
    model: str
    cached: bool = False
    latency_ms: int = 0
    attempts: List[Dict[str, Any]] = []


class LlmPlanRequest(BaseModel):
    """What the handheld needs to write the creative half of a plan.

    Deliberately contains **no place name and no coordinates**: the server cannot leak
    what it was never told (brief §5.1, §11).
    """

    mood: str = Field(default="chill", max_length=40)
    people: int = Field(default=2, ge=1, le=12)
    transport: str = Field(default="walk", max_length=20)
    max_minutes: int = Field(default=120, ge=20, le=720)
    distance_km: float = Field(default=2.0, ge=0.1, le=60.0)
    category: str = Field(default="other", max_length=40)
    interests: List[str] = Field(default_factory=list, max_length=8)
    food_exclusions: List[str] = Field(default_factory=list, max_length=8)
    players: List[str] = Field(default_factory=list, max_length=12)
    games: List[str] = Field(default_factory=list, max_length=3)
    language: str = Field(default="en", max_length=10)


#: The shape the handheld validates against, described to the provider so a
#: JSON-mode model has something concrete to aim at.
PLAN_COPY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "description", "hints", "games"],
    "properties": {
        "title": {"type": "string", "maxLength": 60},
        "description": {"type": "string", "maxLength": 240},
        "vibe_line": {"type": "string", "maxLength": 140},
        "arrival_line": {"type": "string", "maxLength": 180},
        "agent_greeting": {"type": "string", "maxLength": 140},
        "hints": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["waypoint_idx", "text"],
                "properties": {
                    "waypoint_idx": {"type": "integer", "minimum": 0, "maximum": 24},
                    "text": {"type": "string", "minLength": 6, "maxLength": 140},
                },
            },
        },
        "games": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "tasks"],
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "photo_hunt", "bingo", "color_hunt",
                            "funny_tasks", "secret_missions", "imposter",
                        ],
                    },
                    "title": {"type": "string", "maxLength": 60},
                    "duration_min": {"type": "integer", "minimum": 2, "maximum": 180},
                    "tasks": {
                        "type": "array",
                        "maxItems": 30,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["text"],
                            "properties": {
                                "text": {"type": "string", "minLength": 3, "maxLength": 120},
                                "item_key": {"type": "string", "maxLength": 32},
                                "points": {"type": "integer", "minimum": 1, "maximum": 100},
                            },
                        },
                    },
                },
            },
        },
        "tasks_per_member": {
            "type": "object",
            "additionalProperties": {"type": "array", "items": {"type": "string"}},
        },
    },
}


def _device_id(device: Device) -> str:
    return device.id if device else "unknown"


# --------------------------------------------------------------------------- #
@router.post("/text", response_model=LlmTextResponse)
def llm_text(
    body: LlmTextRequest,
    response: Response,
    device: Device = Depends(get_current_device),
):
    """Free-form text, optionally JSON-schema constrained."""
    try:
        result = gateway.complete(
            prompt=body.prompt,
            system=body.system,
            schema=body.schema_,
            task=body.task,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            device_id=_device_id(device),
            use_cache=body.cache,
        )
    except RateLimited as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "llm_rate_limited",
                "message": "Too many LLM requests. Try again shortly.",
                "retry_after_s": round(exc.retry_after_s, 1),
            },
            headers={"Retry-After": str(int(exc.retry_after_s))},
        )
    except GatewayUnavailable as exc:
        # 503 rather than 500: the handheld's chain is expected to step past this and
        # use its template content. It is a supported outcome, not a crash.
        raise HTTPException(
            status_code=503,
            detail={"error": "llm_unavailable", "message": str(exc)},
            headers={"Retry-After": "30"},
        )

    return LlmTextResponse(
        text=result.text,
        provider=result.provider,
        model=result.model,
        cached=result.cached,
        latency_ms=result.latency_ms,
        attempts=result.attempts,
    )


@router.post("/plan", response_model=LlmTextResponse)
def llm_plan(
    body: LlmPlanRequest,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """One call that returns the creative half of a plan as JSON."""
    player_list = ", ".join(body.players) if body.players else f"{body.people} explorers"
    game_list = ", ".join(body.games) if body.games else "photo_hunt"
    interests = ", ".join(body.interests) if body.interests else "whatever looks good"
    exclusions = ", ".join(body.food_exclusions) if body.food_exclusions else "none"

    prompt = (
        "Write the words for a surprise walking trip. The destination is SECRET.\n\n"
        "Facts you may use:\n"
        f"- mood: {body.mood}\n"
        f"- group: {body.people} people ({player_list})\n"
        f"- how they travel: {body.transport}\n"
        f"- time budget: {body.max_minutes} minutes\n"
        f"- route length: {body.distance_km:.1f} km\n"
        f"- the destination is a {body.category}\n"
        f"- things they like: {interests}\n"
        f"- food to avoid: {exclusions}\n"
        f"- games to include: {game_list}\n"
        f"- language: {body.language}\n\n"
        "Rules, all of them important:\n"
        "1. NEVER name a place, a street, a landmark or a business. Not in any field.\n"
        "2. Hints must be things a person can see, smell or hear on the way, useful\n"
        "   without giving the destination away.\n"
        '3. "hints" is one entry per waypoint, with waypoint_idx counting from 0.\n'
        "4. Game tasks are short, concrete and fun to photograph. Give each one an\n"
        '   "item_key": one lowercase word.\n'
        "5. Keep every field inside its length limit.\n"
        "6. agent_greeting is one friendly sentence, spoken aloud.\n"
        "7. Answer with JSON only."
    )

    try:
        result = gateway.complete(
            prompt=prompt,
            system=DEFAULT_SYSTEM,
            schema=PLAN_COPY_SCHEMA,
            task="plan",
            temperature=0.85,
            max_tokens=1800,
            device_id=_device_id(device),
        )
    except RateLimited as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "llm_rate_limited",
                "message": "Too many LLM requests. Try again shortly.",
                "retry_after_s": round(exc.retry_after_s, 1),
            },
            headers={"Retry-After": str(int(exc.retry_after_s))},
        )
    except GatewayUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "llm_unavailable", "message": str(exc)},
            headers={"Retry-After": "30"},
        )

    return LlmTextResponse(
        text=result.text,
        provider=result.provider,
        model=result.model,
        cached=result.cached,
        latency_ms=result.latency_ms,
        attempts=result.attempts,
    )


@router.get("/status")
def llm_status(device: Device = Depends(get_current_device)):
    """Which providers the server can actually reach, and how the cache is doing."""
    return gateway.status()
