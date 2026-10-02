"""Polling a running backtest must never spend the backtest cost shield.

The UI polls ``GET /backtest/async/{job_id}`` every 1.5 seconds. That path
used to fall in the ``backtest`` scope (2/min burst, 10/day), so with rate
limiting on — production, never staging — the third poll of every job got a
429, and every poll also drew down the user's daily backtests and the
planet-wide ``backtest_llm`` bucket. Polls are free (one dict lookup or one DB
row), so they get their own scope that feeds no global bucket.

Both halves are asserted, because a "fix" that merely raised the backtest
cap would pass the first and fail the second:

  1. 30 polls inside one minute — none is rate-limited;
  2. afterwards, STARTING a backtest still has its full, untouched budget.

Rate limiting runs before auth, so a 401 here means "allowed through the
limiter" and a 429 means "limited". No token is needed to observe either.

Run: python -m tests.test_backtest_poll_limits
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "true"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"
os.environ["RATE_LIMIT_BYPASS_IPS"] = ""
os.environ.setdefault("JWT_SECRET", "poll-limits-dummy-secret")

from unittest import mock  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from tests.fakes import FakeLLMRouter, FakeUserDB  # noqa: E402

mock.patch("src.database.PostgresDB", return_value=FakeUserDB()).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

import src.api as api  # noqa: E402

client = TestClient(api.app)

assert api._scope_for_path("/backtest/async/abc") == "backtest_poll"
assert api._scope_for_path("/backtest/async") == "backtest"
assert api._scope_for_path("/backtest") == "backtest"
assert "backtest_poll" not in api.SCOPE_GLOBAL, "polls must not draw on any global budget"
print("✅ a poll maps to backtest_poll; starting a job still maps to backtest")

poll_codes = [client.get("/backtest/async/job-123").status_code for _ in range(30)]
assert 429 not in poll_codes, poll_codes
print(f"✅ 30 polls inside one minute → none rate-limited ({sorted(set(poll_codes))})")

starts = [client.post("/backtest/async", json={"query": "x"}).status_code for _ in range(3)]
assert starts[:2] == [401, 401], f"the backtest budget must be untouched by polls: {starts}"
assert starts[2] == 429, f"...and still enforced on the third start: {starts}"
print("✅ after 30 polls, starting a backtest still has its full 2/min — then 429 as designed")

mock.patch.stopall()
print("\nALL POLL-LIMIT CHECKS PASSED")
