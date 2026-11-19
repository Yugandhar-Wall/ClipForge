"""Animated overlays made with HyperFrames + GSAP (optional, toggled in the app).

How it fits in:
- motion/templates/*.html are GSAP animations (one per element). HyperFrames renders each one
  in headless Chrome to a transparent PNG sequence. Text, emoji and positions are variables, so
  no AI is needed to make an animation.
- plan() looks at a clip's finished timeline and swaps the plain text overlays (hook, tease,
  end question, countdown number, @creator) for animated ones, and adds reaction stickers.
- render_assets() renders everything that's missing in one batch per template. Results are
  cached by content, so "#3", the creator tag etc. are only ever rendered once.
- Anything that fails keeps its normal text overlay: motion can never break a run.
"""
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from .tools import CACHE_DIR, ROOT, ffmpeg_exe

MOTION_DIR = os.path.join(ROOT, "motion")
TPL_DIR = os.path.join(MOTION_DIR, "templates")
ASSET_DIR = os.path.join(CACHE_DIR, "motion")
READY = os.path.join(MOTION_DIR, "ready.json")
CLI = os.path.join(MOTION_DIR, "node_modules", "hyperframes", "bin", "hyperframes.mjs")
FPS = 30
VERSION = "2"

# template -> animation length in seconds (must match data-duration in the html)
LENGTH = {"hook": 1.5, "cta": 1.3, "card": 1.2, "sticker": 1.5, "tag": 0.9}
ELEMENTS = {"hook": "Hook & tease text", "cta": "End question", "countdown": "Countdown numbers",
            "stickers": "Reaction stickers", "tag": "Creator name tag"}

_POS = re.compile(r"\\pos\((\d+),(\d+)\)")
_status_cache = {}


# ---------------------------------------------------------------- setup / status
def _ready():
    try:
        with open(READY, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _node():
    r = _ready().get("node")
    if r and os.path.exists(r):
        return r
    local = os.path.join(MOTION_DIR, "node", "node.exe" if os.name == "nt" else "bin/node")
    if os.path.exists(local):
        return local
    return shutil.which("node")


def _ffprobe():
    pats = ["ffprobe.exe", "ffprobe"] if os.name == "nt" else ["ffprobe"]
    for p in pats:
        hits = glob.glob(os.path.join(MOTION_DIR, "node_modules", "@ffprobe-installer", "*", p))
        if hits:
            return hits[0]
    return shutil.which("ffprobe")


def status():
    """(ok, message). Cached for the session once it's OK."""
    if _status_cache.get("ok"):
        return True, _status_cache["msg"]
    node = _node()
    missing = []
    if not node:
        missing.append("Node.js 22+")
    if not os.path.exists(CLI):
        missing.append("HyperFrames")
    if not os.path.exists(os.path.join(TPL_DIR, "gsap.min.js")):
        missing.append("GSAP")
    if not _ffprobe():
        missing.append("ffprobe")
    if missing:
        return False, ("Motion graphics are not installed (" + ", ".join(missing) +
                       " missing). Double-click setup_motion.bat once.")
    try:
        v = subprocess.run([node, "-v"], capture_output=True, text=True, timeout=20).stdout.strip()
        if int(v.lstrip("v").split(".")[0]) < 22:
            return False, f"Motion graphics need Node.js 22 or newer (found {v}). Run setup_motion.bat."
    except Exception as e:
        return False, f"Node.js didn't start ({e}). Run setup_motion.bat."
    _status_cache.update(ok=True, msg=f"Motion graphics ready (Node {v}).")
    return True, _status_cache["msg"]


def _env():
    env = dict(os.environ)
    env.update(HYPERFRAMES_FFMPEG_PATH=ffmpeg_exe(), HYPERFRAMES_FFPROBE_PATH=_ffprobe() or "",
               HYPERFRAMES_NO_TELEMETRY="1", DO_NOT_TRACK="1", HYPERFRAMES_RENDER_LOCK="off")
    browser = _ready().get("browser")
    if browser and os.path.exists(browser):
        env["HYPERFRAMES_BROWSER_PATH"] = browser
    node_dir = os.path.dirname(_node() or "")
    if node_dir:
        env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
    return env


# ---------------------------------------------------------------- assets
def _tpl_hash():
    h = hashlib.sha1(VERSION.encode())
    for name in sorted(os.listdir(TPL_DIR)):
        if name.endswith((".html", ".js", ".css")):
            with open(os.path.join(TPL_DIR, name), "rb") as f:
                h.update(name.encode() + f.read())
    return h.hexdigest()[:10]


def asset_key(kind, vars_):
    blob = json.dumps([kind, vars_, _tpl_hash()], sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def asset_dir(key):
    return os.path.join(ASSET_DIR, key)


def _have(key):
    return os.path.exists(os.path.join(asset_dir(key), ".done"))


def _prune(max_age_days=45):
    """Forget animations not used for a while (each is a few MB)."""
    now = time.time()
    for d in glob.glob(os.path.join(ASSET_DIR, "*")):
        try:
            if now - os.path.getmtime(os.path.join(d, ".done")) > max_age_days * 86400:
                shutil.rmtree(d, ignore_errors=True)
            elif os.path.basename(d).startswith("hf_") and now - os.path.getmtime(d) > 86400:
                shutil.rmtree(d, ignore_errors=True)  # leftovers of an interrupted render
        except OSError:
            pass


def render_assets(reqs, log=print):
    """Render every request whose asset isn't cached. Returns the set of keys available."""
    _prune()
    for r in reqs:  # mark cached ones as used
        try:
            os.utime(os.path.join(asset_dir(r["key"]), ".done"))
        except OSError:
            pass
    todo = {}
    for r in reqs:
        if not _have(r["key"]):
            todo.setdefault(r["kind"], {})[r["key"]] = r["vars"]
    n = sum(len(v) for v in todo.values())
    if n:
        log(f"Motion graphics: rendering {n} animation(s) with HyperFrames...")
    t0 = time.time()
    for kind, rows in todo.items():
        _render_batch(kind, rows, log)
    if n:
        log(f"  done in {time.time() - t0:.0f}s")
    return {r["key"] for r in reqs if _have(r["key"])}


def _render_batch(kind, rows, log):
    os.makedirs(ASSET_DIR, exist_ok=True)
    keys = list(rows)
    work = tempfile.mkdtemp(prefix=f"hf_{kind}_", dir=ASSET_DIR)
    batch = os.path.join(work, "rows.json")
    with open(batch, "w", encoding="utf-8") as f:
        json.dump([rows[k] for k in keys], f, ensure_ascii=False)
    tpl = "hook" if kind == "slam" else kind
    cmd = [_node(), CLI, "render", TPL_DIR, "-c", f"{tpl}.html", "--format", "png-sequence",
           "--fps", str(FPS), "--batch", batch, "--batch-concurrency", "2",
           "-o", os.path.join(work, "r_{index}"), "--json"]
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=_env(), cwd=MOTION_DIR, timeout=120 + 40 * len(keys),
                           creationflags=flags)
        out = p.stdout.strip()
        if p.returncode != 0 and not out:
            raise RuntimeError((p.stderr or "")[-400:].strip() or f"exit code {p.returncode}")
    except Exception as e:
        log(f"  motion '{kind}' failed: {e}")
        shutil.rmtree(work, ignore_errors=True)
        return
    try:
        res = json.loads(out[out.index("{"):])
        statuses = {int(r["index"]): r.get("status") for r in res.get("rows", [])}
        if not res.get("ok", True) and not statuses:
            log(f"  motion '{kind}' failed: {res.get('error', {}).get('message', 'unknown error')}")
    except Exception:
        statuses = {}
    for i, key in enumerate(keys):
        src = os.path.join(work, f"r_{i}")
        frames = sorted(glob.glob(os.path.join(src, "frame_*.png")))
        if statuses.get(i, "completed") != "completed" or not frames:
            log(f"  motion '{kind}' #{i + 1} didn't render; its plain text version is used.")
            continue
        try:
            box = _crop_frames(frames)
        except Exception as e:
            log(f"  motion '{kind}' #{i + 1} unreadable ({e}); its plain text version is used.")
            continue
        dst = asset_dir(key)
        shutil.rmtree(dst, ignore_errors=True)
        shutil.move(src, dst)
        with open(os.path.join(dst, ".done"), "w") as f:
            json.dump({"n": len(frames), "x": box[0], "y": box[1]}, f)
    shutil.rmtree(work, ignore_errors=True)


def _crop_frames(frames):
    """Crop every frame to the union of their visible pixels (most of the 1080x1920 canvas
    is transparent): far less to decode and blend for every video frame. Returns (x, y)."""
    from PIL import Image
    box = None
    for p in frames:
        with Image.open(p) as im:
            b = im.getchannel("A").getbbox()
        if b:
            box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]),
                                         max(box[2], b[2]), max(box[3], b[3]))
    if box is None:
        box = (0, 0, 2, 2)
    x0, y0 = box[0] // 2 * 2, box[1] // 2 * 2          # even sizes/offsets for the encoder
    x1, y1 = min(1080, -(-box[2] // 2) * 2), min(1920, -(-box[3] // 2) * 2)
    for p in frames:
        with Image.open(p) as im:
            im.crop((x0, y0, x1, y1)).save(p + ".tmp.png", compress_level=1)
        os.replace(p + ".tmp.png", p)
    return x0, y0


def overlay_for(req):
    """Overlay description for render.py (or None if the asset is missing)."""
    d = asset_dir(req["key"])
    try:
        with open(os.path.join(d, ".done")) as f:
            meta = json.load(f)
    except Exception:
        return None
    return {"dir": d, "t0": req["t0"], "t1": req["t1"], "alen": meta["n"] / FPS or LENGTH[req["tpl"]],
            "fade": req["fade"], "x": meta["x"], "y": meta["y"]}


# ---------------------------------------------------------------- planning
def _parse(ev):
    layer, style, text = ev["line"]
    m = _POS.search(text)
    x, y = (int(m.group(1)), int(m.group(2))) if m else (540, 960)
    plain = text[text.rfind("}") + 1:] if "}" in text else text
    return style, plain.strip(), x, y


_EMOJI = [(r"\b(ha){2,}|lol|lmao|laugh|funny|😂", "😂", "LMAO"),
          (r"oh no|no no|nooo|help|run|scared|ahh", "😱", "NO WAY"),
          (r"what|huh|wait|how", "🤨", "WAIT WHAT"),
          (r"fuck|shit|damn|hell", "🤬", "BRO"),
          (r"let'?s go|yes+|we did it|clutch|gg", "🔥", "LET'S GO"),
          (r"bro|dude|why", "💀", "BRUH")]


def _sticker_from_signal(sig):
    note = (sig.get("note") or "").lower()
    if sig["kind"] == "laugh":
        return "😂", "LMAO"
    for pat, emo, text in _EMOJI:
        if re.search(pat, note):
            first = re.split(r",", note)[0].strip().upper()
            words = first.split()
            short = " ".join(words[:2]) if words and len(" ".join(words[:2])) <= 12 else text
            return emo, (short + ("!" if not short.endswith(("!", "?")) else "")) if short else text
    return "😳", ""


def _clean_sticker_text(t):
    t = re.sub(r"[^\w !?'.]", "", str(t or ""), flags=re.UNICODE).strip().upper()
    return " ".join(t.split()[:3])[:16]


def _one_emoji(e):
    e = str(e or "").strip()
    return e[:4] if e and not e.isascii() else ""


def plan(tl, clip, cfg, geo, signals):
    """Decide the animated overlays for one finished timeline.
    Returns (requests, event_ids_replaced); requests carry output times."""
    on = set(cfg.get("motion_elements") or ELEMENTS)
    cap_y, hook_y = geo
    L = tl.length
    reqs, replaced = [], []

    def add(kind, tpl, vars_, t0, t1, ev_id=None):
        t1 = min(t1, L)
        if t1 - t0 < 0.3:
            return
        reqs.append({"kind": kind, "tpl": tpl, "vars": vars_, "key": asset_key(kind, vars_),
                     "t0": round(t0, 3), "t1": round(t1, 3), "fade": t1 < L - 0.05, "ev": ev_id})

    for i, ev in enumerate(tl.events):
        style, text, x, y = _parse(ev)
        if not text:
            continue
        if style == "Hook" and "hook" in on:
            if y > hook_y + 100:   # countdown label: sits under the big animated number
                y += 60
            add("hook", "hook", {"text": text, "y": y, "style": "box"}, ev["t0"], ev["t1"], i)
        elif style == "Tease" and "hook" in on:
            add("slam", "hook", {"text": text, "y": y, "style": "slam"}, ev["t0"], ev["t1"], i)
        elif style == "Cta" and "cta" in on:
            add("cta", "cta", {"text": text, "y": y}, ev["t0"], ev["t1"], i)
        elif style == "Card" and "countdown" in on:
            add("card", "card", {"text": text, "y": y}, ev["t0"], ev["t1"], i)
        elif style == "Label" and text.startswith("@") and "tag" in on:
            ty = hook_y - 185 if y < hook_y else hook_y + 185   # clear of hook / CTA boxes
            add("tag", "tag", {"text": text, "y": max(150, ty)}, ev["t0"], ev["t1"], i)

    if "stickers" in on and clip is not None:
        _plan_stickers(tl, clip, signals, hook_y, add)
    return reqs


def _plan_stickers(tl, clip, signals, hook_y, add):
    L = tl.length
    s, e = clip["s"], clip["e"]
    picks = []
    for st in clip.get("stickers") or []:            # the AI's picks first
        try:
            t = float(st.get("t"))
        except (TypeError, ValueError, AttributeError):
            continue
        emo = _one_emoji(st.get("emoji"))
        if emo and s + 0.5 <= t <= e - 0.5:
            picks.append((t, emo, _clean_sticker_text(st.get("text"))))
    if not picks:                                     # otherwise the strongest reactions
        for sig in sorted(signals, key=lambda x: -x["strength"]):
            if sig["kind"] in ("exclaim", "laugh") and sig["strength"] >= 2.5 and s + 1.5 <= sig["t"] <= e - 1:
                emo, text = _sticker_from_signal(sig)
                picks.append((sig["t"], emo, _clean_sticker_text(text)))
    limit = 3 if L >= 25 else 2
    placed = []
    for t, emo, text in picks:
        o = tl.out_time(t)
        if o is None or o < 2.0 or o > L - 3.2:
            continue
        if any(abs(o - p) < 4.0 for p in placed):
            continue
        side = len(placed) % 2
        add("sticker", "sticker", {"emoji": emo, "text": text, "x": 800 if side == 0 else 280,
                                   "y": hook_y + 340, "tilt": 1 if side == 0 else -1},
            o, o + LENGTH["sticker"])
        placed.append(o)
        if len(placed) >= limit:
            break


def apply(jobs, cfg, signals, geo, log=print):
    """jobs: [(clip_or_None, Timeline)]. Renders the animations and attaches them to each
    timeline as tl.overlays, removing the text events they replace. Never raises."""
    ok, msg = status()
    if not ok:
        log(msg + " Using the normal text overlays.")
        return
    try:
        plans = [(tl, plan(tl, clip, cfg, geo, signals)) for clip, tl in jobs]
        have = render_assets([r for _, rs in plans for r in rs], log)
    except Exception as e:
        log(f"Motion graphics skipped ({type(e).__name__}: {e}). Using the normal text overlays.")
        return
    used = 0
    for tl, rs in plans:
        drop, ovs = set(), []
        for r in rs:
            if r["key"] not in have:
                continue
            ov = overlay_for(r)
            if ov:
                ovs.append(ov)
                if r["ev"] is not None:
                    drop.add(r["ev"])
        tl.events = [ev for i, ev in enumerate(tl.events) if i not in drop]
        tl.overlays = ovs
        used += len(ovs)
    log(f"  {used} animated overlays added.")


def selftest():
    ok, msg = status()
    print(msg)
    if not ok:
        return 1
    t0 = time.time()
    req = {"kind": "hook", "tpl": "hook", "vars": {"text": f"MOTION TEST {int(t0)}", "y": 420,
                                                    "style": "box"}}
    req["key"] = asset_key("hook", req["vars"])
    got = render_assets([req], print)
    if req["key"] in got:
        n = len(glob.glob(os.path.join(asset_dir(req["key"]), "frame_*.png")))
        print(f"PASS: rendered {n} frames in {time.time() - t0:.1f}s")
        shutil.rmtree(asset_dir(req["key"]), ignore_errors=True)
        return 0
    print("FAIL: nothing was rendered (see the message above).")
    return 1


if __name__ == "__main__":
    sys.exit(selftest())
