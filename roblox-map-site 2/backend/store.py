"""Site content: one JSON document, validated on every save."""
import json
import os
import re
import threading
import time

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CONTENT_PATH = os.path.join(DATA_DIR, "content.json")

APPS = {"roblox", "discord", "youtube", "tiktok", "facebook", "instagram", "x", "line", "email", "other"}
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_lock = threading.Lock()

DEFAULT = {
    "profile": {
        "name": "ชื่อผู้สร้าง",
        "tagline": "สร้างแมพ Roblox ให้คนเข้ามาเล่นด้วยกัน",
        "about": "แก้ข้อความนี้ได้ที่หลังบ้าน เล่าสั้นๆ ว่าคุณทำแมพแนวไหน และอยากให้คนเล่นได้ความรู้สึกแบบไหน",
    },
    "games": [
        {
            "id": "sample-1", "title": "แมพตัวอย่าง 01",
            "blurb": "นี่คือการ์ดตัวอย่าง ลบได้ที่หลังบ้าน แล้ววางลิงก์เกม Roblox ของคุณเพื่อดึงรูปปกและยอดคนเล่นจริง",
            "url": "", "placeId": None, "universeId": None,
            "tags": ["ตัวอย่าง"], "pinned": False, "status": "wip", "addedAt": "2026-11-12",
        },
    ],
    "links": [],
    "updates": [
        {"id": "u-1", "date": "2026-11-12", "title": "เปิดเว็บแล้ว", "body": "หน้ารวมแมพทั้งหมดอยู่ที่นี่ที่เดียว"},
    ],
}


class Invalid(ValueError):
    pass


def _text(value, limit, field, required=False):
    if not isinstance(value, str):
        raise Invalid(f"{field} ต้องเป็นข้อความ")
    value = value.strip()
    if required and not value:
        raise Invalid(f"กรุณากรอก {field}")
    if len(value) > limit:
        raise Invalid(f"{field} ยาวเกิน {limit} ตัวอักษร")
    return value


def _ident(value):
    if not isinstance(value, str) or not ID_RE.match(value):
        raise Invalid("รหัสรายการไม่ถูกต้อง")
    return value


def _url(value, field, allow_mailto=False):
    value = _text(value, 300, field)
    if not value:
        return ""
    ok = value.startswith(("https://", "http://")) or (allow_mailto and value.startswith("mailto:"))
    if not ok:
        raise Invalid(f"{field} ต้องขึ้นต้นด้วย https://")
    return value


def _int_or_none(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Invalid("รหัสเกม Roblox ไม่ถูกต้อง")
    return value


def _items(value, limit, field):
    if not isinstance(value, list) or len(value) > limit:
        raise Invalid(f"{field} มีได้ไม่เกิน {limit} รายการ")
    if field != "แท็ก" and not all(isinstance(v, dict) for v in value):
        raise Invalid(f"{field} ไม่ถูกต้อง")
    return value


def validate(raw):
    """Rebuild the document from whitelisted fields so nothing unexpected is stored."""
    if not isinstance(raw, dict):
        raise Invalid("ข้อมูลไม่ถูกต้อง")
    p = raw.get("profile") or {}
    out = {
        "profile": {
            "name": _text(p.get("name", ""), 60, "ชื่อ", required=True),
            "tagline": _text(p.get("tagline", ""), 140, "คำโปรย"),
            "about": _text(p.get("about", ""), 600, "เกี่ยวกับ"),
        },
        "games": [], "links": [], "updates": [],
    }
    for g in _items(raw.get("games", []), 60, "แมพ"):
        tags = [_text(t, 24, "แท็ก") for t in _items(g.get("tags", []), 6, "แท็ก")]
        status = g.get("status", "live")
        if status not in ("live", "wip"):
            raise Invalid("สถานะแมพไม่ถูกต้อง")
        out["games"].append({
            "id": _ident(g.get("id")),
            "title": _text(g.get("title", ""), 80, "ชื่อแมพ", required=True),
            "blurb": _text(g.get("blurb", ""), 400, "คำอธิบายแมพ"),
            "url": _url(g.get("url", ""), "ลิงก์เกม"),
            "placeId": _int_or_none(g.get("placeId")),
            "universeId": _int_or_none(g.get("universeId")),
            "tags": [t for t in tags if t],
            "pinned": bool(g.get("pinned")),
            "status": status,
            "addedAt": _text(g.get("addedAt", ""), 30, "วันที่เพิ่ม"),
        })
    for link in _items(raw.get("links", []), 20, "ช่องทางติดต่อ"):
        app = link.get("app")
        if app not in APPS:
            raise Invalid("ไม่รู้จักแอปนี้")
        url = _url(link.get("url", ""), "ลิงก์ติดต่อ", allow_mailto=True)
        if not url:
            raise Invalid("กรุณากรอกลิงก์ติดต่อให้ครบ")
        out["links"].append({
            "id": _ident(link.get("id")), "app": app,
            "label": _text(link.get("label", ""), 60, "ชื่อบัญชี"), "url": url,
        })
    for u in _items(raw.get("updates", []), 100, "อัปเดต"):
        out["updates"].append({
            "id": _ident(u.get("id")),
            "date": _text(u.get("date", ""), 20, "วันที่"),
            "title": _text(u.get("title", ""), 100, "หัวข้ออัปเดต", required=True),
            "body": _text(u.get("body", ""), 1000, "รายละเอียดอัปเดต"),
        })
    return out


_client = None
_cache = {"at": 0.0, "doc": None}
CACHE_SECONDS = 30


def _firestore():
    """Firestore when FIREBASE_SERVICE_ACCOUNT is set (production); None means use the local file."""
    global _client
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if not raw:
        return None
    if _client is None:
        import firebase_admin
        from firebase_admin import credentials, firestore
        firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)))
        _client = firestore.client()
    return _client


def load():
    db = _firestore()
    if db:
        if _cache["doc"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
            return json.loads(json.dumps(_cache["doc"]))
        snap = db.collection("site").document("content").get()
        doc = snap.to_dict() if snap.exists else json.loads(json.dumps(DEFAULT))
        _cache.update(at=time.time(), doc=doc)
        return json.loads(json.dumps(doc))
    try:
        with open(CONTENT_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return json.loads(json.dumps(DEFAULT))


def save(raw):
    clean = validate(raw)
    db = _firestore()
    if db:
        db.collection("site").document("content").set(clean)
        _cache.update(at=time.time(), doc=clean)
        return clean
    os.makedirs(DATA_DIR, exist_ok=True)
    with _lock:
        tmp = CONTENT_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONTENT_PATH)
    return clean
