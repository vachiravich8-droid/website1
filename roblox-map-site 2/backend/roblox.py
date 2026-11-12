"""Live game data from Roblox's public APIs, cached so the site never hammers them."""
import json
import re
import threading
import time
import urllib.request

STATS_TTL = 60
THUMB_TTL = 60 * 60
TIMEOUT = 6

_stats = {}
_thumbs = {}
_lock = threading.Lock()


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "map-showcase/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        return json.load(res)


def _stale(cache, ids, ttl):
    now = time.time()
    return [i for i in ids if i not in cache or now - cache[i]["at"] > ttl]


def _refresh_stats(ids):
    data = _get("https://games.roblox.com/v1/games?universeIds=" + ",".join(map(str, ids)))
    now = time.time()
    for g in data.get("data", []):
        _stats[g["id"]] = {
            "at": now,
            "name": g.get("name", ""),
            "description": g.get("description") or "",
            "rootPlaceId": g.get("rootPlaceId"),
            "playing": g.get("playing") or 0,
            "visits": g.get("visits") or 0,
            "favorites": g.get("favoritedCount") or 0,
        }


def _refresh_thumbs(ids):
    data = _get(
        "https://thumbnails.roblox.com/v1/games/multiget/thumbnails?size=768x432&format=Png"
        "&countPerUniverse=1&universeIds=" + ",".join(map(str, ids))
    )
    now = time.time()
    for entry in data.get("data", []):
        images = [t.get("imageUrl") for t in entry.get("thumbnails", []) if t.get("state") == "Completed"]
        _thumbs[entry["universeId"]] = {"at": now, "url": images[0] if images else None}


def live(universe_ids):
    """Return {universeId: stats}. On a Roblox outage, serve whatever is cached."""
    ids = sorted({i for i in universe_ids if i})
    if not ids:
        return {}
    with _lock:
        for cache, ttl, refresh in ((_stats, STATS_TTL, _refresh_stats), (_thumbs, THUMB_TTL, _refresh_thumbs)):
            stale = _stale(cache, ids, ttl)
            if stale:
                try:
                    refresh(stale)
                except Exception:
                    pass
        out = {}
        for i in ids:
            if i in _stats:
                s = _stats[i]
                out[i] = {
                    "playing": s["playing"], "visits": s["visits"], "favorites": s["favorites"],
                    "thumb": (_thumbs.get(i) or {}).get("url"),
                }
        return out


def resolve(text):
    """Turn a pasted Roblox game link (or place ID) into the fields the admin form needs."""
    match = re.search(r"roblox\.com/(?:[a-z]{2}/)?games/(\d+)", text) or re.fullmatch(r"\s*(\d{4,20})\s*", text)
    if not match:
        raise ValueError("ไม่พบรหัสเกมในลิงก์นี้ ลองวางลิงก์แบบ roblox.com/games/123456")
    place_id = int(match.group(1))
    try:
        universe_id = _get(f"https://apis.roblox.com/universes/v1/places/{place_id}/universe").get("universeId")
    except Exception:
        universe_id = None
    if not universe_id:
        raise ValueError("Roblox ไม่รู้จักเกมนี้ ตรวจว่าเกมตั้งเป็นสาธารณะแล้ว")
    stats = live([universe_id]).get(universe_id)
    with _lock:
        detail = _stats.get(universe_id, {})
    return {
        "placeId": place_id,
        "universeId": universe_id,
        "url": f"https://www.roblox.com/games/{place_id}",
        "title": detail.get("name", ""),
        "blurb": detail.get("description", "")[:400],
        "thumb": (stats or {}).get("thumb"),
    }
