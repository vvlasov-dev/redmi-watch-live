"""HTTP router: features register routes, the Handler dispatches by prefix.

Registration ORDER is preserved and matching is first-prefix-wins, so a more
specific prefix must be registered before its parent (e.g. /vibrate/stop before
/vibrate, /state_demo before /state). This mirrors the old monolithic if/elif
chain exactly; keep that discipline when adding routes.

Handlers are plain functions taking the live BaseHTTPRequestHandler instance
(`h`) so they can use h._send / h._read_json and close over their own module's
globals — no context-adapter indirection, minimal churn from the split.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

_ROUTES = {"GET": [], "POST": []}
_default_get = None   # fallback when no GET route matched (set by the app: serve the SPA)


def register(method, prefix, fn):
    """Append a route. Order matters — register specific prefixes first."""
    _ROUTES[method].append((prefix, fn))


def set_default_get(fn):
    """The catch-all GET handler (serves index.dc.html). Kept out of the route
    table so feature GET routes registered later still win over it."""
    global _default_get
    _default_get = fn


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")   # deploys must show up on plain reload
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        ln = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(ln) if ln else b"{}"
        return json.loads(raw or b"{}")

    def _match(self, method):
        for prefix, fn in _ROUTES[method]:
            if self.path.startswith(prefix):
                fn(self)
                return True
        return False

    def do_POST(self):
        try:
            if self._match("POST"):
                return
        except Exception as e:
            self._send(json.dumps({"ok": False, "error": str(e)}))
            return
        self.do_GET()      # POST fallthrough serves the GET view (unchanged behavior)

    def do_GET(self):
        if self._match("GET"):
            return
        if _default_get:
            _default_get(self)


def serve(port, host="0.0.0.0"):
    httpd = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
