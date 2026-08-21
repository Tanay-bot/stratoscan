"""Live probe #2: verify the CORRECT Upstash REST wire formats.

Docs (upstash.com/docs/redis/features/restapi):
  - Single command: POST {base} body = ["CMD", arg1, ...]
  - Pipeline:       POST {base}/pipeline body = [[cmd, args...], ...]
  - Transaction:    POST {base}/multi-exec body = [[cmd, args...], ...]  (atomic)
  - Response: {"result": ...} | {"error": ...}; pipeline -> list of those.
Cleanup deletes all probe keys at the end.
"""

import json
import ssl
import urllib.error
import urllib.request
import uuid

import certifi

BASE = "https://pleased-jaguar-136301.upstash.io"
TOKEN = "gQAAAAAAAhRtAAIgcDJkNmUxZDBhYzY4NDE0YzE1OWM5YjRjYjdjNmM2MzVjOQ"
CTX = ssl.create_default_context(cafile=certifi.where())


def post(path, payload):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


k = f"rl2:probe:{uuid.uuid4().hex[:6]}"
print(f"probe key prefix: {k}")

print("1. body-array execute INCR:", post("", ["INCR", f"{k}:c"]))
print("2. body-array execute TTL:", post("", ["TTL", f"{k}:c"]))
print("3. multi-exec INCR+EXPIRE+TTL:", post("/multi-exec", [["INCR", f"{k}:c"], ["EXPIRE", f"{k}:c", "300"], ["TTL", f"{k}:c"]]))
print("4. pipeline ZADD+ZCARD+ZRANGE WITHSCORES+TTL:")
print("  ", post("/pipeline", [["ZADD", f"{k}:w", 1750000000.0, "m1"], ["ZADD", f"{k}:w", 1750000060.0, "m2"], ["ZCARD", f"{k}:w"], ["ZRANGE", f"{k}:w", 0, 0, "WITHSCORES"], ["TTL", f"{k}:w"]]))
print("5. ZREMRANGEBYSCORE (exclusive):", post("", ["ZREMRANGEBYSCORE", f"{k}:w", "-inf", "(1750000000"]))
print("6. GET after SET via body-array:", post("", ["SET", f"{k}:s", "1"]), post("", ["GET", f"{k}:s"]))
print("7. DEL cleanup:", post("", ["DEL", f"{k}:c", f"{k}:w", f"{k}:s"]))
