"""Hermetic tests for the CLI entry point (src/main.py) — no network, no live DB.

Run: python -m tests.test_main

Covers the CLI layer that had ZERO direct test coverage:

1. create_parser: all subcommands present, screen/backtest/refresh-news/warm-cache
   options parse correctly with defaults.
2. Bad --log-level rejected by argparse (SystemExit).
3. cmd_backtest date validation (Tier 4): malformed --start/--end aborts BEFORE
   any LLM call or engine construction; start > end aborts; valid dates thread
   through to backtest_top_results.
4. main([]) with no command prints help and returns 0 (safe path).
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest import mock  # noqa: E402

import src.main as main_mod  # noqa: E402


# ── 1. Parser structure ─────────────────────────────────────────────────────
print("1. create_parser structure...")

parser = main_mod.create_parser()
choices = set()
for a in parser._actions:
    if getattr(a, "choices", None):
        choices |= set(a.choices)
for cmd in (
    "screen", "backtest", "refresh", "refresh-news", "warm-cache",
    "setup", "scheduler", "enrich", "serve",
):
    assert cmd in choices, f"missing subcommand: {cmd}"

# screen: query (nargs+), --limit default 50, --enrich flag
args = parser.parse_args(["screen", "small", "cap", "tech", "--limit", "25"])
assert args.command == "screen"
assert args.query == ["small", "cap", "tech"]
assert args.limit == 25 and args.enrich is False

# backtest: --top-n / --start / --end defaults
args = parser.parse_args(["backtest", "buy", "rsi", "--top-n", "3",
                          "--start", "2020-01-01", "--end", "2024-12-31"])
assert args.command == "backtest"
assert args.top_n == 3 and args.start == "2020-01-01" and args.end == "2024-12-31"

# refresh-news + warm-cache: --limit defaults None
args = parser.parse_args(["refresh-news"])
assert args.command == "refresh-news" and args.limit is None
args = parser.parse_args(["warm-cache", "-n", "7"])
assert args.command == "warm-cache" and args.limit == 7

# serve: host/port/no-reload/log-level
args = parser.parse_args(["serve", "--port", "8080", "--no-reload"])
assert args.command == "serve"
assert args.port == 8080 and args.no_reload is True and args.host == "0.0.0.0"
print("✅ all subcommands + options parse with correct defaults")


# ── 2. Argparse validation ──────────────────────────────────────────────────
print("2. argparse validation...")

try:
    parser.parse_args(["serve", "--log-level", "bogus"])
    raise AssertionError("bad --log-level must be rejected")
except SystemExit:
    pass
print("✅ invalid --log-level rejected")


# ── 3. cmd_backtest date validation (Tier 4 contract) ───────────────────────
print("3. backtest date validation...")

# Malformed --start aborts BEFORE the LLM interpreter or engine construction
with mock.patch("src.interpreter.interpret_query") as m_iq, \
     mock.patch("src.backtester.BacktestEngine") as m_be:
    rc = main_mod.main(["backtest", "buy", "rsi", "--start", "2020/01/01"])
assert rc == 0
assert not m_iq.called, "bad --start must abort before the LLM call"
assert not m_be.called

# start > end aborts before the engine
with mock.patch("src.interpreter.interpret_query"), \
     mock.patch("src.backtester.BacktestEngine") as m_be2:
    main_mod.main(["backtest", "q", "--start", "2025-01-01", "--end", "2020-01-01"])
assert not m_be2.called

# Valid dates thread through to backtest_top_results
engine_inst = mock.Mock()
engine_inst.backtest_top_results.return_value = {}
with mock.patch("src.interpreter.interpret_query") as m_iq3, \
     mock.patch("src.backtester.BacktestEngine", return_value=engine_inst):
    rc = main_mod.main(["backtest", "buy", "rsi",
                        "--start", "2020-01-01", "--end", "2024-12-31", "-n", "4"])
assert rc == 0 and m_iq3.called
_, kwargs = engine_inst.backtest_top_results.call_args
assert kwargs["start_date"] == "2020-01-01"
assert kwargs["end_date"] == "2024-12-31"
assert kwargs["top_n"] == 4
print("✅ malformed date, start>end, and valid-date threading")


# ── 4. main() no-command help path ──────────────────────────────────────────
print("4. main([]) help path...")

# main() has no command -> prints help (spied via the argparse class method)
with mock.patch("argparse.ArgumentParser.print_help") as m_help:
    rc = main_mod.main([])
assert rc == 0
assert m_help.called, "no-command main() must print help"
print("✅ main([]) returns 0 (help path)")

mock.patch.stopall()
print()
print("🎯 All main CLI tests pass!")
