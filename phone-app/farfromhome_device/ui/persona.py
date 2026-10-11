"""The agent persona (how the handheld talks) and push-to-talk voice input.

Two parts:

* :class:`Persona` — the personality layer. It turns engine events into short
  sentences and publishes ``agent.say``. *"Keep personality friendly, short and
  funny"* (brief §5.3). The persona never invents facts: every line is built from a
  bus payload, so it cannot name the destination before the reveal, because the
  reveal payload is the only place a name exists.
* :class:`VoiceInput` — push-to-talk. Records through the HAL and transcribes with
  local STT when one is available; when it is not, the button-based path is the
  fallback the brief requires.

Text-to-speech goes through the HAL speaker (Piper on the Pi), so this module never
imports a TTS library.
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..core.bus import Bus
from ..core.config import Settings
from ..hal.base import HardwareBundle

logger = logging.getLogger("ffh.ui.persona")


# --------------------------------------------------------------------------- #
# Lines
# --------------------------------------------------------------------------- #
GREETINGS = (
    "Right, let us go somewhere. Follow the arrow.",
    "Off we go. I know where, you do not.",
    "Arrow up. Mystery intact.",
)

START_LINES = (
    "The arrow knows the way. I am just the voice.",
    "Keep it steady. We are not in a hurry.",
    "If you get lost, that is technically exploring.",
)

GPS_LOST_LINES = (
    "Losing the satellites. Hold still a moment.",
    "No signal. The arrow is holding its breath.",
)

GPS_BACK_LINES = (
    "Found you again. Carry on.",
    "Signal is back. Off we go.",
)

OFF_ROUTE_LINES = (
    "You have wandered off the line. No rush, the arrow still points.",
    "A small detour. I approve, quietly.",
)

ARRIVAL_LINES = (
    "You found it. Take a good look around.",
    "Here it is. This is the place.",
)

WRAP_LINES = (
    "That is a trip. Let us make something to remember it by.",
    "Done and dusted. Time for the collage.",
)

CALIBRATION_LINES = (
    "Wave me in a figure of eight for a moment - north is confused.",
    "Give the compass a figure of eight, it is getting dizzy.",
)

GAME_LINES = (
    "Game on. Uploads are open.",
    "Everyone in? Then let us begin.",
)


@dataclass
class PersonaMemory:
    spoken: List[str] = field(default_factory=list)
    max_lines: int = 60

    def remember(self, line: str) -> None:
        self.spoken.append(line)
        del self.spoken[: -self.max_lines]

    def repeat_last(self) -> str:
        return self.spoken[-1] if self.spoken else "I have not said anything yet."


class Persona:
    """Subscribes to the bus and speaks."""

    def __init__(
        self,
        settings: Settings,
        bus: Bus,
        hardware: Optional[HardwareBundle] = None,
        seed: Optional[int] = None,
    ) -> None:
        self.settings = settings
        self.bus = bus
        self.hardware = hardware
        self.memory = PersonaMemory()
        self._random = random.Random(seed)
        self._unsubscribers: List[Any] = []
        self._last_said_at = 0.0
        self._greeted = False
        self.spoken_count = 0

    # ------------------------------------------------------------------ #
    def attach(self) -> None:
        topics = (
            "trip.started", "trip.arrived", "trip.ended", "sensor.gps_lost",
            "sensor.gps_back", "off_route", "game.started", "heading.update",
            "reveal.due", "sos.triggered",
        )
        for topic in topics:
            self._unsubscribers.append(self.bus.subscribe(topic, self._on_message))
        logger.debug("persona attached to %d topics", len(topics))

    def detach(self) -> None:
        for unsubscribe in self._unsubscribers:
            try:
                unsubscribe()
            except Exception:
                pass
        self._unsubscribers.clear()

    # ------------------------------------------------------------------ #
    def _on_message(self, message: Any) -> None:
        topic = message.topic
        payload = message.payload or {}

        if topic == "trip.started":
            self.say(self._pick(START_LINES))
        elif topic == "trip.arrived":
            self.say(self._pick(ARRIVAL_LINES))
        elif topic == "trip.ended":
            self.say(self._pick(WRAP_LINES))
        elif topic == "sensor.gps_lost":
            self.say(self._pick(GPS_LOST_LINES))
        elif topic == "sensor.gps_back":
            self.say(self._pick(GPS_BACK_LINES))
        elif topic == "off_route":
            self.say(str(payload.get("text") or self._pick(OFF_ROUTE_LINES)))
        elif topic == "game.started":
            self.say(self._pick(GAME_LINES))
        elif topic == "reveal.due":
            text = str(payload.get("text") or "")
            if text:
                self.say(text)
        elif topic == "heading.update":
            if payload.get("needs_calibration") and self._throttle(12.0):
                self.say(self._pick(CALIBRATION_LINES))
        elif topic == "sos.triggered":
            self.say("SOS sent. Stay where you are and breathe.")

    # ------------------------------------------------------------------ #
    def greet(self) -> str:
        if self._greeted:
            return self.memory.repeat_last()
        self._greeted = True
        return self.say(self._pick(GREETINGS))

    def say(self, text: str, mood: str = "calm") -> str:
        """Say a line: publish ``agent.say`` and, if enabled, speak it aloud."""
        line = " ".join(str(text).split())
        if not line:
            return ""
        self.memory.remember(line)
        self.spoken_count += 1
        self.bus.publish("agent.say", {"text": line, "mood": mood}, source="persona")
        if self.settings.speech_enabled and self.hardware is not None:
            speaker = self.hardware.speaker
            if speaker.available or speaker.tts_available:
                # Never block the caller: Piper on a Pi takes a moment to start.
                threading.Thread(
                    target=self._speak, args=(speaker, line), daemon=True, name="persona-tts"
                ).start()
        return line

    @staticmethod
    def _speak(speaker: Any, line: str) -> None:
        try:
            speaker.say(line)
        except Exception:
            logger.debug("speaking failed", exc_info=True)

    def _pick(self, lines: Sequence[str]) -> str:
        return self._random.choice(list(lines))

    def _throttle(self, gap_s: float) -> bool:
        now = time.monotonic()
        if now - self._last_said_at < gap_s:
            return False
        self._last_said_at = now
        return True

    def status(self) -> Dict[str, Any]:
        return {
            "spoken_count": self.spoken_count,
            "last_line": self.memory.repeat_last(),
            "speech_enabled": self.settings.speech_enabled,
            "tts_available": bool(self.hardware and self.hardware.speaker.tts_available),
        }


# --------------------------------------------------------------------------- #
# Voice input
# --------------------------------------------------------------------------- #
class VoiceInput:
    """Push-to-talk: record through the HAL, transcribe locally when possible.

    The brief (§5.3) asks for local STT with "a small model (Whisper tiny/base or
    Vosk), with an optional cloud fallback", and requires *"Always allow
    typing/button input"*. The model itself is not vendored here (it is hundreds of
    megabytes); :meth:`listen_once` uses, in order:

    1. a callable ``transcriber`` (wired to Vosk/Whisper by the app entry point);
    2. ``Mic.transcribe`` when the HAL driver supports it (``STT_COMMAND``);
    3. the simulator's canned phrases, so the laptop demo works;
    4. ``None``, which the UI turns into "use the buttons".
    """

    def __init__(
        self,
        settings: Settings,
        hardware: Optional[HardwareBundle] = None,
        transcriber: Optional[Callable[[bytes], str]] = None,
        record_seconds: float = 4.0,
    ) -> None:
        self.settings = settings
        self.hardware = hardware
        self.transcriber = transcriber
        self.record_seconds = record_seconds
        self.last_audio_bytes = 0
        self.last_result = ""
        self._recording = False

    @property
    def available(self) -> bool:
        return bool(self.hardware and self.hardware.mic.available)

    def start(self) -> bool:
        if not self.available:
            return False
        self._recording = True
        try:
            self.hardware.mic.push_to_talk_start()
        except Exception:
            logger.debug("push_to_talk_start failed", exc_info=True)
        return True

    async def listen_once(self, seconds: Optional[float] = None) -> str:
        """Record once and return the transcript (``""`` when nothing worked)."""
        if not self.available:
            return ""
        duration = seconds or self.record_seconds
        audio = await asyncio.to_thread(self._record, duration)
        if not audio:
            return ""
        self.last_audio_bytes = len(audio)

        text = ""
        if self.transcriber is not None:
            try:
                text = await asyncio.to_thread(self.transcriber, audio)
            except Exception:
                logger.debug("custom transcriber failed", exc_info=True)
        if not text:
            transcribe = getattr(self.hardware.mic, "transcribe", None)
            if callable(transcribe):
                try:
                    text = await asyncio.to_thread(transcribe, audio)
                except Exception:
                    logger.debug("HAL transcribe failed", exc_info=True)
        if not text:
            next_phrase = getattr(self.hardware.mic, "next_canned_phrase", None)
            if callable(next_phrase):
                # The simulator: proves the whole voice path with no model installed.
                text = str(next_phrase())
        self.last_result = text
        logger.info("voice input: %r (%d bytes)", text, len(audio))
        return text

    def stop(self) -> Optional[bytes]:
        self._recording = False
        if not self.available:
            return None
        try:
            return self.hardware.mic.push_to_talk_stop()
        except Exception:
            return None

    def _record(self, seconds: float) -> Optional[bytes]:
        try:
            return self.hardware.mic.record(seconds)  # type: ignore[union-attr]
        except Exception:
            logger.exception("recording failed")
            return None

    @property
    def recording(self) -> bool:
        return self._recording

    def status(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "recording": self._recording,
            "last_result": self.last_result,
            "last_audio_bytes": self.last_audio_bytes,
            "record_seconds": self.record_seconds,
            "custom_transcriber": self.transcriber is not None,
        }


def build_transcriber(model_path: str = "", engine: str = "auto") -> Optional[Callable[[bytes], str]]:
    """Wire up Vosk or Whisper **if they happen to be installed**.

    Returns ``None`` when neither is present, which is the normal case on a laptop
    and is not an error: the caller falls back to buttons.
    """
    engine = (engine or "auto").strip().lower()

    if engine in ("auto", "vosk"):
        try:
            import json
            import wave
            from io import BytesIO

            from vosk import KaldiRecognizer, Model  # type: ignore

            if not model_path:
                logger.info("Vosk is installed but no model path was given; voice input is off")
            else:
                model = Model(model_path)

                def vosk_transcribe(wav_bytes: bytes) -> str:
                    with wave.open(BytesIO(wav_bytes), "rb") as handle:
                        recogniser = KaldiRecognizer(model, handle.getframerate())
                        recogniser.SetWords(False)
                        output = ""
                        while True:
                            chunk = handle.readframes(4000)
                            if not chunk:
                                break
                            if recogniser.AcceptWaveform(chunk):
                                output += " " + json.loads(recogniser.Result()).get("text", "")
                        output += " " + json.loads(recogniser.FinalResult()).get("text", "")
                    return output.strip()

                logger.info("voice input: Vosk with model %s", model_path)
                return vosk_transcribe
        except ImportError:
            if engine == "vosk":
                logger.warning("Vosk was requested but is not installed")
        except Exception:
            logger.exception("could not initialise Vosk")

    if engine in ("auto", "whisper"):
        try:
            import io
            import wave

            import whisper  # type: ignore

            model = whisper.load_model(model_path or "tiny")

            def whisper_transcribe(wav_bytes: bytes) -> str:
                import numpy as np

                with wave.open(io.BytesIO(wav_bytes), "rb") as handle:
                    frames = handle.readframes(handle.getnframes())
                    rate = handle.getframerate()
                audio = np.frombuffer(frames, dtype=np.int16).astype("float32") / 32768.0
                if rate != 16000:
                    # Whisper wants 16 kHz; a cheap resample is fine for speech.
                    ratio = 16000 / float(rate)
                    indices = (np.arange(int(len(audio) * ratio)) / ratio).astype("int32")
                    audio = audio[np.clip(indices, 0, len(audio) - 1)]
                return str(model.transcribe(audio, language="en", fp16=False).get("text", "")).strip()

            logger.info("voice input: whisper %s", model_path or "tiny")
            return whisper_transcribe
        except ImportError:
            if engine == "whisper":
                logger.warning("Whisper was requested but is not installed")
        except Exception:
            logger.exception("could not initialise Whisper")

    logger.info("no local speech-to-text engine available; buttons are the fallback")
    return None
