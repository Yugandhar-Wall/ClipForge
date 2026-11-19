"""ClipForge - local web app. Run:  python app.py  (opens http://127.0.0.1:7860)"""
import os
import queue
import threading
import traceback

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

from cf import gameplay as GP
from cf import live as LIVE
from cf import publish as PUB
from cf import remote as RM
from cf.brand import ANIMATIONS, ASSETS_DIR, remove_logo, store_logo
from cf.formats import FORMATS
from cf.memes import library as meme_library
from cf.motion import ELEMENTS as MOTION_ELEMENTS, status as motion_check
from cf.pipeline import DEFAULTS, process
from cf.rules import read_rules_file
from cf.settings import load_settings, save_settings, update_settings
from cf.tools import OUTPUT_DIR
from cf.transcribe import LOCAL_MODELS

STT_CHOICES = [("Cloud · Whisper Large V3 (best)", "cloud:openai/whisper-large-v3"),
               ("Cloud · Whisper V3 Turbo (cheaper)", "cloud:openai/whisper-large-v3-turbo")] + [(f"Local: {m}", m) for m in LOCAL_MODELS]
HIGHLIGHTS = ["auto", "yellow", "green", "cyan", "pink", "orange", "white"]

# Saved form fields: settings key -> how to read the widget's value.
_TEXT = lambda v: (v or "").strip()
SAVED = {
    "content_hint": _TEXT, "creator": _TEXT, "formats": lambda v: list(v or []) or ["standard"],
    "top_n_on": bool, "top_n": int, "n_clips": int, "min_len": int, "max_len": int,
    "split_on": bool, "split_game": lambda v: v or GP.ANY,
    "motion_on": bool, "motion_elements": lambda v: list(v or []),
    "meme_on": bool, "meme_gain": float, "sfx_on": bool, "sfx_gain": float,
    "highlight": str, "words_per_line": int, "cta_on": bool, "progress_on": bool,
    "punch_on": bool, "layout": str, "zoom": float,
    "logo_anim": str, "logo_size": float, "rules_text": lambda v: v or "",
    "api_key": _TEXT, "model": lambda v: (v or "auto").strip() or "auto", "allow_free": bool,
    "whisper_model": str, "fallback_model": str, "language": _TEXT,
    "yt_on": bool, "yt_mode": str, "yt_interval": int, "yt_first_delay": int, "yt_category": str,
    "telegram_token": _TEXT, "telegram_chat": _TEXT,
    "live_interval": int, "live_min_score": int, "live_max_clips": int,
    "live_upload": bool, "live_keep": bool, "live_chat_on": bool,
}
KEEP_ON_RESET = ("api_key", "telegram_token", "telegram_chat", "share_on", "share_user", "share_hash")
EMPTY_RESULTS = ("", None, None, "")


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
    return (f"✅ {len(lib)} sounds: " + ", ".join(sorted(lib)) +
            ". The AI adds 0-2 per clip, only where one fits.")


def split_status():
    lib = GP.library()
    if not lib:
        return "⚠️ " + GP.describe()
    return f"✅ Gameplay library: {GP.describe()}. Each clip gets a different video, muted."


def game_choices():
    return [("Any game (mix)", GP.ANY)] + [(GP.nice(g), g) for g in sorted(GP.library())]


# ---------------------------------------------------------------- run
def collect_cfg(val):
    """Every setting on every page -> one cfg dict, saved to settings.json."""
    cfg = {k: conv(val[k]) for k, conv in SAVED.items()}
    cfg["logo_path"] = val["logo_state"] or ""
    cfg = {**load_settings(), **cfg}  # keep keys the form doesn't show (settings version etc.)
    cfg.pop("range_start", None), cfg.pop("range_end", None)
    save_settings(cfg)
    return cfg


def make_run(w):
    """Builds the click handler; `w` maps names to widgets so inputs arrive by name, not position."""
    def run(data):
        val = {k: data[c] for k, c in w.items() if c in data}
        source = str((val["path_or_url"] or "").strip().strip('"') or (val["upload"] or ""))
        if not source:
            yield "Pick a video file or paste a path / URL.", None, None, ""
            return
        cfg = collect_cfg(val)
        # From/To belong to this one video, so they are used for this run but never saved
        cfg["range_start"], cfg["range_end"] = _TEXT(val["range_start"]), _TEXT(val["range_end"])

        q, lines, box = queue.Queue(), [], {}

        def worker():
            try:
                box["res"] = process(source, cfg, log=q.put)
                if cfg.get("yt_on"):
                    try:
                        PUB.after_run(box["res"], cfg, log=q.put)
                    except Exception as e:   # clips are made; an upload problem must not hide them
                        q.put(f"YouTube upload problem: {e}")
            except Exception as e:
                q.put(f"ERROR: {e}")
                box["err"] = traceback.format_exc()
            finally:
                q.put(None)

        threading.Thread(target=worker, daemon=True).start()
        while True:
            msg = q.get()
            if msg is None:
                break
            lines.append(str(msg))
            yield "\n".join(lines[-200:]), None, None, ""
        res = box.get("res")
        if not res:
            yield "\n".join(lines) + "\n\n" + box.get("err", ""), None, None, ""
            return
        n = len(res["clips"])
        md = [f"### {n} clip{'' if n == 1 else 's'} saved\n\n`{res['out_dir']}`\n\n---"]
        for i, c in enumerate(res["clips"], 1):
            kws = c.get("keywords") or []
            md.append(f"**{i}. {c['title']}**  \n`{c['format']}` · {c['length']:.0f}s"
                      + (f" · score {c['score']}" if c.get("score") is not None else "")
                      + (f" · gameplay: {c['gameplay']}" if c.get("gameplay") else "")
                      + f"\n\n{c['caption']}\n\n_hook:_ {c['hook']}\n\n"
                      f"_keywords ({len(kws)}):_ {', '.join(kws) or 'none'}\n\n---")
        files = [c["file"] for c in res["clips"]]
        yield "\n".join(lines), files, files[0] if files else None, "\n\n".join(md)
    return run


# ---------------------------------------------------------------- small handlers
def on_logo(path):
    if not path:
        return gr.update(), gr.update(), "No file."
    try:
        stored = store_logo(path)
    except Exception as e:
        return gr.update(), gr.update(), f"Couldn't read that image: {e}"
    update_settings(logo_path=stored)
    return stored, stored, "Logo saved. It appears on every clip, under the captions."


def on_remove_logo():
    remove_logo()
    update_settings(logo_path="")
    return None, "", "Logo removed."


def on_rules_file(path):
    if not path:
        return gr.update(), ""
    try:
        text = read_rules_file(path)
    except Exception as e:
        return gr.update(), f"Couldn't read that file: {e}"
    update_settings(rules_text=text)
    return text, f"Loaded {len(text):,} characters. Check the text below; you can edit it."


def on_motion(on):
    update_settings(motion_on=bool(on))
    return gr.update(visible=on), gr.update(value=motion_status() if on else "", visible=on)


def on_meme(on):
    update_settings(meme_on=bool(on))
    return gr.update(visible=on), gr.update(value=meme_status() if on else "", visible=on)


def on_split(on):
    update_settings(split_on=bool(on))
    choices = game_choices()
    keep = load_settings().get("split_game") or GP.ANY
    keep = keep if keep in [v for _, v in choices] else GP.ANY
    return (gr.update(visible=on, choices=choices, value=keep),
            gr.update(value=split_status() if on else "", visible=on))


def yt_status():
    if not PUB.client_file():
        return "⚠️ No Google OAuth file. Put the client_secret...json from Google Cloud in the ClipForge folder."
    if not PUB.connected():
        return "Not connected. Click **Connect YouTube**: a Google sign-in opens in your browser."
    try:
        return f"✅ Connected to **{PUB.channel_title()}**."
    except Exception as e:
        return f"⚠️ {e}"


def on_connect():
    try:
        title = PUB.connect()
        return f"✅ Connected to **{title}**."
    except Exception as e:
        return f"⚠️ {e}"


def on_disconnect():
    PUB.disconnect()
    return "Disconnected. Click **Connect YouTube** to sign in again."


def on_test_telegram(token, chat):
    ok, err = PUB.telegram({"telegram_token": token, "telegram_chat": chat},
                           "👋 ClipForge test message: notifications are working.")
    if ok:
        update_settings(telegram_token=(token or "").strip(), telegram_chat=(chat or "").strip())
        return "✅ Sent. Check Telegram. (Bot saved.)"
    return f"⚠️ Telegram said: {err}"


def queue_view():
    items = PUB.review_list()
    return (PUB.summary(), gr.update(choices=items, value=[]),
            gr.update(visible=bool(items)))


def on_upload_queue(*vals):
    cfg = {**load_settings(), **{k: SAVED[k](v) for k, v in zip(YT_KEYS, vals)}}
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


def share_status():
    s = load_settings()
    if not s.get("share_on"):
        return "Off. The app only works on this PC."
    if not s.get("share_hash"):
        return "⚠️ On, but no password yet: set one below, then restart ClipForge."
    return (f"✅ On for user **{s.get('share_user') or 'klipr'}**. After a restart, the phone link is "
            "printed in the ClipForge window and sent to your Telegram. It changes every restart.")


def on_share(on, user, pw):
    user = (user or "").strip()
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


YT_KEYS = ["yt_mode", "yt_interval", "yt_first_delay", "yt_category", "telegram_token", "telegram_chat"]


def make_reset(w, order):
    """Reset the form to the defaults (API key kept), clear the logo, rules and last results.
    Cached transcripts/downloads and finished clips are kept."""
    def reset():
        s = load_settings()
        fresh = {**DEFAULTS, **{k: s.get(k, "") for k in KEEP_ON_RESET},
                 "settings_version": s.get("settings_version", 2)}
        fresh.pop("range_start", None), fresh.pop("range_end", None)
        save_settings(fresh)
        try:
            remove_logo()
        except Exception:
            pass
        values = {
            **{k: fresh[k] for k in SAVED},
            "upload": None, "path_or_url": "", "range_start": "", "range_end": "", "live_url": "",
            "logo_state": "", "logo_preview": None, "logo_file": None, "logo_msg": "",
            "rules_file": None, "rules_msg": "",
            "log": "", "files": None, "preview": None, "info": "",
            "motion_msg": "", "meme_msg": "", "split_msg": "",
        }
        ups = []
        for k in order:
            if k == "motion_elements":
                ups.append(gr.update(value=values[k], visible=False))
            elif k == "meme_gain":
                ups.append(gr.update(value=values[k], visible=False))
            elif k == "split_game":
                ups.append(gr.update(value=GP.ANY, choices=game_choices(), visible=False))
            elif k in ("motion_msg", "meme_msg", "split_msg"):
                ups.append(gr.update(value="", visible=False))
            else:
                ups.append(values[k])
        ups.append("Everything is back to the defaults. Your API key, Telegram bot, YouTube "
                   "connection, phone access and finished clips were kept.")
        gr.Info("Settings reset to the defaults.")
        return ups
    return reset



# ---------------------------------------------------------------- live monitor
def _clock(sec):
    sec = max(0, int(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def live_status_text(sess):
    if not sess:
        return "**Not watching anything.** Paste a live stream link and press Start."
    st = sess.status()
    head = {"starting": "⏳ **Starting...**", "waiting": "⏳ **Waiting for the stream to go live**",
            "recording": "🔴 **Watching live**", "stopping": "⏳ **Stopping: finishing the last check**",
            "stopped": "⏹ **Stopped**", "error": "⚠️ **Stopped**"}.get(st["state"], st["state"])
    if st["title"]:
        head += f" · {st['title'][:80]}"
    if st["error"]:
        return head + f"\n\n{st['error']}"
    bits = [f"running {_clock(st['elapsed'])}", f"{_clock(st['recorded'])} recorded",
            f"{st['checks']} check{'' if st['checks'] == 1 else 's'}",
            f"**{st['clips']} clip{'' if st['clips'] == 1 else 's'}**", f"{st['uploaded']} uploaded"]
    if st["next_in"] is not None:
        bits.append(f"next check in {int(st['next_in']) // 60}:{int(st['next_in']) % 60:02d}")
    if st["chat"] is not None:
        bits.append(f"{st['chat']:,} chat messages read")
    if st["state"] in ("recording", "stopping"):
        bits.append(f"{st['disk_mb']:.0f} MB on disk")
    return head + "\n\n" + " · ".join(bits)


def live_view(seen):
    """Everything the Live page shows; heavy parts only change when something happened."""
    sess = LIVE.current()
    running = bool(sess and sess.running)
    key = f"{len(sess.clips)}|{len(sess.lines)}|{sess.lines[-1] if sess.lines else ''}" if sess else ""
    log = files = info = gr.update()
    if key != seen:
        log = "\n".join(sess.lines) if sess else ""
        clips = list(sess.clips) if sess else []
        files = [c["file"] for c in clips if os.path.exists(c["file"])] or None
        info = "\n\n".join(
            f"**{c['title']}**  \n{c.get('stream_time', '?')} into the stream · {c['length']:.0f}s"
            + (f" · score {c['score']}" if c.get("score") is not None else "")
            for c in reversed(clips))
        if clips:
            info = f"`{sess.out_dir}`\n\n" + info
    return (live_status_text(sess), log, files, info, key, gr.Timer(active=running),
            gr.update(interactive=not running), gr.update(interactive=running))


def make_live_start(w):
    def live_start(data):
        val = {k: data[c] for k, c in w.items() if c in data}
        try:
            LIVE.start(val["live_url"], collect_cfg(val))
        except Exception as e:
            gr.Warning(str(e))
        return live_view("")
    return live_start


def live_stop():
    LIVE.stop()
    return live_view("")


PAGES = [("create", "🎬", "Create"), ("live", "🔴", "Live"), ("style", "🎨", "Style"),
         ("campaign", "📣", "Campaign"), ("publish", "📤", "Publish"), ("settings", "⚙️", "Settings")]


def make_nav(page):
    def nav():
        return [gr.Tabs(selected=page)] + [gr.update(elem_classes=["cf-nav", "active"] if p == page else ["cf-nav"])
                                           for p, _, _ in PAGES]
    return nav

# ---------------------------------------------------------------- look
_SANS = ["ui-sans-serif", "-apple-system", "Segoe UI Variable Display", "Segoe UI", "Helvetica", "Arial", "sans-serif"]
THEME = gr.themes.Base(
    primary_hue=gr.themes.colors.blue, secondary_hue=gr.themes.colors.stone,
    neutral_hue=gr.themes.colors.stone, radius_size=gr.themes.sizes.radius_sm,
    text_size=gr.themes.sizes.text_md, spacing_size=gr.themes.sizes.spacing_md,
    font=_SANS, font_mono=["ui-monospace", "SFMono-Regular", "Consolas", "Menlo", "monospace"],
).set(
    body_background_fill="#ffffff", body_background_fill_dark="#191919",
    body_text_color="#37352f", body_text_color_dark="#e3e2e0",
    body_text_color_subdued="#787774", body_text_color_subdued_dark="#9b9a97",
    background_fill_primary="#ffffff", background_fill_primary_dark="#191919",
    background_fill_secondary="#f7f7f5", background_fill_secondary_dark="#202020",
    block_background_fill="#ffffff", block_background_fill_dark="#191919",
    block_border_color="#e9e9e7", block_border_color_dark="#2f2f2f", block_border_width="1px",
    block_shadow="none", block_shadow_dark="none",
    block_label_background_fill="transparent", block_label_background_fill_dark="transparent",
    block_label_text_color="#787774", block_label_text_color_dark="#9b9a97",
    block_label_text_weight="500", block_title_text_weight="500",
    block_title_text_color="#5f5e5b", block_title_text_color_dark="#b4b3b0",
    input_background_fill="#fbfbfa", input_background_fill_dark="#202020",
    input_border_color="#e3e2e0", input_border_color_dark="#373737",
    input_border_color_focus="#2383e2", input_border_color_focus_dark="#2383e2",
    input_shadow="none", input_shadow_focus="0 0 0 3px rgb(35 131 226 / 0.18)",
    border_color_primary="#e9e9e7", border_color_primary_dark="#2f2f2f",
    button_primary_background_fill="#2383e2", button_primary_background_fill_dark="#2383e2",
    button_primary_background_fill_hover="#0f75d6", button_primary_background_fill_hover_dark="#3a93ea",
    button_primary_text_color="#ffffff", button_primary_text_color_dark="#ffffff",
    button_primary_border_color="#2383e2", button_primary_border_color_dark="#2383e2",
    button_secondary_background_fill="#ffffff", button_secondary_background_fill_dark="#252525",
    button_secondary_background_fill_hover="#f1f1ef", button_secondary_background_fill_hover_dark="#2f2f2f",
    button_secondary_text_color="#37352f", button_secondary_text_color_dark="#e3e2e0",
    button_secondary_border_color="#dfdfde", button_secondary_border_color_dark="#373737",
    checkbox_background_color_selected="#2383e2", checkbox_background_color_selected_dark="#2383e2",
    checkbox_border_color_selected="#2383e2", checkbox_border_color_selected_dark="#2383e2",
    slider_color="#2383e2", slider_color_dark="#2383e2",
    color_accent_soft="#f1f1ef", color_accent_soft_dark="#2f2f2f",
    input_border_width="1px", button_border_width="1px", input_radius="6px",
    button_large_radius="6px", button_small_radius="6px",
)

CSS = """
.gradio-container { max-width: 100% !important; padding: 0 !important; margin: 0 !important; }
.gradio-container > .main, .gradio-container .main.fillable { max-width: 100% !important; margin: 0 !important; padding: 0 !important; }
.gradio-container .wrap, .gradio-container .contain { padding: 0 !important; gap: 0 !important; }
#cf-shell .html-container { padding: 0 !important; }
footer, .reset-button { display: none !important; }

/* ---- shell: sidebar + page */
#cf-shell { gap: 0 !important; flex-wrap: nowrap !important; align-items: stretch !important; min-height: 100vh; }
#cf-side { flex: 0 0 244px !important; min-width: 244px !important; max-width: 244px; background: #f7f7f5;
  border-right: 1px solid #e9e9e7; padding: 14px 8px 12px !important; gap: 2px !important;
  position: sticky; top: 0; height: 100vh; align-self: flex-start; overflow-y: auto; }
.dark #cf-side { background: #202020; border-color: #2f2f2f; }
#cf-brand { padding: 4px 8px 12px !important; border: none !important; background: transparent !important; }
#cf-brand .name { font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
#cf-brand .mark { width: 22px; height: 22px; border-radius: 5px; background: #37352f; color: #fff;
  display: inline-flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; }
.dark #cf-brand .mark { background: #e3e2e0; color: #191919; }
#cf-brand .sub { font-size: 12px; color: #91918e; margin: 2px 0 0 30px; }
button.cf-nav { justify-content: flex-start !important; text-align: left !important; background: transparent !important;
  border: none !important; box-shadow: none !important; color: #5f5e5b !important; font-weight: 500 !important;
  font-size: 14px !important; padding: 6px 10px !important; min-height: 32px !important; height: 32px;
  border-radius: 6px !important; width: 100%; gap: 8px; flex: none !important; }
button.cf-nav:hover { background: rgb(55 53 47 / 0.06) !important; }
button.cf-nav.active { background: rgb(55 53 47 / 0.09) !important; color: #37352f !important; font-weight: 600 !important; }
.dark button.cf-nav { color: #9b9a97 !important; }
.dark button.cf-nav:hover { background: rgb(255 255 255 / 0.055) !important; }
.dark button.cf-nav.active { background: rgb(255 255 255 / 0.09) !important; color: #e3e2e0 !important; }
#cf-side-gap { flex: 1 1 auto !important; min-height: 12px; border: none !important; background: transparent !important; padding: 0 !important; }
#cf-reset-note { padding: 0 10px !important; border: none !important; background: transparent !important; min-height: 0 !important; }
#cf-reset-note p { font-size: 12px !important; color: #91918e; margin: 4px 0 0; }
#cf-main { flex: 1 1 auto !important; min-width: 0 !important; padding: 0 !important; }
#cf-pages { max-width: 920px; margin: 0 auto !important; padding: 34px 44px 90px !important; border: none !important;
  background: transparent !important; gap: 0 !important; }
#cf-pages > .tab-wrapper, #cf-pages > .tab-nav, #cf-pages > div > .tab-container { display: none !important; }
#cf-pages .tabitem { padding: 0 !important; border: none !important; background: transparent !important; }
#cf-pages .tabitem > .column, #cf-pages .tabitem > div > .column { gap: 14px !important; }

/* ---- page head */
.cf-head { padding: 0 !important; border: none !important; background: transparent !important; margin-bottom: 6px; }
.cf-head .emoji { font-size: 40px; line-height: 1.1; }
.cf-head h1 { font-size: 32px; font-weight: 700; letter-spacing: -0.01em; margin: 8px 0 4px; color: inherit; }
.cf-head p { color: #787774; font-size: 15px; margin: 0; }

/* ---- sections */
.cf-card { border: 1px solid #e9e9e7 !important; border-radius: 8px !important; background: var(--block-background-fill) !important;
  padding: 18px 20px !important; gap: 14px !important; }
.dark .cf-card { border-color: #2f2f2f !important; }
.cf-card .form { border: none !important; background: transparent !important; box-shadow: none !important;
  gap: 14px !important; overflow: visible !important; }
.cf-card .block.padded { padding: 0 !important; background: transparent !important; box-shadow: none !important; }
.cf-card .block { box-shadow: none !important; }
.cf-card .row { gap: 14px !important; }
.cf-title { font-size: 15px; font-weight: 600; margin: 0 0 2px; }
.cf-sub { font-size: 13px; color: #787774; margin: 0 0 4px; }
.cf-note p { font-size: 13px !important; color: #787774; }
.cf-note code { font-size: 12px; }
.cf-actions { gap: 10px !important; margin: 4px 0; justify-content: flex-start !important; flex-wrap: wrap !important; }
.cf-actions button { height: 38px; font-weight: 600; flex: 0 0 auto !important; min-width: 148px !important;
  width: auto !important; padding: 0 18px !important; }
#cf-go { min-width: 200px !important; height: 42px; font-size: 15px; }
.cf-card button.sm, .cf-card button.secondary { min-height: 32px; }
.cf-card .row > button.sm { flex: 0 0 auto !important; min-width: 160px !important; width: auto !important; }
#cf-log textarea, #cf-ytlog textarea, #cf-livelog textarea { font-family: var(--font-mono); font-size: 12.5px; line-height: 1.5; }
#cf-live-status { padding: 12px 14px !important; border-radius: 6px !important; background: #f7f7f5 !important; border: none !important; }
.dark #cf-live-status { background: #202020 !important; }
#cf-live-status p { margin: 0 0 2px; font-size: 14px; }

/* ---- desktop: the sidebar is pinned to the window and never scrolls away */
@media (min-width: 821px) {
  #cf-side { position: fixed !important; top: 0; left: 0; bottom: 0; height: 100vh !important; z-index: 20; }
  #cf-main { margin-left: 244px !important; }
}

/* ---- phone: the sidebar becomes a top bar you can swipe */
@media (max-width: 820px) {
  #cf-shell { flex-direction: column !important; }
  #cf-main { padding-top: 50px !important; }
  #cf-side { position: fixed !important; top: 0; left: 0; right: 0; z-index: 30; height: auto; flex: none !important; min-width: 0 !important;
    max-width: none; width: 100% !important; flex-direction: row !important; flex-wrap: nowrap !important;
    overflow-x: auto; overflow-y: hidden; border-right: none; border-bottom: 1px solid #e9e9e7;
    padding: 8px 10px !important; gap: 4px !important; align-items: center; scrollbar-width: none; }
  #cf-side::-webkit-scrollbar { display: none; }
  #cf-brand, #cf-side-gap, #cf-reset-note { display: none !important; }
  button.cf-nav { width: auto !important; flex: 0 0 auto !important; white-space: nowrap; padding: 6px 10px !important; }
  #cf-pages { padding: 18px 14px 70px !important; }
  .cf-head .emoji { font-size: 30px; }
  .cf-head h1 { font-size: 24px; margin-top: 4px; }
  .cf-card { padding: 14px !important; }
  #cf-preview { height: auto !important; max-height: 70vh; }
}
"""


def card(title=None, sub=None):
    col = gr.Column(elem_classes="cf-card")
    if title:
        with col:
            gr.HTML(f'<div class="cf-title">{title}</div>' + (f'<div class="cf-sub">{sub}</div>' if sub else ""))
    return col


def page_head(emoji, title, sub):
    gr.HTML(f'<div class="emoji">{emoji}</div><h1>{title}</h1><p>{sub}</p>', elem_classes="cf-head")


# ---------------------------------------------------------------- layout
def build():
    s = load_settings()
    logo_now = s["logo_path"] if s.get("logo_path") and os.path.exists(s["logo_path"]) else ""
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
                                         elem_classes=["cf-nav", "active"] if pid == "create" else ["cf-nav"])
                gr.HTML("", elem_id="cf-side-gap")
                reset_btn = gr.Button("↺  Reset all settings", elem_id="nav-reset", elem_classes=["cf-nav"])
                reset_note = gr.Markdown(elem_id="cf-reset-note")

            # ================================================= pages
            with gr.Column(elem_id="cf-main"):
                with gr.Tabs(elem_id="cf-pages", selected="create") as pages:
                    # ------------------------------------------------ Create
                    with gr.Tab("Create", id="create"):
                        page_head("🎬", "Create", "Turn a finished video into vertical shorts. "
                                  "Pick the video, press Make clips, and collect the results below.")
                        with card("Video", "A file on this PC or a YouTube link. Use From / To to clip only part of it."):
                            w["path_or_url"] = gr.Textbox(label="Full path on this PC, or a YouTube link",
                                                          placeholder=r"D:\Videos\stream.mp4")
                            w["upload"] = gr.File(label="Or drop a small video file here", type="filepath",
                                                  file_types=["video"], height=150)
                            with gr.Row():
                                w["range_start"] = gr.Textbox(label="From (h:mm:ss, blank = start)",
                                                              placeholder="2:56:29")
                                w["range_end"] = gr.Textbox(label="To (blank = end)", placeholder="")
                            with gr.Row():
                                w["content_hint"] = gr.Textbox(label="What is this video?", value=s["content_hint"])
                                w["creator"] = gr.Textbox(label="Creator name (blank = none)", value=s["creator"])
                        with card("Clips", "How many to make and how long they can be. Live uses the same lengths."):
                            with gr.Row():
                                w["n_clips"] = gr.Slider(1, 30, value=s["n_clips"], step=1, label="Clips")
                                w["min_len"] = gr.Slider(8, 60, value=s["min_len"], step=1, label="Min seconds")
                                w["max_len"] = gr.Slider(15, 180, value=s["max_len"], step=1, label="Max seconds")
                            with gr.Row():
                                w["top_n_on"] = gr.Checkbox(label="Also make a Top-N countdown", value=s["top_n_on"])
                                w["top_n"] = gr.Slider(3, 10, value=s["top_n"], step=1, label="N")
                        with gr.Row(elem_classes="cf-actions"):
                            go = gr.Button("Make clips", variant="primary", elem_id="cf-go")
                        with card("Progress"):
                            w["log"] = gr.Textbox(show_label=False, lines=10, max_lines=14, autoscroll=True,
                                                  elem_id="cf-log",
                                                  placeholder="The log appears here while clips are made.")
                        with card("Results", "The top clip plays here; every clip has a .txt with its title, caption and keywords."):
                            with gr.Row():
                                with gr.Column(scale=2, min_width=240):
                                    w["preview"] = gr.Video(label="Top clip", height=480, elem_id="cf-preview")
                                with gr.Column(scale=3, min_width=260):
                                    w["files"] = gr.Files(label="All clips")
                                    w["info"] = gr.Markdown(elem_classes="cf-note")

                    # ------------------------------------------------ Live
                    with gr.Tab("Live", id="live"):
                        page_head("🔴", "Live", "Watch a stream while it happens. Every few minutes the newest part "
                                  "is checked (speech + live chat) and viral moments become clips automatically.")
                        with card("Stream"):
                            w["live_url"] = gr.Textbox(label="Live stream link (YouTube, Twitch, Kick...)",
                                                       placeholder="https://www.youtube.com/live/...")
                            with gr.Row(elem_classes="cf-actions"):
                                live_go = gr.Button("Start watching", variant="primary", elem_id="cf-live-go")
                                live_stop_btn = gr.Button("Stop", variant="secondary", interactive=False,
                                                          elem_id="cf-live-stop")
                            live_status = gr.Markdown(live_status_text(LIVE.current()), elem_id="cf-live-status")
                        with card("Checks", "Clips use your Style, Campaign and clip-length settings."):
                            with gr.Row():
                                w["live_interval"] = gr.Slider(2, 15, value=s["live_interval"], step=1,
                                                               label="Check every (minutes)")
                                w["live_min_score"] = gr.Slider(50, 95, value=s["live_min_score"], step=1,
                                                                label="Minimum viral score",
                                                                info="Higher = fewer, stronger clips")
                                w["live_max_clips"] = gr.Slider(1, 5, value=s["live_max_clips"], step=1,
                                                                label="Most clips per check")
                            w["live_chat_on"] = gr.Checkbox(label="Read live chat (YouTube and Twitch)",
                                                            value=s["live_chat_on"])
                            w["live_upload"] = gr.Checkbox(label="Upload each clip to YouTube as private "
                                                                 "(connect on the Publish page)", value=s["live_upload"])
                            w["live_keep"] = gr.Checkbox(label="Keep the full recording (off = checked parts are "
                                                               "deleted, only a few minutes stay on disk)",
                                                         value=s["live_keep"])
                        with card("Activity"):
                            live_log = gr.Textbox(show_label=False, lines=12, max_lines=16, autoscroll=True,
                                                  elem_id="cf-livelog",
                                                  placeholder="Checks and clips are listed here while a stream is watched.")
                        with card("Clips from this stream"):
                            live_files = gr.Files(label="Clips")
                            live_info = gr.Markdown(elem_classes="cf-note")
                        live_seen = gr.State("")
                        live_timer = gr.Timer(2, active=False)

                    # ------------------------------------------------ Style
                    with gr.Tab("Style", id="style"):
                        page_head("🎨", "Style", "How every clip looks and sounds. Used by Create and Live.")
                        with card("Edit formats"):
                            w["formats"] = gr.CheckboxGroup(list(FORMATS), value=s["formats"],
                                                            label="Formats the AI may use (it picks the best per clip and keeps them varied)")
                        with card("Split-screen"):
                            w["split_on"] = gr.Checkbox(label="Content on top, gameplay underneath",
                                                        value=s["split_on"])
                            w["split_game"] = gr.Dropdown(games, value=split_game_now, label="Gameplay",
                                                          visible=s["split_on"])
                            w["split_msg"] = gr.Markdown(split_status() if s["split_on"] else "",
                                                         visible=s["split_on"], elem_classes="cf-note")
                        with card("Motion graphics"):
                            w["motion_on"] = gr.Checkbox(label="Animated hooks, cards and stickers (HyperFrames + GSAP)",
                                                         value=s["motion_on"])
                            w["motion_elements"] = gr.CheckboxGroup(
                                [(v, k) for k, v in MOTION_ELEMENTS.items()], value=s["motion_elements"],
                                label="Animate", visible=s["motion_on"])
                            w["motion_msg"] = gr.Markdown(motion_status() if s["motion_on"] else "",
                                                          visible=s["motion_on"], elem_classes="cf-note")
                        with card("Sound"):
                            with gr.Row():
                                w["sfx_on"] = gr.Checkbox(label="Sound effects", value=s["sfx_on"])
                                w["sfx_gain"] = gr.Slider(0.1, 1.2, value=s["sfx_gain"], step=0.05,
                                                          label="SFX volume")
                            with gr.Row():
                                w["meme_on"] = gr.Checkbox(label="Trending meme sounds", value=s["meme_on"])
                                w["meme_gain"] = gr.Slider(0.2, 1.5, value=s["meme_gain"], step=0.05,
                                                           label="Meme volume", visible=s["meme_on"])
                            w["meme_msg"] = gr.Markdown(meme_status() if s["meme_on"] else "",
                                                        visible=s["meme_on"], elem_classes="cf-note")
                        with card("Captions and layout"):
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
                                w["layout"] = gr.Radio(["blur", "crop"], value=s["layout"],
                                                       label="Layout when split-screen is off (blur = whole frame on blurred bg, crop = fill screen)")
                                w["zoom"] = gr.Slider(1.0, 1.8, value=s["zoom"], step=0.05,
                                                      label="Zoom (blur layout)")

                    # ------------------------------------------------ Campaign
                    with gr.Tab("Campaign", id="campaign"):
                        page_head("📣", "Campaign", "Sponsor logo and campaign rules. Leave both empty for your own clips.")
                        with card("Sponsor logo", "Shown under the captions on every clip, never on the content. "
                                  "A PNG with a transparent background looks best."):
                            with gr.Row():
                                w["logo_file"] = gr.File(label="Upload logo", file_types=["image"],
                                                         type="filepath", height=150)
                                w["logo_preview"] = gr.Image(value=logo_now or None, label="Current logo",
                                                             interactive=False, height=150)
                            w["logo_state"] = gr.State(logo_now)
                            with gr.Row():
                                w["logo_anim"] = gr.Radio(ANIMATIONS, value=s["logo_anim"],
                                                          label="Logo animation (float = hover, pulse = pop every 3 s)")
                                w["logo_size"] = gr.Slider(0.5, 1.6, value=s["logo_size"], step=0.05,
                                                           label="Logo size")
                            with gr.Row():
                                remove_btn = gr.Button("Remove logo", size="sm", variant="secondary")
                                w["logo_msg"] = gr.Markdown(elem_classes="cf-note")
                        with card("Campaign rules", "Only clips that pass every rule are made. Rules about what is on "
                                  "screen can't be checked automatically; they're listed in each clip's .txt."):
                            w["rules_file"] = gr.File(label="Rules file (.txt .md .pdf .docx)",
                                                      file_types=[".txt", ".md", ".pdf", ".docx"],
                                                      type="filepath", height=150)
                            w["rules_msg"] = gr.Markdown(elem_classes="cf-note")
                            w["rules_text"] = gr.Textbox(label="Campaign rules (paste or upload; blank = no campaign)",
                                                         value=s["rules_text"], lines=8, max_lines=20)

                    # ------------------------------------------------ Publish
                    with gr.Tab("Publish", id="publish"):
                        page_head("📤", "Publish", "Upload clips to YouTube as private, review them, and get a "
                                  "Telegram message at every step.")
                        with card("YouTube"):
                            yt_state = gr.Markdown(yt_status(), elem_classes="cf-note")
                            with gr.Row(elem_classes="cf-actions"):
                                yt_connect = gr.Button("Connect YouTube", variant="secondary")
                                yt_disconnect = gr.Button("Disconnect", variant="secondary")
                            w["yt_on"] = gr.Checkbox(label="Upload to YouTube when Create finishes",
                                                     value=s["yt_on"])
                            w["yt_mode"] = gr.Radio(
                                [("Private: I review and publish", "review"),
                                 ("Scheduled: goes public automatically", "scheduled")],
                                value=s["yt_mode"], label="After upload")
                            with gr.Row():
                                w["yt_interval"] = gr.Slider(1, 120, value=s["yt_interval"], step=1,
                                                             label="Minutes between videos (scheduled)")
                                w["yt_first_delay"] = gr.Slider(0, 240, value=s["yt_first_delay"], step=5,
                                                                label="First one goes public after (min)")
                            w["yt_category"] = gr.Dropdown(list(PUB.CATEGORIES.items()), value=s["yt_category"],
                                                           label="Category")
                            gr.Markdown("Every video is uploaded **private**, not made for kids, not "
                                        "age-restricted, with the clip's title, caption and keywords. "
                                        "Campaign clips and live clips are never scheduled: they wait for your review.",
                                        elem_classes="cf-note")
                        with card("Review queue", "Private videos wait here. Tick the ones you checked and publish them."):
                            q_sum, _, _ = queue_view()
                            yt_queue = gr.Markdown(q_sum, elem_classes="cf-note")
                            yt_review = gr.CheckboxGroup(choices=PUB.review_list(), value=[],
                                                         label="Waiting for review")
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
                        page_head("⚙️", "Settings", "Keys and models. Set once and forget.")
                        with card("AI"):
                            w["api_key"] = gr.Textbox(label="OpenRouter API key", value=s["api_key"],
                                                      type="password")
                            w["model"] = gr.Textbox(label="Model ('auto' = cheapest that works)",
                                                    value=s["model"])
                            w["allow_free"] = gr.Checkbox(label="Try free models first", value=s["allow_free"])
                        with card("Transcription"):
                            w["whisper_model"] = gr.Dropdown(STT_CHOICES, value=s["whisper_model"],
                                                             label="Engine",
                                                             info="Cloud costs about $0.03-0.22 per hour of audio; local is free but slow.")
                            with gr.Row():
                                w["fallback_model"] = gr.Dropdown(LOCAL_MODELS, value=s.get("fallback_model") or "small",
                                                                  label="If cloud fails, use local model")
                                w["language"] = gr.Textbox(label="Language (blank = auto, e.g. en, hi)",
                                                           value=s["language"])
                        with card("Phone access", "A private link you can open on your phone from anywhere. Login required."):
                            sh_state = gr.Markdown(share_status(), elem_classes="cf-note")
                            sh_on = gr.Checkbox(label="Allow access from my phone (share link with login)",
                                                value=s["share_on"])
                            with gr.Row():
                                sh_user = gr.Textbox(label="Username", value=s["share_user"] or "klipr")
                                sh_pw = gr.Textbox(label="New password (8+ characters; blank = keep)",
                                                   type="password")
                            with gr.Row():
                                sh_save = gr.Button("Save phone access", variant="secondary", size="sm")
                                sh_msg = gr.Markdown(elem_classes="cf-note")

        # ------------------------------------------------ events
        all_inputs = {w[k] for k in list(SAVED) + ["upload", "path_or_url", "range_start", "range_end",
                                                   "logo_state", "live_url"]}
        for pid, _, _ in PAGES:
            nav[pid].click(make_nav(pid), None, [pages] + [nav[p] for p, _, _ in PAGES], show_progress="hidden")
        go.click(make_run(w), all_inputs, [w["log"], w["files"], w["preview"], w["info"]]).then(
            queue_view, None, [yt_queue, yt_review, yt_publish])
        live_out = [live_status, live_log, live_files, live_info, live_seen, live_timer, live_go, live_stop_btn]
        live_go.click(make_live_start(w), all_inputs, live_out)
        live_stop_btn.click(live_stop, None, live_out)
        live_timer.tick(live_view, [live_seen], live_out, show_progress="hidden")
        ui.load(lambda: live_view(""), None, live_out, show_progress="hidden")
        w["logo_file"].upload(on_logo, [w["logo_file"]], [w["logo_preview"], w["logo_state"], w["logo_msg"]])
        remove_btn.click(on_remove_logo, None, [w["logo_preview"], w["logo_state"], w["logo_msg"]])
        w["rules_file"].upload(on_rules_file, [w["rules_file"]], [w["rules_text"], w["rules_msg"]])
        w["motion_on"].change(on_motion, [w["motion_on"]], [w["motion_elements"], w["motion_msg"]])
        w["meme_on"].change(on_meme, [w["meme_on"]], [w["meme_gain"], w["meme_msg"]])
        w["split_on"].change(on_split, [w["split_on"]], [w["split_game"], w["split_msg"]])
        sh_save.click(on_share, [sh_on, sh_user, sh_pw], [sh_msg, sh_state]).then(
            lambda: "", None, [sh_pw])
        yt_connect.click(on_connect, None, [yt_state])
        yt_disconnect.click(on_disconnect, None, [yt_state])
        tg_test.click(on_test_telegram, [w["telegram_token"], w["telegram_chat"]], [tg_msg])
        yt_refresh.click(queue_view, None, [yt_queue, yt_review, yt_publish])
        yt_upload.click(on_upload_queue, [w[k] for k in YT_KEYS], [yt_log, yt_queue, yt_review, yt_publish])
        yt_publish.click(on_publish, [yt_review], [yt_log, yt_queue, yt_review, yt_publish])
        nav["publish"].click(queue_view, None, [yt_queue, yt_review, yt_publish], show_progress="hidden")
        order = [k for k in w]
        reset_btn.click(make_reset(w, order), None, [w[k] for k in order] + [reset_note])
    ui.cf_widgets = w   # for tests
    return ui


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(ASSETS_DIR, exist_ok=True)
    os.makedirs(GP.GAMEPLAY_DIR, exist_ok=True)
    s = load_settings()
    share, why = RM.ready(s)
    if why:
        print("\n" + why + " Set a password in Settings > Phone access.\n")
    kw = {}
    if share:
        kw = {"share": True, "auth": RM.make_auth(s.get("share_user") or "klipr", s["share_hash"]),
              "auth_message": "ClipForge: sign in"}
    share_errors = []
    if share:
        # Gradio hides why a share link failed; record the real reason and retry once
        import time as _t
        import gradio.networking as _gnet
        _orig_tunnel = _gnet.setup_tunnel

        def _tunnel(*a, **k):
            for attempt in (1, 2):
                try:
                    return _orig_tunnel(*a, **k)
                except Exception as e:
                    share_errors.append(e)
                    if attempt == 1:
                        print("Share link attempt 1 failed, retrying...")
                        _t.sleep(3)
            raise share_errors[-1]
        _gnet.setup_tunnel = _tunnel
    ui = build().queue()
    ui.launch(inbrowser=True, allowed_paths=[OUTPUT_DIR, ASSETS_DIR], theme=THEME, css=CSS,
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
