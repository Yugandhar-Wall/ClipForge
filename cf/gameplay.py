"""Background gameplay for the split-screen format (content on top, gameplay underneath).

Put your own videos in the `gameplay` folder, one sub-folder per game, e.g.
    gameplay/minecraft_parkour/run1.mp4
    gameplay/subway_surfers/run1.mp4
Videos directly inside `gameplay` count as the game "other".
The gameplay is always muted and never repeats the same video twice in a row.
"""
import json
import os
import random

from .tools import CACHE_DIR, ROOT, probe

GAMEPLAY_DIR = os.path.join(ROOT, "gameplay")
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")
ANY = "any"
_DUR_CACHE = os.path.join(CACHE_DIR, "gameplay_durations.json")


def library():
    """{game: [paths]} for every game folder that has at least one video."""
    lib = {}
    if not os.path.isdir(GAMEPLAY_DIR):
        return lib
    for name in sorted(os.listdir(GAMEPLAY_DIR)):
        p = os.path.join(GAMEPLAY_DIR, name)
        if os.path.isdir(p):
            vids = sorted(os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith(VIDEO_EXT))
            if vids:
                lib[name] = vids
        elif name.lower().endswith(VIDEO_EXT):
            lib.setdefault("other", []).append(p)
    return lib


def game_choices():
    return [ANY] + sorted(library())


def nice(name):
    return name.replace("_", " ").replace("-", " ").title()


def describe():
    lib = library()
    if not lib:
        return ("No gameplay videos yet. Put them in the gameplay folder inside ClipForge, "
                "one sub-folder per game (e.g. gameplay\\minecraft_parkour\\run1.mp4).")
    return ", ".join(f"{nice(k)} ({len(v)})" for k, v in lib.items())


def _durations(paths):
    try:
        with open(_DUR_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    out, bad, changed = {}, [], False
    for p in paths:
        try:
            key = f"{p}|{os.path.getsize(p)}|{int(os.path.getmtime(p))}"
            if key not in cache:
                info = probe(p)
                if not info.get("width"):
                    raise ValueError("no video track")
                cache[key] = info["duration"]
                changed = True
            out[p] = cache[key]
        except Exception:
            bad.append(os.path.basename(p))   # unreadable / half-downloaded: skip it
    if changed:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_DUR_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=1)
    return out, bad


class Picker:
    """Hands out (path, start, length) for each clip, cycling through the videos so
    consecutive clips get different gameplay."""

    def __init__(self, game=ANY, seed=None):
        lib = library()
        if not lib:
            raise RuntimeError(describe())
        if game and game != ANY:
            if game not in lib:
                raise RuntimeError(f"No gameplay videos for '{nice(game)}'. Available: {describe()}")
            paths = lib[game]
        else:
            paths = [p for v in lib.values() for p in v]
        durs, self.skipped = _durations(paths)
        self.skipped += [os.path.basename(p) for p, d in durs.items() if d <= 3]
        self.dur = {p: d for p, d in durs.items() if d > 3}
        if not self.dur:
            raise RuntimeError("None of the gameplay videos could be used (unreadable or under "
                               f"3 seconds): {', '.join(self.skipped)}")
        self.rng = random.Random(seed)
        self.order = list(self.dur)
        self.rng.shuffle(self.order)
        self.i = 0

    def pick(self, need):
        p = self.order[self.i % len(self.order)]
        self.i += 1
        d = self.dur[p]
        room = d - need - 1.0
        start = self.rng.uniform(0, room) if room > 0 else 0.0
        return {"path": p, "start": round(start, 2), "length": d}
