"""System-tray companion for the Redmi Watch dashboard.

Separate process: talks to the service only over HTTP, so it can never
destabilize the watch connection. Run hidden with pythonw tray.py.

Icon color = status:  green connected · red disconnected · violet sleep recording.
Tooltip = live HR. Menu: open dashboard, sync, gentle cue, sleep toggle, exit.
"""
import json
import threading
import time
import urllib.request
import webbrowser

import pystray
from PIL import Image, ImageDraw

BASE = "http://127.0.0.1:8765"

state = {"connected": False, "hr": 0, "sleep": False, "ok": False}


def _get(path, timeout=4):
    return json.load(urllib.request.urlopen(BASE + path, timeout=timeout))


def _post(path):
    try:
        req = urllib.request.Request(BASE + path, b"{}", {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=4)
    except Exception:
        pass


def fetch():
    try:
        d = _get("/state")
        state["connected"] = bool(d.get("connected"))
        state["hr"] = (d.get("stats") or {}).get("hr_cur") or 0
        state["sleep"] = bool((d.get("sleep_session") or {}).get("active"))
        state["ok"] = True
    except Exception:
        state.update(connected=False, hr=0, ok=False)


def icon_img():
    if state["sleep"]:
        col = (139, 92, 246)      # violet: sleep recording
    elif state["connected"]:
        col = (52, 199, 123)      # green: live
    else:
        col = (235, 87, 87)       # red: disconnected / service down
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([18, 2, 46, 12], radius=4, fill=(70, 70, 78))    # strap top
    d.rounded_rectangle([18, 52, 46, 62], radius=4, fill=(70, 70, 78))   # strap bottom
    d.rounded_rectangle([10, 8, 54, 56], radius=14, fill=(38, 38, 44))   # case
    d.rounded_rectangle([16, 14, 48, 50], radius=10, fill=col)           # face = status
    return img


def tooltip():
    if not state["ok"]:
        return "Redmi Watch: сервис не отвечает"
    parts = []
    parts.append("пульс %d" % state["hr"] if state["hr"] else "нет пульса")
    if state["sleep"]:
        parts.append("запись сна")
    parts.append("на связи" if state["connected"] else "нет связи")
    return "Redmi Watch: " + " · ".join(parts)


def run():
    icon = pystray.Icon("redmi_watch", icon_img(), tooltip())

    def do_open(_i, _m):
        webbrowser.open(BASE + "/")

    def do_sync(_i, _m):
        _post("/sync")

    def do_cue(_i, _m):
        _post("/cue")

    def do_sleep(_i, _m):
        _post("/sleep/stop" if state["sleep"] else "/sleep/start")
        fetch()

    def do_exit(_i, _m):
        icon.stop()

    icon.menu = pystray.Menu(
        pystray.MenuItem("Открыть дашборд", do_open, default=True),
        pystray.MenuItem("Синхронизировать", do_sync),
        pystray.MenuItem("Мягкий сигнал на часы", do_cue),
        pystray.MenuItem(lambda _: "Остановить запись сна" if state["sleep"] else "Начать запись сна", do_sleep),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Выход", do_exit),
    )

    def poll():
        while True:
            fetch()
            try:
                icon.icon = icon_img()
                icon.title = tooltip()
            except Exception:
                pass
            time.sleep(5)

    threading.Thread(target=poll, daemon=True).start()
    icon.run()


if __name__ == "__main__":
    fetch()
    run()
