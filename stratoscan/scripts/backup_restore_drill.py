"""Backup-and-restore drill for all three databases (Day 15).

A backup you have not restored is a hope, not a backup. For each database
(main, backtest, news) this:

  1. fingerprints every table in ``public`` — row count plus an md5 over the
     sorted md5 of every row, so a restore that drops, duplicates or alters a
     single row is caught;
  2. dumps it with ``pg_dump -Fc`` (the format the runbook keeps);
  3. restores the dump into a fresh scratch database ``<name>_restore_drill``
     on the same server (or ``--restore-server``);
  4. fingerprints the restore and compares, table by table;
  5. drops the scratch database (``--keep`` leaves it for inspection).

Every stage is timed; the report is printed and, with ``--report``, written as
markdown for the runbook. Exit code 1 on any mismatch.

    python scripts/backup_restore_drill.py                       # URLs from the environment
    python scripts/backup_restore_drill.py --report drill.md --out-dir /tmp/backups

Needs ``pg_dump`` / ``pg_restore`` at least as new as the server, and a role
allowed to CREATE DATABASE on the restore server. Never point ``--restore-server``
at production: the scratch database is dropped and recreated.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import psycopg2

DATABASES = (("main", "DATABASE_URL"), ("backtest", "BACKTEST_DATABASE_URL"), ("news", "NEWS_DATABASE_URL"))


def with_db(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + dbname, parts.query, parts.fragment))


def db_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def fingerprint(url: str) -> Dict[str, Tuple[int, str]]:
    """{table: (rows, md5 over sorted row md5s)} for every table in public."""
    out: Dict[str, Tuple[int, str]] = {}
    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
        for (table,) in cur.fetchall():
            cur.execute(
                f'SELECT count(*), coalesce(md5(string_agg(h, \'\' ORDER BY h)), \'-\') '
                f'FROM (SELECT md5(t::text) AS h FROM public."{table}" t) s'
            )
            n, digest = cur.fetchone()
            out[table] = (int(n), digest)
    return out


def run(cmd: List[str]) -> float:
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return time.perf_counter() - t0


def drill_one(label: str, url: str, restore_server: str, out_dir: str, keep: bool) -> Dict:
    src = db_name(url)
    scratch = f"{src}_restore_drill"
    dump = os.path.join(out_dir, f"{src}.dump")
    report: Dict = {"database": label, "source": src}

    t0 = time.perf_counter()
    before = fingerprint(url)
    report["fingerprint_s"] = round(time.perf_counter() - t0, 2)

    report["dump_s"] = round(run(["pg_dump", "-Fc", "--no-owner", "--no-acl", "-f", dump, url]), 2)
    report["dump_bytes"] = os.path.getsize(dump)

    admin = with_db(restore_server, "postgres")
    conn = psycopg2.connect(admin)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{scratch}"')
        cur.execute(f'CREATE DATABASE "{scratch}"')
    target = with_db(restore_server, scratch)
    report["restore_s"] = round(run(["pg_restore", "--no-owner", "--no-acl", "--exit-on-error", "-d", target, dump]), 2)

    after = fingerprint(target)
    mismatches = sorted(
        t for t in set(before) | set(after) if before.get(t) != after.get(t)
    )
    report.update(
        tables=len(before),
        rows=sum(n for n, _ in before.values()),
        largest={t: before[t][0] for t in sorted(before, key=lambda t: -before[t][0])[:3]},
        mismatches=mismatches,
        ok=not mismatches,
    )
    if not keep:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{scratch}"')
    conn.close()
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--restore-server", default="", help="server URL for scratch restores (default: each source's own server)")
    ap.add_argument("--out-dir", default="", help="where to keep the dumps (default: a temp dir)")
    ap.add_argument("--keep", action="store_true", help="keep the scratch databases")
    ap.add_argument("--report", default="", help="write a markdown report here")
    args = ap.parse_args()

    out_dir = args.out_dir or tempfile.mkdtemp(prefix="stratoscan-backups-")
    os.makedirs(out_dir, exist_ok=True)
    results = []
    for label, env in DATABASES:
        url: Optional[str] = os.environ.get(env, "").strip()
        if not url:
            print(f"⏭  {label}: {env} not set — skipped")
            continue
        r = drill_one(label, url, args.restore_server or url, out_dir, args.keep)
        results.append(r)
        mark = "✅" if r["ok"] else "❌"
        print(f"{mark} {label:8} {r['tables']:2} tables {r['rows']:>9,} rows  dump {r['dump_s']:6.2f}s "
              f"({r['dump_bytes'] / 1e6:.1f} MB)  restore {r['restore_s']:6.2f}s  "
              f"{'identical' if r['ok'] else 'MISMATCH: ' + ', '.join(r['mismatches'])}")

    ok = bool(results) and all(r["ok"] for r in results)
    print(json.dumps({"ok": ok, "dumps": out_dir, "results": results}, indent=2))
    if args.report:
        lines = [f"# Backup/restore drill — {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}", "",
                 "| database | tables | rows | dump | size | restore | result |", "|---|---|---|---|---|---|---|"]
        for r in results:
            lines.append(f"| {r['database']} | {r['tables']} | {r['rows']:,} | {r['dump_s']} s | "
                         f"{r['dump_bytes'] / 1e6:.1f} MB | {r['restore_s']} s | "
                         f"{'identical' if r['ok'] else 'MISMATCH ' + ', '.join(r['mismatches'])} |")
        open(args.report, "w").write("\n".join(lines) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
