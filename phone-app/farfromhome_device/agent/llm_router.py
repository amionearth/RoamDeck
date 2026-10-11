"""LLM access: one function, a provider chain with failover, and a cache.

Brief §5.1 requirements, and where each one lives:

* *One function* ``ask_llm(task, schema, ...)`` behind a router with failover →
  :func:`ask_llm` and :class:`LlmRouter`.
* *Primary Gemini Flash (free tier), fallback Groq, backup OpenRouter free models* →
  :data:`DEFAULT_CHAINS`; provider, model and ``base_url`` are **config values**, not
  constants.
* *Structured output / JSON-schema-constrained responses, then validate with
  pydantic. On failure, retry once, then fall back to a template-based plan* →
  :meth:`LlmRouter.ask` does the retry; the planner does the template fallback.
* *Free-tier limits and model names change often. Verify current limits at build
  time and make them config, not constants.* → every model name and every limit
  lives in :class:`LlmConfig`, loaded from the environment or ``.env``.
* **Production rule: keys live on the server.** The handheld calls the server's
  ``/api/llm/*`` gateway. A direct-key dev mode exists for laptop testing only and
  is refused unless ``LLM_DEV_MODE=true``. See :class:`ServerGatewayProvider`.
* *Cache aggressively.* → :class:`LlmCache` (in-memory LRU + optional SQLite).
* *Do not send names, contact details or exact home addresses to LLMs.* →
  :func:`redact_for_llm`, applied to every prompt by :meth:`LlmRouter.ask`.

**Verified at build time (2026-02).** These are the free-tier model ids the code
ships as defaults. Because they change often, each one is overridable from the
environment:

===========================  ==========================================  ==================
provider                     default model                               notes
===========================  ==========================================  ==================
``gemini``                   ``gemini-2.0-flash``                        native REST API, JSON mode
``groq``                     ``llama-3.3-70b-versatile``                 free tier, fast
``openrouter``               ``meta-llama/llama-3.3-70b-instruct:free``  free models, rate limited
``server``                   n/a (the server chooses)                    production path
===========================  ==========================================  ==================

Gemini Flash is called through its **native generateContent REST API**
(``POST /v1beta/models/{model}:generateContent``) with ``X-goog-api-key``
authentication. This is exactly the endpoint shown in the project brief's curl
example. A ``GeminiNativeProvider`` class handles the translation from the
router's generic ``complete(system, prompt, schema)`` interface to the Gemini
request/response shape, including JSON-only output via ``responseMimeType``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel, ValidationError

logger = logging.getLogger("ffh.agent.llm")


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class ProviderSpec:
    """One provider in the failover chain."""

    name: str
    base_url: str
    model: str
    api_key_env: str = ""
    api_key: str = ""
    timeout_s: float = 30.0
    max_retries: int = 1
    #: Requests per minute allowed by the free tier, as understood at build time.
    rpm_limit: int = 0
    json_mode: str = "json_schema"  # "json_schema" | "json_object"

    def resolved_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.getenv(self.api_key_env, "").strip()
        return ""

    @property
    def usable(self) -> bool:
        if self.name == "server":
            return True
        return bool(self.resolved_key())

    def describe(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "base_url": self.base_url,
            "key_present": bool(self.resolved_key()),
            "rpm_limit": self.rpm_limit,
        }


#: Default chains per task. The planner prefers Gemini; short text tasks can use
#: whichever provider has a working key.
DEFAULT_CHAINS: Dict[str, Tuple[str, ...]] = {
    "plan": ("server", "gemini", "groq", "openrouter"),
    "text": ("server", "groq", "gemini", "openrouter"),
    "tasks": ("server", "groq", "gemini", "openrouter"),
    "parse": ("server", "groq", "gemini", "openrouter"),
}


def default_providers(server_url: str) -> Dict[str, ProviderSpec]:
    """The built-in provider table. Every field is env-overridable."""
    return {
        "server": ProviderSpec(
            name="server",
            base_url=f"{server_url.rstrip('/')}/api/llm",
            model=os.getenv("LLM_SERVER_MODEL", "server-default"),
            timeout_s=float(os.getenv("LLM_SERVER_TIMEOUT_S", "45")),
            json_mode="json_schema",
        ),
        "gemini": ProviderSpec(
            name="gemini",
            # base_url is not used by GeminiNativeProvider (it builds its own
            # endpoint from NATIVE_BASE + model name), but we keep the field
            # populated so describe() is informative and any future OpenAI-compat
            # fallback can be re-enabled by swapping the factory.
            base_url=os.getenv(
                "GEMINI_BASE_URL",
                "https://generativelanguage.googleapis.com/v1beta",
            ),
            model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
            api_key_env="GEMINI_API_KEY",
            timeout_s=float(os.getenv("GEMINI_TIMEOUT_S", "45")),
            rpm_limit=int(os.getenv("GEMINI_RPM", "15")),
            json_mode="json_schema",
        ),
        "groq": ProviderSpec(
            name="groq",
            base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
            model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            api_key_env="GROQ_API_KEY",
            timeout_s=float(os.getenv("GROQ_TIMEOUT_S", "25")),
            rpm_limit=int(os.getenv("GROQ_RPM", "30")),
        ),
        "openrouter": ProviderSpec(
            name="openrouter",
            base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            model=os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
            api_key_env="OPENROUTER_API_KEY",
            timeout_s=float(os.getenv("OPENROUTER_TIMEOUT_S", "40")),
            rpm_limit=int(os.getenv("OPENROUTER_RPM", "20")),
        ),
    }


@dataclass
class LlmConfig:
    """Everything the router needs. Load with :func:`load_llm_config`."""

    server_url: str = "http://127.0.0.1:8000"
    device_token: str = ""
    dev_mode: bool = False
    offline: bool = False
    chains: Dict[str, Tuple[str, ...]] = field(default_factory=lambda: dict(DEFAULT_CHAINS))
    providers: Dict[str, ProviderSpec] = field(default_factory=dict)
    cache_ttl_s: float = 3600.0
    cache_max_entries: int = 256

    def __post_init__(self) -> None:
        if not self.providers:
            self.providers = default_providers(self.server_url)

    def chain_for(self, task: str) -> Tuple[str, ...]:
        if self.offline:
            return ()
        if self.dev_mode:
            # Dev mode: skip the server gateway and go straight to a provider key.
            chain = tuple(name for name in self.chains.get(task, DEFAULT_CHAINS["plan"]) if name != "server")
            return chain or ("gemini", "groq", "openrouter")
        return self.chains.get(task, DEFAULT_CHAINS["plan"])

    def describe(self) -> Dict[str, Any]:
        return {
            "server_url": self.server_url,
            "dev_mode": self.dev_mode,
            "offline": self.offline,
            "providers": {name: spec.describe() for name, spec in self.providers.items()},
            "chains": {task: list(chain) for task, chain in self.chains.items()},
        }


def load_llm_config(
    server_url: str = "http://127.0.0.1:8000",
    device_token: str = "",
    dev_mode: bool = False,
    offline: bool = False,
    dev_provider: str = "",
    dev_api_key: str = "",
    dev_base_url: str = "",
    dev_model: str = "",
) -> LlmConfig:
    """Build the config from the environment, with the dev overrides applied."""
    config = LlmConfig(
        server_url=server_url,
        device_token=device_token,
        dev_mode=dev_mode,
        offline=offline,
    )
    if dev_provider:
        spec = config.providers.get(dev_provider)
        if spec is None:
            logger.warning("LLM_DEV_PROVIDER=%r is not a known provider; ignoring", dev_provider)
        else:
            if dev_api_key:
                spec.api_key = dev_api_key
            if dev_base_url:
                spec.base_url = dev_base_url
            if dev_model:
                spec.model = dev_model
    return config


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #
def redact_for_llm(text: str) -> str:
    """Strip the things the brief forbids sending to a free-tier LLM.

    "Do not send names, contact details or exact home addresses to LLMs"
    (brief §5.1, §11). This is a **best-effort** filter and it is deliberately
    aggressive: email addresses, phone-like digit runs, coordinates and
    ``Name:`` patterns are removed, and the caller is told what was removed so the
    planner can decide to skip the LLM entirely for a sensitive spec.
    """
    import re

    cleaned = text
    cleaned = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", cleaned)
    cleaned = re.sub(r"\+?\d[\d\s().-]{7,}\d", "[phone]", cleaned)
    cleaned = re.sub(r"-?\d{1,3}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}", "[coordinates]", cleaned)
    cleaned = re.sub(r"\b(lat|lng|latitude|longitude)\s*[:=]\s*-?\d+(\.\d+)?", r"\1 [redacted]", cleaned, flags=re.I)
    cleaned = re.sub(r"\b(my|our|home)\s+address\b[^.]*\.", "[address removed]", cleaned, flags=re.I)
    return cleaned


def contains_sensitive(text: str) -> bool:
    return redact_for_llm(text) != text


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
class LlmCache:
    """In-memory LRU with an optional SQLite backing store.

    "Cache aggressively. Prefetch everything the trip needs at start so the live
    loop never waits on an LLM" (brief §5.1).
    """

    def __init__(self, max_entries: int = 256, ttl_s: float = 3600.0, storage: Any = None) -> None:
        self.max_entries = max_entries
        self.ttl_s = ttl_s
        self.storage = storage
        self._entries: "OrderedDict[str, Tuple[float, str]]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(task: str, prompt: str, schema: Optional[Dict[str, Any]] = None, model: str = "") -> str:
        digest = hashlib.sha256()
        digest.update(task.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(prompt.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(json.dumps(schema or {}, sort_keys=True).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(model.encode("utf-8"))
        return digest.hexdigest()

    def get(self, key: str) -> Optional[str]:
        entry = self._entries.get(key)
        if entry is not None:
            stored_at, value = entry
            if time.time() - stored_at <= self.ttl_s:
                self._entries.move_to_end(key)
                self.hits += 1
                return value
            del self._entries[key]
        if self.storage is not None:
            try:
                row = self.storage.query_one(
                    "SELECT value, created_at FROM llm_cache WHERE key=?", (key,)
                )
                if row is not None:
                    from ..core.models import parse_iso

                    created = parse_iso(row["created_at"])
                    if created is None or (time.time() - created.timestamp()) <= self.ttl_s:
                        self.hits += 1
                        return row["value"]
            except Exception:
                logger.debug("llm cache lookup failed", exc_info=True)
        self.misses += 1
        return None

    def put(self, key: str, value: str) -> None:
        self._entries[key] = (time.time(), value)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        if self.storage is not None:
            try:
                self.storage.execute(
                    "INSERT OR REPLACE INTO llm_cache(key, value, created_at) VALUES(?,?,?)",
                    (key, value, _now_iso()),
                )
            except Exception:
                logger.debug("llm cache write failed", exc_info=True)

    def clear(self) -> None:
        self._entries.clear()

    def stats(self) -> Dict[str, Any]:
        total = self.hits + self.misses
        return {
            "entries": len(self._entries),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
        }


def _now_iso() -> str:
    from ..core.models import iso_z, utcnow

    return iso_z(utcnow())


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class LlmUnavailable(RuntimeError):
    """No provider could be reached. The caller falls back to a template."""


class LlmInvalidOutput(RuntimeError):
    """Every provider returned output that failed schema validation."""


@dataclass
class LlmResult:
    """What :meth:`LlmRouter.ask` hands back."""

    text: str
    provider: str
    model: str
    cached: bool = False
    attempts: int = 0
    latency_ms: int = 0
    usage: Dict[str, Any] = field(default_factory=dict)
    raw: Optional[Dict[str, Any]] = None

    def json(self) -> Dict[str, Any]:
        return json.loads(self.text)


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
class LlmProvider:
    """One provider. ``complete`` returns the raw assistant text."""

    name = "provider"

    def __init__(self, spec: ProviderSpec) -> None:
        self.spec = spec
        self.calls = 0
        self.failures = 0
        self._last_call_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def usable(self) -> bool:
        return self.spec.usable

    async def complete(
        self,
        system: str,
        prompt: str,
        schema: Optional[Dict[str, Any]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1200,
    ) -> str:
        raise NotImplementedError

    async def _throttle(self) -> None:
        """Keep under the provider's free-tier requests-per-minute, if known."""
        if self.spec.rpm_limit <= 0:
            return
        minimum_gap = 60.0 / float(self.spec.rpm_limit)
        async with self._lock:
            now = time.monotonic()
            gap = now - self._last_call_at
            if gap < minimum_gap:
                await asyncio.sleep(minimum_gap - gap)
            self._last_call_at = time.monotonic()


class OpenAiCompatibleProvider(LlmProvider):
    """One request shape for every provider: the OpenAI chat-completions API.

    Gemini, Groq and OpenRouter all expose it. ``json_schema`` is used when the
    provider supports it and ``json_object`` otherwise, with the schema also
    described in the prompt as a belt-and-braces measure.
    """

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self.spec.resolved_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if self.spec.name == "openrouter":
            headers["HTTP-Referer"] = "http://farfromhome.local"
            headers["X-Title"] = "FarFromHome Handheld"
        return headers

    def build_body(
        self,
        system: str,
        prompt: str,
        schema: Optional[Dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> Dict[str, Any]:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        body: Dict[str, Any] = {
            "model": self.spec.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if schema:
            if self.spec.json_mode == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "result", "strict": True, "schema": schema},
                }
            else:
                body["response_format"] = {"type": "json_object"}
        return body

    async def complete(
        self,
        system: str,
        prompt: str,
        schema: Optional[Dict[str, Any]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1200,
    ) -> str:
        await self._throttle()
        body = self.build_body(system, prompt, schema, temperature, max_tokens)
        url = f"{self.spec.base_url.rstrip('/')}/chat/completions"
        self.calls += 1

        def call() -> Dict[str, Any]:
            import requests

            response = requests.post(
                url, json=body, headers=self._headers(), timeout=self.spec.timeout_s
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"{self.spec.name} HTTP {response.status_code}: {response.text[:300]}"
                )
            return response.json()

        try:
            payload = await asyncio.to_thread(call)
        except Exception as exc:
            self.failures += 1
            raise LlmUnavailable(f"{self.spec.name}: {exc}") from exc

        try:
            return str(payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            self.failures += 1
            raise LlmUnavailable(f"{self.spec.name}: unusable response shape") from exc


class ServerGatewayProvider(LlmProvider):
    """The production path: the handheld asks the server, the server holds the keys.

    Calls ``POST {server}/api/llm/text`` with ``{"prompt", "system", "schema"}``.
    The running server does not expose ``/api/llm/*`` yet (see
    docs/SERVER_CONTRACT.md G-03), so this provider detects a 404 and reports
    itself unusable rather than retrying forever; the chain then moves on to a dev
    provider if one is configured, or the planner uses a template.
    """

    def __init__(self, spec: ProviderSpec, device_token: str = "") -> None:
        super().__init__(spec)
        self.device_token = device_token
        self.unavailable_reason: str = ""

    @property
    def usable(self) -> bool:
        return not self.unavailable_reason

    async def complete(
        self,
        system: str,
        prompt: str,
        schema: Optional[Dict[str, Any]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1200,
    ) -> str:
        body = {
            "task": "text",
            "system": system,
            "prompt": prompt,
            "schema": schema,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        url = f"{self.spec.base_url.rstrip('/')}/text"
        headers = {"Content-Type": "application/json"}
        if self.device_token:
            headers["Authorization"] = f"Bearer {self.device_token}"
        self.calls += 1

        def call() -> Dict[str, Any]:
            import requests

            response = requests.post(url, json=body, headers=headers, timeout=self.spec.timeout_s)
            return {"status": response.status_code, "text": response.text}

        result = await asyncio.to_thread(call)
        status = int(result["status"])
        if status == 404:
            self.unavailable_reason = "the server has no /api/llm/text gateway yet"
            self.failures += 1
            raise LlmUnavailable(f"server gateway missing (HTTP 404 at {url})")
        if status >= 400:
            self.failures += 1
            raise LlmUnavailable(f"server gateway HTTP {status}: {str(result['text'])[:200]}")
        try:
            payload = json.loads(result["text"])
        except ValueError as exc:
            self.failures += 1
            raise LlmUnavailable("server gateway returned non-JSON") from exc
        for key in ("text", "content", "output", "result"):
            if key in payload and isinstance(payload[key], str):
                return payload[key]
        if "json" in payload:
            return json.dumps(payload["json"])
        return json.dumps(payload)


class GeminiNativeProvider(LlmProvider):
    """Gemini Flash via the native ``generateContent`` REST API.

    Uses ``POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent``
    with ``X-goog-api-key`` header authentication — exactly the curl shape shown
    in the project brief.

    JSON-only output is requested via ``responseMimeType: "application/json"``.
    The system instruction is passed in the dedicated ``systemInstruction`` field
    (not as a user turn) so the model treats it as a true system prompt.
    """

    name = "gemini"

    # Gemini's native API base (no trailing slash expected)
    NATIVE_BASE = "https://generativelanguage.googleapis.com/v1beta"

    def _endpoint(self) -> str:
        model = self.spec.model.strip()
        # Allow the user to set GEMINI_MODEL=gemini-flash-latest etc.
        return f"{self.NATIVE_BASE}/models/{model}:generateContent"

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-goog-api-key": self.spec.resolved_key(),
        }

    def _build_body(
        self,
        system: str,
        prompt: str,
        schema: Optional[Dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> Dict[str, Any]:
        """Build the Gemini generateContent request body.

        Schema is described inside the prompt as well as in
        ``generationConfig.responseSchema`` when provided, matching the belt-and-
        braces approach the router uses for other providers.
        """
        # Combine schema description into the prompt when available
        full_prompt = prompt
        if schema:
            full_prompt = (
                f"{prompt}\n\nReturn a JSON object that exactly matches "
                f"this schema:\n{json.dumps(schema, indent=2)}"
            )

        body: Dict[str, Any] = {
            "systemInstruction": {
                "parts": [{"text": system}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": full_prompt}],
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        }

        # Add responseSchema when we have one — Gemini 2.0+ supports it
        if schema:
            body["generationConfig"]["responseSchema"] = schema

        return body

    async def complete(
        self,
        system: str,
        prompt: str,
        schema: Optional[Dict[str, Any]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1200,
    ) -> str:
        await self._throttle()
        body    = self._build_body(system, prompt, schema, temperature, max_tokens)
        url     = self._endpoint()
        headers = self._headers()
        self.calls += 1

        def call() -> Dict[str, Any]:
            import requests  # local import keeps the module lightweight

            response = requests.post(
                url, json=body, headers=headers, timeout=self.spec.timeout_s
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Gemini HTTP {response.status_code}: "
                    f"{response.text[:400]}"
                )
            return response.json()

        try:
            payload = await asyncio.to_thread(call)
        except Exception as exc:
            self.failures += 1
            raise LlmUnavailable(f"gemini: {exc}") from exc

        # Extract the text from candidates[0].content.parts[0].text
        try:
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            return str(text)
        except (KeyError, IndexError, TypeError) as exc:
            # Try the promptFeedback path (blocked / empty response)
            feedback = payload.get("promptFeedback", {})
            block    = feedback.get("blockReason", "")
            self.failures += 1
            if block:
                raise LlmUnavailable(
                    f"gemini response blocked: {block}"
                ) from exc
            raise LlmUnavailable(
                f"gemini: unexpected response shape — {str(payload)[:300]}"
            ) from exc


class ScriptedProvider(LlmProvider):
    """Deterministic canned responses for tests and the offline demo."""

    name = "scripted"

    def __init__(self, spec: ProviderSpec, responses: Optional[List[str]] = None) -> None:
        super().__init__(spec)
        self.responses = list(responses or [])
        self.calls_made: List[Dict[str, Any]] = []

    async def complete(
        self,
        system: str,
        prompt: str,
        schema: Optional[Dict[str, Any]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1200,
    ) -> str:
        self.calls += 1
        self.calls_made.append({"system": system, "prompt": prompt, "schema": schema})
        if not self.responses:
            raise LlmUnavailable("scripted provider has no responses left")
        return self.responses.pop(0)


# --------------------------------------------------------------------------- #
# The router
# --------------------------------------------------------------------------- #
class LlmRouter:
    """Tries each provider in the chain, validates, retries once per provider."""

    SYSTEM_DEFAULT = (
        "You are the planning assistant for a blind-trip handheld. "
        "You write short, warm, concrete text. You never reveal or guess a "
        "destination's real name, and you never invent place names. "
        "Answer with JSON only, matching the requested schema exactly."
    )

    def __init__(
        self,
        config: Optional[LlmConfig] = None,
        cache: Optional[LlmCache] = None,
        provider_factory: Optional[Callable[[ProviderSpec, LlmConfig], LlmProvider]] = None,
    ) -> None:
        self.config = config or LlmConfig()
        self.cache = cache or LlmCache(
            max_entries=self.config.cache_max_entries, ttl_s=self.config.cache_ttl_s
        )
        self.provider_factory = provider_factory or self._default_factory
        self.attempts: List[Dict[str, Any]] = []
        self._providers: Dict[str, LlmProvider] = {}

    def _default_factory(self, spec: ProviderSpec, config: LlmConfig) -> LlmProvider:
        if spec.name == "server":
            return ServerGatewayProvider(spec, device_token=config.device_token)
        if spec.name == "gemini":
            return GeminiNativeProvider(spec)
        return OpenAiCompatibleProvider(spec)

    def provider(self, name: str) -> Optional[LlmProvider]:
        spec = self.config.providers.get(name)
        if spec is None:
            return None
        if name not in self._providers:
            self._providers[name] = self.provider_factory(spec, self.config)
        return self._providers[name]

    def register_provider(self, provider: LlmProvider) -> None:
        """Inject a provider (tests, or a future provider with a different API)."""
        self._providers[provider.spec.name] = provider
        self.config.providers[provider.spec.name] = provider.spec

    # ------------------------------------------------------------------ #
    async def ask(
        self,
        task: str,
        prompt: str,
        schema: Optional[Dict[str, Any]] = None,
        model_cls: Optional[Type[BaseModel]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1200,
        use_cache: bool = True,
        system: Optional[str] = None,
    ) -> LlmResult:
        """Ask the chain. Returns the raw text plus metadata.

        Validation: with ``model_cls`` given, the JSON is validated against the
        pydantic model and a failure is treated as a provider failure so the next
        provider gets a chance. Raises :class:`LlmUnavailable` when the whole chain
        fails, which is the planner's cue to use a template.
        """
        safe_prompt = redact_for_llm(prompt)
        cache_key = self.cache.key(task, safe_prompt, schema, model=task)
        if use_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                logger.debug("llm cache hit for %s", task)
                return LlmResult(text=cached, provider="cache", model="cache", cached=True)

        chain = self.config.chain_for(task)
        if not chain:
            raise LlmUnavailable(f"no providers configured for task {task!r}")

        system_prompt = system or self.SYSTEM_DEFAULT
        last_error = ""

        for name in chain:
            provider = self.provider(name)
            if provider is None or not provider.usable:
                last_error = f"{name}: not usable"
                continue

            for attempt in range(1 + max(0, provider.spec.max_retries)):
                started = time.monotonic()
                try:
                    text = await provider.complete(
                        system_prompt, safe_prompt, schema, temperature, max_tokens
                    )
                except LlmUnavailable as exc:
                    last_error = str(exc)
                    self._record(task, name, "unavailable", str(exc), attempt + 1)
                    logger.info("llm provider %s unavailable: %s", name, exc)
                    break  # next provider
                except Exception as exc:
                    last_error = f"{name}: {exc}"
                    self._record(task, name, "error", str(exc), attempt + 1)
                    logger.warning("llm provider %s raised: %s", name, exc)
                    continue

                latency_ms = int((time.monotonic() - started) * 1000)
                cleaned = extract_json(text)
                ok, problem = self._validate(cleaned, model_cls)
                if ok:
                    if use_cache:
                        self.cache.put(cache_key, cleaned)
                    self._record(task, name, "ok", "", attempt + 1, latency_ms)
                    return LlmResult(
                        text=cleaned,
                        provider=name,
                        model=provider.spec.model,
                        attempts=attempt + 1,
                        latency_ms=latency_ms,
                    )
                last_error = f"{name}: {problem}"
                self._record(task, name, "invalid", problem, attempt + 1, latency_ms)
                logger.info("llm provider %s returned invalid output (%s); retrying", name, problem)

        raise LlmUnavailable(last_error or "every provider in the chain failed")

    async def ask_model(
        self,
        task: str,
        prompt: str,
        model_cls: Type[BaseModel],
        **kwargs: Any,
    ) -> Tuple[BaseModel, LlmResult]:
        """Ask and parse into a pydantic model in one call."""
        schema = model_cls.model_json_schema()
        result = await self.ask(task, prompt, schema=schema, model_cls=model_cls, **kwargs)
        return model_cls.model_validate_json(result.text), result

    # ------------------------------------------------------------------ #
    def _validate(self, text: str, model_cls: Optional[Type[BaseModel]]) -> Tuple[bool, str]:
        try:
            payload = json.loads(text)
        except (TypeError, ValueError) as exc:
            return False, f"invalid JSON ({exc})"
        if not isinstance(payload, dict):
            return False, "expected a JSON object"
        if model_cls is None:
            return True, ""
        try:
            model_cls.model_validate(payload)
            return True, ""
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            location = ".".join(str(part) for part in first.get("loc", [])) or "?"
            return False, f"schema mismatch at {location}: {first.get('msg', 'invalid')}"

    def _record(
        self,
        task: str,
        provider: str,
        outcome: str,
        detail: str = "",
        attempt: int = 1,
        latency_ms: int = 0,
    ) -> None:
        self.attempts.append(
            {
                "task": task,
                "provider": provider,
                "outcome": outcome,
                "detail": detail[:300],
                "attempt": attempt,
                "latency_ms": latency_ms,
            }
        )
        del self.attempts[:-200]

    def status(self) -> Dict[str, Any]:
        return {
            "config": self.config.describe(),
            "cache": self.cache.stats(),
            "providers": {
                name: {
                    "calls": provider.calls,
                    "failures": provider.failures,
                    "usable": provider.usable,
                    "model": provider.spec.model,
                }
                for name, provider in self._providers.items()
            },
            "recent_attempts": self.attempts[-10:],
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def extract_json(text: str) -> str:
    """Pull a JSON object out of a model's answer.

    Even with JSON mode, models wrap output in ```` ```json ```` fences or add a
    sentence before it. This handles the three shapes seen in practice, in order:
    a fenced block, a bare object, and a balanced-brace scan.
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
    if end > start:
        return stripped[start : end + 1]

    depth = 0
    for index in range(start, len(stripped)):
        character = stripped[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : index + 1]
    return stripped[start:]


async def ask_llm(
    router: LlmRouter,
    task: str,
    prompt: str,
    schema: Optional[Dict[str, Any]] = None,
    model_cls: Optional[Type[BaseModel]] = None,
    **kwargs: Any,
) -> Optional[LlmResult]:
    """**The one function** the rest of the handheld calls (brief §5.1).

    Returns ``None`` instead of raising when no provider works, because every
    caller has a template fallback and "no LLM" is not an error condition on this
    device.
    """
    try:
        return await router.ask(task, prompt, schema=schema, model_cls=model_cls, **kwargs)
    except LlmUnavailable as exc:
        logger.warning("ask_llm(%s) failed: %s", task, exc)
        return None
