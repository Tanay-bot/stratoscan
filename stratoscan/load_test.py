"""Load test: where do /screen and /backtest/async break? (Day 13)

    python load_test.py --base http://127.0.0.1:8100            # against e2e_stack.py
    python load_test.py --levels 1,4,16,32 --seconds 15 --report LOAD_RESULTS.md

Steps concurrency up level by level and records, per endpoint and level:
throughput, p50/p95/p99 latency and the error rate. "Breaks" means the first
level where p95 exceeds the budget (``--screen-p95`` / ``--backtest-p95``) or
more than 1% of requests fail.

- /screen gets a DIFFERENT query each request (the RSI threshold and limit
  vary), so the 60-second screen cache cannot make it look faster than it is.
- /backtest/async is measured start-to-finish: start the job, poll until it is
  done, count the whole wait. Each worker uses its own Pro account (the
  monthly allowance is an entitlement, not what is being measured), promoted
  by the E2E admin.

Run it against the E2E stack (rate limiting OFF there, so this measures the
service, not the limiter). Production numbers differ — see LOAD.md for how
the measured figures and the provider quotas combine into the ceiling.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
PASSWORD = "correct-horse-battery-1"


def call(base: str, method: str, path: str, body: Any = None, token: str = "",
         timeout: float = 120.0) -> Tuple[int, Any, float]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", f"Bearer {token}")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except Exception:  # noqa: BLE001 — a dropped connection is a failure, not a crash
        return 0, None, time.perf_counter() - t0
    dt = time.perf_counter() - t0
    try:
        return status, json.loads(raw or b"null"), dt
    except ValueError:
        return status, None, dt


def _verify_from_mail(base: str, email: str) -> None:
    log = os.environ.get("E2E_MAIL_LOG", os.path.join(HERE, "data", "e2e_mail.jsonl"))
    for _ in range(100):
        if os.path.exists(log):
            for line in reversed(open(log, encoding="utf-8").read().splitlines()):
                m = json.loads(line)
                if email in m["to"]:
                    tok = re.search(r"verify-email\?token=([\w\-.~%]+)", m["text"])
                    if tok:
                        call(base, "GET", f"/auth/verify-email?token={tok.group(1)}")
                        return
        time.sleep(0.1)
    raise SystemExit(f"no verification email for {email}")


def admin_token(base: str) -> str:
    email = "admin@e2e.test"
    status, body, _ = call(base, "POST", "/auth/register", {"email": email, "password": PASSWORD})
    if status == 200:
        _verify_from_mail(base, email)
    status, body, _ = call(base, "POST", "/auth/login", {"email": email, "password": PASSWORD})
    if status != 200:
        raise SystemExit(f"admin login failed: {status} {body}")
    return body["access_token"]


def pro_account(base: str, admin: str, tag: str) -> str:
    email = f"load.{tag}.{int(time.time() * 1000)}@e2e.test"
    status, body, _ = call(base, "POST", "/auth/register", {"email": email, "password": PASSWORD})
    if status != 200:
        raise SystemExit(f"register failed: {status} {body}")
    token, uid = body["access_token"], body["user"]["id"]
    call(base, "PATCH", f"/admin/users/{uid}", {"plan": "pro"}, token=admin)
    return token


def pct(xs: List[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def run_level(fn, workers: int, seconds: float) -> Dict[str, Any]:
    lat: List[float] = []
    errors = 0
    lock = threading.Lock()
    stop = time.monotonic() + seconds

    def worker(i: int) -> None:
        nonlocal errors
        n = 0
        while time.monotonic() < stop:
            ok, dt = fn(i, n)
            n += 1
            with lock:
                if ok:
                    lat.append(dt)
                else:
                    errors += 1

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(worker, range(workers)))
    wall = time.monotonic() - t0
    total = len(lat) + errors
    return {
        "workers": workers, "requests": total, "ok": len(lat), "errors": errors,
        "error_rate": errors / total if total else 0.0,
        "throughput_per_s": len(lat) / wall if wall else 0.0,
        "p50": pct(lat, 50), "p95": pct(lat, 95), "p99": pct(lat, 99),
        "mean": statistics.fmean(lat) if lat else float("nan"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8100")
    ap.add_argument("--levels", default="1,4,8,16,32,64")
    ap.add_argument("--backtest-levels", default="1,2,4,8,16")
    ap.add_argument("--seconds", type=float, default=15)
    ap.add_argument("--screen-p95", type=float, default=2.0, help="p95 budget, seconds")
    ap.add_argument("--backtest-p95", type=float, default=60.0, help="start→done p95 budget, seconds")
    ap.add_argument("--report", default="")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    if call(base, "GET", "/health")[0] != 200:
        raise SystemExit(f"{base} is not up — start e2e_stack.py first")

    # ── /screen ──────────────────────────────────────────────────────────
    counter = iter(range(10**9))
    counter_lock = threading.Lock()

    def screen(i: int, n: int) -> Tuple[bool, float]:
        # Every request unique (25,000 query × limit combinations) so the
        # 60-second screen cache never answers instead of the database.
        with counter_lock:
            k = next(counter)
        q = f"large cap stocks with RSI below {20 + (k // 500) % 50}"
        status, body, dt = call(base, "POST", "/screen", {"query": q, "limit": 1 + k % 500}, timeout=30)
        return status == 200, dt

    results: Dict[str, List[Dict[str, Any]]] = {"screen": [], "backtest": []}
    for w in [int(x) for x in args.levels.split(",")]:
        r = run_level(screen, w, args.seconds)
        results["screen"].append(r)
        print(f"/screen        {w:>3} workers  {r['throughput_per_s']:7.1f}/s  p50 {r['p50']*1000:7.0f}ms  "
              f"p95 {r['p95']*1000:7.0f}ms  errors {r['error_rate']:.1%}", flush=True)

    # ── /backtest/async, start → done ───────────────────────────────────
    admin = admin_token(base)
    max_w = max(int(x) for x in args.backtest_levels.split(","))
    tokens = [pro_account(base, admin, str(i)) for i in range(max_w)]
    queries = ["oversold large cap stocks with RSI below 30", "large cap stocks with bullish momentum",
               "technology stocks with RSI below 40 trading above their 200-day moving average",
               "utilities with dividend yield above 3% and RSI below 35"]

    def backtest(i: int, n: int) -> Tuple[bool, float]:
        t0 = time.perf_counter()
        q = queries[(i + n) % len(queries)]
        status, body, _ = call(base, "POST", "/backtest/async", {"query": q, "top_n": 3}, token=tokens[i])
        if status != 200 or not body:
            return False, time.perf_counter() - t0
        job = body["job_id"]
        while time.perf_counter() - t0 < args.backtest_p95 * 3:
            s, b, _ = call(base, "GET", f"/backtest/async/{job}", token=tokens[i], timeout=30)
            if s == 200 and b and b.get("status") in ("done", "error", "interrupted"):
                return b.get("status") == "done", time.perf_counter() - t0
            time.sleep(0.25)
        return False, time.perf_counter() - t0

    for w in [int(x) for x in args.backtest_levels.split(",")]:
        r = run_level(backtest, w, max(args.seconds, 30))
        results["backtest"].append(r)
        print(f"/backtest/async {w:>2} workers  {r['throughput_per_s']*60:7.1f}/min  p50 {r['p50']:6.1f}s  "
              f"p95 {r['p95']:6.1f}s  errors {r['error_rate']:.1%}", flush=True)

    def breaking(rows, budget):
        for r in rows:
            if r["p95"] > budget or r["error_rate"] > 0.01:
                return r
        return None

    sb, bb = breaking(results["screen"], args.screen_p95), breaking(results["backtest"], args.backtest_p95)
    best_screen = max(results["screen"], key=lambda r: r["throughput_per_s"] if r["error_rate"] <= 0.01 else 0)
    best_bt = max(results["backtest"], key=lambda r: r["throughput_per_s"] if r["error_rate"] <= 0.01 else 0)
    summary = {
        "screen_peak_per_s": round(best_screen["throughput_per_s"], 1),
        "screen_breaks_at_workers": sb["workers"] if sb else None,
        "backtest_peak_per_min": round(best_bt["throughput_per_s"] * 60, 1),
        "backtest_breaks_at_workers": bb["workers"] if bb else None,
    }
    print(json.dumps(summary))
    if args.report:
        lines = [f"# Load test — {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}", "",
                 f"Target: `{base}` · {os.cpu_count()} CPUs · {args.seconds:.0f}s per level", "",
                 "| endpoint | workers | ok/s | p50 | p95 | p99 | errors |", "|---|---|---|---|---|---|---|"]
        for r in results["screen"]:
            lines.append(f"| /screen | {r['workers']} | {r['throughput_per_s']:.1f} | {r['p50']*1000:.0f} ms | "
                         f"{r['p95']*1000:.0f} ms | {r['p99']*1000:.0f} ms | {r['error_rate']:.1%} |")
        for r in results["backtest"]:
            lines.append(f"| /backtest/async (start→done) | {r['workers']} | {r['throughput_per_s']*60:.1f}/min | "
                         f"{r['p50']:.1f} s | {r['p95']:.1f} s | {r['p99']:.1f} s | {r['error_rate']:.1%} |")
        lines += ["", "```json", json.dumps(summary, indent=2), "```", ""]
        open(args.report, "w").write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
