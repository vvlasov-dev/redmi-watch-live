"""Run the full protocol regression suite. Exit non-zero if anything fails.

Usage:  python run_tests.py
"""
import subprocess
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = ["selftest.py", "test_activity.py", "test_sleep_engine.py",
          "test_todos.py", "test_watch_io.py"]

failed = 0
for s in SUITES:
    print(f"\n===== {s} =====")
    r = subprocess.run([sys.executable, os.path.join(HERE, s)])
    if r.returncode != 0:
        failed += 1

print(f"\n===== {'ALL PASSED' if not failed else str(failed) + ' SUITE(S) FAILED'} =====")
sys.exit(1 if failed else 0)
