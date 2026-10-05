"""Day 13 — the shared DB pool waits for a free connection instead of failing.

The load test found it: psycopg2's ThreadedConnectionPool raises
"connection pool exhausted" the instant every connection is out, so eight
concurrent users produced 503s. ``_get_shared_conn`` now waits (bounded) for
a connection to come back. Hermetic: a fake pool with psycopg2's exact
exhaustion behaviour.

Run: python -m tests.test_db_pool
"""

from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("JWT_SECRET", "db-pool-dummy-secret")

import psycopg2.pool  # noqa: E402

from src import database  # noqa: E402


class _Cur:
    def execute(self, *a):
        pass

    def close(self):
        pass


class _Conn:
    def cursor(self):
        return _Cur()


class FakePool:
    """psycopg2's semantics: getconn raises when maxconn are out."""

    def __init__(self, maxconn):
        self.maxconn, self.out, self.peak = maxconn, 0, 0
        self.lock = threading.Lock()
        self.closed = False

    def getconn(self):
        with self.lock:
            if self.out >= self.maxconn:
                raise psycopg2.pool.PoolError("connection pool exhausted")
            self.out += 1
            self.peak = max(self.peak, self.out)
            return _Conn()

    def putconn(self, conn):
        with self.lock:
            self.out -= 1


pool = FakePool(2)
done, errors = [], []


def user(i):
    try:
        with database._get_shared_conn(lambda: pool):
            time.sleep(0.05)
        done.append(i)
    except Exception as e:  # noqa: BLE001
        errors.append(e)


threads = [threading.Thread(target=user, args=(i,)) for i in range(16)]
t0 = time.monotonic()
[t.start() for t in threads]
[t.join() for t in threads]
assert not errors, errors
assert len(done) == 16 and pool.peak == 2 and pool.out == 0, (len(done), pool.peak, pool.out)
assert time.monotonic() - t0 < 5, "waiters are woken by returns, not by slow polling"
print("✅ 16 concurrent checkouts through a 2-connection pool all succeed (peak 2, none dropped)")

# Bounded: a pool that never frees a connection fails after the wait, not never.
stuck = FakePool(1)
stuck.out = 1
database.POOL_CHECKOUT_WAIT_SECONDS, saved = 0.3, database.POOL_CHECKOUT_WAIT_SECONDS
try:
    t0 = time.monotonic()
    try:
        with database._get_shared_conn(lambda: stuck):
            pass
        raise AssertionError("an exhausted pool must eventually fail")
    except psycopg2.pool.PoolError:
        assert 0.25 <= time.monotonic() - t0 < 2, time.monotonic() - t0
finally:
    database.POOL_CHECKOUT_WAIT_SECONDS = saved
print("✅ a pool that never frees a connection fails after the bounded wait (→ 503 upstream)")

print("\nALL DB-POOL CHECKS PASSED")
