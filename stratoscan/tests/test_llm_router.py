"""Tests for the multi-provider LLM router (src/llm_router.py).

These are pure unit tests — every OpenAI call is mocked, so they never
touch the network or burn API quota. They verify:
  1. Round-robin across multiple keys within a provider
  2. Instant failover: transient error on provider A -> served by provider B
  3. Hard errors (401/402/403/404) get a long cooldown so they're skipped fast
  4. A provider whose keys all fail is skipped on the next request
  5. build_providers() falls back to legacy single-provider config

Run: /usr/local/bin/python3.14 -m tests.test_llm_router
"""

import os
import sys
from unittest import mock

sys.path.insert(0, ".")

from src.llm_router import (  # noqa: E402
    HARD_COOLDOWN,
    ProviderEndpoint,
    LLMRouter,
    build_providers,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


class FakeResponse:
    """Minimal stand-in for the OpenAI response object."""

    def __init__(self, text: str = "OK"):
        self._text = text
        self.choices = [mock.Mock()]
        self.choices[0].message.content = text


def make_error(status: int, name: str = "APIStatusError"):
    """Build an exception that looks like an OpenAI SDK error."""
    if name == "RateLimitError":
        cls = __import__("openai").RateLimitError
    elif name == "APITimeoutError":
        cls = __import__("openai").APITimeoutError
    else:
        cls = __import__("openai").APIStatusError
    # APIStatusError requires a response-like object
    err = cls(
        message=f"HTTP {status} error",
        response=mock.Mock(status_code=status),
        body={"error": {"message": "boom", "type": "error"}},
    )
    return err


def make_providers(names: list, keys=None) -> list:
    """Build simple provider endpoints for tests."""
    key_pool = keys or ["k1", "k2"]
    return [
        ProviderEndpoint(
            name=name,
            base_url=f"https://{name}.example/v1/",
            model="test-model",
            keys=list(key_pool),
            timeout=5.0,
        )
        for name in names
    ]


def make_router(providers) -> LLMRouter:
    router = LLMRouter(providers)
    # Patch _get_client so calls are fully mocked (no real OpenAI client)
    def fake_client(provider, key):
        client = mock.Mock()
        client.chat.completions.create = provider._fake_create
        return client

    router._get_client = mock.Mock(side_effect=fake_client)
    return router


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_round_robin_across_keys():
    """Two keys on one provider should alternate, not reuse the same key."""
    providers = make_providers(["a"])
    used = []

    def create(messages, model=None, temperature=0.1, max_tokens=2000, **kw):
        # The router calls _get_client(provider, key) — capture the key via mock
        used.append(providers[0]._current_key)
        return FakeResponse()

    providers[0]._fake_create = create
    router = make_router(providers)

    # Monkeypatch _get_client to record which key was handed out
    real_get = router._get_client

    def get_and_record(provider, key):
        provider._current_key = key
        return real_get(provider, key)

    router._get_client = get_and_record

    for _ in range(4):
        router.chat_completion(messages=[{"role": "user", "content": "hi"}])

    # Round-robin: k1, k2, k1, k2
    assert used == ["k1", "k2", "k1", "k2"], f"expected round-robin, got {used}"


def test_failover_to_second_provider():
    """If provider A throws a transient error, provider B serves the call."""
    # Single key per provider so a failure = exactly one attempt on A.
    providers = make_providers(["a", "b"], keys=["k1"])
    calls = {"a": 0, "b": 0}

    def create_a(messages, **kw):
        calls["a"] += 1
        raise make_error(429, "RateLimitError")

    def create_b(messages, **kw):
        calls["b"] += 1
        return FakeResponse("SERVED_BY_B")

    providers[0]._fake_create = create_a
    providers[1]._fake_create = create_b
    router = make_router(providers)

    # transient_retries=0 keeps this test focused on failover (no retry noise)
    resp = router.chat_completion(
        messages=[{"role": "user", "content": "hi"}], transient_retries=0
    )

    assert calls == {"a": 1, "b": 1}, f"expected one attempt each, got {calls}"
    assert resp.choices[0].message.content == "SERVED_BY_B"
    assert router.last_used[0] == "b"
    assert router.total_failovers == 1


def test_hard_error_gets_long_cooldown():
    """A 402 (no credit) should cool the key down for HARD_COOLDOWN seconds."""
    providers = make_providers(["a"])

    def create_a(messages, **kw):
        raise make_error(402)

    providers[0]._fake_create = create_a
    router = make_router(providers)

    with mock.patch("src.llm_router.time.time", return_value=1_000_000):
        try:
            router.chat_completion(messages=[{"role": "user", "content": "hi"}])
            assert False, "should have raised"
        except RuntimeError:
            pass

    with router._lock:
        until = router._cooldown_until[("a", "k1")]
    assert until >= 1_000_000 + HARD_COOLDOWN - 1, f"cooldown too short: {until}"


def test_cooled_provider_is_skipped_fast():
    """After all keys fail, the whole provider is cooled and skipped next call."""
    providers = make_providers(["a", "b"], keys=["k1"])

    def create_a(messages, **kw):
        raise make_error(500)

    def create_b(messages, **kw):
        return FakeResponse("OK")

    providers[0]._fake_create = create_a
    providers[1]._fake_create = create_b
    router = make_router(providers)

    router.chat_completion(
        messages=[{"role": "user", "content": "hi"}], transient_retries=0
    )
    calls_before = router.total_attempts

    # Second call: provider A is in cooldown, so B should serve it directly
    # with ZERO attempts on A (no wasted latency).
    router.chat_completion(messages=[{"role": "user", "content": "hi"}])

    assert router.total_attempts == calls_before, (
        "provider A should have been skipped entirely on the second call"
    )
    assert router.total_failovers == 1, "only the first call should fail over"


def test_all_providers_fail_raises():
    """If every provider fails, chat_completion raises RuntimeError."""
    providers = make_providers(["a", "b"], keys=["k1"])

    def create_a(messages, **kw):
        raise make_error(429, "RateLimitError")

    def create_b(messages, **kw):
        raise make_error(503)

    providers[0]._fake_create = create_a
    providers[1]._fake_create = create_b
    router = make_router(providers)

    try:
        router.chat_completion(
            messages=[{"role": "user", "content": "hi"}], transient_retries=0
        )
        assert False, "should have raised RuntimeError"
    except RuntimeError as e:
        assert "All LLM providers failed" in str(e)


def test_transient_retry_recovers_without_failover():
    """A single 429 that clears on retry should NOT fail over or fail the screen."""
    providers = make_providers(["a", "b"], keys=["k1"])
    calls = {"a": 0, "b": 0}

    def flaky_a(messages, **kw):
        calls["a"] += 1
        if calls["a"] == 1:
            raise make_error(429, "RateLimitError")
        return FakeResponse("RECOVERED")

    def create_b(messages, **kw):
        calls["b"] += 1
        return FakeResponse("B")

    providers[0]._fake_create = flaky_a
    providers[1]._fake_create = create_b
    router = make_router(providers)

    with mock.patch("src.llm_router.time.sleep"):  # don't actually wait 1.5s
        resp = router.chat_completion(messages=[{"role": "user", "content": "hi"}])

    assert calls == {"a": 2, "b": 0}, f"expected retry on A only, got {calls}"
    assert resp.choices[0].message.content == "RECOVERED"
    assert router.total_failovers == 0, "no failover should have happened"
    assert router.last_used[0] == "a"


def test_prefer_provider_tried_first():
    """prefer='b' should try provider B first, but still fall through to A."""
    providers = make_providers(["a", "b"], keys=["k1"])
    calls = {"a": 0, "b": 0}

    def create_a(messages, **kw):
        calls["a"] += 1
        return FakeResponse("A")

    def create_b(messages, **kw):
        calls["b"] += 1
        raise make_error(429, "RateLimitError")

    providers[0]._fake_create = create_a
    providers[1]._fake_create = create_b
    router = make_router(providers)

    resp = router.chat_completion(
        messages=[{"role": "user", "content": "hi"}],
        transient_retries=0,
        prefer="b",  # B is preferred but failing -> falls through to A
    )

    assert calls == {"a": 1, "b": 1}, f"expected B then A, got {calls}"
    assert resp.choices[0].message.content == "A"
    assert router.last_used[0] == "a", "failed-over from preferred B to A"


def test_prefer_provider_success_does_not_use_others():
    """When the preferred provider serves, no other provider is touched."""
    providers = make_providers(["a", "b"], keys=["k1"])
    calls = {"a": 0, "b": 0}

    def create_a(messages, **kw):
        calls["a"] += 1
        return FakeResponse("A")

    def create_b(messages, **kw):
        calls["b"] += 1
        return FakeResponse("B_FAST")

    providers[0]._fake_create = create_a
    providers[1]._fake_create = create_b
    router = make_router(providers)

    resp = router.chat_completion(
        messages=[{"role": "user", "content": "hi"}], prefer="b"
    )

    assert calls == {"a": 0, "b": 1}, f"expected B only, got {calls}"
    assert resp.choices[0].message.content == "B_FAST"
    assert router.last_used[0] == "b"


def test_build_providers_legacy_fallback():
    """With no LLM_PROVIDERS, build_providers uses the legacy single-provider vars."""
    with mock.patch("src.llm_router.settings") as fake_settings:
        fake_settings.llm_providers = ""
        fake_settings.screener_llm_key = "legacy-key"
        fake_settings.llm_base_url = "https://legacy.example/v1/"
        fake_settings.llm_model = "legacy-model"
        fake_settings.llm_provider_timeout = 45.0

        providers = build_providers()

    assert len(providers) == 1
    assert providers[0].name == "legacy"
    assert providers[0].keys == ["legacy-key"]
    assert providers[0].model == "legacy-model"


def test_build_providers_per_provider_timeout():
    """<NAME>_TIMEOUT env overrides the global llm_provider_timeout per provider.

    Regression for the "timeouts too short" fix: providers must be able to
    carry their own (longer) per-call timeout, with a sane fallback when the
    override is absent or malformed.
    """
    with mock.patch("src.llm_router.settings") as fake_settings, mock.patch.dict(
        os.environ,
        {
            "FAST_BASE_URL": "https://fast.example/v1/",
            "FAST_MODEL": "m",
            "FAST_KEYS": "k1",
            "FAST_TIMEOUT": "30",
            "SLOW_BASE_URL": "https://slow.example/v1/",
            "SLOW_MODEL": "m",
            "SLOW_KEYS": "k2",
        },
        clear=False,
    ):
        # build_providers reads the provider LIST from settings, provider
        # VARS from the environment — set both sides.
        fake_settings.llm_providers = "fast,slow"
        fake_settings.llm_provider_timeout = 120.0
        fake_settings.screener_llm_key = None
        fake_settings.llm_base_url = None
        fake_settings.llm_model = None

        providers = build_providers()

    by_name = {p.name: p for p in providers}
    assert by_name["fast"].timeout == 30.0, "FAST_TIMEOUT override not applied"
    assert by_name["slow"].timeout == 120.0, "global default not applied when no override"

    # Malformed override falls back to the global default, doesn't crash
    with mock.patch("src.llm_router.settings") as fake_settings, mock.patch.dict(
        os.environ,
        {
            "WEIRD_BASE_URL": "https://w.example/v1/",
            "WEIRD_MODEL": "m",
            "WEIRD_KEYS": "k1",
            "WEIRD_TIMEOUT": "not-a-number",
        },
        clear=False,
    ):
        fake_settings.llm_providers = "weird"
        fake_settings.llm_provider_timeout = 120.0
        fake_settings.screener_llm_key = None
        fake_settings.llm_base_url = None
        fake_settings.llm_model = None
        providers = build_providers()

    assert providers[0].name == "weird"
    assert providers[0].timeout == 120.0


# ── Runner ────────────────────────────────────────────────────────────────────


def main():
    tests = [
        ("round-robin across keys", test_round_robin_across_keys),
        ("failover to second provider", test_failover_to_second_provider),
        ("hard error gets long cooldown", test_hard_error_gets_long_cooldown),
        ("cooled provider is skipped fast", test_cooled_provider_is_skipped_fast),
        ("all providers fail raises", test_all_providers_fail_raises),
        ("transient retry recovers", test_transient_retry_recovers_without_failover),
        ("legacy fallback", test_build_providers_legacy_fallback),
        ("per-provider timeout override", test_build_providers_per_provider_timeout),
        ("prefer provider tried first", test_prefer_provider_tried_first),
        ("prefer provider success skips others", test_prefer_provider_success_does_not_use_others),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ❌ {name}: {e}")
    print()
    if failed:
        print(f"❌ {failed} router test(s) FAILED")
        sys.exit(1)
    print("🎯 All LLM router tests pass!")


if __name__ == "__main__":
    main()
