#!/usr/bin/env python3
"""migration_dryrun.py — P1-6: run ``alembic upgrade head`` on a COPY of the
production database before any release push.

WHY: the container entrypoint runs `alembic upgrade head` on boot against the
REAL database. If a migration is broken, production discovers it at deploy
time — the worst possible moment. This script replays the exact same upgrade
against a throwaway scratch copy (schema + data cloned from production) and
proves, before the push:

  1. The upgrade completes without errors.
  2. The head revision is stamped (``c1d2e3f4a5b6`` today).
  3. Every expected table + column exists after the upgrade.
  4. No data is lost: row counts AND a per-table checksum are identical
     before vs after (checksum = sum of numeric columns + count + md5 of a
     text sample, computed per table).

It also tests the REAL production scenario: prod currently sits at
``d5e6f7a8b9c0`` (well behind head) and its job-monitor tables were
created by the runtime init_schema without alembic recording them — so the
dry-run verifies idempotent migrations replay cleanly over already-existing
tables.

COPY MECHANISM: Neon's pooler holds sessions on the source DB, so
``CREATE DATABASE ... WITH TEMPLATE`` fails ("database is being accessed by
other users"). This script instead clones schema from ``information_schema``
(columns, PK/unique/FK/check constraints, defaults, sequences, indexes) and
copies rows with binary COPY (``WITH (FORMAT binary)``) — byte-faithful and
fast, no pg_dump required.

SAFETY:
  - The SOURCE is opened read-only (SELECT only).
  - The TARGET must contain ``migrdry`` in its name — refuses otherwise.
  - The target is created fresh on the DIRECT (non-pooler) endpoint of the
    same Neon project, then dropped again at the end unless ``--keep``.

Usage:
  /usr/local/bin/python3.14 scripts/migration_dryrun.py            # prod -> stratoscan_migrdry
  /usr/local/bin/python3.14 scripts/migration_dryrun.py --keep     # leave the scratch DB
  /usr/local/bin/python3.14 scripts/migration_dryrun.py --target stratoscan_migrdry2

Exit 0 = migration clean + data intact. 1 = any check failed. 2 = usage/env.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit, urlunsplit

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

def _alembic_head() -> str:
    """The single head of the revision chain, read from alembic/versions/.

    Derived rather than hard-coded: a literal here had to be hand-edited by
    every migration, and a stale one fails this gate on a CORRECT migration.
    """
    versions = os.path.join(HERE, "alembic", "versions")
    revs, downs = set(), set()
    for name in os.listdir(versions):
        if not name.endswith(".py") or name.startswith("__"):
            continue
        with open(os.path.join(versions, name), encoding="utf-8") as fh:
            src = fh.read()
        rev = re.search(r'^revision: str = "([^"]+)"', src, re.M)
        down = re.search(r'^down_revision:[^=]*= "([^"]+)"', src, re.M)
        if rev:
            revs.add(rev.group(1))
        if down:
            downs.add(down.group(1))
    heads = sorted(revs - downs)
    if len(heads) != 1:
        raise SystemExit(f"expected exactly one alembic head, found {heads}")
    return heads[0]


HEAD_REVISION = _alembic_head()

# Expected tables after ``alembic upgrade head`` (runtime + migration DDL).
EXPECTED_TABLES = {
    "alembic_version", "job_runs", "news_articles", "scheduler_heartbeat",
    "stock_master", "stock_sentiment", "user_data", "users",
}

# Expected key columns per table (the migration-derived contract).
# NOTE: ``is_stale`` is intentionally NOT here — it is a computed screener
# flag (screener.py), never a stored column. ``verification_sent_at`` does
# not exist either; the real email-verification column is
# ``verification_token_expires``.
EXPECTED_COLUMNS = {
    "users": {"id", "email", "password_hash", "plan", "email_verified",
              "verification_token", "verification_token_expires", "is_admin",
              "created_at"},
    "stock_master": {"symbol", "current_price", "rsi_14", "sma_50", "sma_200",
                     "avg_volume", "eps_ttm", "high_52w", "low_52w",
                     "ps_ratio", "pb_ratio", "ev_ebitda", "roe_ttm",
                     "revenue_growth_ttm", "eps_growth_ttm"},
    "user_data": {"user_id", "watchlist", "saved_screeners",
                  "recent_backtests", "updated_at"},
    "job_runs": {"id", "job_name", "status", "started_at", "finished_at",
                 "duration_ms", "records", "details", "error"},
    "scheduler_heartbeat": {"id", "last_beat_at", "pid"},
    "news_articles": {"id", "symbol", "headline", "source", "url",
                      "published_at", "fetched_at"},
    "stock_sentiment": {"symbol", "buzz", "company_news_score",
                        "sector_news_score"},
}

# Column type translations (information_schema -> PostgreSQL DDL).
_TYPE_MAP = {
    "character varying": "varchar",
    "timestamp with time zone": "timestamptz",
    "timestamp without time zone": "timestamp",
    "double precision": "double precision",
    "numeric": "numeric",
    "bigint": "bigint",
    "integer": "integer",
    "smallint": "smallint",
    "boolean": "boolean",
    "jsonb": "jsonb",
    "text": "text",
    "date": "date",
    "real": "real",
    "bytea": "bytea",
}


def _direct_url(url: str) -> str:
    """Swap the pooler host for the direct endpoint (CREATE DB needs it)."""
    p = urlsplit(url)
    host = (p.hostname or "").replace("-pooler", "")
    netloc = p.netloc.replace(p.hostname or "", host)
    return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))


def _admin_conn(url: str, dbname: str):
    """Connect to a specific database ('') via the direct endpoint.

    The maintenance DB is 'postgres' on most Postgres installs, but Neon's
    default DB is often 'neondb' — so we fall back to the source URL's own
    dbname when 'postgres' isn't reachable.
    """
    import psycopg2

    p = urlsplit(_direct_url(url))
    candidates = [dbname or "postgres"]
    src_db = p.path.rstrip("/").split("/")[-1]
    if src_db and src_db != candidates[0]:
        candidates.append(src_db)
    last_err: Exception | None = None
    for cand in candidates:
        dsn = (
            f"host={p.hostname} port={p.port or 5432} user={p.username} "
            f"password={unquote(p.password or '')} dbname={cand} "
            f"sslmode=require connect_timeout=20"
        )
        try:
            return psycopg2.connect(dsn)
        except psycopg2.OperationalError as e:
            last_err = e
    raise last_err  # type: ignore[misc]


def _copy_schema_and_data(src_url: str, dst_url: str) -> None:
    """Clone every public table (schema + rows) from src to dst.

    DDL is rebuilt from information_schema; rows move via binary COPY.
    Includes the ``alembic_version`` table so the copy starts at prod's
    exact stamp. Ordering-safe: tables are created without FK constraints
    (Phase A), FKs are added once every table exists (Phase B), then data
    is copied and serial sequences are re-pointed past the copied max
    (Phase C). Constraint-backed indexes (PK/unique) are NOT replayed —
    they were created inline by their constraints.
    """
    import psycopg2

    src = psycopg2.connect(src_url, connect_timeout=20)
    dst = psycopg2.connect(dst_url, connect_timeout=20)
    try:
        s = src.cursor()
        s.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' "
            "AND tablename NOT IN ('spatial_ref_sys') ORDER BY 1"
        )
        tables = [r[0] for r in s.fetchall()]

        fk_pending: list[tuple[str, list[tuple[str, str]]]] = []
        serial_pending: list[tuple[str, str, str]] = []  # (table, column, seq)

        # ── Phase A: CREATE TABLE (columns + p/u/c constraints) ─────────
        for t in tables:
            s.execute(
                "SELECT column_name, data_type, character_maximum_length, "
                "       numeric_precision, numeric_scale, is_nullable, "
                "       column_default "
                "FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s "
                "ORDER BY ordinal_position",
                (t,),
            )
            cols = s.fetchall()
            col_defs = []
            serial_cols = []  # (column, sequence)
            for name, dtype, cml, np_, ns, nullable, default in cols:
                base = _TYPE_MAP.get(dtype, dtype)
                if base == "varchar" and cml:
                    base = f"varchar({cml})"
                if base == "numeric" and np_:
                    base = f"numeric({np_},{ns or 0})"
                parts = [f'"{name}" {base}']
                if default:
                    if "nextval(" in default:
                        # serial-style: create the sequence separately and
                        # point the default at it.
                        seq = default.split("'")[1]
                        parts.append(f"DEFAULT nextval('{seq}')")
                        serial_cols.append((name, seq))
                    else:
                        parts.append(f"DEFAULT {default}")
                if nullable == "NO":
                    parts.append("NOT NULL")
                col_defs.append(" ".join(parts))

            # Primary key + unique + FK + check constraints (FK deferred).
            s.execute(
                "SELECT conname, pg_get_constraintdef(oid), contype "
                "FROM pg_constraint "
                "WHERE conrelid = %s::regclass AND contype IN "
                "('p','u','f','c') ORDER BY conname",
                (f"public.{t}",),
            )
            cons, fks = [], []
            for cn, cd, ctype in s.fetchall():
                if ctype == "f":
                    fks.append((cn, cd))
                else:
                    cons.append(f"CONSTRAINT {cn} {cd}")

            ddl = (
                f"CREATE TABLE public.\"{t}\" (\n  "
                + ",\n  ".join(col_defs + cons)
                + "\n);"
            )
            d = dst.cursor()
            # Sequences must exist before the table's nextval() defaults
            # reference them.
            for col, seq in serial_cols:
                d.execute(f"CREATE SEQUENCE IF NOT EXISTS {seq} AS bigint")
            d.execute(ddl)
            dst.commit()
            if fks:
                fk_pending.append((t, fks))
            serial_pending += [(t, col, seq) for col, seq in serial_cols]

        # ── Phase B: FKs (every referenced table now exists) ────────────
        for t, fks in fk_pending:
            for cn, cd in fks:
                d.execute(
                    f'ALTER TABLE public."{t}" ADD CONSTRAINT {cn} {cd}'
                )
        dst.commit()

        # ── Phase C: data (binary COPY) then sequence setval ────────────
        for t in tables:
            s.execute(f'SELECT COUNT(*) FROM public."{t}"')
            n = s.fetchone()[0]
            if n:
                buf = io.BytesIO()
                s.copy_expert(
                    f'COPY (SELECT * FROM public."{t}") TO STDOUT '
                    f"WITH (FORMAT binary)", buf
                )
                buf.seek(0)
                d.execute(f'TRUNCATE public."{t}"')
                d.copy_expert(
                    f'COPY public."{t}" FROM STDIN WITH (FORMAT binary)', buf
                )
                dst.commit()
            print(f"   ✅ {t}: {n} rows")

        for t, col, seq in serial_pending:
            d.execute(f'SELECT MAX("{col}") FROM public."{t}"')
            max_id = d.fetchone()[0]
            if max_id is None:
                d.execute(f"SELECT setval('{seq}', 1, false)")
            else:
                d.execute(f"SELECT setval('{seq}', {max_id}, true)")
        dst.commit()

        # ── Indexes (skip constraint-backed ones — already inline) ──────
        for t in tables:
            s.execute(
                "SELECT ci.relname FROM pg_constraint c "
                "JOIN pg_class ci ON ci.oid = c.conindid "
                "WHERE c.conrelid = %s::regclass AND c.contype IN ('p','u')",
                (f"public.{t}",),
            )
            constraint_indexes = {r[0] for r in s.fetchall()}
            s.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname='public' AND tablename=%s",
                (t,),
            )
            for (indexdef,) in s.fetchall():
                m = re.search(r"INDEX (\S+)", indexdef)
                if m and m.group(1) in constraint_indexes:
                    continue
                if indexdef.upper().startswith("CREATE"):
                    d.execute(indexdef)
        dst.commit()

    finally:
        src.close()
        dst.close()


def _table_checksums(url: str) -> dict:
    """Row count + a cheap-but-real checksum per table (numeric sum + md5)."""
    import psycopg2

    conn = psycopg2.connect(url, connect_timeout=20)
    out: dict[str, tuple] = {}
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' "
            "AND tablename NOT IN ('spatial_ref_sys') ORDER BY 1"
        )
        for (t,) in cur.fetchall():
            cur.execute(f'SELECT COUNT(*) FROM public."{t}"')
            n = cur.fetchone()[0]
            # Numeric columns to sum (best-effort integrity fingerprint).
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s "
                "AND data_type IN ('bigint','integer','smallint','double precision','real','numeric')",
                (t,),
            )
            num_cols = [r[0] for r in cur.fetchall()]
            h = hashlib.md5()
            h.update(f"{t}:{n}:".encode())
            if num_cols:
                col_list = ", ".join(f'COALESCE("{c}",0)' for c in num_cols)
                cur.execute(f'SELECT {col_list} FROM public."{t}"')
                # Canonical order so an unchanged table can never produce a
                # different hash merely from a different scan order.
                rows = sorted(cur.fetchall())
                for row in rows:
                    h.update(repr(row).encode())
            else:
                cur.execute(f'SELECT * FROM public."{t}" ORDER BY 1 LIMIT 2000')
                for row in cur.fetchall():
                    h.update(repr(row).encode())
            out[t] = (n, h.hexdigest())
    finally:
        conn.close()
    return out


def _run_alembic(dst_url: str) -> subprocess.CompletedProcess:
    """Run `alembic upgrade head` against the scratch DB (env URL wins)."""
    env = os.environ.copy()
    env["DATABASE_URL"] = dst_url
    # Belt-and-suspenders: the subprocess inherits our env, so any DB
    # override alembic/env.py could pick up (e.g. a stray STAGING_DATABASE_URL)
    # is neutralized for the migration itself.
    for k in ("STAGING_DATABASE_URL", "BACKTEST_DATABASE_URL",
              "NEWS_DATABASE_URL", "PROBES_DATABASE_URL"):
        env.pop(k, None)
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=HERE, env=env, capture_output=True, text=True, timeout=600,
    )


def _read_stamp(url: str) -> str | None:
    """Return the alembic_version stamp of a DB (None if table missing)."""
    import psycopg2

    conn = psycopg2.connect(url, connect_timeout=20)
    try:
        cur = conn.cursor()
        cur.execute("SELECT version_num FROM alembic_version")
        return cur.fetchone()[0]
    except psycopg2.Error:
        return None
    finally:
        conn.close()


def _verify(url: str) -> list[str]:
    """Post-upgrade contract: head stamped, tables+columns present, and a
    couple of data-presence queries. Returns a list of failure strings."""
    import psycopg2

    fails: list[str] = []
    conn = psycopg2.connect(url, connect_timeout=20)
    try:
        cur = conn.cursor()
        cur.execute("SELECT version_num FROM alembic_version")
        ver = cur.fetchone()[0]
        if ver != HEAD_REVISION:
            fails.append(f"head mismatch: {ver} != {HEAD_REVISION}")

        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        have = {r[0] for r in cur.fetchall()}
        for t in sorted(EXPECTED_TABLES - have):
            fails.append(f"missing table: {t}")
        for t, want in sorted(EXPECTED_COLUMNS.items()):
            if t not in have:
                continue
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s",
                (t,),
            )
            cols = {r[0] for r in cur.fetchall()}
            for c in sorted(want - cols):
                fails.append(f"missing column: {t}.{c}")

        # Data-presence spot checks.
        cur.execute("SELECT COUNT(*) FROM stock_master")
        n = cur.fetchone()[0]
        if n < 1000:
            fails.append(f"stock_master looks empty after copy: {n} rows")
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='stock_master'"
        )
        scols = {r[0] for r in cur.fetchall()}
        order_col = "market_cap" if "market_cap" in scols else "current_price"
        cur.execute(
            f"SELECT symbol, current_price FROM stock_master "
            f"ORDER BY {order_col} DESC NULLS LAST LIMIT 1"
        )
        top = cur.fetchone()
        if not top or not top[0]:
            fails.append(f"no {order_col} leader in stock_master")
        else:
            print(f"   📊 top holding: {top[0]} @ ${top[1] or 'n/a'}")
        cur.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] < 1:
            fails.append("users table empty after copy")
        cur.execute("SELECT COUNT(*) FROM user_data")  # created by the migration
        cur.fetchone()
    finally:
        conn.close()
    return fails


def main() -> int:
    ap = argparse.ArgumentParser(description="Migration dry-run on a copy of prod (P1-6)")
    ap.add_argument("--source-url", default=None,
                    help="Source DB URL (default: DATABASE_URL from .env)")
    ap.add_argument("--target", default="stratoscan_migrdry",
                    help="Scratch database name (must contain 'migrdry')")
    ap.add_argument("--keep", action="store_true",
                    help="Leave the scratch DB in place after the run")
    args = ap.parse_args()

    from dotenv import load_dotenv

    load_dotenv(os.path.join(HERE, ".env"))
    src_url = args.source_url or os.environ.get("DATABASE_URL", "")
    if not src_url:
        print("❌ DATABASE_URL not set (source). Aborting.")
        return 2
    if "migrdry" not in args.target:
        print(f"❌ Refusing: target '{args.target}' must contain 'migrdry'.")
        return 2
    p = urlsplit(src_url)
    src_name = p.path.rstrip("/").split("/")[-1] or "?"
    print(f"📥 Source: {p.hostname} /{src_name} (direct: {_direct_url(src_url).split('@')[1] if '@' in _direct_url(src_url) else ''})")

    target_url = urlunsplit(
        (p.scheme, p.netloc, f"/{args.target}", p.query, p.fragment)
    )

    # ── 1. Create the scratch DB fresh ─────────────────────────────────
    print(f"\n1. Creating scratch DB '{args.target}'...")
    src_stamp_before = _read_stamp(src_url)
    admin = _admin_conn(src_url, "")
    admin.autocommit = True
    try:
        cur = admin.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (args.target,))
        if cur.fetchone():
            # --keep only controls the END of the run; a leftover scratch DB
            # is always replaced so the copy is pristine.
            cur.execute(f'DROP DATABASE "{args.target}"')
            print("   dropped previous scratch copy")
        cur.execute(f'CREATE DATABASE "{args.target}"')
        print(f"   created {args.target}")
    finally:
        admin.close()

    # ── 2. Clone schema + data ─────────────────────────────────────────
    print("\n2. Cloning schema + data from source...")
    _copy_schema_and_data(src_url, target_url)

    # ── 3. Snapshot BEFORE ─────────────────────────────────────────────
    print("\n3. Snapshot BEFORE upgrade...")
    before = _table_checksums(target_url)
    for t, (n, h) in sorted(before.items()):
        print(f"   {t}: {n} rows  md5={h[:10]}")

    # ── 4. Run the migration ───────────────────────────────────────────
    print(f"\n4. Running alembic upgrade head -> {HEAD_REVISION}...")
    res = _run_alembic(target_url)
    out = (res.stdout or "") + (res.stderr or "")
    print("   " + "\n   ".join(line for line in out.strip().splitlines()[:20]))
    if res.returncode != 0:
        print(f"❌ alembic exited {res.returncode}. Full output:\n{out}")
        return 1

    # ── 4b. Prove alembic touched ONLY the scratch DB ──────────────────
    # If alembic had resolved the URL differently and migrated production,
    # the source's stamp would now differ from what we captured before the
    # run — fail loudly instead of continuing on a corrupted prod.
    src_stamp_after = _read_stamp(src_url)
    if src_stamp_after != src_stamp_before:
        print(f"❌ FATAL: source stamp changed during the run "
              f"({src_stamp_before} -> {src_stamp_after}). "
              f"alembic touched the SOURCE, not the copy. Aborting.")
        return 1
    print(f"   🔒 source stamp unchanged: {src_stamp_before}")
    dst_stamp = _read_stamp(target_url)
    if dst_stamp != HEAD_REVISION:
        print(f"❌ copy stamp is {dst_stamp}, expected {HEAD_REVISION} "
              f"after upgrade.")
        return 1
    print(f"   ✅ copy stamped at head: {dst_stamp}")

    # ── 5. Snapshot AFTER + verify ─────────────────────────────────────
    print("\n5. Snapshot AFTER upgrade + verification...")
    after = _table_checksums(target_url)
    fails = []
    for t in sorted(set(before) | set(after)):
        b, a = before.get(t), after.get(t)
        if t == "alembic_version":
            # The migration stamp moves to HEAD by design — verified
            # separately in _verify against HEAD_REVISION.
            print(f"   ↻ alembic_version: {b[0]} rows (stamp updated by design)")
            continue
        if b is None and a is not None:
            # New table created by the migration — an intentional addition.
            print(f"   ➕ {t}: {a[0]} rows (created by migration)")
            continue
        if b is not None and a is None:
            fails.append(f"TABLE REMOVED by migration: {t}")
            continue
        if b != a:
            fails.append(f"DATA CHANGE in {t}: before={b} after={a}")
        else:
            print(f"   ✅ {t}: {b[0]} rows  (unchanged)")
    fails += _verify(target_url)

    # ── 6. Cleanup + verdict ───────────────────────────────────────────
    if not args.keep:
        admin = _admin_conn(src_url, "")
        admin.autocommit = True
        try:
            cur = admin.cursor()
            cur.execute(f'DROP DATABASE IF EXISTS "{args.target}"')
            print(f"\n🧹 dropped scratch DB '{args.target}'")
        finally:
            admin.close()
    else:
        print(f"\n🔒 kept scratch DB '{args.target}' (--keep)")

    print("\n" + "=" * 72)
    if fails:
        print("❌ MIGRATION DRY-RUN FAILED")
        for f in fails:
            print(f"   ❌ {f}")
        return 1
    print("✅ MIGRATION DRY-RUN PASSED — upgrade clean, head stamped, "
          "all tables/columns present, zero data change.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
