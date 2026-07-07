"""Claude Code Stop hook: ping the watch with a short summary when Claude finishes.

Reads the hook JSON on stdin, pulls the last assistant message from the
transcript, distills it to a watch-friendly line, and POSTs it to the local
dashboard's /notify endpoint (which strips emoji and sends it to the watch).

Debounced so rapid back-and-forth doesn't spam the wrist. Fails silently:
a hook must never block Claude.

Wire via a Stop hook in settings.json:
  { "type": "command",
    "command": "<python> <path>\\watch_notify_hook.py" }
"""
import json
import os
import re
import sys
import time
import urllib.request

NOTIFY_URL = "http://127.0.0.1:8765/notify"
DEBOUNCE_SEC = 90          # at most one AUTO ping per this window (explicit summaries bypass)
MIN_CHARS = 80             # skip trivial one-liner turns (auto mode only)
_HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(_HERE, ".watch_notify_last")
SUMMARY_FILE = os.path.join(_HERE, ".watch_summary")   # a summary I wrote this turn


def _last_assistant_text(transcript_path):
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    last = ""
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                msg = obj.get("message", obj)
                role = msg.get("role") or obj.get("role") or obj.get("type")
                if role != "assistant":
                    continue
                content = msg.get("content")
                if isinstance(content, str):
                    txt = content
                elif isinstance(content, list):
                    txt = "\n".join(b.get("text", "") for b in content
                                    if isinstance(b, dict) and b.get("type") == "text")
                else:
                    txt = ""
                if txt.strip():
                    last = txt
    except Exception:
        return ""
    return last


def _distill(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.S)     # code blocks
    text = re.sub(r"`[^`]*`", "", text)                     # inline code
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)    # links -> label
    text = re.sub(r"^[\s>#*\-|]+", "", text, flags=re.M)    # md line prefixes
    text = re.sub(r"[*_#>`|]", "", text)                    # stray md
    text = re.sub(r"\s+", " ", text).strip()
    # Prefer whole sentences: a clean thought reads better on a wrist than a
    # hard char cut mid-word. Take sentences up to ~LIMIT; fall back to a word cut.
    LIMIT = 220
    if len(text) <= LIMIT:
        return text
    head = text[:LIMIT]
    ends = list(re.finditer(r"[.!?…:](?:\s|$)", head))
    if ends and ends[-1].end() >= 70:
        return head[:ends[-1].end()].strip()
    return head.rsplit(" ", 1)[0].strip() + "…"


def _debounced():
    now = time.time()
    try:
        if os.path.exists(STATE) and now - os.path.getmtime(STATE) < DEBOUNCE_SEC:
            return True
    except Exception:
        pass
    try:
        with open(STATE, "w") as f:
            f.write(str(now))
    except Exception:
        pass
    return False


def _explicit_summary():
    """A summary I wrote this turn via watch_send.py — preferred over auto-distill."""
    try:
        if os.path.exists(SUMMARY_FILE) and time.time() - os.path.getmtime(SUMMARY_FILE) < 300:
            with open(SUMMARY_FILE, encoding="utf-8") as f:
                body = f.read().strip()
            os.remove(SUMMARY_FILE)
            return body or None
    except Exception:
        pass
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    body = _explicit_summary()
    if body:
        _debounced()  # mark, so the next auto-ping doesn't double-fire
    else:
        body = _distill(_last_assistant_text(data.get("transcript_path")))
        if len(body) < MIN_CHARS:
            return
        if _debounced():
            return
    payload = json.dumps({"title": "Claude закончил", "body": body}).encode("utf-8")
    req = urllib.request.Request(NOTIFY_URL, data=payload,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        urllib.request.urlopen(req, timeout=4)
    except Exception:
        pass


if __name__ == "__main__":
    main()
