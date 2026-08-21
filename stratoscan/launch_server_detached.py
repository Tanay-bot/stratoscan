#!/usr/bin/env python3
"""Launch the Stratoscan API server fully detached from the calling session.

Starts `run.py --no-reload --port 8000` in a new session (setsid) with all
stdin/stdout/stderr redirected to api_server.log, so it survives the tool
call that spawned it and keeps running until explicitly killed.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "api_server.log")
PIDFILE = os.path.join(HERE, "server.pid")

logf = open(LOG, "ab")

proc = subprocess.Popen(
    [sys.executable, "run.py", "--no-reload", "--port", "8000"],
    cwd=HERE,
    stdin=subprocess.DEVNULL,
    stdout=logf,
    stderr=subprocess.STDOUT,
    start_new_session=True,
)

with open(PIDFILE, "w") as f:
    f.write(str(proc.pid))

print(f"DETACHED_PID={proc.pid}", flush=True)
