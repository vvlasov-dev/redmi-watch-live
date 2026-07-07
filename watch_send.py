"""Queue a watch summary that the Stop hook delivers when the turn ends.

Used by the notify-watch skill. Writing to the summary file (instead of POSTing
directly) means the automatic Stop hook is the single delivery point — you get
exactly one clean ping per turn, with the summary YOU wrote taking priority over
the hook's auto-distilled fallback.

Usage:  python watch_send.py "Заголовок" "Текст сводки"
(the title is currently ignored; the hook uses a fixed short title)
"""
import os
import sys

SUMMARY_FILE = r"C:\Users\L5DKA\AppData\Local\RedmiWatchLive\.watch_summary"


def main():
    body = sys.argv[2] if len(sys.argv) > 2 else (sys.argv[1] if len(sys.argv) > 1 else "")
    try:
        os.makedirs(os.path.dirname(SUMMARY_FILE), exist_ok=True)
        with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
            f.write(body.strip())
        print("summary queued (delivered to watch when the turn ends)")
    except Exception as e:
        print("could not queue summary: %s" % e)


if __name__ == "__main__":
    main()
