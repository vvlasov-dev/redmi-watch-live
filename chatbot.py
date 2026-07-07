"""Local chat backend: talks to the Anthropic Messages API with the user's
watch data + personal context injected. The API key is read from config/env
and never stored by this code beyond the running process.
"""
import json
import os
import time
import urllib.request
import urllib.error

API_URL = "https://api.anthropic.com/v1/messages"
_cfg = {"api_key": "", "model": "claude-sonnet-4-6", "personal_path": None}


def configure(api_key="", model="claude-sonnet-4-6", personal_path=None):
    _cfg["api_key"] = (api_key or os.environ.get("ANTHROPIC_API_KEY", "")).strip()
    _cfg["model"] = model or "claude-sonnet-4-6"
    _cfg["personal_path"] = personal_path


def _d(ts):
    try:
        return time.strftime("%m-%d", time.localtime(int(ts)))
    except Exception:
        return "?"


def build_system(snapshot_fn=None, days_fn=None):
    parts = [
        "Ты — персональный ассистент по здоровью и данным пользователя (GigaChad). "
        "Отвечай кратко, по делу, на русском. Данные ниже — с его умных часов "
        "Redmi Watch 5 Active и из личных заметок. Не выдумывай показатели, "
        "которых нет; если данных мало — так и скажи."
    ]
    try:
        snap = snapshot_fn() if snapshot_fn else {}
        st = snap.get("stats", {}) or {}
        bat = snap.get("battery") or {}
        h = snap.get("health") or {}
        parts.append(
            "Текущее: пульс сейчас=%s, покой=%s, макс=%s, шаги=%s, калории=%s, батарея=%s%%, vitality=%s." % (
                st.get("hr_cur"), h.get("hr_resting"), h.get("hr_max"),
                h.get("steps") if h.get("steps") is not None else st.get("steps_now"),
                h.get("calories"), bat.get("level"), h.get("vitality")))
        sl = snap.get("sleep")
        if sl:
            parts.append("Последний сон: всего=%sмин (глубокий=%s, лёгкий=%s, REM=%s, бодрств=%s)." % (
                sl.get("asleep_min"), sl.get("deep_min"), sl.get("light_min"),
                sl.get("rem_min"), sl.get("awake_min")))
    except Exception:
        pass
    try:
        days = days_fn(14) if days_fn else []
        if days:
            trend = "; ".join(
                "%s: шаги=%s, пульс~%s, стресс=%s, vitality=%s" % (
                    _d(d.get("date_ts")), d.get("steps"), d.get("hr_avg"),
                    d.get("stress_avg"), d.get("vitality"))
                for d in days[-7:])
            parts.append("История последних дней: " + trend)
    except Exception:
        pass
    p = _cfg["personal_path"]
    if p and os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                txt = f.read().strip()
            if txt:
                parts.append("Личный контекст пользователя (его заметки/цели):\n" + txt[:4000])
        except Exception:
            pass
    return "\n\n".join(parts)


def handle_chat(messages, snapshot_fn=None, days_fn=None):
    """messages: [{role:'user'|'assistant', content:str}, ...]. Returns {'reply': str}."""
    if not _cfg["api_key"]:
        return {"reply": "Чат не настроен. Впиши свой Anthropic API-ключ в config.json "
                         "(поле \"anthropic_api_key\") и перезапусти сервис. Ключ остаётся "
                         "локально на этом ПК."}
    # keep only valid roles/content
    clean = [{"role": m.get("role", "user"), "content": str(m.get("content", ""))}
             for m in messages if m.get("content")]
    if not clean:
        return {"reply": "Пустое сообщение."}
    body = {
        "model": _cfg["model"],
        "max_tokens": 1024,
        "system": build_system(snapshot_fn, days_fn),
        "messages": clean[-20:],
    }
    req = urllib.request.Request(
        API_URL, data=json.dumps(body).encode("utf-8"),
        headers={"x-api-key": _cfg["api_key"], "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return {"reply": text or "(пустой ответ модели)"}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        return {"reply": "Ошибка API (%s). %s" % (e.code, detail)}
    except Exception as e:
        return {"reply": "Не удалось связаться с API: %s" % e}
