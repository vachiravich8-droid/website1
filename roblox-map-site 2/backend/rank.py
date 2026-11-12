"""Decides the order maps appear on the home page."""


def rank_games(games):
    """Return the maps in the order visitors should see them.

    Each game is a dict with: pinned (bool), status ("live" | "wip"), addedAt ("YYYY-MM-DD"),
    and live (None, or a dict with playing / visits / favorites).
    """
    # Sorted in two passes because Python's sort is stable: newest first, then the main rules on top.
    newest_first = sorted(games, key=lambda g: g.get("addedAt") or "", reverse=True)
    return sorted(newest_first, key=lambda g: (
        not g.get("pinned"),
        g.get("status") != "live",
        -((g.get("live") or {}).get("playing") or 0),
    ))
