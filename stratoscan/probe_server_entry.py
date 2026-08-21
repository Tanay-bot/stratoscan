"""Probe-server launcher for verify_throughput.py (P0-12).

The throughput probe's bench fires ~1,200 requests and its own comments
document a design assumption of a ~2,000/hr general (read) budget. The real
production `read` bucket is burst 60/min (pinned in test_rate_limits.py and
verified by verify_load_behavior.py, which deliberately bursts past it). If
we booted the probe server with the real 60/min bucket, the probe's bench
would exhaust it mid-run and the 429 responses (which carry no
`ratelimit-limit` header) would break the probe's header assertion.

So this launcher gives the *probe server* a read budget that matches the
probe's documented assumption — RATE_LIMITS is read from the module global
at request time, so mutating it here before uvicorn serves is sufficient.
The rate limiter stays ON (headers + 429s still work); only the read
bucket's headroom is enlarged. The real limiter contract is unchanged in
production and is covered by the hermetic suites + verify_load_behavior.

Run: /usr/local/bin/python3.14 probe_server_entry.py --port N
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import src.api as api  # noqa: E402

# Enlarge ONLY the read scope (covers /stocks, /health, /metrics, /config,
# /docs — the endpoints the probe benches). Keep daily/weekly sane so the
# probe's ~1,200 bench requests fit with room to spare.
api.RATE_LIMITS["read"] = {"burst": 10000, "daily": 50000, "weekly": 250000}

import uvicorn  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe server with throughput-friendly read budget")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--log-level", default="warning")
    args = ap.parse_args()
    uvicorn.run(
        api.app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
