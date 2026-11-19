"""ClipForge - local web app. Run:  python app.py  (opens http://127.0.0.1:7860)

Everything for a run is on one Clip page, top to bottom:
  Video | Live switch -> 1 source -> 2 layout (draw the facecam/gameplay boxes) -> 3 clips
  -> Style / Campaign (optional, folded) -> Start / Stop / New session -> progress -> results.
Publish (YouTube + Telegram) and Settings (keys, models, storage, phone access) are separate
pages because they are set once.

Runs happen in the background (Run here, live.LiveSession for streams); the page polls them
every second, so Stop works, and reloading the page reattaches to a run in progress.
"""
import os
import threading
import time
import traceback
from collections import deque

import gradio as gr

if os.name == "nt":
    # Windows prints a scary but harmless "ConnectionResetError [WinError 10054]" traceback
    # whenever a browser tab drops a connection. Silence just that case.
    import asyncio.proactor_events as _pe
    _orig_lost = _pe._ProactorBasePipeTransport._call_connection_lost

    def _quiet_lost(self, exc):
        try:
            _orig_lost(self, exc)
        except ConnectionResetError:
            pass
    _pe._ProactorBasePipeTransport._call_connection_lost = _quiet_lost

from cf import frames as FR
from cf import gameplay as GP
from cf import live as LIVE
from cf import publish as PUB
from cf import remote as RM
from cf import storage
from cf.brand import ANIMATIONS, ASSETS_DIR, remove_logo, store_logo
from cf.captions import COLORS
from cf.cloud_stt import MODELS as CLOUD_MODELS
from cf.formats import FORMATS
from cf.memes import library as meme_library
from cf.motion import ELEMENTS as MOTION_ELEMENTS, status as motion_check
from cf.pipeline import process
from cf.rules import read_rules_file
from cf.settings import load_settings, save_settings, update_settings
from cf.tools import OUTPUT_DIR, Cancelled, fmt_ts, stop_scope
from cf.transcribe import LOCAL_MODELS
from ui import boxpicker as BP
from ui.theme import CSS, THEME

STT_CHOICES = ([("Cloud · Whisper Large V3 (best)", f"cloud:{CLOUD_MODELS[0]}"),
                ("Cloud · Whisper V3 Turbo (cheaper)", f"cloud:{CLOUD_MODELS[1]}")]
               + [(f"Local: {m}", m) for m in LOCAL_MODELS])
HIGHLIGHTS = ["auto"] + list(COLORS)
LAYOUTS = [("🎥  Facecam + gameplay", "facecam"), ("🖼  Full frame", "blur"),
           ("⬛  Fill screen", "crop"), ("🎮  Split with gameplay", "split")]
LAYOUT_NOTES = {
    "facecam": "For streams: the streamer's camera on top, the game below, captions on the line "
               "between them. Load a frame and draw the two boxes.",
    "blur": "The whole video, nothing cut off, on a blurred copy of itself. Zoom above 1.0 crops the sides.",
    "crop": "Fills the vertical screen by cutting off the sides. Fine for videos already centred on the action.",
    "split": "The video on top, a muted gameplay video (Minecraft parkour, Subway Surfers...) underneath. "
             "Gameplay comes from the gameplay folder.",
}
_TEXT = lambda v: (v or "").strip()   # noqa: E731

# Preferences shown on the pages: settings key -> how to read the widget. Saved on every Start.
PREFS = {
    "mode": str, "layout": str, "zoom": float, "split_game": lambda v: v or GP.ANY,
    "n_clips": int, "min_len": int, "max_len": int, "top_n_on": bool, "top_n": int,
    "formats": lambda v: list(v or []) or ["standard"], "highlight": str, "words_per_line": int,
    "cta_on": bool, "progress_on": bool, "punch_on": bool, "sfx_on": bool, "sfx_gain": float,
    "meme_on": bool, "meme_gain": float, "motion_on": bool, "motion_elements": lambda v: list(v or []),
    "logo_anim": str, "logo_size": float,
    "live_interval": int, "live_min_score": int, "live_max_clips": int,
    "live_upload": bool, "live_keep": bool, "live_chat_on": bool,
    "api_key": _TEXT, "model": lambda v: (v or "auto").strip() or "auto", "allow_free": bool,
    "whisper_model": str, "fallback_model": str, "language": _TEXT, "keep_days": int,
    "yt_on": bool, "yt_mode": str, "yt_interval": int, "yt_first_delay": int, "yt_category": str,
    "telegram_token": _TEXT, "telegram_chat": _TEXT,
}
# What belongs to this run only (never saved; cleared by New session).
RUN_FIELDS = ("path_or_url", "upload", "range_start", "range_end", "live_url", "content_hint",
              "creator", "rules_text", "logo_state")
YT_KEYS = ["yt_mode", "yt_interval", "yt_first_delay", "yt_category", "telegram_token", "telegram_chat"]


# ---------------------------------------------------------------- background run (Video mode)
class Run:
    """One Video-mode job: process() + YouTube upload, in a thread, stoppable."""

    def __init__(self, source, cfg):
        self.source, self.cfg = source, cfg
        self.lines = deque(maxlen=800)
        self.result, self.error, self.state = None, "", "running"
        self.started, self.ended = time.time(), None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def log(self, msg):
        for part in str(msg).splitlines() or [""]:
            self.lines.append(f"{time.strftime('%H:%M:%S')}  {part}")

    @property
    def running(self):
        return self._thread.is_alive()

    def stop(self):
        if self.running and not self._stop.is_set():
            self.log("Stopping...")
            self.state = "stopping"
        self._stop.set()

    def _run(self):
        with stop_scope(self._stop):
            try:
                self.result = process(self.source, self.cfg, log=self.log)
                if self.cfg.get("yt_on"):
                    try:
                        PUB.after_run(self.result, self.cfg, log=self.log)
                    except Exception as e:   # clips are made; an upload problem must not hide them
                        self.log(f"YouTube upload problem: {e}")
                self.state = "done"
            except Cancelled:
                self.log("Stopped.")
                self.state = "stopped"
            except Exception as e:
                self.error = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
                self.log(f"ERROR: {e}")
                print(traceback.format_exc())
                self.state = "error"
            finally:
                self.ended = time.time()
        try:
            storage.clean(self.cfg.get("keep_days", 6), live_running=_live_running(), log=self.log)
        except Exception as e:
            print(f"storage clean failed: {e}")


RUN = {"job": None, "kind": None}       # the run the Clip page shows: kind "video" or "live"


def _live_running():
    s = LIVE.current()
    return bool(s and s.running)


def _busy():
    job = RUN["job"]
    return bool(job and job.running) or _live_running()


# ---------------------------------------------------------------- status texts
def motion_status():
    ok, msg = motion_check()
    return ("✅ " if ok else "⚠️ ") + msg + ("" if ok else " Until then clips use the normal text overlays.")


def meme_status():
    try:
        lib = meme_library(log=lambda m: None)
    except Exception as e:
        return f"⚠️ Couldn't read the sounds folder: {e}"
    if not lib:
        return ("⚠️ No sounds yet. Put mp3/wav files in the **sounds** folder inside ClipForge "
                "and describe when to use them in sounds\\sounds.txt.")
    return f"✅ {len(lib)} sounds: " + ", ".join(sorted(lib)) + ". The AI adds 0-2 per clip, only where one fits."


def split_status():
    if not GP.library():
        return "⚠️ " + GP.describe()
    return f"Gameplay library: {GP.describe()}. Each clip gets a different video, muted."


def game_choices():
    return [("Any game (mix)", GP.ANY)] + [(GP.nice(g), g) for g in sorted(GP.library())]


def yt_status():
    if not PUB.client_file():
        return "⚠️ No Google OAuth file. Put the client_secret...json from Google Cloud in the ClipForge folder."
    if not PUB.connected():
        return "Not connected. Click **Connect YouTube**: a Google sign-in opens in your browser."
    try:
        return f"✅ Connected to **{PUB.channel_title()}**."
    except Exception as e:
        return f"⚠️ {e}"


def share_status():
    s = load_settings()
    if not s.get("share_on"):
        return "Off. The app only works on this PC."
    if not s.get("share_hash"):
        return "⚠️ On, but no password yet: set one below, then restart ClipForge."
    return (f"✅ On for user **{s.get('share_user') or 'klipr'}**. After a restart, the phone link is "
            "printed in the ClipForge window and sent to your Telegram. It changes every restart.")


def storage_status():
    s = load_settings()
    return (f"ClipForge uses **{storage.describe()}**. Clips and temporary files older than "
            f"**{s.get('keep_days', 6)} days** are deleted automatically (at startup and after every run).")


# ---------------------------------------------------------------- the run
def _source(val):
    if val["mode"] == "live":
        return _TEXT(val["live_url"])
    return str(_TEXT(val["path_or_url"]).strip('"') or (val["upload"] or ""))


def collect_cfg(val):
    """Every setting on every page -> one cfg dict. Preferences are saved, run inputs are not."""
    cfg = {**load_settings(), **{k: conv(val[k]) for k, conv in PREFS.items() if k in val}}
    save_settings(cfg)                         # drops RUN_KEYS by itself
    cfg.update({"content_hint": _TEXT(val["content_hint"]), "creator": _TEXT(val["creator"]),
                "rules_text": val["rules_text"] or "", "logo_path": val["logo_state"] or "",
                "range_start": _TEXT(val["range_start"]) if val["mode"] == "video" else "",
                "range_end": _TEXT(val["range_end"]) if val["mode"] == "video" else ""})
    cfg["split_on"] = cfg["layout"] == "split"
    _, boxes = BP.read(val["picker"])
    cfg["facecam"] = boxes if cfg["layout"] == "facecam" else None
    return cfg


def make_start(w):
    def start(data):
        val = {k: data[c] for k, c in w.items() if c in data}
        try:
            if _busy():
                raise gr.Error("Something is already running. Stop it first.")
            source = _source(val)
            if not source:
                raise gr.Error("Add a video first: paste a link or a file path, or drop a file."
                               if val["mode"] == "video" else "Paste the live stream's link first.")
            cfg = collect_cfg(val)
            if not (cfg.get("api_key") or "").strip():
                raise gr.Error("Add your OpenRouter API key on the Settings page first.")
            if cfg["layout"] == "facecam" and not cfg["facecam"]:
                raise gr.Error("Facecam layout: press Load frame and draw the facecam and gameplay boxes first.")
            if val["mode"] == "live":
                RUN["job"], RUN["kind"] = LIVE.start(source, cfg), "live"
            else:
                RUN["job"], RUN["kind"] = Run(source, cfg), "video"
        except gr.Error:
            raise
        except Exception as e:
            raise gr.Error(str(e)) from None
        return view("")
    return start


def stop_run():
    job = RUN["job"]
    if job and job.running:
        job.stop()
    return view("")


def _clip_md(c, live=False):
    kws = c.get("keywords") or []
    head = f"**{c['title']}**  \n`{c['format']}` · {c['length']:.0f}s"
    if c.get("score") is not None:
        head += f" · score {c['score']}"
    if live:
        head += f" · {c.get('stream_time', '?')} into the stream"
    if c.get("gameplay"):
        head += f" · gameplay: {c['gameplay']}"
    body = f"\n\n{c['caption']}\n\n_hook:_ {c.get('hook', '')}"
    if c.get("trigger") or c.get("reason"):
        body += f"\n\n_why:_ {(c['trigger'] + ' - ') if c.get('trigger') else ''}{c.get('reason', '')}"
    if not live:
        body += f"\n\n_keywords ({len(kws)}):_ {', '.join(kws) or 'none'}"
    return head + body


def _status_line(job, kind):
    if not job:
        return "Ready. Add a video, choose a layout and press **Start**."
    if kind == "live":
        st = job.status()
        head = {"starting": "⏳ Starting...", "waiting": "⏳ Waiting for the stream to go live",
                "recording": "🔴 **Watching live**", "stopping": "⏳ Stopping...",
                "stopped": "⏹ Stopped", "error": "⚠️ Stopped with an error"}.get(st["state"], st["state"])
        if st["error"]:
            return f"{head}: {st['error'][:200]}"
        bits = [f"{fmt_ts(st['elapsed'], hours=True)} running", f"{st['checks']} checks",
                f"**{st['clips']} clips**", f"{st['uploaded']} uploaded"]
        if st["next_in"] is not None:
            bits.append(f"next check in {int(st['next_in']) // 60}:{int(st['next_in']) % 60:02d}")
        if st["chat"] is not None:
            bits.append(f"{st['chat']:,} chat msgs")
        return head + " · " + " · ".join(bits)
    took = fmt_ts((job.ended or time.time()) - job.started, hours=True)
    if job.state in ("running", "stopping"):
        last = job.lines[-1].split("  ", 1)[-1] if job.lines else "Starting..."
        return f"{'⏳ Stopping' if job.state == 'stopping' else '⏳ Working'} · {took} · {last[:110]}"
    if job.state == "done":
        r = job.result or {}
        cost = f" · est. cost ${r.get('est_cost_usd', 0):.3f}" if r.get("est_cost_usd") else ""
        return f"✅ **Done: {len(r.get('clips', []))} clips** in {took}{cost}"
    if job.state == "stopped":
        return f"⏹ Stopped after {took}."
    return f"⚠️ {job.error[:220]}"


def view(seen):
    """Everything the Clip page shows about the current run; heavy parts only when changed.
    Returns status, log, files, preview video, clip list, seen-key, timer, start, stop, new,
    progress card, results card."""
    job, kind = RUN["job"], RUN["kind"]
    running = bool(job and job.running)
    status = _status_line(job, kind)
    key = ""
    if job:
        key = f"{kind}|{id(job)}|{len(job.lines)}|{job.lines[-1] if job.lines else ''}|{running}"
    log = files = video = info = progress_card = results_card = gr.update()
    if key != seen:
        log = "\n".join(job.lines) if job else ""
        clips = []
        out_dir = ""
        if job and kind == "live":
            clips, out_dir = list(job.clips), job.out_dir
        elif job and job.result:
            clips, out_dir = job.result.get("clips", []), job.result.get("out_dir", "")
        paths = [c["file"] for c in clips if os.path.exists(c["file"])]
        files = paths or None
        video = paths[0] if paths else None
        info = ""
        if clips:
            info = f"`{out_dir}`\n\n---\n\n" + "\n\n---\n\n".join(
                _clip_md(c, kind == "live") for c in (reversed(clips) if kind == "live" else clips))
        progress_card = gr.update(visible=job is not None)
        results_card = gr.update(visible=bool(clips))
    return (status, log, files, video, info, key, gr.Timer(active=running),
            gr.update(interactive=not running), gr.update(interactive=running),
            gr.update(interactive=not running), progress_card, results_card)


# ---------------------------------------------------------------- frame + boxes
def _frame_source(mode, path, upload, live_url):
    if mode == "live":
        return _TEXT(live_url)
    return str(_TEXT(path).strip('"') or (upload or ""))


def _file_url(path):
    return "/gradio_api/file=" + path.replace("\\", "/")


def load_frame(mode, path, upload, live_url, frame, picker, layout, zoom, logo, logo_size, next_one=False):
    source = _frame_source(mode, path, upload, live_url)
    attempt = (frame or {}).get("attempt", -1) + 1 if next_one and (frame or {}).get("source") == source else 0
    try:
        fr = FR.grab(source, attempt=attempt, live=mode == "live")
    except Exception as e:
        return gr.update(), frame, gr.update(), f"⚠️ {e}"
    old, boxes = BP.read(picker)
    if not boxes or not old or (old.get("w"), old.get("h")) != (fr["width"], fr["height"]):
        boxes = FR.default_boxes(fr["width"], fr["height"], (boxes or {}).get("split", 0.4))
    frame = {**fr, "attempt": attempt, "source": source}
    when = f" at {fmt_ts(fr['at'])}" if fr.get("at") is not None else " (live now)"
    msg = (f"Frame{when} from **{fr['title'][:60] or 'your video'}**. "
           + ("Drag the boxes over the facecam and the gameplay." if layout == "facecam" else ""))
    return (BP.value(_file_url(fr["path"]), fr["width"], fr["height"], boxes), frame,
            _preview(frame, layout, boxes, zoom, logo, logo_size), msg)


def _preview(frame, layout, boxes, zoom, logo, logo_size):
    """Update for the preview image: shown only when there is a preview."""
    path = None
    if frame and frame.get("path") and os.path.exists(frame["path"]) and (layout != "facecam" or boxes):
        try:
            path = FR.preview(frame["path"], layout, boxes, zoom=zoom, logo_path=logo, logo_size=logo_size,
                              split_bottom=layout == "split")
        except Exception as e:
            print(f"preview failed: {e}")
    return gr.update(value=path, visible=path is not None)


def refresh_preview(frame, picker, layout, zoom, logo, logo_size):
    _, boxes = BP.read(picker)
    return _preview(frame, layout, boxes, zoom, logo, logo_size)


def on_layout(layout, frame, picker, zoom, logo, logo_size):
    _, boxes = BP.read(picker)
    return (gr.update(visible=layout == "facecam"), gr.update(visible=layout == "blur"),
            gr.update(visible=layout == "split", choices=game_choices()),
            gr.update(value=split_status() if layout == "split" else "", visible=layout == "split"),
            LAYOUT_NOTES[layout], _preview(frame, layout, boxes, zoom, logo, logo_size))


def on_mode(mode):
    """Video and Live show only the settings they use ("Clips per video" and Top-N are Video
    only: Live uses "Most clips per check" instead)."""
    video, live = mode == "video", mode == "live"
    return (gr.update(visible=video), gr.update(visible=live), gr.update(visible=video),
            gr.update(visible=live),
            gr.update(value="▶  Start watching" if live else "▶  Start clipping"),
            gr.update(visible=video))


def clip_hints(mode, n_clips, top_n_on, max_len, formats):
    """Warnings for settings that would silently do nothing: (Clips card note, formats note)."""
    clips = ""
    if mode == "video" and top_n_on and int(n_clips) < 3:
        clips = (f"⚠️ The Top-N countdown needs at least 3 clips per video: with {int(n_clips)} "
                 "it won't be made.")
    fmt = ""
    if "two_part" in (formats or []) and int(max_len) < 30:
        fmt = (f"⚠️ two_part needs moments of 30 s or more, and Max seconds is {int(max_len)}: "
               "it won't be used.")
    return gr.update(value=clips, visible=bool(clips)), gr.update(value=fmt, visible=bool(fmt))


def on_min_len(lo, hi):
    """Min can't pass Max: Max follows."""
    return gr.update(value=lo) if int(lo) > int(hi) else gr.update()


def on_max_len(hi, lo):
    """Max can't go under Min: Min follows."""
    return gr.update(value=hi) if int(hi) < int(lo) else gr.update()


# ---------------------------------------------------------------- small handlers
def on_logo(path, frame, picker, layout, zoom, logo_size):
    if not path:
        return gr.update(), gr.update(), "No file.", gr.update(), gr.update()
    try:
        stored = store_logo(path)
    except Exception as e:
        return gr.update(), gr.update(), f"Couldn't read that image: {e}", gr.update(), gr.update()
    return (stored, stored, "Logo added for this run.",
            refresh_preview(frame, picker, layout, zoom, stored, logo_size), gr.update(visible=True))


def on_remove_logo(frame, picker, layout, zoom, logo_size):
    remove_logo()
    return (None, "", "Logo removed.", refresh_preview(frame, picker, layout, zoom, "", logo_size),
            gr.update(visible=False))


def on_rules_file(path):
    if not path:
        return gr.update(), ""
    try:
        text = read_rules_file(path)
    except Exception as e:
        return gr.update(), f"Couldn't read that file: {e}"
    return text, f"Loaded {len(text):,} characters. Check the text below; you can edit it."


def on_motion(on):
    return gr.update(visible=on), gr.update(value=motion_status() if on else "", visible=on)


def on_meme(on):
    return gr.update(visible=on), gr.update(value=meme_status() if on else "", visible=on)


def on_connect():
    try:
        return f"✅ Connected to **{PUB.connect()}**."
    except Exception as e:
        return f"⚠️ {e}"


def on_disconnect():
    PUB.disconnect()
    return "Disconnected. Click **Connect YouTube** to sign in again."


def on_test_telegram(token, chat):
    ok, err = PUB.telegram({"telegram_token": token, "telegram_chat": chat},
                           "👋 ClipForge test message: notifications are working.")
    if ok:
        update_settings(telegram_token=_TEXT(token), telegram_chat=_TEXT(chat))
        return "✅ Sent. Check Telegram. (Bot saved.)"
    return f"⚠️ Telegram said: {err}"


def queue_view():
    items = PUB.review_list()
    return PUB.summary(), gr.update(choices=items, value=[]), gr.update(visible=bool(items))


def on_upload_queue(*vals):
    cfg = {**load_settings(), **{k: PREFS[k](v) for k, v in zip(YT_KEYS, vals)}}
    lines = []
    try:
        PUB.upload_queue(cfg, log=lines.append)
    except Exception as e:
        lines.append(f"⚠️ {e}")
    summ, review, btn = queue_view()
    return "\n".join(lines) or "Nothing to upload.", summ, review, btn


def on_publish(ids):
    if not ids:
        return "Tick the videos to publish first.", *queue_view()
    lines = []
    n = PUB.publish(ids, load_settings(), log=lines.append)
    return f"Published {n} of {len(ids)}." + ("\n" + "\n".join(lines) if lines else ""), *queue_view()


def on_share(on, user, pw):
    user = _TEXT(user)
    if on and not user:
        return "⚠️ Enter a username.", share_status()
    if pw and len(pw) < 8:
        return "⚠️ Use at least 8 characters.", share_status()
    kv = {"share_on": bool(on), "share_user": user or "klipr"}
    if pw:
        kv["share_hash"] = RM.hash_password(pw)
    update_settings(**kv)
    if on and not load_settings().get("share_hash"):
        return "⚠️ Saved, but set a password before the link can start.", share_status()
    return ("Saved. Restart ClipForge (close the window, run run.bat) to apply it."
            + (" Password changed." if pw else "")), share_status()


def save_prefs(*vals, keys=()):
    update_settings(**{k: PREFS[k](v) for k, v in zip(keys, vals)})


def on_clean(days):
    update_settings(keep_days=int(days))
    freed = storage.clean(int(days), live_running=_live_running())
    return f"Freed {freed / 1e6:.0f} MB." if freed else "Nothing old enough to delete.", storage_status()


def make_new_session(w, order):
    """New session: clear everything that belongs to the last run - inputs, frame and boxes,
    logo, rules, log, results - and forget a finished live session. Preferences stay."""
    def new_session():
        if _busy():
            gr.Warning("A run is still going: press Stop first.")
            return [gr.update() for _ in order] + list(view("")) + [gr.update()]
        RUN["job"], RUN["kind"] = None, None
        LIVE.forget()
        try:
            remove_logo()
        except Exception:
            pass
        blank = {"path_or_url": "", "upload": None, "range_start": "", "range_end": "", "live_url": "",
                 "content_hint": "", "creator": "", "rules_text": "", "rules_file": None, "rules_msg": "",
                 "logo_state": "", "logo_preview": None, "logo_file": None, "logo_msg": "",
                 "picker": "", "frame": None, "layout_preview": gr.update(value=None, visible=False),
                 "frame_msg": ""}
        gr.Info("New session: everything from the last run is cleared.")
        return ([blank[k] if k in blank else gr.update() for k in order] + list(view("refresh"))
                + [gr.update(visible=False)])
    return new_session


# ---------------------------------------------------------------- page building blocks
PAGES = [("clip", "🎬", "Clip"), ("publish", "📤", "Publish"), ("settings", "⚙️", "Settings")]


def make_nav(page):
    def nav():
        return [gr.Tabs(selected=page)] + [gr.update(elem_classes=["cf-nav", "active"] if p == page else ["cf-nav"])
                                           for p, _, _ in PAGES]
    return nav


def card(title=None, sub=None, step=None, visible=True):
    col = gr.Column(elem_classes="cf-card", visible=visible)
    if title:
        with col:
            num = f'<span class="cf-step">{step}</span>' if step else ""
            gr.HTML(f'<div class="cf-title">{num}{title}</div>' + (f'<div class="cf-sub">{sub}</div>' if sub else ""))
    return col


def page_head(emoji, title, sub):
    gr.HTML(f'<div class="emoji">{emoji}</div><h1>{title}</h1><p>{sub}</p>', elem_classes="cf-head")


# ---------------------------------------------------------------- layout
def build():
    s = load_settings()
    mode = s.get("mode") if s.get("mode") in ("video", "live") else "video"
    layout = s.get("layout") if s.get("layout") in dict((v, k) for k, v in LAYOUTS) else "blur"
    games = game_choices()
    split_game_now = s.get("split_game") if s.get("split_game") in [v for _, v in games] else GP.ANY
    w, nav = {}, {}
    with gr.Blocks(title="ClipForge") as ui:
        with gr.Row(elem_id="cf-shell", equal_height=False):
            # ================================================= sidebar
            with gr.Column(elem_id="cf-side", scale=0, min_width=244):
                gr.HTML('<div class="name"><span class="mark">C</span>ClipForge</div>'
                        '<div class="sub">Shorts from long videos</div>', elem_id="cf-brand")
                for pid, emoji, label in PAGES:
                    nav[pid] = gr.Button(f"{emoji}  {label}", elem_id=f"nav-{pid}",
                                         elem_classes=["cf-nav", "active"] if pid == "clip" else ["cf-nav"])
                gr.HTML("", elem_id="cf-side-gap")

            # ================================================= pages
            with gr.Column(elem_id="cf-main"):
                with gr.Tabs(elem_id="cf-pages", selected="clip") as pages:
                    # ------------------------------------------------ Clip
                    with gr.Tab("Clip", id="clip"):
                        page_head("🎬", "Clip", "Turn a video or a live stream into vertical shorts.")
                        w["mode"] = gr.Radio([("🎬  Video", "video"), ("🔴  Live stream", "live")], value=mode,
                                             show_label=False, elem_id="cf-mode", container=False)

                        with card("Source", "A file on this PC, a published YouTube link, or a live stream link.",
                                  step=1):
                            with gr.Column(visible=mode == "video") as video_src:
                                w["path_or_url"] = gr.Textbox(label="YouTube link or file path",
                                                              placeholder=r"https://youtu.be/...   or   D:\Videos\stream.mp4")
                                w["upload"] = gr.File(label="Or drop a video file here", type="filepath",
                                                      file_types=["video"], height=120)
                                with gr.Row():
                                    w["range_start"] = gr.Textbox(label="From (h:mm:ss, blank = start)",
                                                                  placeholder="0:00")
                                    w["range_end"] = gr.Textbox(label="To (blank = end)", placeholder="")
                            with gr.Column(visible=mode == "live") as live_src:
                                w["live_url"] = gr.Textbox(label="Live stream link (YouTube, Twitch, Kick...)",
                                                           placeholder="https://www.youtube.com/live/...")
                            with gr.Row():
                                w["content_hint"] = gr.Textbox(label="What is it? (helps the AI)",
                                                               placeholder="e.g. IShowSpeed playing GTA 6 RP")
                                w["creator"] = gr.Textbox(label="Creator name (optional)", placeholder="e.g. IShowSpeed")

                        with card("Layout", "How the vertical clip is framed. Load a frame to see a preview.",
                                  step=2):
                            w["layout"] = gr.Radio(LAYOUTS, value=layout, show_label=False, elem_id="cf-layout",
                                                   container=False)
                            layout_note = gr.Markdown(LAYOUT_NOTES[layout], elem_classes="cf-note")
                            with gr.Row(elem_classes="cf-actions"):
                                load_btn = gr.Button("🖼  Load frame", variant="secondary")
                                next_btn = gr.Button("↻  Another frame", variant="secondary")
                            w["frame_msg"] = gr.Markdown(elem_classes="cf-note")
                            with gr.Row(elem_id="cf-frame-row", equal_height=False):
                                with gr.Column(scale=3, min_width=300):
                                    w["picker"] = BP.picker(visible=layout == "facecam", elem_id="cf-picker")
                                    w["zoom"] = gr.Slider(1.0, 1.8, value=s["zoom"], step=0.05,
                                                          label="Zoom (1.0 = whole frame, higher crops the sides)",
                                                          visible=layout == "blur")
                                    w["split_game"] = gr.Dropdown(games, value=split_game_now, label="Gameplay underneath",
                                                                  visible=layout == "split")
                                    split_msg = gr.Markdown(split_status() if layout == "split" else "",
                                                            visible=layout == "split", elem_classes="cf-note")
                                with gr.Column(scale=2, min_width=200):
                                    w["layout_preview"] = gr.Image(label="Preview of a clip", interactive=False,
                                                                   height=460, elem_id="cf-layout-preview",
                                                                   show_label=True, buttons=[], visible=False)
                            w["frame"] = gr.State(None)

                        with card("Clips", "How many and how long.", step=3):
                            with gr.Row():
                                w["n_clips"] = gr.Slider(1, 30, value=s["n_clips"], step=1, label="Clips per video", elem_id="cf-n-clips",
                                                         visible=mode == "video")
                                w["min_len"] = gr.Slider(8, 60, value=s["min_len"], step=1, label="Min seconds", elem_id="cf-min-len")
                                w["max_len"] = gr.Slider(15, 180, value=s["max_len"], step=1, label="Max seconds", elem_id="cf-max-len")
                            with gr.Row(visible=mode == "video") as topn_row:
                                w["top_n_on"] = gr.Checkbox(label="Also make a Top-N countdown", value=s["top_n_on"],
                                                            elem_id="cf-topn-on")
                                w["top_n"] = gr.Slider(3, 10, value=s["top_n"], step=1, visible=s["top_n_on"],
                                                       label="N (at most the number of clips made)", elem_id="cf-topn")
                            clips_note = gr.Markdown(visible=False, elem_classes="cf-note", elem_id="cf-clips-note")
                            with gr.Column(visible=mode == "live") as live_opts:
                                with gr.Row():
                                    w["live_interval"] = gr.Slider(2, 15, value=s["live_interval"], step=1,
                                                                   label="Check every (minutes)")
                                    w["live_min_score"] = gr.Slider(50, 95, value=s["live_min_score"], step=1,
                                                                    label="Minimum score (75-85 works best)")
                                    w["live_max_clips"] = gr.Slider(1, 5, value=s["live_max_clips"], step=1,
                                                                    label="Most clips per check")
                                with gr.Row():
                                    w["live_chat_on"] = gr.Checkbox(label="Use live chat as a hint", value=s["live_chat_on"])
                                    w["live_upload"] = gr.Checkbox(label="Upload clips to YouTube as private",
                                                                   value=s["live_upload"])
                                    w["live_keep"] = gr.Checkbox(label="Keep the full recording", value=s["live_keep"])

                        with gr.Accordion("🎨  Style: formats, captions, sound, motion", open=False, elem_classes="cf-acc"):
                            w["formats"] = gr.CheckboxGroup(list(FORMATS), value=s["formats"],
                                                            label="Edit formats the AI may use (it picks per clip and keeps them varied)")
                            formats_note = gr.Markdown(visible=False, elem_classes="cf-note", elem_id="cf-formats-note")
                            with gr.Row():
                                w["highlight"] = gr.Dropdown(HIGHLIGHTS, value=s["highlight"],
                                                             label="Caption highlight (auto = varies per clip)")
                                w["words_per_line"] = gr.Slider(1, 6, value=s["words_per_line"], step=1,
                                                                label="Caption words per line")
                            with gr.Row():
                                w["cta_on"] = gr.Checkbox(label="End question", value=s["cta_on"])
                                w["progress_on"] = gr.Checkbox(label="Progress bar", value=s["progress_on"])
                                w["punch_on"] = gr.Checkbox(label="Punch-in zooms", value=s["punch_on"])
                            with gr.Row():
                                w["sfx_on"] = gr.Checkbox(label="Sound effects", value=s["sfx_on"], elem_id="cf-sfx-on")
                                w["sfx_gain"] = gr.Slider(0.1, 1.2, value=s["sfx_gain"], step=0.05, label="SFX volume", elem_id="cf-sfx-gain",
                                                          visible=s["sfx_on"])
                            with gr.Row():
                                w["meme_on"] = gr.Checkbox(label="Trending meme sounds", value=s["meme_on"])
                                w["meme_gain"] = gr.Slider(0.2, 1.5, value=s["meme_gain"], step=0.05,
                                                           label="Meme volume", visible=s["meme_on"])
                            meme_msg = gr.Markdown(meme_status() if s["meme_on"] else "", visible=s["meme_on"],
                                                   elem_classes="cf-note")
                            w["motion_on"] = gr.Checkbox(label="Animated hooks, cards and stickers (motion graphics)",
                                                         value=s["motion_on"])
                            w["motion_elements"] = gr.CheckboxGroup(
                                [(v + (" (Top-N only)" if k == "countdown" else ""), k)
                                 for k, v in MOTION_ELEMENTS.items()], value=s["motion_elements"],
                                label="Animate", visible=s["motion_on"])
                            motion_msg = gr.Markdown(motion_status() if s["motion_on"] else "",
                                                     visible=s["motion_on"], elem_classes="cf-note")

                        with gr.Accordion("📣  Campaign: sponsor logo and rules (optional, this run only)", open=False,
                                          elem_classes="cf-acc"):
                            with gr.Row():
                                w["logo_file"] = gr.File(label="Sponsor logo (PNG with transparency looks best)",
                                                         file_types=["image"], type="filepath", height=120)
                                w["logo_preview"] = gr.Image(label="Current logo", interactive=False, height=120)
                            w["logo_state"] = gr.State("")
                            w["logo_msg"] = gr.Markdown(elem_classes="cf-note")
                            with gr.Column(visible=False, elem_id="cf-logo-opts") as logo_opts:     # only once a logo is added
                                with gr.Row():
                                    w["logo_anim"] = gr.Radio(ANIMATIONS, value=s["logo_anim"], label="Logo animation")
                                    w["logo_size"] = gr.Slider(0.5, 1.6, value=s["logo_size"], step=0.05,
                                                               label="Logo size")
                                remove_btn = gr.Button("Remove logo", size="sm", variant="secondary")
                            w["rules_file"] = gr.File(label="Campaign rules file (.txt .md .pdf .docx)",
                                                      file_types=[".txt", ".md", ".pdf", ".docx"], type="filepath",
                                                      height=110)
                            w["rules_msg"] = gr.Markdown(elem_classes="cf-note")
                            w["rules_text"] = gr.Textbox(label="Campaign rules (only clips that pass every rule are made)",
                                                         lines=6, max_lines=20, placeholder="Paste the rules, or upload them above.")

                        with gr.Row(elem_id="cf-actionbar"):
                            start_btn = gr.Button("▶  Start watching" if mode == "live" else "▶  Start clipping",
                                                  variant="primary", elem_id="cf-start")
                            stop_btn = gr.Button("■  Stop", variant="secondary", interactive=False, elem_id="cf-stop")
                            new_btn = gr.Button("↺  New session", variant="secondary", elem_id="cf-new")
                            status = gr.Markdown(_status_line(None, None), elem_id="cf-status")

                        with card("Progress", visible=False) as progress_col:
                            log_box = gr.Textbox(show_label=False, lines=9, max_lines=14, autoscroll=True,
                                                 elem_id="cf-log", placeholder="What ClipForge is doing appears here.")
                        with card("Results", "Every clip also has a .txt next to it with its title, caption and keywords.",
                                  visible=False) as results_col:
                            with gr.Row():
                                with gr.Column(scale=2, min_width=240):
                                    video_out = gr.Video(label="Top clip", height=480, elem_id="cf-preview")
                                with gr.Column(scale=3, min_width=260):
                                    files_out = gr.Files(label="All clips")
                                    info_out = gr.Markdown(elem_classes="cf-note")
                        seen = gr.State("")
                        timer = gr.Timer(1.0, active=False)

                    # ------------------------------------------------ Publish
                    with gr.Tab("Publish", id="publish"):
                        page_head("📤", "Publish", "Upload clips to YouTube as private, review them, and get a "
                                  "Telegram message at every step.")
                        with card("YouTube"):
                            yt_state = gr.Markdown(yt_status(), elem_classes="cf-note")
                            with gr.Row(elem_classes="cf-actions"):
                                yt_connect = gr.Button("Connect YouTube", variant="secondary")
                                yt_disconnect = gr.Button("Disconnect", variant="secondary")
                            w["yt_on"] = gr.Checkbox(label="Upload to YouTube when a video run finishes", value=s["yt_on"])
                            w["yt_mode"] = gr.Radio([("Private: I review and publish", "review"),
                                                     ("Scheduled: goes public automatically", "scheduled")],
                                                    value=s["yt_mode"], label="After upload")
                            with gr.Row(visible=s["yt_mode"] == "scheduled", elem_id="cf-sched") as sched_row:
                                w["yt_interval"] = gr.Slider(1, 120, value=s["yt_interval"], step=1,
                                                             label="Minutes between videos (scheduled)")
                                w["yt_first_delay"] = gr.Slider(0, 240, value=s["yt_first_delay"], step=5,
                                                                label="First one goes public after (min)")
                            w["yt_category"] = gr.Dropdown(list(PUB.CATEGORIES.items()), value=s["yt_category"],
                                                           label="Category")
                            yt_save = gr.Button("Save", size="sm", variant="secondary")
                            gr.Markdown("Every video is uploaded **private**, not made for kids, with the clip's title, "
                                        "caption and keywords. Campaign and live clips are never scheduled: they wait "
                                        "for your review.", elem_classes="cf-note")
                        with card("Review queue", "Private videos wait here. Tick the ones you checked and publish them."):
                            q_sum, _, _ = queue_view()
                            yt_queue = gr.Markdown(q_sum, elem_classes="cf-note")
                            yt_review = gr.CheckboxGroup(choices=PUB.review_list(), value=[], label="Waiting for review")
                            with gr.Row(elem_classes="cf-actions"):
                                yt_publish = gr.Button("Publish selected now", variant="primary",
                                                       visible=bool(PUB.review_list()))
                                yt_upload = gr.Button("Upload queued clips", variant="secondary")
                                yt_refresh = gr.Button("Refresh", variant="secondary")
                            yt_log = gr.Textbox(show_label=False, lines=4, max_lines=10,
                                                placeholder="Upload messages appear here.", elem_id="cf-ytlog")
                        with card("Telegram notifications"):
                            with gr.Row():
                                w["telegram_token"] = gr.Textbox(label="Bot token", value=s["telegram_token"],
                                                                 type="password")
                                w["telegram_chat"] = gr.Textbox(label="Chat id", value=s["telegram_chat"])
                            with gr.Row():
                                tg_test = gr.Button("Send test message", variant="secondary", size="sm")
                                tg_msg = gr.Markdown(elem_classes="cf-note")

                    # ------------------------------------------------ Settings
                    with gr.Tab("Settings", id="settings"):
                        page_head("⚙️", "Settings", "Keys, models, storage and phone access. Set once and forget.")
                        with card("AI"):
                            w["api_key"] = gr.Textbox(label="OpenRouter API key", value=s["api_key"], type="password")
                            w["model"] = gr.Textbox(label="Model ('auto' = cheapest that works)", value=s["model"])
                            w["allow_free"] = gr.Checkbox(label="Try free models first (only used when Model is 'auto')",
                                                          value=s["allow_free"])
                        with card("Transcription"):
                            w["whisper_model"] = gr.Dropdown(STT_CHOICES, value=s["whisper_model"], label="Engine", elem_id="cf-stt",
                                                             info="Cloud costs about $0.03-0.22 per hour of audio; local is free but slow.")
                            with gr.Row():
                                w["fallback_model"] = gr.Dropdown(LOCAL_MODELS, value=s.get("fallback_model") or "small",
                                                                  label="If cloud fails, use local model", elem_id="cf-fallback",
                                                                  visible=str(s["whisper_model"]).startswith("cloud:"))
                                w["language"] = gr.Textbox(label="Language (blank = auto, e.g. en, hi)", value=s["language"])
                        ai_save = gr.Button("Save AI and transcription settings", size="sm", variant="secondary")
                        with card("Storage", "Everything ClipForge makes is temporary."):
                            storage_md = gr.Markdown(storage_status(), elem_classes="cf-note")
                            w["keep_days"] = gr.Slider(1, 30, value=s.get("keep_days", 6), step=1,
                                                       label="Delete clips and temporary files after (days)")
                            with gr.Row():
                                clean_btn = gr.Button("Save and clean up now", size="sm", variant="secondary")
                                clean_msg = gr.Markdown(elem_classes="cf-note")
                        with card("Phone access", "A private link you can open on your phone from anywhere. Login required."):
                            sh_state = gr.Markdown(share_status(), elem_classes="cf-note")
                            sh_on = gr.Checkbox(label="Allow access from my phone (share link with login)",
                                                value=s["share_on"])
                            with gr.Row():
                                sh_user = gr.Textbox(label="Username", value=s["share_user"] or "klipr")
                                sh_pw = gr.Textbox(label="New password (8+ characters; blank = keep)", type="password")
                            with gr.Row():
                                sh_save = gr.Button("Save phone access", variant="secondary", size="sm")
                                sh_msg = gr.Markdown(elem_classes="cf-note")

        # ------------------------------------------------ events
        for pid, _, _ in PAGES:
            nav[pid].click(make_nav(pid), None, [pages] + [nav[p] for p, _, _ in PAGES], show_progress="hidden")
        nav["publish"].click(queue_view, None, [yt_queue, yt_review, yt_publish], show_progress="hidden")
        nav["settings"].click(storage_status, None, [storage_md], show_progress="hidden")

        run_out = [status, log_box, files_out, video_out, info_out, seen, timer, start_btn, stop_btn, new_btn,
                   progress_col, results_col]
        all_inputs = {w[k] for k in list(PREFS) + list(RUN_FIELDS) + ["picker"] if k in w}
        start_btn.click(make_start(w), all_inputs, run_out)
        stop_btn.click(stop_run, None, run_out)
        timer.tick(view, [seen], run_out, show_progress="hidden")
        ui.load(lambda: view(""), None, run_out, show_progress="hidden")

        w["mode"].change(on_mode, [w["mode"]], [video_src, live_src, topn_row, live_opts, start_btn, w["n_clips"]])
        hint_in = [w["mode"], w["n_clips"], w["top_n_on"], w["max_len"], w["formats"]]
        for comp in hint_in:
            comp.change(clip_hints, hint_in, [clips_note, formats_note], show_progress="hidden")
        w["min_len"].change(on_min_len, [w["min_len"], w["max_len"]], [w["max_len"]], show_progress="hidden")
        w["max_len"].change(on_max_len, [w["max_len"], w["min_len"]], [w["min_len"]], show_progress="hidden")
        w["top_n_on"].change(lambda on: gr.update(visible=on), [w["top_n_on"]], [w["top_n"]], show_progress="hidden")
        w["sfx_on"].change(lambda on: gr.update(visible=on), [w["sfx_on"]], [w["sfx_gain"]], show_progress="hidden")
        w["yt_mode"].change(lambda m: gr.update(visible=m == "scheduled"), [w["yt_mode"]], [sched_row],
                            show_progress="hidden")
        w["whisper_model"].change(lambda m: gr.update(visible=str(m).startswith("cloud:")), [w["whisper_model"]],
                                  [w["fallback_model"]], show_progress="hidden")
        ui.load(clip_hints, hint_in, [clips_note, formats_note], show_progress="hidden")
        w["mode"].change(lambda m: update_settings(mode=m), [w["mode"]], None)
        prev_in = [w["frame"], w["picker"], w["layout"], w["zoom"], w["logo_state"], w["logo_size"]]
        w["layout"].change(on_layout, [w["layout"], w["frame"], w["picker"], w["zoom"], w["logo_state"], w["logo_size"]],
                           [w["picker"], w["zoom"], w["split_game"], split_msg, layout_note, w["layout_preview"]])
        frame_in = [w["mode"], w["path_or_url"], w["upload"], w["live_url"], w["frame"], w["picker"], w["layout"],
                    w["zoom"], w["logo_state"], w["logo_size"]]
        frame_out = [w["picker"], w["frame"], w["layout_preview"], w["frame_msg"]]
        load_btn.click(load_frame, frame_in, frame_out)
        next_btn.click(lambda *a: load_frame(*a, next_one=True), frame_in, frame_out)
        w["picker"].change(refresh_preview, prev_in, [w["layout_preview"]], show_progress="hidden")
        w["zoom"].release(refresh_preview, prev_in, [w["layout_preview"]], show_progress="hidden")
        w["logo_size"].release(refresh_preview, prev_in, [w["layout_preview"]], show_progress="hidden")

        w["logo_file"].upload(on_logo, [w["logo_file"], w["frame"], w["picker"], w["layout"], w["zoom"], w["logo_size"]],
                              [w["logo_preview"], w["logo_state"], w["logo_msg"], w["layout_preview"], logo_opts])
        remove_btn.click(on_remove_logo, [w["frame"], w["picker"], w["layout"], w["zoom"], w["logo_size"]],
                         [w["logo_preview"], w["logo_state"], w["logo_msg"], w["layout_preview"], logo_opts])
        w["rules_file"].upload(on_rules_file, [w["rules_file"]], [w["rules_text"], w["rules_msg"]])
        w["motion_on"].change(on_motion, [w["motion_on"]], [w["motion_elements"], motion_msg])
        w["meme_on"].change(on_meme, [w["meme_on"]], [w["meme_gain"], meme_msg])

        order = list(w)
        new_btn.click(make_new_session(w, order), None, [w[k] for k in order] + run_out + [logo_opts])

        yt_keys = ["yt_on"] + YT_KEYS
        yt_save.click(lambda *v: save_prefs(*v, keys=yt_keys), [w[k] for k in yt_keys], None).then(
            lambda: gr.Info("Saved."), None, None)
        ai_keys = ["api_key", "model", "allow_free", "whisper_model", "fallback_model", "language"]
        ai_save.click(lambda *v: save_prefs(*v, keys=ai_keys), [w[k] for k in ai_keys], None).then(
            lambda: gr.Info("Saved."), None, None)
        clean_btn.click(on_clean, [w["keep_days"]], [clean_msg, storage_md])
        sh_save.click(on_share, [sh_on, sh_user, sh_pw], [sh_msg, sh_state]).then(lambda: "", None, [sh_pw])
        yt_connect.click(on_connect, None, [yt_state])
        yt_disconnect.click(on_disconnect, None, [yt_state])
        tg_test.click(on_test_telegram, [w["telegram_token"], w["telegram_chat"]], [tg_msg])
        yt_refresh.click(queue_view, None, [yt_queue, yt_review, yt_publish])
        yt_upload.click(on_upload_queue, [w[k] for k in YT_KEYS], [yt_log, yt_queue, yt_review, yt_publish])
        yt_publish.click(on_publish, [yt_review], [yt_log, yt_queue, yt_review, yt_publish])
    return ui


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(ASSETS_DIR, exist_ok=True)
    os.makedirs(GP.GAMEPLAY_DIR, exist_ok=True)
    s = load_settings()
    save_settings(s)                               # drops old run inputs from settings.json
    try:
        remove_logo()                              # a sponsor logo belongs to one run
    except Exception:
        pass
    threading.Thread(target=lambda: storage.clean(s.get("keep_days", 6), log=print), daemon=True).start()
    share, why = RM.ready(s)
    if why:
        print("\n" + why + " Set a password in Settings > Phone access.\n")
    kw = {}
    share_errors = []
    if share:
        kw = {"share": True, "auth": RM.make_auth(s.get("share_user") or "klipr", s["share_hash"]),
              "auth_message": "ClipForge: sign in"}
        RM.retry_share_tunnel(share_errors)
    ui = build().queue()
    ui.launch(inbrowser=True, allowed_paths=[OUTPUT_DIR, ASSETS_DIR, FR.FRAMES_DIR], theme=THEME, css=CSS,
              prevent_thread_lock=True, **kw)
    if share:
        url = getattr(ui, "share_url", None)
        if url:
            print(f"\nPhone link (login: {s.get('share_user') or 'klipr'}): {url}\n")
            ok, err = PUB.telegram(s, f"📱 ClipForge is running. Phone link:\n{url}\n"
                                      f"Login: {s.get('share_user') or 'klipr'} + your password. "
                                      "The link changes every restart.")
            if not ok and (s.get("telegram_token") or "").strip():
                print(f"(Couldn't send the link to Telegram: {err})")
        else:
            print("\nThe phone link couldn't be created. The app still works on this PC.")
            print(RM.explain_share_error(share_errors[-1] if share_errors else None) + "\n")
    try:
        ui.block_thread()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
