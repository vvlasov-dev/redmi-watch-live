"""Morning review of a sleep-tracking session.

Standalone (HTTP + session file only — never touches the service).
Answers the key research question: did the watch hand over an IN-PROGRESS
sleep file (stages mid-night), or only a finished one after wake-up?

Usage:
  python morning_report.py            # print the analysis
  python morning_report.py --send     # ...and push a short summary to the watch
"""
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
SESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sleep_session.json")


def fmt_t(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def load():
    sess = None
    try:
        with open(SESS, encoding="utf-8") as f:
            sess = json.load(f)
    except Exception:
        pass
    state = None
    try:
        state = json.load(urllib.request.urlopen(BASE + "/state", timeout=6))
    except Exception:
        pass
    return sess, state


def analyze(sess, state):
    out = []
    if not sess or not sess.get("start_ts"):
        return ["Сессии сна не найдено (.sleep_session.json пуст)."], ""
    probes = sess.get("probes") or []
    start = sess["start_ts"]
    dur_h = (int(time.time()) - start) / 3600 if sess.get("active") else None
    out.append("Сессия: старт %s%s, зондов %d" % (
        fmt_t(start), " (ещё активна, %.1f ч)" % dur_h if dur_h else "", len(probes)))

    # THE research question: interim sleep files
    sf = [p for p in probes if p.get("kind") == "sleep_file"]
    interim = [p for p in sf if p.get("is_awake") is False]
    finished = [p for p in sf if p.get("is_awake") is not False]
    if interim:
        first = interim[0]
        out.append("ГЛАВНОЕ: промежуточный файл сна ЕСТЬ — часы отдают сон в процессе!")
        out.append("  первый: %s (стадий %s, сон %s мин); всего промежуточных: %d" % (
            fmt_t(first["ts"]), first.get("stages"), first.get("asleep_min"), len(interim)))
        out.append("  => движок может использовать СТАДИИ ОТ ЧАСОВ (бэкенд A).")
    elif sf:
        out.append("ГЛАВНОЕ: промежуточных файлов сна НЕ было — только завершённый после пробуждения (%d шт)." % len(finished))
        out.append("  => движок работает по пульсу (бэкенд B): оценка REM по HR-треку.")
    else:
        out.append("ГЛАВНОЕ: за сессию не пришло ни одного файла сна — часы отдают его только после пробуждения "
                   "(или синки не добегали). => бэкенд B (по пульсу).")

    ds = [p for p in probes if p.get("kind") == "devstate" and p.get("asleep") is not None]
    asleep_marks = [p for p in ds if p.get("asleep")]
    if asleep_marks:
        out.append("Статус спишь/бодрствуешь от часов приходил: да (первый 'спишь' в %s)." % fmt_t(asleep_marks[0]["ts"]))
    else:
        out.append("Живой статус 'спишь' от часов не приходил (пуш не сработал) — детект пробуждения тоже по пульсу.")

    det = [p for p in probes if p.get("kind") == "details"]
    if det:
        out.append("Минутные данные докачивались %d раз (последний %s) — HR-трек ночи есть." % (len(det), fmt_t(det[-1]["ts"])))

    # last-night sleep record (after the morning sync)
    watch_line = ""
    sl = (state or {}).get("sleep") or {}
    if sl.get("asleep_min"):
        h, m = divmod(int(sl["asleep_min"]), 60)
        out.append("Итог ночи от часов: %dч %02dм (глубокий %s / REM %s / лёгкий %s мин), уснул %s, проснулся %s." % (
            h, m, sl.get("deep_min"), sl.get("rem_min"), sl.get("light_min"),
            fmt_t(sl["bed_ts"]) if sl.get("bed_ts") else "?",
            fmt_t(sl["wake_ts"]) if sl.get("wake_ts") else "?"))
        watch_line = "Спал %dч %02dм: глубокий %sм, REM %sм. " % (h, m, sl.get("deep_min"), sl.get("rem_min"))

    verdict = ("Промежуточный файл сна есть - используем стадии от часов." if interim
               else "Промежуточного файла нет - REM оцениваем по пульсу.")
    watch_msg = (watch_line + "Разбор ночи: зондов %d. %s" % (len(probes), verdict))[:380]
    return out, watch_msg


def main():
    sess, state = load()
    lines, watch_msg = analyze(sess, state)
    print("\n".join(lines))
    if "--send" in sys.argv and watch_msg:
        body = {"title": "Утренний отчёт", "body": watch_msg}
        req = urllib.request.Request(BASE + "/notify", json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     {"Content-Type": "application/json; charset=utf-8"})
        urllib.request.urlopen(req, timeout=6)
        print("\n[отправлено на часы]")


if __name__ == "__main__":
    main()
