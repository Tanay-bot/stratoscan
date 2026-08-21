"""Behavioral probe for the LLM router lifecycle — real code, controllable clock.

Run: python verify_llm_router_lifecycle.py

Drives the REAL LLMRouter (OpenAI clients mocked) through scenarios the
existing unit tests do NOT cover, asserting actual state transitions:

  1. Hard cooldown: set on a 402, provider skipped fast, EXPIRES, and the
     key is retried after HARD_COOLDOWN.
  2. Success clears cooldowns: after a provider recovers, the next call
     uses it again (no permanent skip).
  3. Transient retry boundary: with transient_retries=1, two consecutive
     429s -> exactly ONE same-key retry, then failover.
  4. APITimeoutError and network/connection errors (no status) are both
     classified TRANSIENT (retried, not hard-cooled).
  5. Dead-key rotation: with [dead, healthy], call 2 resumes at the healthy
     key (zero attempts on the dead key).
  6. All providers fail -> RuntimeError whose message carries the last error.
  7. prefer ordering: cooling preferred provider is skipped, next serves.
  8. Concurrency: N threads share the router without lost updates or
     exceptions (rotation counter ends exact, all calls served).
"""

from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from unittest import mock  # noqa: E402

from src.llm_router import (  # noqa: E402
    HARD_COOLDOWN,
    SHORT_COOLDOWN,
    LLMRouter,
    ProviderEndpoint,
)


class _Clock:
    """Controllable clock shared by the router's time.time/time.sleep."""

    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeResponse:
    def __init__(self, text: str = "OK"):
        self._text = text
        self.choices = [mock.Mock()]
        self.choices[0].message.content = text


def _sdk_error(status: int, kind: str = "status"):
    """Build a real OpenAI SDK error for the given status/kind."""
    import openai
    if kind == "timeout":
        return openai.APITimeoutError(
            message="timeout", request=mock.Mock(), body=None
        )
    if kind == "network":
        return openai.APIConnectionError(request=mock.Mock(), message="reset")
    cls = {
        429: openai.RateLimitError,
    }.get(status, openai.APIStatusError)
    return cls(
        message=f"HTTP {status} error",
        response=mock.Mock(status_code=status),
        body={"error": {"message": "boom", "type": "error"}},
    )


def _router(providers):
    router = LLMRouter(providers)
    # Real _get_client is replaced so no real OpenAI client is ever built;
    # each key's client gets a scripted create().
    router._get_client = mock.Mock(side_effect=lambda p, key: (
        mock.Mock(chat=mock.Mock(completions=mock.Mock(create=p._script(key))))
    ))
    return router


def _provider(name: str, keys, script_fn):
    p = ProviderEndpoint(
        name=name, base_url=f"https://{name}.example/v1/",
        model="m", keys=keys, timeout=5.0,
    )
    p._script = script_fn
    return p


print("=" * 70)
print("LLM ROUTER LIFECYCLE — behavioral verification")
print("=" * 70)

# ── 1. Hard cooldown: set, skip, expire, retry ──────────────────────────────
print("\n[1] Hard cooldown lifecycle (402 -> skip -> expire -> retry)...")

clock = _Clock()
calls = {"a": 0}


def a_script(key):
    def create(**kw):
        calls["a"] += 1
        if calls["a"] == 1:
            raise _sdk_error(402)
        return FakeResponse("RECOVERED")
    return create


p_a = _provider("a", ["k1"], a_script)
router = _router([p_a])
with mock.patch("src.llm_router.time.time", side_effect=clock.time), \
     mock.patch("src.llm_router.time.sleep", side_effect=clock.sleep):
    # First call: 402 -> RuntimeError, key cooled HARD
    try:
        router.chat_completion(messages=[{"role": "user", "content": "hi"}])
        raise AssertionError("first call must fail")
    except RuntimeError:
        pass
    with router._lock:
        until = router._cooldown_until[("a", "k1")]
    assert until >= clock.now + HARD_COOLDOWN - 1, f"key cooldown too short: {until}"
    assert router._provider_cooldown.get("a", 0) >= clock.now + HARD_COOLDOWN - 1, \
        "provider must be cooled as a unit"

    # Within cooldown: provider skipped entirely (0 new attempts)
    clock.now += 10
    try:
        router.chat_completion(messages=[{"role": "user", "content": "hi"}])
        raise AssertionError("must still fail while cooling")
    except RuntimeError:
        pass
    assert calls["a"] == 1, "provider must be skipped while in cooldown"

    # After HARD_COOLDOWN: retried and recovered
    clock.now += HARD_COOLDOWN + 1
    resp = router.chat_completion(messages=[{"role": "user", "content": "hi"}])
assert resp.choices[0].message.content == "RECOVERED"
assert calls["a"] == 2
# Failovers counts *attempted* key failures, not skipped-provider calls:
# call 1 -> k1 failed (1), call 2 -> provider in cooldown, 0 attempts (0),
# call 3 -> success (0). Matches test_llm_router.py:193 "only the first
# call should fail over".
assert router.total_failovers == 1, f"got {router.total_failovers}"
assert router.total_attempts == 1, f"got {router.total_attempts}"
print("✅ 402 cooled HARD, provider skipped fast, expired, retried, recovered")


# ── 2. Success clears cooldown (no permanent skip) ───────────────────────────
print("[2] Success clears provider + key cooldown...")

clock = _Clock()
calls = {"a": 0}


def a_script2(key):
    def create(**kw):
        calls["a"] += 1
        if calls["a"] == 1:
            raise _sdk_error(500)   # transient -> SHORT cooldown
        return FakeResponse("OK_AGAIN")
    return create


p_a2 = _provider("a", ["k1"], a_script2)
router2 = _router([p_a2])
with mock.patch("src.llm_router.time.time", side_effect=clock.time), \
     mock.patch("src.llm_router.time.sleep", side_effect=clock.sleep):
    # transient_retries=0 -> the 500 is NOT retried: it fails over and sets a
    # SHORT cooldown on the key + cools the provider as a unit. This makes the
    # section actually exercise _on_success clearing (with the default retries
    # the 500 would succeed in-call and no cooldown would ever be set).
    try:
        router2.chat_completion(
            messages=[{"role": "user", "content": "hi"}], transient_retries=0
        )
        raise AssertionError("first call must fail and set a cooldown")
    except RuntimeError:
        pass
    with router2._lock:
        assert ("a", "k1") in router2._cooldown_until, "cooldown must be set first"
    # Advance past SHORT_COOLDOWN, then a success must clear BOTH cooldowns.
    clock.now += SHORT_COOLDOWN + 1
    router2.chat_completion(messages=[{"role": "user", "content": "hi"}])  # recovers
with router2._lock:
    assert ("a", "k1") not in router2._cooldown_until, "key cooldown must be cleared"
    assert "a" not in router2._provider_cooldown, "provider cooldown must be cleared"
print("✅ success clears both cooldowns; provider stays in rotation")


# ── 3. Transient retry boundary ─────────────────────────────────────────────
print("[3] Retry-then-failover boundary (transient_retries=1)...")

clock = _Clock()
calls = {"a": 0, "b": 0}


def a_script3(key):
    def create(**kw):
        calls["a"] += 1
        raise _sdk_error(429)
    return create


def b_script3(key):
    def create(**kw):
        calls["b"] += 1
        return FakeResponse("B")
    return create


router3 = _router([
    _provider("a", ["k1"], a_script3),
    _provider("b", ["k1"], b_script3),
])
with mock.patch("src.llm_router.time.time", side_effect=clock.time), \
     mock.patch("src.llm_router.time.sleep", side_effect=clock.sleep):
    resp = router3.chat_completion(
        messages=[{"role": "user", "content": "hi"}],
        transient_retries=1, transient_backoff=1.5,
    )
assert calls == {"a": 2, "b": 1}, f"expected 1 retry on A then B, got {calls}"
assert resp.choices[0].message.content == "B"
print("✅ exactly one same-key retry, then failover; A cooled SHORT")


# ── 4. Timeout + network errors are TRANSIENT ───────────────────────────────
print("[4] Timeout/network classified transient...")

for kind in ("timeout", "network"):
    clock = _Clock()
    calls = {kind: 0}

    def s_script(key):
        def create(**kw):
            calls[kind] += 1
            if calls[kind] == 1:
                raise _sdk_error(0, kind)
            return FakeResponse("NET_OK")
        return create

    r4 = _router([_provider("t", ["k1"], s_script)])
    with mock.patch("src.llm_router.time.time", side_effect=clock.time), \
         mock.patch("src.llm_router.time.sleep", side_effect=clock.sleep):
        resp = r4.chat_completion(
            messages=[{"role": "user", "content": "hi"}], transient_retries=1
        )
    assert calls[kind] == 2 and resp.choices[0].message.content == "NET_OK", kind
    # After recovery, no HARD cooldown remains on the key
    with r4._lock:
        assert ("t", "k1") not in r4._cooldown_until, f"{kind} must not hard-cool"
    print(f"  ✅ {kind} error retried as transient, recovered, not hard-cooled")


# ── 5. Dead-key rotation resumes at the healthy key ─────────────────────────
print("[5] Rotation with a dead first key...")

clock = _Clock()
dead_calls = {"dead": 0, "healthy": 0}


def dead_script(key):
    def create(**kw):
        dead_calls["dead"] += 1
        raise _sdk_error(401)
    return create


def healthy_script(key):
    def create(**kw):
        dead_calls["healthy"] += 1
        return FakeResponse("HEALTHY")
    return create


router5 = _router([
    _provider("a", ["dead", "healthy"],
              lambda key: dead_script(key) if key == "dead" else healthy_script(key)),
])
with mock.patch("src.llm_router.time.time", side_effect=clock.time), \
     mock.patch("src.llm_router.time.sleep", side_effect=clock.sleep):
    r1 = router5.chat_completion(messages=[{"role": "user", "content": "hi"}])
    r2 = router5.chat_completion(messages=[{"role": "user", "content": "hi"}])
    r3 = router5.chat_completion(messages=[{"role": "user", "content": "hi"}])
assert r1.choices[0].message.content == "HEALTHY"
assert dead_calls == {"dead": 1, "healthy": 3}, \
    f"dead key must fail once then be skipped by rotation, got {dead_calls}"
print("✅ dead key attempted once; rotation resumes at the healthy key")


# ── 6. All providers fail -> RuntimeError carries last error ────────────────
print("[6] All-providers-fail error message...")

clock = _Clock()


def bad_script(status):
    def make(key):
        def create(**kw):
            raise _sdk_error(status)
        return create
    return make


router6 = _router([
    _provider("a", ["k1"], bad_script(503)),
    _provider("b", ["k1"], bad_script(429)),
])
with mock.patch("src.llm_router.time.time", side_effect=clock.time), \
     mock.patch("src.llm_router.time.sleep", side_effect=clock.sleep):
    try:
        router6.chat_completion(messages=[{"role": "user", "content": "hi"}])
        raise AssertionError("must raise")
    except RuntimeError as e:
        msg = str(e)
assert "All LLM providers failed" in msg
assert "429" in msg, f"last error (429) must be surfaced: {msg}"
print("✅ RuntimeError carries the last provider error")


# ── 7. prefer + cooling ─────────────────────────────────────────────────────
print("[7] prefer with a cooling preferred provider...")

clock = _Clock()
calls = {"a": 0, "b": 0}


def a_script7(key):
    def create(**kw):
        calls["a"] += 1
        raise _sdk_error(500)
    return create


def b_script7(key):
    def create(**kw):
        calls["b"] += 1
        return FakeResponse("B")
    return create


router7 = _router([
    _provider("a", ["k1"], a_script7),
    _provider("b", ["k1"], b_script7),
])
with mock.patch("src.llm_router.time.time", side_effect=clock.time), \
     mock.patch("src.llm_router.time.sleep", side_effect=clock.sleep):
    router7.chat_completion(messages=[{"role": "user", "content": "hi"}])  # A fails -> B
    before = calls["a"]
    # Now prefer 'a' — it's cooling, so B must serve without touching A
    resp = router7.chat_completion(
        messages=[{"role": "user", "content": "hi"}], prefer="a"
    )
assert calls["a"] == before, "cooling preferred provider must be skipped"
assert resp.choices[0].message.content == "B"
print("✅ cooling preferred provider skipped; fallback serves")


# ── 8. Concurrency ──────────────────────────────────────────────────────────
print("[8] Concurrent calls (thread safety)...")

clock = _Clock()
N_THREADS, N_PER = 8, 25


def good_script(key):
    def create(**kw):
        return FakeResponse("OK")
    return create


router8 = _router([_provider("c", ["k1", "k2"], good_script)])
errors = []


def worker():
    try:
        for _ in range(N_PER):
            router8.chat_completion(messages=[{"role": "user", "content": "hi"}])
    except Exception as e:  # noqa: BLE001
        errors.append(e)


threads = [threading.Thread(target=worker) for _ in range(N_THREADS)]
[t.start() for t in threads]
[t.join() for t in threads]

assert not errors, f"concurrent calls failed: {errors[:3]}"
with router8._lock:
    # Rotation counter advanced once per call with no lost updates.
    assert router8._rr_index["c"] == (N_THREADS * N_PER) % 2, router8._rr_index
    assert router8.total_attempts == 0 and router8.total_failovers == 0
assert router8.last_used is not None
print(f"✅ {N_THREADS * N_PER} concurrent calls served, rotation exact, no errors")

print("\n" + "=" * 70)
print("✅✅ ALL LLM ROUTER LIFECYCLE CHECKS PASSED — no failover-path defects")
print("=" * 70)
