"""Shared helpers: locating ffmpeg, running it, probing media, logging."""
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SFX_DIR = os.path.join(ROOT, "sfx")
FONTS_DIR = os.path.join(ROOT, "fonts")
OUTPUT_DIR = os.path.join(ROOT, "output")
CACHE_DIR = os.path.join(ROOT, "cache")

_FFMPEG = None


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
        flags = 0x08000000 if sys.platform == "win32" else 0
        try:
            h = subprocess.run([exe, "-hide_banner", "-h", "demuxer=hls"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=20, creationflags=flags).stdout
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
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y"] + args
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=flags)
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


def fmt_ts(sec):
    sec = max(0, int(sec))
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
