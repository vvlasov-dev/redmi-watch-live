"""Shim: re-exports everything from core.store.

Existing callers (dashboard.py, sleep_engine.py) keep working unchanged;
new code should import from core.store directly.
"""
from core.store import *  # noqa: F401, F403
from core.store import _lock, _db  # noqa: F401
