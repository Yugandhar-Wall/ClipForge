"""Still frames of a video, for drawing the facecam / gameplay boxes, and a preview of the
finished 9:16 layout made from that frame with the same filters the real render uses.

grab() works with a file on this PC, a published video link (YouTube etc., nothing is
downloaded: ffmpeg reads one frame straight from the stream) and a live stream (the newest
frame). preview() returns a small JPEG of what a clip will look like.
"""
import hashlib
import os
import re
import time

from .brand import fitted_logo, logo_filters
from .captions import caption_events, hook_event, ass_for_window
from .render import H, W, layout_geometry
from .tools import CACHE_DIR, ROOT, hls_input_opts, is_hls, js_runtimes, probe, run_ffmpeg, strip_ansi

FRAMES_DIR = os.path.join(CACHE_DIR, "frames")
POSITIONS = (0.35, 0.5, 0.2, 0.65, 0.8, 0.12, 0.9)   # where "another frame" looks next
_info_cache = {}                                       # url -> (time, stream info)


class FrameError(RuntimeError):
    pass


def _jpeg_size(path):
    from PIL import Image
    with Image.open(path) as im:
        return im.width, im.height


def _out(source, tag):
    key = hashlib.sha1(f"{source}|{tag}|{time.time()}".encode()).hexdigest()[:12]
    os.makedirs(FRAMES_DIR, exist_ok=True)
    return os.path.join(FRAMES_DIR, f"frame_{key}.jpg")


def _stream_info(url):
    """Direct video address of a published video (cached for 10 minutes)."""
    hit = _info_cache.get(url)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
            "socket_timeout": 30, "js_runtimes": js_runtimes(),
            "format": "bv*[height<=1080][vcodec!=none]/b[height<=1080]/b"}
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=False)
    except Exception as e:
        msg = strip_ansi(e).strip().splitlines()[-1][:300]
        raise FrameError(f"Couldn't open that link: {msg}") from None
    if info.get("is_live"):
        raise FrameError("That link is a live stream: switch to Live mode.")
    fmt = next((f for f in (info.get("requested_formats") or [info])
                if f.get("vcodec") not in (None, "none") and f.get("url")), None)
    if not fmt:
        raise FrameError("No video stream found for that link.")
    out = {"url": fmt["url"], "headers": dict(fmt.get("http_headers") or {}),
           "duration": float(info.get("duration") or 0), "title": info.get("title") or ""}
    _info_cache[url] = (time.time(), out)
    return out


def _input_args(url, headers):
    args = []
    if headers:
        args += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    if is_hls(url):
        args += hls_input_opts()
    return args


def grab(source, attempt=0, live=False):
    """One frame. attempt 0, 1, 2... walks through different moments of a finished video
    (a live stream always gives its newest frame). Returns {path, width, height, at, title}."""
    source = (source or "").strip().strip('"')
    if not source:
        raise FrameError("Add a video first (a link or a file).")
    is_url = bool(re.match(r"https?://", source))
    out = _out(source, attempt)
    if live:
        if not is_url:
            raise FrameError("Paste the live stream's link.")
        from .live import NotLiveYet, resolve
        try:
            info = resolve(source)
        except NotLiveYet as e:
            raise FrameError(f"The stream isn't live yet ({e}). Load the frame once it has started.") from None
        except Exception as e:
            raise FrameError(str(e)) from None
        if not info["live"] or not info["inputs"]:
            raise FrameError("That link isn't live right now. Use Video mode for finished videos.")
        url, headers = info["inputs"][0]
        run_ffmpeg(_input_args(url, headers) + ["-i", url, "-frames:v", "1", "-q:v", "3", out])
        at, title = None, info.get("title") or ""
    elif is_url:
        info = _stream_info(source)
        dur = info["duration"]
        at = dur * POSITIONS[attempt % len(POSITIONS)] if dur else 30.0 * (attempt + 1)
        run_ffmpeg(["-ss", f"{at:.2f}"] + _input_args(info["url"], info["headers"]) +
                   ["-i", info["url"], "-frames:v", "1", "-q:v", "3", out])
        title = info["title"]
    else:
        if not os.path.exists(source):
            raise FrameError(f"File not found: {source}")
        dur = probe(source)["duration"] or 0
        at = dur * POSITIONS[attempt % len(POSITIONS)]
        run_ffmpeg(["-ss", f"{at:.2f}", "-i", source, "-frames:v", "1", "-q:v", "3", out])
        title = os.path.basename(source)
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        raise FrameError("Couldn't read a frame from that video.")
    w, h = _jpeg_size(out)
    return {"path": out, "width": w, "height": h, "at": at, "title": title}


def default_boxes(width, height, split=0.4):
    """A starting point for the two boxes: facecam in the top-left corner, gameplay in the
    middle. Normalised [x, y, w, h], already in the right shapes."""
    from .render import facecam_heights
    top, bot = facecam_heights(split)
    cam_w = 0.28
    cam_h = cam_w * width / (W / top) / height
    game_h = 1.0
    game_w = game_h * height * (W / bot) / width
    if game_w > 1.0:
        game_w, game_h = 1.0, width / (W / bot) / height
    return {"split": split, "cam": [0.02, 0.03, cam_w, cam_h],
            "game": [(1 - game_w) / 2, (1 - game_h) / 2, game_w, game_h]}


def preview(frame, layout, boxes=None, zoom=1.0, logo_path=None, logo_size=1.0, split_bottom=False):
    """What a clip will look like: the frame through the real layout filters, plus sample
    captions and hook text in their real places. Returns a 540x960 JPEG path."""
    src_w, src_h = _jpeg_size(frame)
    logo_on = bool(logo_path) and os.path.exists(logo_path)
    lay = "split" if split_bottom else layout
    if logo_on and lay == "crop":
        lay = "blur"
    vf, cap_y, hook_y, logo_y = layout_geometry(lay, src_w, src_h, float(zoom or 1.0),
                                                logo=logo_on, boxes=boxes)
    graph = [vf.format(inp="[0:v]", zoom="")]
    chain = "[base]"
    if lay == "split":       # background gameplay goes under the content: grey stand-in
        graph.append(f"color=c=0x2b2b2b:s={W}x{H - (H // 2)}:d=1[gp]")
        graph.append("[top][gp]vstack=inputs=2,setsar=1[base]")
    inputs = ["-loop", "1", "-t", "1", "-i", frame]
    if logo_on:
        png, _, lh = fitted_logo(logo_path, float(logo_size or 1.0))
        inputs += ["-loop", "1", "-t", "1", "-i", png]
        cy = min(logo_y + lh // 2, H - lh // 2 - 24)
        graph += logo_filters(chain, "[1:v]", "[lgo]", cy, 0.0, "none")
        chain = "[lgo]"
    words = [{"w": w, "s": i * 0.3, "e": i * 0.3 + 0.28}
             for i, w in enumerate("CAPTIONS GO HERE".split())]
    events = caption_events(words, cap_y, "yellow", 3) + [hook_event("YOUR HOOK TEXT", 0, 2, hook_y)]
    os.makedirs(FRAMES_DIR, exist_ok=True)
    ass = os.path.join(FRAMES_DIR, "preview.ass")
    with open(ass, "w", encoding="utf-8") as f:
        f.write(ass_for_window(events, 0.7, 2.0))      # past the fade-ins: everything fully shown
    rel = os.path.relpath(ass, ROOT).replace("\\", "/")
    graph.append(f"{chain}ass={rel}:fontsdir=fonts,scale=540:960[out]")
    out = os.path.join(FRAMES_DIR, f"preview_{hashlib.sha1(str(time.time()).encode()).hexdigest()[:10]}.jpg")
    run_ffmpeg(inputs + ["-filter_complex", ";".join(graph), "-map", "[out]", "-frames:v", "1",
                         "-q:v", "3", os.path.abspath(out)], cwd=ROOT)
    return out
