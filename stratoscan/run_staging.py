#!/usr/bin/env python3
"""Launch the Stratoscan API against the STAGING environment.

Loads ``staging.env`` (instead of ``.env``) into the process environment
BEFORE importing ``src.config``, so every setting — DATABASE_URL, rate
limits, CORS, etc. — resolves to the staging resources. The production
``.env`` is never touched.

Usage:
    /usr/local/bin/python3.14 run_staging.py                    # 0.0.0.0:8010
    /usr/local/bin/python3.14 run_staging.py --port 8090
    /usr/local/bin/python3.14 run_staging.py --no-reload
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    ap = argparse.ArgumentParser(description="Run Stratoscan API on staging")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--no-reload", action="store_true")
    ap.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error", "critical"],
    )
    args = ap.parse_args()

    staging_env = os.path.join(HERE, "staging.env")
    if not os.path.exists(staging_env):
        print(
            "❌ staging.env not found. Copy staging.env.example to staging.env "
            "and fill in the values first.",
            file=sys.stderr,
        )
        return 1

    # Load staging.env with override BEFORE importing src.config. config.py
    # calls load_dotenv(.env) with the default override=False, so the values
    # we set here win and the production .env is never applied.
    from dotenv import load_dotenv

    load_dotenv(staging_env, override=True)

    # Sanity: confirm we're pointed at the staging database, not prod.
    db_url = os.environ.get("DATABASE_URL", "")
    if "stratoscan_staging" not in db_url:
        print(
            "❌ staging.env DATABASE_URL does not point at stratoscan_staging. "
            "Refusing to start (safety).",
            file=sys.stderr,
        )
        return 1

    print(f"🚀 Stratoscan STAGING starting on http://{args.host}:{args.port}")
    print(f"   DB: {db_url.split('@')[-1][:60]}...")

    import uvicorn

    uvicorn.run(
        "src.api:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
