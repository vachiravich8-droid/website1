#!/usr/bin/env python3
"""Map showcase site: static pages + JSON API. Run with: python3 server.py"""
import json
import mimetypes
import os
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from backend import auth, roblox, store
from backend.rank import rank_games

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
PORT = int(os.environ.get("PORT", "4173"))
MAX_BODY = 200_000
LOOPBACK = {"127.0.0.1", "::1"}
CSP = (
    "default-src 'self'; img-src 'self' https: data:; script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
    "media-src 'self' blob: https://d8j0ntlcm91z4.cloudfront.net; connect-src 'self' https://d8j0ntlcm91z4.cloudfront.net; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def public_site():
    content = store.load()
    stats = roblox.live(g.get("universeId") for g in content["games"])
    games = [{**g, "live": stats.get(g.get("universeId"))} for g in content["games"]]
    content["games"] = rank_games(games)
    return content


class Handler(BaseHTTPRequestHandler):
    server_version = "MapShowcase"

    def log_message(self, fmt, *args):
        if os.environ.get("QUIET") != "1":
            super().log_message(fmt, *args)

    # ---- helpers -------------------------------------------------------
    def send_json(self, payload, status=200, cookie=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Content-Security-Policy", CSP)
        super().end_headers()

    def session_token(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return cookie["sid"].value if "sid" in cookie else None

    def require_admin(self):
        if not auth.check(self.session_token()):
            raise ApiError(401, "กรุณาเข้าสู่ระบบ")

    def read_json(self):
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise ApiError(415, "ต้องส่งข้อมูลเป็น JSON")
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            raise ApiError(403, "คำขอมาจากเว็บอื่น")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError(413, "ข้อมูลใหญ่เกินไป")
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            raise ApiError(400, "JSON ไม่ถูกต้อง")

    def client_ip(self):
        # Behind Render's proxy the socket address is the proxy; the real client is the last hop it appended.
        forwarded = self.headers.get("X-Forwarded-For", "")
        if os.environ.get("RENDER") and forwarded:
            return forwarded.split(",")[-1].strip()
        return self.client_address[0]

    def session_cookie(self, token, max_age=auth.SESSION_TTL):
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        return f"sid={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"

    # ---- routing -------------------------------------------------------
    def dispatch(self, method):
        path = urlsplit(self.path).path
        try:
            if path.startswith("/api/"):
                self.api(method, path)
            elif method == "GET":
                self.static(path)
            else:
                raise ApiError(405, "ไม่รองรับ")
        except ApiError as e:
            self.send_json({"error": str(e)}, e.status)
        except (store.Invalid, ValueError) as e:
            self.send_json({"error": str(e)}, 400)
        except PermissionError as e:
            self.send_json({"error": str(e)}, 403)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def api(self, method, path):
        ip = self.client_ip()
        route = (method, path)
        if route == ("GET", "/api/site"):
            return self.send_json(public_site())
        if route == ("GET", "/api/admin/state"):
            return self.send_json({"configured": auth.is_configured(), "authed": auth.check(self.session_token())})
        if route == ("POST", "/api/admin/setup"):
            if ip not in LOOPBACK:
                raise ApiError(403, "ตั้งรหัสผ่านครั้งแรกได้จากเครื่องที่รันเซิร์ฟเวอร์เท่านั้น")
            auth.setup(self.read_json().get("password"))
            return self.send_json({"ok": True}, cookie=self.session_cookie(auth.new_session()))
        if route == ("POST", "/api/admin/login"):
            if auth.locked_out(ip):
                raise ApiError(429, "ลองผิดหลายครั้งเกินไป รอ 10 นาทีแล้วลองใหม่")
            if not auth.verify(self.read_json().get("password"), ip):
                raise ApiError(401, "รหัสผ่านไม่ถูกต้อง")
            return self.send_json({"ok": True}, cookie=self.session_cookie(auth.new_session()))
        if route == ("POST", "/api/admin/logout"):
            self.read_json()
            auth.revoke(self.session_token())
            return self.send_json({"ok": True}, cookie=self.session_cookie("", 0))
        if route == ("GET", "/api/admin/content"):
            self.require_admin()
            return self.send_json(store.load())
        if route == ("PUT", "/api/admin/content"):
            self.require_admin()
            return self.send_json(store.save(self.read_json()))
        if route == ("POST", "/api/admin/resolve"):
            self.require_admin()
            return self.send_json(roblox.resolve(str(self.read_json().get("url", ""))))
        raise ApiError(404, "ไม่พบ")

    def static(self, path):
        target = os.path.realpath(os.path.join(PUBLIC, unquote(path).lstrip("/")))
        if target != PUBLIC and not target.startswith(PUBLIC + os.sep):
            raise ApiError(404, "ไม่พบ")
        if os.path.isdir(target):
            if not path.endswith("/"):
                self.send_response(301)
                self.send_header("Location", path + "/")
                self.end_headers()
                return
            target = os.path.join(target, "index.html")
        if not os.path.isfile(target):
            raise ApiError(404, "ไม่พบหน้านี้")
        with open(target, "rb") as f:
            body = f.read()
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json")):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"เว็บ:      http://localhost:{PORT}/")
    print(f"หลังบ้าน:  http://localhost:{PORT}/admin/")
    server.serve_forever()
