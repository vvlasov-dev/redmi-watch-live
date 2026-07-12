"""Queue a short summary to be delivered to the watch as a notification.

Writes the text to a summary file that the service picks up and pushes once,
so an external task-done trigger gets exactly one clean ping per run.

Usage:  python watch_send.py "Заголовок" "Текст сводки"
"""
import os
import sys

SUMMARY_FILE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                            "RedmiWatchLive", ".watch_summary")


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
