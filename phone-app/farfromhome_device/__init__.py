"""FarFromHome handheld device client (Raspberry Pi 5).

Package layout mirrors the four processes in the brief plus shared services:

    core/    config, pydantic models, SQLite, event bus
    hal/     GPS, IMU, Camera, Mic, Speaker, Display, Buttons (+ simulators)
    engine/  deterministic live trip loop (no LLM)
    agent/   planner + LLM router (creative text only)
    ui/      screens, persona, voice
    net/     pairing, outbox, REST client, WebSocket, sync daemon
    games/   game templates and local scoring
    wrapup/  collage + stamp generation
"""

__version__ = "1.0.0"
__product__ = "FarFromHome"
__working_title__ = "BlindTrip"
