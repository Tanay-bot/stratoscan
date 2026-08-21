"""Run a python module/command fully detached (new session) with logging.

Usage: python3 run_detached.py --log <file> -- <cmd...>
e.g.:  python3 run_detached.py --log news_refresh.log -- python -m src.main refresh-news
"""
import subprocess
import sys


def main() -> int:
    argv = sys.argv[1:]
    log_file = "detached.log"
    if argv and argv[0] == "--log":
        log_file = argv[1]
        argv = argv[2:]
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        print("usage: run_detached.py --log <file> -- <cmd...>")
        return 2
    with open(log_file, "ab") as f:
        proc = subprocess.Popen(
            argv,
            stdout=f,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    print(proc.pid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
