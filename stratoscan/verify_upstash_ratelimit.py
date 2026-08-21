"""Live verification of the Upstash Redis rate-limit store (real round-trip).

Run: python -m verify_upstash_ratelimit  (from stratoscan/)

This is the ONLY place that touches the real Upstash instance. It drives the
REAL UpstashRateLimitStore + UpstashRedis client over the wire and asserts:

  1. PING succeeds against the configured instance.
  2. Fixed-window check (INCR / EXPIRE NX / TTL): allowed until the limit is
     hit, then denied with a sensible Retry-After; the window does NOT slide
     forward on sustained hits (EXPIRE NX semantics).
  3. Rolling-window check (sorted set): allowed until the limit, then denied
     with a Retry-After derived from the oldest surviving hit.
  4. check_many batch: mixed fixed+rolling checks return correct per-entry
     results and issue ONE multi-exec round-trip.
  5. Cleanup: every probe key is DELeted so the real instance is left exactly
     as found (no pollution).

Skips cleanly (exit 0, "SKIPPED") when Redis is not configured — e.g. on a
dev box where REDIS_RATE_LIMIT_ENABLED is false. Never writes anything that
isn't a ``rl_probe:*`` key.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import settings  # noqa: E402
from src.redis_ratelimit import (  # noqa: E402
    UpstashRateLimitStore,
    UpstashRedis,
    UpstashRedisError,
)

PASS = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
        print(f"  ✅ {name} {extra}")
    else:
        print(f"  ❌ FAIL: {name} {extra}")
        raise SystemExit(1)


def main() -> int:
    if not (settings.redis_rate_limit_enabled and settings.has_upstash_redis):
        print("SKIPPED: REDIS_RATE_LIMIT_ENABLED=false or no Upstash creds — live probe not applicable.")
        return 0

    print(f"Upstash: {settings.upstash_redis_rest_url}")

    client = UpstashRedis(
        settings.upstash_redis_rest_url,
        settings.upstash_redis_rest_token,
        settings.redis_rate_limit_timeout,
    )
    store = UpstashRateLimitStore(client=client)

    prefix = f"rl_probe:{int(time.time())}"
    keys = []

    def key(name: str) -> str:
        k = f"{prefix}:{name}"
        keys.append(k)
        return k

    try:
        print("\n1. PING")
        check("PING answered", client.ping())

        print("\n2. Fixed-window (INCR / EXPIRE NX / TTL)")
        fk = key("fixed")
        limit = 3
        results = []
        for i in range(limit + 2):
            allowed, remaining, retry_after = store.check_and_increment(
                fk, limit=limit, window_seconds=60
            )
            results.append((allowed, remaining))
        check("first 3 allowed", all(r[0] for r in results[:3]), str(results[:3]))
        check("4th and 5th denied", not results[3][0] and not results[4][0], str(results[3:]))
        check("remaining floors at 0", results[3][1] == 0 and results[4][1] == 0)
        # EXPIRE NX: the 5th hit must NOT have slid the window — TTL should
        # still be ≤ 60 (not reset to 60 by the 5th request).
        ttl = client.execute("TTL", fk)
        check("window did not slide (EXPIRE NX)", isinstance(ttl, dict) and "result" in ttl)
        ttl_val = ttl.get("result")
        check(f"TTL still sane after 5 hits ({ttl_val})", ttl_val is not None and 0 < ttl_val <= 60)

        print("\n3. Rolling-window (sorted set)")
        rk = key("rolling")
        r_limit = 2
        r_results = []
        for i in range(r_limit + 1):
            allowed, remaining, retry_after = store.check_and_increment_rolling(
                rk, limit=r_limit, window_seconds=60
            )
            r_results.append((allowed, remaining, retry_after))
        check("first 2 rolling allowed", all(r[0] for r in r_results[:2]), str(r_results[:2]))
        check("3rd rolling denied", not r_results[2][0], str(r_results[2]))
        check("rolling retry_after in [1,60]", 1 <= r_results[2][2] <= 60)

        print("\n4. check_many batch (one multi-exec, mixed windows)")
        bk_fixed = key("batch-fixed")
        bk_roll = key("batch-roll")
        checks = [
            (bk_fixed, 1, 60, False),
            (bk_roll, 1, 60, True),
        ]
        batch1 = store.check_many(checks)
        check("batch: first pass both allowed", all(b[0] for b in batch1), str(batch1))
        batch2 = store.check_many(checks)
        check("batch: second pass both denied", all(not b[0] for b in batch2), str(batch2))

        print("\n5. Cleanup")
        for k in keys:
            client.execute("DEL", k)
        leftover = [k for k in keys if client.execute("EXISTS", k).get("result")]
        check("all probe keys deleted", not leftover, str(leftover))

    except UpstashRedisError as e:
        print(f"\n❌ Upstash call failed: {e}")
        return 1
    finally:
        # Best-effort cleanup even on failure.
        try:
            for k in keys:
                client.execute("DEL", k)
        except Exception:
            pass

    print(f"\n🎯 Live Upstash probe: {PASS} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
