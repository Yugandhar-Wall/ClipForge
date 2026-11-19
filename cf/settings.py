"""Saved app settings (settings.json next to app.py) and their defaults.

Only preferences are saved (keys, style, clip lengths, layout type...). What belongs to one
run - the video's description, creator, campaign rules, sponsor logo, From/To, facecam boxes -
is never saved, so every session starts clean (RUN_KEYS)."""
import json
import os

from .formats import FORMATS
from .motion import ELEMENTS as MOTION_ELEMENTS
from .tools import ROOT

SETTINGS = os.path.join(ROOT, "settings.json")

DEFAULTS = {
    "api_key": "", "model": "auto", "allow_free": False, "n_clips": 6,
    "min_len": 20, "max_len": 59, "whisper_model": "cloud:openai/whisper-large-v3",
    "fallback_model": "small", "language": "",
    "yt_on": False, "yt_mode": "review", "yt_interval": 10, "yt_first_delay": 10,
    "yt_category": "20", "telegram_token": "", "telegram_chat": "",
    "share_on": False, "share_user": "klipr", "share_hash": "",
    "layout": "blur", "split_on": False, "split_game": "any", "zoom": 1.0, "sfx_on": True, "sfx_gain": 0.55,
    "highlight": "auto", "words_per_line": 3, "content_hint": "", "facecam": None, "mode": "video",
    "formats": list(FORMATS), "top_n_on": True, "top_n": 5, "creator": "",
    "cta_on": True, "progress_on": True, "punch_on": True,
    "logo_path": "", "logo_anim": "float", "logo_size": 1.0, "rules_text": "",
    "motion_on": False, "motion_elements": list(MOTION_ELEMENTS),
    "meme_on": False, "meme_gain": 0.8, "range_start": "", "range_end": "",
    "live_interval": 5, "live_min_score": 75, "live_max_clips": 2, "live_upload": True,
    "live_keep": False, "live_chat_on": True,
    "keep_days": 6,
}
RUN_KEYS = ("content_hint", "creator", "rules_text", "logo_path", "range_start", "range_end", "facecam")
VERSION = 3


def load_settings():
    s = dict(DEFAULTS)
    if os.path.exists(SETTINGS):
        try:
            with open(SETTINGS, encoding="utf-8") as f:
                s.update({k: v for k, v in json.load(f).items() if k not in RUN_KEYS})
        except Exception:
            pass
    if not s.get("api_key"):
        s["api_key"] = os.environ.get("OPENROUTER_API_KEY", "")
    if int(s.get("settings_version") or 1) < 2:
        # v2: cloud transcription becomes the default; the old local model is kept as fallback
        if not str(s.get("whisper_model", "")).startswith("cloud:"):
            s["fallback_model"] = s.get("whisper_model") or "small"
            s["whisper_model"] = DEFAULTS["whisper_model"]
        s["settings_version"] = 2
    if int(s.get("settings_version") or 1) < 3:
        # v3: the old default zoom (1.25) cut 12% off both sides of the frame in the blur layout
        if float(s.get("zoom") or 1.0) == 1.25:
            s["zoom"] = 1.0
        s["settings_version"] = VERSION
    return s


def save_settings(s):
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in s.items() if k not in RUN_KEYS}, f, indent=2)


def update_settings(**kv):
    """Change a few keys without touching the rest."""
    s = load_settings()
    s.update(kv)
    save_settings(s)
