#!/usr/bin/env python3
"""Convenience script to start the Stratoscan FastAPI server.

Usage:
    python run.py                    # Start on 0.0.0.0:8000 with hot reload
    python run.py --port 8080        # Custom port
    python run.py --no-reload        # Production mode (no auto-reload)
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    """Parse args and start the uvicorn server."""
    parser = argparse.ArgumentParser(
        description="Start the Stratoscan API server",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind to (default: 8000)",
    )
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Disable auto-reload (use in production)",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Logging level (default: info)",
    )

    args = parser.parse_args()

    # Import uvicorn here so requirements check fails gracefully
    try:
        import uvicorn
    except ImportError:
        print(
            "❌ uvicorn is not installed. Run: pip install 'uvicorn[standard]'",
            file=sys.stderr,
        )
        return 1

    reload_enabled = not args.no_reload

    print(f"🚀 Stratoscan API starting on http://{args.host}:{args.port}")
    if reload_enabled:
        print("   Hot reload enabled — edit code to restart automatically")
    print(f"   Docs: http://localhost:{args.port}/docs")
    print(f"   Redoc: http://localhost:{args.port}/redoc")

    uvicorn.run(
        "src.api:app",
        host=args.host,
        port=args.port,
        reload=reload_enabled,
        log_level=args.log_level,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
