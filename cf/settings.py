"""Saved app settings (settings.json next to app.py)."""
import json
import os

from .pipeline import DEFAULTS
from .tools import ROOT

SETTINGS = os.path.join(ROOT, "settings.json")


def load_settings():
    s = dict(DEFAULTS)
    if os.path.exists(SETTINGS):
        try:
            with open(SETTINGS, encoding="utf-8") as f:
                s.update(json.load(f))
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
    return s


def save_settings(s):
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)


def update_settings(**kv):
    """Change a few keys without touching the rest."""
    s = load_settings()
    s.update(kv)
    save_settings(s)
