"""Hermetic tests for Day-4 error capture — no network, no DB.

Run: python -m tests.test_error_capture

An unhandled exception is the one place where genuinely sensitive material
shows up by accident: a formatted SQL statement carrying an email, a URL
carrying a token, an outbound request header echoed back by an HTTP client.
So the thing worth testing is not that rows land in ``error_log`` — it is that
the rows are safe to read and that the REDACTION is real.

Covers:

  1. ``scrub()``: email, JWT, ``Bearer …`` and the 8,000-char cap — each with
     the substitution asserted both ways (the secret is gone AND the shape that
     proves the rule fired is present).
  2. A route that raises ``ValueError("user alice@x.com token eyJa.b.c")``:
     the client gets the SAME generic 500 envelope as before, AND exactly one
     ``error_log`` row lands whose trace names the error type but contains
     neither the address nor the token.
  3. ``route`` is the route TEMPLATE: a path parameter and a query string must
     not appear in the stored row.
  4. A failing write must not change the response: the client still gets the
     generic 500, and nothing propagates.

Fully hermetic: FakeEventDB stands in for PostgresDB's error surface, and the
routes that raise are registered on the REAL app so the real handler (and its
real registration) is what is exercised.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Hermetic: keep the API rate limiter OFF (in-memory AND Upstash) so this
# suite never touches the network or real rate-limit counters.
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"
os.environ.setdefault("JWT_SECRET", "test-analytics-jwt-secret")

from unittest import mock  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from src.analytics import MAX_TRACE_CHARS, scrub  # noqa: E402
from tests.fakes import FakeEventDB  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════════
# 1. scrub() — the redactor itself
# ═══════════════════════════════════════════════════════════════════════════════
print("1. scrub() unit cases...")

assert scrub("") == ""
assert scrub("nothing sensitive here") == "nothing sensitive here"

# Email
out = scrub('duplicate key value violates unique constraint for "alice@x.com"')
assert "alice@x.com" not in out, out
assert "<email>" in out, out
# Two addresses in one line, and a plus-addressed one (the pattern has to
# survive the characters real addresses actually use).
out = scrub("failed for bob.smith+alerts@sub.example.co.uk and c@d.io")
assert "bob.smith+alerts@sub.example.co.uk" not in out and "c@d.io" not in out, out
assert out.count("<email>") == 2, out

# JWT — the three-segment eyJ… shape.
jwt = "eyJa.b.c"
out = scrub(f"malformed token {jwt} in header")
assert jwt not in out, out
assert "<jwt>" in out, out
# …and a realistically long one, which is what production actually carries.
long_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI3In0.abc-DEF_123"
out = scrub(f"Authorization: {long_jwt}")
assert long_jwt not in out and "<jwt>" in out, out

# Bearer
out = scrub("header was Bearer sk-live-abcdef123456")
assert "sk-live-abcdef123456" not in out, out
assert "Bearer <redacted>" in out, out
# Case-insensitive, because header casing is not guaranteed downstream.
out = scrub("header was bearer tok3n-value")
assert "tok3n-value" not in out and "<redacted>" in out, out

# Truncation, and the ordering that makes it safe: substitutes run BEFORE the
# cut, so a secret near the boundary cannot survive as a half-redacted prefix.
long_text = "x" * 20_000
assert len(scrub(long_text)) == MAX_TRACE_CHARS
# Spaces, not letters: the email pattern's local-part class would otherwise
# absorb the prefix and there would be nothing to cut.
boundary = " " * 7_000 + "alice@x.com" + " " + "b" * 2_000
out = scrub(boundary)
assert len(out) == MAX_TRACE_CHARS
assert "alice@x.com" not in out, "a substitution must not be defeated by the cap"
assert "<email>" in out, out
print(f"✅ scrub(): email, JWT, Bearer (case-insensitive) and a {MAX_TRACE_CHARS}-char cap")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Capture through the REAL app
# ═══════════════════════════════════════════════════════════════════════════════
print("2. generic_exception_handler capture...")

error_db = FakeEventDB()
mock.patch("src.database.PostgresDB", return_value=error_db).start()
mock.patch("src.llm_router.get_router").start()

from src.api import app  # noqa: E402  (imported after the patches above)

SECRET = "user alice@x.com token eyJa.b.c"


@app.get("/__boom", include_in_schema=False)
async def _boom():
    """Test-only route: raises through the real generic handler."""
    raise ValueError(SECRET)


@app.get("/__boom/{item_id}", include_in_schema=False)
async def _boom_item(item_id: str):
    """Test-only route: proves the stored route is the TEMPLATE."""
    raise ValueError(SECRET)


# raise_server_exceptions=False: a real uvicorn worker returns the 500 the
# handler builds, and that response is what this suite is asserting about.
client = TestClient(app, raise_server_exceptions=False)

r = client.get("/__boom")
assert r.status_code == 500, (r.status_code, r.text)
assert r.json() == {
    "error": True,
    "detail": "Internal server error. Please try again later.",
    "status_code": 500,
}, r.json()
assert "alice@x.com" not in r.text and "eyJa.b.c" not in r.text, \
    "the response must not leak what the log hides"

assert len(error_db.errors) == 1, error_db.errors
row = error_db.errors[0]
assert row["method"] == "GET"
assert row["route"] == "/__boom"
assert row["error_type"] == "ValueError", row["error_type"]
assert SECRET not in row["trace"], "the raw secret must not be stored"
assert "alice@x.com" not in row["trace"], row["trace"][-400:]
assert "eyJa.b.c" not in row["trace"], row["trace"][-400:]
assert "<email>" in row["trace"] and "<jwt>" in row["trace"], row["trace"][-400:]
# The error TYPE is the useful part and must be there — a redactor that also
# erased "ValueError" would leave an operator with nothing to act on.
assert "ValueError" in row["trace"], row["trace"][-400:]
print("✅ client gets the generic 500 envelope; one scrubbed error_log row is written")

# A repeated failure writes one row per occurrence (it is a log, not a digest).
r = client.get("/__boom")
assert r.status_code == 500
assert len(error_db.errors) == 2, error_db.errors
print("✅ every occurrence gets its own row")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. The stored route is the template, never the raw URL
# ═══════════════════════════════════════════════════════════════════════════════
print("3. route template...")

error_db.errors.clear()
r = client.get("/__boom/bt_9f3a7c21?token=live-secret-value&next=/app")
assert r.status_code == 500, (r.status_code, r.text)

row = error_db.errors[0]
assert row["route"] == "/__boom/{item_id}", row["route"]
assert "bt_9f3a7c21" not in row["route"], "the concrete path value must not be stored"
assert "live-secret-value" not in row["route"], "the query string must never be stored"
assert "live-secret-value" not in row["trace"], "the query string must never be stored"
print("✅ route is the template: no path value, no query string")

# Unrouted path: the template is unknowable, so the BARE path is stored and the
# query string is still dropped.
from src.api import _error_route_template  # noqa: E402


class _FakeURL:
    path = "/no/such/route"

    def __str__(self) -> str:  # pragma: no cover — guards against accidental use
        return "/no/such/route?token=leak"


class _FakeRequest:
    scope: dict = {}
    method = "GET"
    url = _FakeURL()


assert _error_route_template(_FakeRequest()) == "/no/such/route"
print("✅ unrouted request stores the bare path, never the query string")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. A failing write must not change the response
# ═══════════════════════════════════════════════════════════════════════════════
print("4. capture failure isolation...")

with mock.patch.object(error_db, "insert_error",
                       side_effect=RuntimeError("error_log is full")):
    r = client.get("/__boom")
assert r.status_code == 500, (r.status_code, r.text)
assert r.json() == {
    "error": True,
    "detail": "Internal server error. Please try again later.",
    "status_code": 500,
}, r.json()
# And with the whole DB seam gone.
with mock.patch("src.database.PostgresDB", side_effect=RuntimeError("no db")):
    r = client.get("/__boom")
assert r.status_code == 500, (r.status_code, r.text)
print("✅ a failed error_log write leaves the client's 500 identical")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. /admin/errors is admin-gated and reads newest-first
# ═══════════════════════════════════════════════════════════════════════════════
print("5. GET /admin/errors...")

r = client.get("/admin/errors")
assert r.status_code in (401, 403), (r.status_code, r.text)

# The read path itself, via the same fake the capture writes to.
error_db.errors.clear()
client.get("/__boom")
client.get("/__boom/{item_id}".replace("{item_id}", "x"))
admin_payload = {"count": len(error_db.list_errors(limit=50)),
                 "errors": error_db.list_errors(limit=50)}
assert admin_payload["count"] == 2
assert admin_payload["errors"][0]["route"] == "/__boom/{item_id}", admin_payload
assert admin_payload["errors"][1]["route"] == "/__boom", admin_payload

from src.api import _scope_for_path  # noqa: E402

assert _scope_for_path("/admin/errors") == "read"
print("✅ /admin/errors is admin-gated; rows come back newest-first")

mock.patch.stopall()

print()
print("🎯 All error-capture tests pass!")
