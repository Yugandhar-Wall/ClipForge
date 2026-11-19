"""Shared helpers: app folders, stopping a run, ffmpeg (find, run, probe), Node.js, JSON files,
time formats."""
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SFX_DIR = os.path.join(ROOT, "sfx")
FONTS_DIR = os.path.join(ROOT, "fonts")
OUTPUT_DIR = os.path.join(ROOT, "output")
CACHE_DIR = os.path.join(ROOT, "cache")
MOTION_DIR = os.path.join(ROOT, "motion")
MOTION_READY = os.path.join(MOTION_DIR, "ready.json")   # written by setup_motion.bat

VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".flv")
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW: no console flashes up
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_FFMPEG = None


# ---------------------------------------------------------------- stopping a run
# A run (a Create job, or one live check) executes inside stop_scope(event). Everything slow
# below it calls check_stop() between steps, runs commands with run_cmd() and blocking network
# calls with stoppable(), so pressing Stop (setting the event) ends the run within about a second.
class Cancelled(BaseException):
    """The user pressed Stop. A BaseException (like KeyboardInterrupt) so that the many
    "never let X break the run" `except Exception` handlers don't swallow it."""


_scope = threading.local()


@contextlib.contextmanager
def stop_scope(event):
    prev = getattr(_scope, "event", None)
    _scope.event = event
    try:
        yield
    finally:
        _scope.event = prev


def stop_event():
    """The Stop event of the run this thread works for (None outside a run)."""
    return getattr(_scope, "event", None)


def stop_requested():
    ev = stop_event()
    return ev is not None and ev.is_set()


def check_stop():
    if stop_requested():
        raise Cancelled("Stopped.")


def pause(seconds):
    """time.sleep that ends early (with Cancelled) on Stop."""
    ev = stop_event()
    if ev is None:
        time.sleep(seconds)
    elif ev.wait(seconds):
        raise Cancelled("Stopped.")


def in_scope(fn):
    """Wrap fn so it runs inside the calling thread's stop scope (for worker threads)."""
    ev = stop_event()

    def run(*a, **k):
        with stop_scope(ev):
            return fn(*a, **k)
    return run


def stoppable(fn, *a, **k):
    """Run a blocking call (HTTP request, metadata lookup) so Stop doesn't wait for it: on Stop it
    is abandoned in the background and Cancelled is raised at once."""
    ev = stop_event()
    if ev is None:
        return fn(*a, **k)
    check_stop()
    box = {}

    def run():
        try:
            with stop_scope(ev):
                box["value"] = fn(*a, **k)
        except BaseException as e:   # handed to the caller below
            box["error"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    while t.is_alive():
        t.join(0.25)
        if ev.is_set():
            raise Cancelled("Stopped.")
    if "error" in box:
        raise box["error"]
    return box["value"]


def run_cmd(cmd, cwd=None, timeout=None, env=None, binary=False):
    """subprocess.run(capture_output=True) that is killed on Stop (and after `timeout` seconds).
    Text output unless binary=True. Returns a CompletedProcess."""
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         creationflags=NO_WINDOW,
                         **({} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}))
    waited = 0.0
    while True:
        try:
            out, err = p.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            waited += 0.5
            if stop_requested() or (timeout is not None and waited >= timeout):
                p.kill()
                p.communicate()
                if stop_requested():
                    raise Cancelled("Stopped.") from None
                raise subprocess.TimeoutExpired(cmd, timeout) from None
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


# ---------------------------------------------------------------- small helpers
def strip_ansi(text):
    """yt-dlp error messages carry terminal colour codes."""
    return _ANSI.sub("", str(text))


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data, indent=None):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)


def node_exe():
    """Node.js: the one setup_motion.bat found or installed, else the one on PATH."""
    paths = [(read_json(MOTION_READY) or {}).get("node"),
             os.path.join(MOTION_DIR, "node", "node.exe" if os.name == "nt" else "bin/node"),
             shutil.which("node")]
    return next((p for p in paths if p and os.path.exists(p)), None)


def js_runtimes():
    """For yt-dlp's YouTube challenge solver: Node.js when present (deno stays the fallback)."""
    node = node_exe()
    return {"node": {"path": node}, "deno": {}} if node else {"deno": {}}


# ---------------------------------------------------------------- ffmpeg


def _has_libass(exe):
    try:
        out = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True,
                             text=True, timeout=30).stdout
        return " ass " in out or re.search(r"\bass\b\s+V->V", out) is not None
    except Exception:
        return False


def ffmpeg_exe():
    """Prefer an ffmpeg on PATH that has libass; else the one bundled via imageio-ffmpeg."""
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    candidates = []
    if os.environ.get("CLIPFORGE_FFMPEG"):
        candidates.append(os.environ["CLIPFORGE_FFMPEG"])
    if shutil.which("ffmpeg"):
        candidates.append(shutil.which("ffmpeg"))
    try:
        import imageio_ffmpeg
        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    for c in candidates:
        if c and _has_libass(c):
            _FFMPEG = c
            return c
    raise RuntimeError(
        "No ffmpeg with subtitle (libass) support found. Install one with "
        "'winget install Gyan.FFmpeg' and restart, or set CLIPFORGE_FFMPEG to its path.")


_HLS_OPTS = {}


def hls_input_opts():
    """Options that let ffmpeg read YouTube's HLS feeds. Newer ffmpeg (7.1+) refuses HLS pieces
    whose web address has no matching file ending ("Invalid data found when processing input"),
    and YouTube's addresses never have one. Only options this ffmpeg knows are returned."""
    exe = ffmpeg_exe()
    if exe not in _HLS_OPTS:
        try:
            h = subprocess.run([exe, "-hide_banner", "-h", "demuxer=hls"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=20, creationflags=NO_WINDOW).stdout
        except Exception:
            h = ""
        opts = []
        if "allowed_extensions" in h:
            opts += ["-allowed_extensions", "ALL"]
        if "extension_picky" in h:
            opts += ["-extension_picky", "0"]
        _HLS_OPTS[exe] = opts
    return list(_HLS_OPTS[exe])


def is_hls(url):
    u = (url or "").lower()
    return ".m3u8" in u or "/hls" in u or "hls_playlist" in u


def run_ffmpeg(args, cwd=None, log=None):
    p = run_cmd([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y"] + args, cwd=cwd)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + (p.stderr or "")[-2000:])
    return p


def probe(path):
    """Return dict(duration, width, height, fps, has_audio) using PyAV (no ffprobe needed)."""
    import av
    with av.open(str(os.fspath(path))) as c:
        dur = (c.duration or 0) / 1_000_000
        v = next((s for s in c.streams if s.type == "video"), None)
        a = next((s for s in c.streams if s.type == "audio"), None)
        info = {"duration": dur, "has_audio": a is not None,
                "width": v.codec_context.width if v else 0,
                "height": v.codec_context.height if v else 0,
                "fps": float(v.average_rate) if v and v.average_rate else 30.0}
        if not dur and v is not None and v.duration:
            info["duration"] = float(v.duration * v.time_base)
        return info


def slug(text, n=40):
    s = re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_").lower()
    return (s[:n] or "clip").rstrip("_")


def parse_ts(text):
    """'2:56:29', '56:29', '10589' or '10589.5' -> seconds. Blank -> None. Raises ValueError."""
    t = str(text or "").strip()
    if not t:
        return None
    parts = t.split(":")
    if len(parts) > 3 or any(not p.strip() for p in parts):
        raise ValueError(f"Can't read the time '{t}'. Use h:mm:ss, like 2:56:29.")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"Can't read the time '{t}'. Use h:mm:ss, like 2:56:29.") from None
    if any(n < 0 for n in nums) or any(n >= 60 for n in nums[1:]):
        raise ValueError(f"Can't read the time '{t}'. Minutes and seconds must be under 60.")
    sec = 0.0
    for n in nums:
        sec = sec * 60 + n
    return sec


def fmt_ts(sec, hours=False):
    """62 -> '01:02'; with hours=True (or past an hour) -> '0:01:02'."""
    sec = max(0, int(sec))
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h or hours else f"{m:02d}:{s:02d}"
