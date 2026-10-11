"""Device-facing LLM gateway with provider failover.

Brief 01 §5.1's production rule: **"LLM keys live on the server. The Pi calls the
server's ``/llm`` gateway."** This module is that gateway. The handheld never holds
a provider key; it asks the server for text and the server holds the secrets, applies
the rate limit, caches, and logs usage.

The chain is the one the brief names: **Gemini Flash (free tier) → Groq → OpenRouter
free models**, all over the OpenAI-compatible chat-completions shape so one request
builder serves every provider. Model ids and base URLs are configuration, not
constants, because free tiers change (brief: *"Verify current limits at build time
and make them config, not constants"*).

Nothing here needs an API key to work: with no keys configured the gateway returns a
clear 503 and the handheld falls back to its template content, which is a supported
outcome rather than an error.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ffh.services.llm_gateway")


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
@dataclass
class GatewayProvider:
    """One provider in the failover chain."""

    name: str
    base_url: str
    model: str
    api_key_env: str
    timeout_s: float = 30.0
    max_tokens_ceiling: int = 4096

    def api_key(self) -> str:
        return os.getenv(self.api_key_env, "").strip()

    @property
    def usable(self) -> bool:
        return bool(self.api_key())

    def describe(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "base_url": self.base_url,
            "key_present": bool(self.api_key()),
        }


def default_chain() -> List[GatewayProvider]:
    """Gemini Flash, then Groq, then OpenRouter. Every field env-overridable."""
    return [
        GatewayProvider(
            name="gemini",
            base_url=os.getenv(
                "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
            ),
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            api_key_env="GEMINI_API_KEY",
            timeout_s=float(os.getenv("GEMINI_TIMEOUT_S", "30")),
        ),
        GatewayProvider(
            name="groq",
            base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
            model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            api_key_env="GROQ_API_KEY",
            timeout_s=float(os.getenv("GROQ_TIMEOUT_S", "25")),
        ),
        GatewayProvider(
            name="openrouter",
            base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            model=os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
            api_key_env="OPENROUTER_API_KEY",
            timeout_s=float(os.getenv("OPENROUTER_TIMEOUT_S", "40")),
        ),
    ]


#: Requests per minute the gateway will serve, per device and in total. Free tiers are
#: small; this is what stops one handheld from burning the day's quota.
DEVICE_RPM = int(os.getenv("LLM_DEVICE_RPM", "20"))
GLOBAL_RPM = int(os.getenv("LLM_GLOBAL_RPM", "60"))

#: How long a cached answer stays good.
CACHE_TTL_S = float(os.getenv("LLM_CACHE_TTL_S", "3600"))


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #
class GatewayUnavailable(RuntimeError):
    """No provider could answer. The caller should fall back to a template."""


class RateLimited(RuntimeError):
    """Too many requests. Carries the number of seconds to wait."""

    def __init__(self, retry_after_s: float) -> None:
        super().__init__("rate limited")
        self.retry_after_s = retry_after_s


# --------------------------------------------------------------------------- #
# caching and rate limiting (in-process; SQLite is available through the caller)
# --------------------------------------------------------------------------- #
class _Window:
    """A fixed-window counter. Simple, and honest about what it does."""

    def __init__(self, limit: int, window_s: float = 60.0) -> None:
        self.limit = max(1, limit)
        self.window_s = window_s
        self._counts: Dict[str, Tuple[int, float]] = {}

    def check(self, key: str) -> Optional[float]:
        """Return ``None`` when allowed, or the seconds to wait when not."""
        now = time.time()
        count, started = self._counts.get(key, (0, now))
        if now - started >= self.window_s:
            return None
        if count >= self.limit:
            return max(1.0, self.window_s - (now - started))
        return None

    def hit(self, key: str) -> None:
        now = time.time()
        count, started = self._counts.get(key, (0, now))
        if now - started >= self.window_s:
            self._counts[key] = (1, now)
        else:
            self._counts[key] = (count + 1, started)


class _Cache:
    """A bounded in-memory cache keyed on the whole request."""

    def __init__(self, max_entries: int = 256, ttl_s: float = CACHE_TTL_S) -> None:
        self.max_entries = max_entries
        self.ttl_s = ttl_s
        self._entries: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(prompt: str, system: str, schema: Optional[Dict[str, Any]], task: str) -> str:
        digest = hashlib.sha256()
        for part in (task, system, prompt, json.dumps(schema or {}, sort_keys=True)):
            digest.update(part.encode("utf-8"))
            digest.update(b"\x00")
        return digest.hexdigest()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        stored_at, value = entry
        if time.time() - stored_at > self.ttl_s:
            del self._entries[key]
            self.misses += 1
            return None
        self.hits += 1
        return value

    def put(self, key: str, value: Dict[str, Any]) -> None:
        if len(self._entries) >= self.max_entries:
            oldest = min(self._entries.items(), key=lambda item: item[1][0])[0]
            self._entries.pop(oldest, None)
        self._entries[key] = (time.time(), value)

    def stats(self) -> Dict[str, Any]:
        total = self.hits + self.misses
        return {
            "entries": len(self._entries),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
        }

    def clear(self) -> None:
        self._entries.clear()


# --------------------------------------------------------------------------- #
# the gateway
# --------------------------------------------------------------------------- #
DEFAULT_SYSTEM = (
    "You are the planning assistant for a blind-trip handheld. "
    "You write short, warm, concrete text. You never reveal or guess a destination's "
    "real name, and you never invent place names. Answer with JSON only, matching the "
    "requested schema exactly."
)


@dataclass
class GatewayResult:
    text: str
    provider: str
    model: str
    cached: bool = False
    latency_ms: int = 0
    attempts: List[Dict[str, Any]] = field(default_factory=list)


class LlmGateway:
    """Tries each provider in the chain and caches what worked."""

    def __init__(
        self,
        chain: Optional[List[GatewayProvider]] = None,
        cache: Optional[_Cache] = None,
        device_rpm: int = DEVICE_RPM,
        global_rpm: int = GLOBAL_RPM,
    ) -> None:
        self.chain = chain if chain is not None else default_chain()
        self.cache = cache or _Cache()
        self.device_window = _Window(device_rpm)
        self.global_window = _Window(global_rpm)
        self.usage: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    @property
    def configured(self) -> bool:
        return any(provider.usable for provider in self.chain)

    def check_rate_limit(self, device_id: str) -> None:
        wait = self.global_window.check("__all__")
        if wait is not None:
            raise RateLimited(wait)
        wait = self.device_window.check(device_id or "unknown")
        if wait is not None:
            raise RateLimited(wait)

    def _record_usage(self, device_id: str, task: str, provider: str, outcome: str,
                      latency_ms: int = 0) -> None:
        self.usage.append(
            {
                "at": time.time(),
                "device_id": device_id,
                "task": task,
                "provider": provider,
                "outcome": outcome,
                "latency_ms": latency_ms,
            }
        )
        del self.usage[:-500]

    # ------------------------------------------------------------------ #
    def complete(
        self,
        prompt: str,
        system: str = "",
        schema: Optional[Dict[str, Any]] = None,
        task: str = "text",
        temperature: float = 0.7,
        max_tokens: int = 1200,
        device_id: str = "",
        use_cache: bool = True,
    ) -> GatewayResult:
        """Ask the chain. Raises :class:`GatewayUnavailable` when nothing answers."""
        self.check_rate_limit(device_id)

        system_prompt = system or DEFAULT_SYSTEM
        cache_key = self.cache.key(prompt, system_prompt, schema, task)
        if use_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                self._record_usage(device_id, task, "cache", "hit")
                return GatewayResult(
                    text=str(cached.get("text", "")),
                    provider="cache",
                    model="cache",
                    cached=True,
                    attempts=cached.get("attempts", []),
                )

        if not self.configured:
            raise GatewayUnavailable(
                "No LLM provider is configured on the server. Set GEMINI_API_KEY, "
                "GROQ_API_KEY or OPENROUTER_API_KEY in the server's .env."
            )

        attempts: List[Dict[str, Any]] = []
        last_error = ""

        for provider in self.chain:
            if not provider.usable:
                attempts.append({"provider": provider.name, "outcome": "no_key"})
                continue

            started = time.monotonic()
            try:
                text = self._call(provider, system_prompt, prompt, schema, temperature, max_tokens)
            except GatewayUnavailable as exc:
                last_error = str(exc)
                attempts.append({"provider": provider.name, "outcome": "unavailable", "detail": str(exc)[:200]})
                self._record_usage(device_id, task, provider.name, "unavailable")
                continue
            except Exception as exc:  # noqa: BLE001 - a provider bug must not be fatal
                last_error = f"{provider.name}: {exc}"
                attempts.append({"provider": provider.name, "outcome": "error", "detail": str(exc)[:200]})
                self._record_usage(device_id, task, provider.name, "error")
                continue

            latency_ms = int((time.monotonic() - started) * 1000)
            cleaned = extract_json(text) if schema else text
            attempts.append({"provider": provider.name, "outcome": "ok", "latency_ms": latency_ms})
            self._record_usage(device_id, task, provider.name, "ok", latency_ms)

            self.device_window.hit(device_id or "unknown")
            self.global_window.hit("__all__")

            if use_cache:
                self.cache.put(cache_key, {"text": cleaned, "attempts": attempts})

            return GatewayResult(
                text=cleaned,
                provider=provider.name,
                model=provider.model,
                latency_ms=latency_ms,
                attempts=attempts,
            )

        raise GatewayUnavailable(last_error or "every provider in the chain failed")

    # ------------------------------------------------------------------ #
    def _call(
        self,
        provider: GatewayProvider,
        system: str,
        prompt: str,
        schema: Optional[Dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        import requests

        body: Dict[str, Any] = {
            "model": provider.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": max(0.0, min(2.0, temperature)),
            "max_tokens": max(16, min(provider.max_tokens_ceiling, max_tokens)),
        }
        if schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "strict": True, "schema": schema},
            }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {provider.api_key()}",
        }
        if provider.name == "openrouter":
            headers["HTTP-Referer"] = "http://farfromhome.local"
            headers["X-Title"] = "FarFromHome Server"

        url = f"{provider.base_url.rstrip('/')}/chat/completions"
        try:
            response = requests.post(url, json=body, headers=headers, timeout=provider.timeout_s)
        except requests.RequestException as exc:
            raise GatewayUnavailable(f"{provider.name}: {exc}") from exc

        if response.status_code == 429:
            raise GatewayUnavailable(f"{provider.name}: rate limited upstream (429)")
        if response.status_code >= 400:
            raise GatewayUnavailable(
                f"{provider.name}: HTTP {response.status_code} {response.text[:200]}"
            )

        try:
            payload = response.json()
            return str(payload["choices"][0]["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise GatewayUnavailable(f"{provider.name}: unusable response") from exc

    # ------------------------------------------------------------------ #
    def status(self) -> Dict[str, Any]:
        return {
            "configured": self.configured,
            "providers": [provider.describe() for provider in self.chain],
            "cache": self.cache.stats(),
            "limits": {"device_rpm": self.device_window.limit, "global_rpm": self.global_window.limit},
            "recent_usage": self.usage[-20:],
        }


def extract_json(text: str) -> str:
    """Pull a JSON object out of a model's answer.

    JSON mode usually works; when it does not, models wrap the object in a fenced
    block or add a sentence. This handles the three shapes seen in practice.
    """
    if not text:
        return ""
    stripped = text.strip()
    if "```" in stripped:
        for marker in ("```json", "```JSON", "```"):
            if marker in stripped:
                _, _, rest = stripped.partition(marker)
                body, _, _ = rest.partition("```")
                candidate = body.strip()
                if candidate.startswith("{"):
                    return candidate
    start = stripped.find("{")
    if start == -1:
        return stripped
    end = stripped.rfind("}")
    return stripped[start : end + 1] if end > start else stripped[start:]


#: One gateway per process.
gateway = LlmGateway()
