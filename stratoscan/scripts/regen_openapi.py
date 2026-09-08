#!/usr/bin/env python3
"""Regenerate the committed OpenAPI snapshots from the live FastAPI app.

``openapi.json`` (compact) and ``openapi.pretty.json`` (indent=2) are the
committed record of the HTTP contract — the frontend's ``types.ts`` and
``FRONTEND_API.md`` are written against them. They are generated artifacts,
never hand-edited.

Run after ANY change to a route, request model or response model::

    python scripts/regen_openapi.py

``tests/test_docs_contract.py`` imports the render helpers below and fails
when the committed files no longer match the live app, so a stale snapshot
cannot reach main. Both files are produced from a single ``app.openapi()``
call, so they can never disagree with each other the way they did before
this script existed.

``APP_ENV`` is pinned to ``development`` before importing the app: production
sets ``openapi_url=None`` (docs are disabled as attack surface). ``app.openapi()``
still returns the schema in that mode, but pinning keeps output deterministic
regardless of the operator's local ``.env``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

os.environ.setdefault("JWT_SECRET", "regen-openapi-dummy-secret")
os.environ["APP_ENV"] = "development"

COMPACT_PATH = _PROJECT_ROOT / "openapi.json"
PRETTY_PATH = _PROJECT_ROOT / "openapi.pretty.json"


def load_spec() -> dict:
    """Return the live OpenAPI schema from the FastAPI app."""
    from src.api import app  # imported late: env must be pinned first

    return app.openapi()


def render_compact(spec: dict) -> str:
    """Serialize the compact snapshot, newline-terminated."""
    return json.dumps(spec) + "\n"


def render_pretty(spec: dict) -> str:
    """Serialize the indented snapshot, newline-terminated."""
    return json.dumps(spec, indent=2) + "\n"


def main() -> int:
    spec = load_spec()
    COMPACT_PATH.write_text(render_compact(spec), encoding="utf-8")
    PRETTY_PATH.write_text(render_pretty(spec), encoding="utf-8")
    print(f"✅ wrote {COMPACT_PATH.name} and {PRETTY_PATH.name}")
    print(f"   {len(spec.get('paths', {}))} paths, "
          f"{len(spec.get('components', {}).get('schemas', {}))} schemas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
