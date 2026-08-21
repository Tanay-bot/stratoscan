"""LLM Provider Router — multi-provider, multi-key instant failover.

Stratoscan's "never let the AI fail" layer. Instead of a single OpenAI
client pointed at one provider, the router holds an ordered list of
provider endpoints, each with a pool of API keys (round-robin), and
automatically fails over to the next available provider/key the instant
one errors — so a rate limit, outage, or dead key never blocks a screen.

Design:
    - Providers are tried in priority order (first = primary).
    - Keys within a provider are round-robin'd.
    - On failure, the offending (provider, key) pair enters a cooldown:
        * transient failures (429/5xx/timeout): short cooldown (15s)
        * hard failures (401/402/403/404 — bad key, no credit, bad model):
          long cooldown (10 min) — retrying them is pointless
    - A provider whose keys all fail is cooled down as a unit, so the
      router skips it fast on the next request instead of burning latency.
    - Thread-safe: round-robin counters and cooldowns are lock-guarded,
      so concurrent /screen requests can share the router safely.

Configuration lives in ``.env`` (see ``LLM_PROVIDERS``):
    LLM_PROVIDERS="zai,zenmux,gemini"          # priority order
    ZAI_BASE_URL="https://api.z.ai/api/paas/v4/"
    ZAI_MODEL="glm-4.5-flash"
    ZAI_KEYS="key1,key2,key3"
    ZENMUX_BASE_URL="https://zenmux.ai/api/v1"
    ZENMUX_MODEL="z-ai/glm-4.7-flash-free"
    ZENMUX_KEYS="key1"

For backward compatibility, if ``LLM_PROVIDERS`` is empty the router
builds a single provider from the legacy ``LLM_BASE_URL`` / ``LLM_MODEL``
/ ``SCREENER_LLM_KEY`` settings.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI
from openai import APIStatusError, APITimeoutError

from src.config import settings

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

SHORT_COOLDOWN = 15.0      # seconds to pause a provider/key after a transient error
HARD_COOLDOWN = 10 * 60.0  # seconds to pause after a hard error (bad key / no credit / bad model)

# HTTP statuses treated as "hard" — retrying soon is pointless
_HARD_STATUSES = {400, 401, 402, 403, 404}


@dataclass
class ProviderEndpoint:
    """A single provider: base URL, model, and a pool of API keys."""

    name: str
    base_url: str
    model: str
    keys: List[str]
    timeout: float = 120.0  # per-call timeout; see settings.llm_provider_timeout

    def __post_init__(self) -> None:
        if not self.keys:
            raise ValueError(f"Provider '{self.name}' has no API keys configured")

    @property
    def display_name(self) -> str:
        """Short label for logs: e.g. ``zai[0]`` or ``zai[1]``."""
        return f"{self.name}"

    def key_hint(self, key: str) -> str:
        """Mask a key for logs (show first 6 chars only)."""
        return f"{key[:6]}...{key[-4:]}"


class LLMRouter:
    """Tries providers in priority order; fails over instantly on error.

    Thread-safe. ``chat_completion`` is the only public entry point the
    interpreter needs.
    """

    def __init__(self, providers: List[ProviderEndpoint]) -> None:
        self.providers: List[ProviderEndpoint] = providers
        self._rr_index: Dict[str, int] = {}            # provider.name -> next key index
        self._cooldown_until: Dict[Tuple[str, str], float] = {}  # (name, key) -> epoch
        self._provider_cooldown: Dict[str, float] = {}           # name -> epoch
        self._lock = threading.Lock()
        self._clients: Dict[Tuple[str, str], OpenAI] = {}        # (base_url, key) -> client
        # Observability
        self.last_used: Optional[Tuple[str, str]] = None  # (name, key_hint)
        self.total_attempts = 0
        self.total_failovers = 0

    # ── Public API ──────────────────────────────────────────────────────────

    def chat_completion(self, *, messages: List[Dict[str, Any]], model: Optional[str] = None,
                        temperature: float = 0.1, max_tokens: int = 2000,
                        transient_retries: int = 1, transient_backoff: float = 1.5,
                        prefer: Optional[str] = None,
                        **kwargs: Any):
        """Send a chat completion through the first available provider.

        Args:
            messages: OpenAI-format message list.
            model: Override the provider's configured model (usually None).
            temperature: Sampling temperature.
            max_tokens: Output token budget (must cover reasoning tokens).
            transient_retries: How many times to retry the SAME key on a
                transient error (429/5xx/timeout) before cooling it and
                failing over. A single momentary 429 then can't fail a screen.
            transient_backoff: Seconds to wait between transient retries.
            prefer: Optional provider name to try FIRST for this call (e.g.
                background batch jobs that want the fastest provider rather
                than the primary). Falls through to the normal priority
                order if the preferred provider is cooling or fails — the
                "never fail" guarantee is unchanged.
            **kwargs: Passed through to ``chat.completions.create``.

        Returns:
            The OpenAI response object.

        Raises:
            RuntimeError: If every provider/key failed (last error attached).
        """
        if not self.providers:
            raise RuntimeError("No LLM providers configured")

        # Reorder providers for this call: preferred first, others after
        # in their original priority order. Pure reordering — cooldowns,
        # key rotation and failover logic are all unchanged.
        ordered = list(self.providers)
        if prefer:
            ordered = [p for p in ordered if p.name == prefer] + \
                      [p for p in ordered if p.name != prefer]

        last_error: Optional[Exception] = None

        for provider in ordered:
            if self._provider_is_cooling(provider.name):
                logger.debug("Skipping provider %s (in cooldown)", provider.display_name)
                continue

            now = time.time()
            max_cooldown = SHORT_COOLDOWN  # worst cooldown among this provider's keys
            for key in self._next_keys(provider):
                if self._is_cooling(provider.name, key):
                    # All keys may be cooling from a prior round — respect the
                    # longest existing key cooldown so the provider isn't
                    # pointlessly re-scanned every SHORT_COOLDOWN seconds.
                    with self._lock:
                        key_until = self._cooldown_until.get((provider.name, key), 0.0)
                    if key_until > now:
                        max_cooldown = max(max_cooldown, key_until - now)
                    continue
                remaining_retries = transient_retries
                while True:
                    try:
                        # Build the client inside the try so a construction
                        # error (e.g. malformed base_url in .env) is also
                        # caught and routed through cooldown + failover.
                        client = self._get_client(provider, key)
                        response = client.chat.completions.create(
                            model=model or provider.model,
                            messages=messages,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            **kwargs,
                        )
                        self._on_success(provider, key)
                        with self._lock:
                            self.last_used = (provider.name, provider.key_hint(key))
                        logger.debug(
                            "LLM call served by %s (%s)",
                            provider.display_name,
                            provider.key_hint(key),
                        )
                        return response
                    except Exception as e:  # noqa: BLE001
                        last_error = e
                        with self._lock:
                            self.total_attempts += 1
                        if self._is_transient(e) and remaining_retries > 0:
                            remaining_retries -= 1
                            logger.info(
                                "Transient %s on %s key %s — retrying in %.1fs",
                                _error_summary(e),
                                provider.display_name,
                                provider.key_hint(key),
                                transient_backoff,
                            )
                            time.sleep(transient_backoff)
                            continue
                        with self._lock:
                            self.total_failovers += 1
                        cooldown = self._classify_cooldown(e)
                        self._set_cooldown(provider.name, key, cooldown)
                        max_cooldown = max(max_cooldown, cooldown)
                        logger.warning(
                            "LLM provider %s key %s failed (%s) — cooling %.0fs, trying next",
                            provider.display_name,
                            provider.key_hint(key),
                            _error_summary(e),
                            cooldown,
                        )
                        break

            # All keys for this provider failed — cool the whole provider for
            # the worst key cooldown (e.g. a hard 402 no-credit key cools the
            # provider for the full 600s) so the next request skips it entirely
            # instead of pointlessly re-scanning keys every few seconds.
            with self._lock:
                self._provider_cooldown[provider.name] = time.time() + max_cooldown

        raise RuntimeError(
            f"All LLM providers failed. Last error: {_error_summary(last_error)}"
        ) from last_error

    # ── Provider selection helpers ──────────────────────────────────────────

    def _next_keys(self, provider: ProviderEndpoint) -> List[str]:
        """Return the provider's keys rotated so round-robin resumes where it stopped."""
        with self._lock:
            idx = self._rr_index.get(provider.name, 0) % len(provider.keys)
            self._rr_index[provider.name] = (idx + 1) % len(provider.keys)
        return provider.keys[idx:] + provider.keys[:idx]

    def _get_client(self, provider: ProviderEndpoint, key: str) -> OpenAI:
        """Return a cached OpenAI client for (base_url, key), building on demand."""
        cache_key = (provider.base_url, key)
        client = self._clients.get(cache_key)
        if client is None:
            client = OpenAI(
                api_key=key,
                base_url=provider.base_url,
                # max_retries=0: the ROUTER owns retry/failover (transient
                # retry + next-key/provider). A client-level retry would
                # DOUBLE the worst-case latency (2x provider.timeout) on a
                # hung provider before we ever fail over — with timeout=120s
                # that's ~240s per key instead of ~120s.
                max_retries=0,
                timeout=provider.timeout,
            )
            with self._lock:
                self._clients[cache_key] = client
        return client

    def _is_cooling(self, name: str, key: str) -> bool:
        with self._lock:
            until = self._cooldown_until.get((name, key), 0.0)
        return time.time() < until

    def _set_cooldown(self, name: str, key: str, seconds: float) -> None:
        with self._lock:
            self._cooldown_until[(name, key)] = time.time() + seconds

    def _provider_is_cooling(self, name: str) -> bool:
        with self._lock:
            until = self._provider_cooldown.get(name, 0.0)
        return time.time() < until

    def _on_success(self, provider: ProviderEndpoint, key: str) -> None:
        """Reset cooldowns after a success so a healthy key stays in rotation."""
        with self._lock:
            self._cooldown_until.pop((provider.name, key), None)
            self._provider_cooldown.pop(provider.name, None)

    def _is_transient(self, e: Exception) -> bool:
        """True for momentary errors worth a same-key retry (429/5xx/timeout)."""
        if isinstance(e, APITimeoutError):
            return True
        status = _error_status(e)
        if status is None:
            return True  # connection / network errors
        return status == 429 or status >= 500

    def _classify_cooldown(self, e: Exception) -> float:
        """Pick a cooldown length: hard errors cool long, transient errors short.

        Note: AuthenticationError (401) is a subclass of APIStatusError so it's
        already handled by the hard-status branch above.
        """
        status = _error_status(e)
        if status is not None and status in _HARD_STATUSES:
            return HARD_COOLDOWN
        # Transient: 429 / 5xx / timeouts — anything else, keep it short.
        return SHORT_COOLDOWN

    def status(self) -> Dict[str, Any]:
        """Snapshot of the router for /health or debugging."""
        with self._lock:
            providers = []
            for p in self.providers:
                providers.append({
                    "name": p.name,
                    "base_url": p.base_url,
                    "model": p.model,
                    "keys": len(p.keys),
                    "cooling": time.time() < self._provider_cooldown.get(p.name, 0.0),
                })
            return {
                "providers": providers,
                "last_used": self.last_used,
                "total_attempts": self.total_attempts,
                "total_failovers": self.total_failovers,
            }


# ═══════════════════════════════════════════════════════════════════════════════
# Module-level factory
# ═══════════════════════════════════════════════════════════════════════════════


def _parse_keys(raw: str) -> List[str]:
    """Split a comma-separated key list, dropping empties."""
    return [k.strip() for k in raw.split(",") if k.strip()]


def build_providers() -> List[ProviderEndpoint]:
    """Build the provider list from settings / environment.

    Priority: explicit ``LLM_PROVIDERS`` list > legacy single-provider config.
    """
    names = [n.strip() for n in (settings.llm_providers or "").split(",") if n.strip()]

    if not names:
        # Legacy mode: single provider from the old vars
        if settings.screener_llm_key and settings.llm_base_url:
            return [ProviderEndpoint(
                name="legacy",
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                keys=[settings.screener_llm_key],
                timeout=settings.llm_provider_timeout,
            )]
        return []

    providers: List[ProviderEndpoint] = []

    for name in names:
        # Per-provider vars are read from the environment (e.g. ZAI_BASE_URL).
        upper = name.upper().replace("-", "_")
        base_url = os.environ.get(f"{upper}_BASE_URL", "").strip()
        model = os.environ.get(f"{upper}_MODEL", "").strip()
        raw_keys = os.environ.get(f"{upper}_KEYS", "").strip()
        keys = _parse_keys(raw_keys)
        if not base_url or not model:
            logger.warning(
                "LLM provider '%s' is listed in LLM_PROVIDERS but missing "
                "%s_BASE_URL or %s_MODEL — skipping",
                name, upper, upper,
            )
            continue
        if not keys:
            # Fall back to the legacy screener key for the first provider only.
            if settings.screener_llm_key and not providers:
                keys = [settings.screener_llm_key]
                logger.warning(
                    "LLM provider '%s' has no %s_KEYS — falling back to SCREENER_LLM_KEY",
                    name, upper,
                )
            else:
                logger.warning(
                    "LLM provider '%s' has no %s_KEYS and no fallback — skipping",
                    name, upper,
                )
                continue
        # Optional per-provider timeout override (<NAME>_TIMEOUT, e.g.
        # ZAI_TIMEOUT=180). Falls back to the global LLM_PROVIDER_TIMEOUT.
        raw_timeout = os.environ.get(f"{upper}_TIMEOUT", "").strip()
        try:
            provider_timeout = float(raw_timeout) if raw_timeout else settings.llm_provider_timeout
        except ValueError:
            logger.warning(
                "LLM provider '%s' has an invalid %s_TIMEOUT (%r) — using global default",
                name, upper, raw_timeout,
            )
            provider_timeout = settings.llm_provider_timeout
        providers.append(ProviderEndpoint(
            name=name,
            base_url=base_url,
            model=model,
            keys=keys,
            timeout=provider_timeout,
        ))

    # Footgun guard: if LLM_PROVIDERS was non-empty but every listed provider
    # was skipped (missing vars/keys), fall back to the legacy single-provider
    # config so a fresh setup never ends up with zero providers.
    if not providers:
        if settings.screener_llm_key and settings.llm_base_url:
            logger.warning(
                "All configured LLM providers were skipped — falling back to "
                "legacy LLM_BASE_URL / SCREENER_LLM_KEY config"
            )
            return [ProviderEndpoint(
                name="legacy",
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                keys=[settings.screener_llm_key],
                timeout=settings.llm_provider_timeout,
            )]
        logger.warning("No LLM providers could be configured")

    return providers


def get_router() -> LLMRouter:
    """Return the process-wide router singleton (lazy, cached)."""
    global _ROUTER
    if _ROUTER is None:
        _ROUTER = LLMRouter(build_providers())
    return _ROUTER


def reset_router() -> None:
    """Drop the cached router (used by tests to force a rebuild)."""
    global _ROUTER
    _ROUTER = None


_ROUTER: Optional[LLMRouter] = None


# ── Error introspection helpers ────────────────────────────────────────────────


def _error_status(e: Exception) -> Optional[int]:
    """Extract an HTTP status code from an OpenAI SDK error, if present."""
    if isinstance(e, APIStatusError):
        return int(e.status_code)
    return None


def _error_summary(e: Optional[Exception]) -> str:
    """One-line error summary for logs."""
    if e is None:
        return "unknown"
    status = _error_status(e)
    if status is not None and hasattr(e, "message"):
        return f"HTTP {status}: {e.message}"
    if isinstance(e, APITimeoutError):
        return "timeout"
    return f"{type(e).__name__}: {e}"
