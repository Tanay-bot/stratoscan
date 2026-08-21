#!/usr/bin/env python3
"""Launch the Stratoscan STAGING API fully detached (port 8010)."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "staging_api_server.log")
PIDFILE = os.path.join(HERE, "staging_server.pid")

logf = open(LOG, "ab")
proc = subprocess.Popen(
    [sys.executable, "run_staging.py", "--no-reload", "--port", "8010"],
    cwd=HERE,
    stdin=subprocess.DEVNULL,
    stdout=logf,
    stderr=subprocess.STDOUT,
    start_new_session=True,
)
with open(PIDFILE, "w") as f:
    f.write(str(proc.pid))
print(f"STAGING_PID={proc.pid}", flush=True)
