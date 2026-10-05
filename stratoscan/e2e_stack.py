"""The end-to-end stack: a real API over a real (local, throwaway) Postgres,
with no network and no paid service (Day 13).

    python e2e_stack.py            # reset + seed the E2E databases, then serve
    python e2e_stack.py --seed-only

What it stands up, all on 127.0.0.1:

- Two fresh databases, created on the server named by ``E2E_ADMIN_DSN``
  (default: the local cluster used in CI): ``stratoscan_e2e`` (main: users,
  jobs, shares, the screener universe) and ``stratoscan_e2e_bt`` (backtest
  history). Both are DROPPED and recreated on every run — nothing here may
  ever point at a real database (the names are fixed, and anything not
  ending in ``_e2e``/``_e2e_bt`` is refused).
- A small, honest universe: 12 large caps across five sectors in
  ``stock_master`` (fresh ``updated_at``), and 10 years of SYNTHETIC daily
  prices for each of them plus SPY in ``historical_prices``, ending today —
  so every backtest reads the cache and nothing is downloaded.
- A mail sink: a tiny SMTP server that decodes each message and appends
  ``{to, subject, text}`` as JSON lines to ``E2E_MAIL_LOG``. The browser
  tests read verification and reset links from it, exactly as a person
  would read their inbox.
- The API itself (uvicorn, in this process) with E2E settings: email
  verification REQUIRED, rate limiting off (the adversarial suite owns
  that), no LLM provider (templates parse deterministically; explanations
  show their findings without a narrative), ``ADMIN_EMAILS=admin@e2e.test``.
  The first-100 grandfathering is pre-exhausted so new E2E accounts are
  Free, which is what the tier journey needs.

Playwright starts this through ``frontend/playwright.config.ts``.
"""

from __future__ import annotations

import argparse
import email
import email.policy
import json
import os
import socket
import socketserver
import sys
import threading
from datetime import date, timedelta
from urllib.parse import urlsplit, urlunsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

API_PORT = int(os.environ.get("E2E_API_PORT", "8100"))
WEB_ORIGIN = os.environ.get("E2E_WEB_ORIGIN", "http://127.0.0.1:4174")
SMTP_PORT = int(os.environ.get("E2E_SMTP_PORT", "2525"))
MAIL_LOG = os.environ.get("E2E_MAIL_LOG", os.path.join(HERE, "data", "e2e_mail.jsonl"))
ADMIN_DSN = os.environ.get("E2E_ADMIN_DSN", "postgresql://stratoscan:stratoscan@127.0.0.1:5432/postgres")
MAIN_DB, BT_DB = "stratoscan_e2e", "stratoscan_e2e_bt"

# (symbol, name, sector, industry, market cap $, price, rsi, beta, pe, div %, roe %)
UNIVERSE = [
    ("AAPL", "Apple Inc", "Technology", "Consumer Electronics", 3.1e12, 190.0, 28.0, 1.2, 29.0, 0.5, 150.0),
    ("MSFT", "Microsoft Corp", "Technology", "Software", 3.0e12, 410.0, 36.0, 0.9, 34.0, 0.7, 38.0),
    ("NVDA", "NVIDIA Corp", "Technology", "Semiconductors", 2.8e12, 115.0, 62.0, 1.7, 55.0, 0.0, 110.0),
    ("JNJ", "Johnson & Johnson", "Healthcare", "Drug Manufacturers", 3.8e11, 155.0, 27.0, 0.5, 14.0, 3.2, 21.0),
    ("PFE", "Pfizer Inc", "Healthcare", "Drug Manufacturers", 1.6e11, 28.0, 31.0, 0.6, 12.0, 6.0, 9.0),
    ("JPM", "JPMorgan Chase & Co", "Financial Services", "Banks", 5.9e11, 205.0, 33.0, 1.1, 11.5, 2.3, 17.0),
    ("BAC", "Bank of America Corp", "Financial Services", "Banks", 3.1e11, 39.0, 29.0, 1.3, 12.5, 2.6, 10.0),
    ("XOM", "Exxon Mobil Corp", "Energy", "Oil & Gas", 4.6e11, 110.0, 38.0, 0.8, 13.0, 3.4, 18.0),
    ("CVX", "Chevron Corp", "Energy", "Oil & Gas", 2.8e11, 150.0, 34.0, 0.9, 14.0, 4.1, 13.0),
    ("NEE", "NextEra Energy", "Utilities", "Utilities", 1.5e11, 72.0, 30.0, 0.5, 19.0, 3.1, 11.0),
    ("DUK", "Duke Energy", "Utilities", "Utilities", 8.5e10, 104.0, 32.0, 0.4, 18.0, 4.0, 8.0),
    ("KO", "Coca-Cola Co", "Consumer Defensive", "Beverages", 2.7e11, 62.0, 34.0, 0.6, 24.0, 3.1, 40.0),
]


def _db_url(name: str) -> str:
    parts = urlsplit(ADMIN_DSN)
    return urlunsplit(parts._replace(path=f"/{name}"))


def reset_databases() -> None:
    import psycopg2

    for name in (MAIN_DB, BT_DB):
        if not (name.endswith("_e2e") or name.endswith("_e2e_bt")):
            raise SystemExit(f"refusing to reset {name!r}")
    conn = psycopg2.connect(ADMIN_DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        for name in (MAIN_DB, BT_DB):
            cur.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
            cur.execute(f"CREATE DATABASE {name}")
    conn.close()


def _synthetic_prices(seed: int, start: date, end: date, start_price: float):
    import numpy as np
    import pandas as pd

    idx = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    # Trending with pullbacks: enough RSI dips and trend breaks that every
    # template trades, without any single outcome being baked in.
    drift = 0.0004 + 0.0002 * np.sin(np.arange(len(idx)) / 160.0)
    close = start_price * np.exp(np.cumsum(rng.normal(drift, 0.016, len(idx))))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.002, len(idx)))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, len(idx))))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, len(idx))))
    return [
        {"date": d.date(), "open": float(o), "high": float(h), "low": float(lo),
         "close": float(c), "raw_close": float(c), "volume": int(5_000_000 + 1_000_000 * (i % 7)),
         "dividends": 0.0, "stock_splits": 0.0}
        for i, (d, o, h, lo, c) in enumerate(zip(idx, open_, high, low, close))
    ]


def seed() -> None:
    import subprocess

    import psycopg2
    from psycopg2.extras import execute_values

    reset_databases()
    env = {**os.environ, "DATABASE_URL": _db_url(MAIN_DB)}
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=HERE, env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"alembic failed:\n{r.stderr[-3000:]}")

    main = psycopg2.connect(_db_url(MAIN_DB))
    with main, main.cursor() as cur:
        from src.database import STOCK_MASTER_SCHEMA  # noqa: F401  (alembic created it)

        rows = []
        for sym, name, sector, industry, cap, price, rsi, beta, pe, div, roe in UNIVERSE:
            rows.append((sym, name, sector, industry, cap, price, 0.4, 12_000_000, 11_000_000, pe,
                         price / pe, div, beta, rsi, price * 0.97, price * 0.90, price * 1.12,
                         price * 0.75, roe, 12.0, 45.0, 22.0, 8.0, 10.0, 0.6, price * 0.02,
                         price * 1.05, price * 0.95))
        execute_values(cur, """
            INSERT INTO stock_master (symbol, company_name, sector, industry, market_cap,
                current_price, price_change_pct, volume, avg_volume, pe_ratio, eps_ttm,
                dividend_yield, beta, rsi_14, sma_50, sma_200, high_52w, low_52w, roe_ttm,
                roa_ttm, gross_margin_ttm, net_margin_ttm, revenue_growth_ttm, eps_growth_ttm,
                macd_12_26, atr_14, bollinger_upper, bollinger_lower, updated_at)
            VALUES %s
        """, rows, template="(" + ",".join(["%s"] * 28) + ", NOW())")
        # New E2E accounts must be Free: the first-100 Pro slots are spent.
        cur.execute("UPDATE app_counters SET value = 100 WHERE name = 'grandfathered_pro'")

    from src.database import BacktestDB

    bt = BacktestDB(database_url=_db_url(BT_DB))
    bt.init_schema()
    bt.close()
    conn = psycopg2.connect(_db_url(BT_DB))
    end = date.today()
    start = end - timedelta(days=365 * 10 + 30)
    with conn, conn.cursor() as cur:
        for i, (sym, *_rest) in enumerate(UNIVERSE + [("SPY",)]):
            bars = _synthetic_prices(1000 + i, start, end, 50.0 + 10 * i)
            execute_values(cur, """
                INSERT INTO historical_prices (symbol, date, open, high, low, close, raw_close,
                                               volume, dividends, stock_splits)
                VALUES %s ON CONFLICT DO NOTHING
            """, [(sym, b["date"], b["open"], b["high"], b["low"], b["close"], b["raw_close"],
                   b["volume"], b["dividends"], b["stock_splits"]) for b in bars])
    conn.close()
    print(f"seeded {len(UNIVERSE)} stocks + SPY, {start}..{end}", flush=True)


# ── the mail sink ───────────────────────────────────────────────────────────

class _SMTPHandler(socketserver.StreamRequestHandler):
    def _send(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())

    def handle(self) -> None:
        self._send("220 e2e-mail-sink ESMTP")
        rcpt: list = []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            cmd = raw.decode(errors="replace").strip()
            verb = cmd.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                self._send("250 e2e-mail-sink")
            elif verb == "MAIL":
                rcpt = []
                self._send("250 OK")
            elif verb == "RCPT":
                rcpt.append(cmd.split(":", 1)[1].strip(" <>"))
                self._send("250 OK")
            elif verb == "DATA":
                self._send("354 End data with <CR><LF>.<CR><LF>")
                lines = []
                while True:
                    line = self.rfile.readline()
                    if not line or line in (b".\r\n", b".\n"):
                        break
                    lines.append(line[1:] if line.startswith(b"..") else line)
                self._record(rcpt, b"".join(lines))
                self._send("250 OK: queued")
            elif verb == "QUIT":
                self._send("221 Bye")
                return
            else:
                self._send("250 OK")

    @staticmethod
    def _record(rcpt: list, data: bytes) -> None:
        msg = email.message_from_bytes(data, policy=email.policy.default)
        part = msg.get_body(preferencelist=("plain", "html"))
        text = part.get_content() if part else ""
        with open(MAIL_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"to": rcpt, "subject": str(msg["subject"]), "text": text}) + "\n")


class _ThreadedSMTP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_mail_sink() -> None:
    os.makedirs(os.path.dirname(MAIL_LOG), exist_ok=True)
    open(MAIL_LOG, "w").close()
    server = _ThreadedSMTP(("127.0.0.1", SMTP_PORT), _SMTPHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def configure_env() -> None:
    """Set the E2E environment BEFORE anything imports src.config (settings
    are read once, at import)."""
    os.environ.update({
        "APP_ENV": "development",
        "DATABASE_URL": _db_url(MAIN_DB),
        "BACKTEST_DATABASE_URL": _db_url(BT_DB),
        "NEWS_DATABASE_URL": "",
        "JWT_SECRET": os.environ.get("JWT_SECRET") or "e2e-only-not-a-secret-" + "x" * 16,
        "SMTP_HOST": "127.0.0.1",
        "SMTP_PORT": str(SMTP_PORT),
        "SMTP_STARTTLS": "false",
        "SMTP_USERNAME": "",
        "SENDGRID_API_KEY": "",
        "EMAIL_FROM_ADDRESS": "e2e@stratoscan.test",
        "EMAIL_VERIFICATION_REQUIRED": "true",
        "APP_BASE_URL": WEB_ORIGIN,
        "SHARE_BASE_URL": f"http://127.0.0.1:{API_PORT}",
        "CORS_ALLOW_ORIGINS": WEB_ORIGIN,
        "RATE_LIMIT_ENABLED": "false",
        "REDIS_RATE_LIMIT_ENABLED": "false",
        "ADMIN_EMAILS": "admin@e2e.test",
        "LLM_PROVIDERS": "",
        "SCREENER_LLM_KEY": "",
        "LLM_BASE_URL": "http://127.0.0.1:9/none",
        "JOB_MONITOR_ENABLED": "false",
        "METRICS_ENABLED": "false",
        "DIGEST_ENABLED": "false",
    })


def serve() -> None:
    start_mail_sink()
    import uvicorn

    uvicorn.run("src.api:app", host="127.0.0.1", port=API_PORT, log_level="warning")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-only", action="store_true")
    args = ap.parse_args()
    configure_env()
    seed()
    if not args.seed_only:
        serve()


if __name__ == "__main__":
    socket.setdefaulttimeout(30)
    main()
