"""Guard: committed documentation artifacts match the code they describe.

Two facts in this repo are stated in a committed file AND defined in code,
with nothing keeping them in step. Both had already drifted when this suite
was written:

  1. **The OpenAPI snapshots.** ``openapi.json`` / ``openapi.pretty.json`` are
     the committed record of the HTTP contract (the frontend's ``types.ts``
     and ``FRONTEND_API.md`` are written against them). Neither had been
     regenerated since the initial commit: they were missing ``/backtest/async``,
     ``/backtest/async/{job_id}`` and ``/admin``, pinned ``ScreenRequest.query``
     at ``maxLength: 500`` when the live model says 4000, and — having been
     generated at different times — disagreed with *each other*.

  2. **The release-gate probe inventory.** ``RELEASE_GATE.md`` lists the probes
     the gate runs; ``run_probes.py`` defines what it actually runs. Prose
     elsewhere variously claimed 15, 17 and 18 probes.

``tests/test_api_smoke.py`` already pins the live *route set*, so this suite
deliberately does not re-check that — it checks the generated artifacts and
the doc table, which nothing else covers.

Hermetic: no network, no DB, no LLM. Reads files and the in-process app only.

Run: python -m tests.test_docs_contract
"""

import ast
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Pin the env BEFORE importing src.api. Production nulls ``openapi_url`` (docs
# are disabled as attack surface), so a local .env with APP_ENV=production
# must not change what this suite compares. Mirrors tests/test_frontend_meta.py.
os.environ["APP_ENV"] = "development"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"
os.environ.setdefault("JWT_SECRET", "docs-contract-dummy-secret")

sys.path.insert(0, os.path.join(_PROJECT_ROOT, "scripts"))
import regen_openapi  # noqa: E402  (path setup must precede the import)

REGEN_HINT = "Regenerate with: python scripts/regen_openapi.py"

# ═══════════════════════════════════════════════════════════════════════════
# 1. OpenAPI snapshots match the live app
# ═══════════════════════════════════════════════════════════════════════════

spec = regen_openapi.load_spec()

_cases = (
    ("openapi.json", regen_openapi.COMPACT_PATH, regen_openapi.render_compact),
    ("openapi.pretty.json", regen_openapi.PRETTY_PATH, regen_openapi.render_pretty),
)

for name, path, render in _cases:
    assert path.exists(), f"{name} is missing. {REGEN_HINT}"
    committed = path.read_text(encoding="utf-8")
    expected = render(spec)
    if committed != expected:
        # Report the difference in contract terms, not as a byte diff — a
        # dropped route is the failure that actually matters.
        import json

        live_paths = set(spec.get("paths", {}))
        old_paths = set(json.loads(committed).get("paths", {}))
        detail = ""
        if live_paths - old_paths:
            detail += f"\n  missing from snapshot: {sorted(live_paths - old_paths)}"
        if old_paths - live_paths:
            detail += f"\n  stale in snapshot:     {sorted(old_paths - live_paths)}"
        if not detail:
            detail = "\n  same paths, but a schema/field differs"
        raise AssertionError(f"{name} is out of date.{detail}\n{REGEN_HINT}")

print(f"✅ openapi.json matches the live app — {len(spec['paths'])} paths")
print(f"✅ openapi.pretty.json matches the live app — {len(spec['paths'])} paths")

# Both snapshots are rendered from one app.openapi() call, so they cannot
# describe different contracts. Pin that so nobody regenerates just one.
import json  # noqa: E402

assert json.loads(regen_openapi.COMPACT_PATH.read_text(encoding="utf-8")) == json.loads(
    regen_openapi.PRETTY_PATH.read_text(encoding="utf-8")
), f"openapi.json and openapi.pretty.json describe different contracts. {REGEN_HINT}"
print("✅ the two snapshots describe the same contract")

# ═══════════════════════════════════════════════════════════════════════════
# 2. RELEASE_GATE.md's probe table matches run_probes.py
# ═══════════════════════════════════════════════════════════════════════════

# Parse (don't import) run_probes.py: importing is safe today but parsing keeps
# this suite immune to any future module-level side effect in that script.
_probes_src = open(os.path.join(_PROJECT_ROOT, "run_probes.py"), encoding="utf-8").read()
_tree = ast.parse(_probes_src)
declared_probes = None
for node in _tree.body:
    if isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "PROBES" for t in node.targets
    ):
        declared_probes = [el.value for el in node.value.elts]
        break

assert declared_probes, "could not find the PROBES list in run_probes.py"

# Table rows look like: | 7 | `verify_failure_drills.py` | what it verifies |
_gate_md = open(os.path.join(_PROJECT_ROOT, "RELEASE_GATE.md"), encoding="utf-8").read()
documented_probes = re.findall(r"^\|\s*\d+\s*\|\s*`([A-Za-z0-9_]+\.py)`", _gate_md, re.M)

assert documented_probes, "RELEASE_GATE.md has no probe table rows — did the format change?"

if documented_probes != declared_probes:
    _msg = "RELEASE_GATE.md's probe table is out of sync with run_probes.py PROBES."
    _only_doc = sorted(set(documented_probes) - set(declared_probes))
    _only_run = sorted(set(declared_probes) - set(documented_probes))
    if _only_doc:
        _msg += f"\n  documented but not run: {_only_doc}"
    if _only_run:
        _msg += f"\n  run but not documented: {_only_run}"
    if not _only_doc and not _only_run:
        # Same names, different sequence or a duplicate — say so explicitly
        # rather than printing two empty sets.
        _dupes = sorted({p for p in declared_probes if declared_probes.count(p) > 1})
        _msg += (
            f"\n  same probe names, but the table is numbered and order/count differ"
            f"\n  run_probes.py has {len(declared_probes)} entries, the table has "
            f"{len(documented_probes)}"
        )
        if _dupes:
            _msg += f"\n  duplicated in run_probes.py: {_dupes}"
    raise AssertionError(_msg)
print(f"✅ RELEASE_GATE.md documents exactly the {len(declared_probes)} probes run_probes.py runs")

for probe in declared_probes:
    assert os.path.exists(
        os.path.join(_PROJECT_ROOT, probe)
    ), f"run_probes.py lists {probe} but the file does not exist"
print(f"✅ all {len(declared_probes)} probe files exist on disk")

# Any prose stating a probe COUNT is drift waiting to happen — that number was
# variously 15, 17 and 18 across the docs. RELEASE_GATE.md owns it (the table
# above is what makes it true); every other file that repeats it is checked here.
_count_files = ("RELEASE_GATE.md", "FAILURE_DRILLS.md")
_checked = 0
for _fname in _count_files:
    _text = open(os.path.join(_PROJECT_ROOT, _fname), encoding="utf-8").read()
    for count in re.findall(r"\b(\d+)\s+(?:behavior\s+)?probes\b", _text):
        assert int(count) == len(declared_probes), (
            f"{_fname} prose says {count} probes but run_probes.py runs "
            f"{len(declared_probes)}"
        )
        _checked += 1
print(f"✅ probe counts in prose agree with run_probes.py ({_checked} mention(s) checked)")

print("\n✅ ALL DOCS CONTRACT TESTS PASSED")
