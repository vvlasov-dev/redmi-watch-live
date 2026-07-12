"""Shim: re-exports from core.client. Logic lives there; this file stays as
a standalone CLI entry point so `python client.py --list` keeps working."""
from core.client import *  # noqa: F401, F403

if __name__ == "__main__":
    from core.client import main
    main()
