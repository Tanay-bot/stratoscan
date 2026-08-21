"""Live probe: verify the Upstash Redis REST credentials and the exact
wire format (single commands, pipeline, sorted sets) needed for the
Redis-backed rate limiter. Cleanup happens at the end."""

import json
import ssl
import urllib.error
import urllib.request

import certifi

BASE = "https://pleased-jaguar-136301.upstash.io"
TOKEN = "gQAAAAAAAhRtAAIgcDJkNmUxZDBhYzY4NDE0YzE1OWM5YjRjYjdjNmM2MzVjOQ"
CTX = ssl.create_default_context(cafile=certifi.where())


def cmd(command, payload):
    req = urllib.request.Request(
        f"{BASE}/{command}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


print("1. PING:", cmd("ping", []))
print("2. SET/GET:", cmd("set", ["rl_probe:hello", "world"]), cmd("get", ["rl_probe:hello"]))
print("3. INCR:", cmd("incr", ["rl_probe:counter"]), cmd("incr", ["rl_probe:counter"]))
print("4. EXPIRE + TTL:", cmd("expire", ["rl_probe:counter", "60"]), cmd("ttl", ["rl_probe:counter"]))
print("5. PIPELINE:", cmd("pipeline", {"pipeline": [["incr", "rl_probe:pipe"], ["expire", "rl_probe:pipe", "60"], ["ttl", "rl_probe:pipe"]]}))
print("6. ZADD/ZCARD:", cmd("zadd", ["rl_probe:week", 1750000000.0, "m1"]), cmd("zcard", ["rl_probe:week"]))
print("7. ZREMRANGEBYSCORE:", cmd("zremrangebyscore", ["rl_probe:week", "-inf", "1750000000"]), cmd("zcard", ["rl_probe:week"]))
print("8. DEL (cleanup):", cmd("del", ["rl_probe:hello", "rl_probe:counter", "rl_probe:pipe", "rl_probe:week"]))
