"""Shim: re-exports from features.sleep.engine. Logic lives there.
test_sleep_engine.py imports this directly and still works unchanged."""
from features.sleep.engine import *  # noqa: F401, F403
from features.sleep.engine import (  # explicit re-exports used by tests
    _wake_ok_phase, _daycue_due, decide, CFG,
)
