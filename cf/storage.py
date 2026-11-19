"""Disk space: everything ClipForge makes is temporary and is deleted after `keep_days` days.

What is cleaned (by age of its newest file):
  output/<video>/<run>/          finished clips (a clip still waiting in the YouTube upload
                                 queue is kept until it is uploaded)
  cache/downloads/               downloaded source videos
  cache/*.wav, *.json            extracted audio, transcripts, parsed campaign rules
  cache/motion/<animation>/      motion graphics (re-made when needed)
  cache/frames/                  frames and layout previews (after 1 day)
  cache/live/                    recording pieces of a live session that is no longer running
Kept: the meme-sound library (cache/memes) and the gameplay duration list - small and reused.
clean() runs when the app starts and after every run; Settings > Storage can run it on demand.
"""
import os
import shutil
import time

from .tools import CACHE_DIR, OUTPUT_DIR

DAY = 86400
KEEP_FOREVER = {os.path.join(CACHE_DIR, "memes"), os.path.join(CACHE_DIR, "gameplay_durations.json")}


def _size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _newest(path):
    """Last time anything in `path` changed."""
    try:
        newest = os.path.getmtime(path)
    except OSError:
        return 0.0
    if os.path.isdir(path):
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(root, f)))
                except OSError:
                    pass
    return newest


def _remove(path):
    size = _size(path)
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    except OSError:
        return 0
    return size


def _queued_files():
    """Clips still waiting for YouTube: never delete those."""
    from .publish import load_queue
    return {os.path.normcase(os.path.abspath(x["file"])) for x in load_queue()
            if x.get("status") in ("pending", "failed")}


def _run_folders():
    """output/<video>/<run> folders."""
    if not os.path.isdir(OUTPUT_DIR):
        return []
    out = []
    for video in os.listdir(OUTPUT_DIR):
        vdir = os.path.join(OUTPUT_DIR, video)
        if os.path.isdir(vdir):
            out += [os.path.join(vdir, r) for r in os.listdir(vdir) if os.path.isdir(os.path.join(vdir, r))]
    return out


def usage():
    """Bytes used, by kind: {"clips", "downloads", "transcripts", "motion", "live", "other"}."""
    use = {"clips": _size(OUTPUT_DIR) if os.path.isdir(OUTPUT_DIR) else 0,
           "downloads": 0, "transcripts": 0, "motion": 0, "live": 0, "other": 0}
    if os.path.isdir(CACHE_DIR):
        for name in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, name)
            kind = {"downloads": "downloads", "motion": "motion", "live": "live"}.get(name)
            if kind is None:
                kind = "transcripts" if name.endswith((".wav", ".json")) else "other"
            use[kind] += _size(p)
    return use


def describe(use=None):
    use = use or usage()
    total = sum(use.values())
    parts = [f"{k} {v / 1e9:.2f} GB" if v >= 1e8 else f"{k} {v / 1e6:.0f} MB"
             for k, v in use.items() if v >= 1e6]
    return f"{total / 1e9:.2f} GB in total" + (f" ({', '.join(parts)})" if parts else "")


def clean(keep_days=6, live_running=False, log=None):
    """Delete everything older than keep_days days. Returns bytes freed."""
    keep_days = max(0.0, float(keep_days))
    now = time.time()
    old = lambda p, days=keep_days: now - _newest(p) > days * DAY   # noqa: E731
    freed = 0
    queued = _queued_files()

    for run in _run_folders():
        if not old(run):
            continue
        files = [os.path.normcase(os.path.abspath(os.path.join(run, f))) for f in os.listdir(run)]
        if any(f in queued for f in files):
            continue
        freed += _remove(run)
    if os.path.isdir(OUTPUT_DIR):
        for video in os.listdir(OUTPUT_DIR):
            vdir = os.path.join(OUTPUT_DIR, video)
            if os.path.isdir(vdir) and not os.listdir(vdir):
                os.rmdir(vdir)

    if os.path.isdir(CACHE_DIR):
        for name in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, name)
            if p in KEEP_FOREVER:
                continue
            if name in ("downloads", "motion"):
                for item in os.listdir(p):
                    q = os.path.join(p, item)
                    # half-made animations (hf_*) go after a day
                    if old(q, 1 if item.startswith("hf_") else keep_days):
                        freed += _remove(q)
            elif name == "frames":
                for item in os.listdir(p):
                    if old(os.path.join(p, item), 1):
                        freed += _remove(os.path.join(p, item))
            elif name == "live":
                if not live_running:
                    freed += _remove(p)
            elif old(p):
                freed += _remove(p)
    if log and freed:
        log(f"Storage: deleted {freed / 1e6:.0f} MB of clips and temporary files older than "
            f"{keep_days:g} day(s).")
    return freed
