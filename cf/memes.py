"""Trending meme sounds from the user's own folder (ClipForge\\sounds).

- Any audio file dropped in sounds\\ is picked up automatically; its file name is its name.
- sounds\\sounds.txt says when each one fits (one line per file, editable). Files without a
  line still work: the AI knows most memes by name.
- Every sound is loudness-matched (they vary by 20+ dB as downloaded) and soft-limited, and
  its "hit" (the moment the punch lands, e.g. 0.25 s into fahhh, 1.1 s into the Among Us
  reveal) is measured, so the hit lands exactly on the beat the AI picked.
"""
import hashlib
import os
import re
import wave

import numpy as np

from .tools import CACHE_DIR, ROOT, ffmpeg_exe, read_json, run_cmd, write_json

SOUNDS_DIR = os.path.join(ROOT, "sounds")
CATALOG = os.path.join(SOUNDS_DIR, "sounds.txt")
OUT_DIR = os.path.join(CACHE_DIR, "memes")
EXTS = (".mp3", ".wav", ".m4a", ".ogg", ".opus", ".flac", ".aac", ".webm")
SR = 44100
TARGET_RMS_DB = -17.0   # loudness of the active part, before the user's volume slider
MAX_LEN = 6.0           # longer files are cut (a meme sound is a punchline, not a song)
VERSION = "2"


def _id(filename):
    return re.sub(r"[^a-z0-9]+", "_", os.path.splitext(filename)[0].lower()).strip("_")


def _descriptions():
    out = {}
    try:
        with open(CATALOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                name, desc = line.split(":", 1)
                out[_id(name.strip())] = desc.strip()
    except FileNotFoundError:
        pass
    return out


def _decode(path):
    p = run_cmd([ffmpeg_exe(), "-v", "error", "-i", path, "-t", str(MAX_LEN), "-f", "f32le",
                 "-ac", "2", "-ar", str(SR), "-"], binary=True)
    if p.returncode != 0:
        raise ValueError(p.stderr.decode("utf-8", "replace").strip()[-200:] or "ffmpeg couldn't read it")
    raw = p.stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).copy()


def _prepare(path, dst):
    """Loudness-match, soft-limit, trim trailing silence. Returns (hit, length)."""
    a = _decode(path)
    if len(a) < SR * 0.05:
        raise ValueError("too short or unreadable")
    mono = np.abs(a).max(axis=1)
    hop = int(SR * 0.01)
    n = len(mono) // hop
    env = np.sqrt((a[: n * hop] ** 2).mean(axis=1).reshape(n, hop).mean(axis=1) + 1e-12)
    peak_env = env.max()
    # active part = frames within 30 dB of the loudest: its RMS is matched to the target
    active = env[env > peak_env * 10 ** (-30 / 20)]
    rms_db = 20 * np.log10(np.sqrt((active ** 2).mean()) + 1e-12)
    a *= 10 ** ((TARGET_RMS_DB - rms_db) / 20)
    a = 0.89 * np.tanh(a / 0.89)                          # soft limiter, ceiling -1 dBFS
    # hit = first moment the envelope reaches half of its maximum
    hit = float(np.argmax(env >= peak_env * 0.5) * hop / SR)
    # trim trailing silence
    loud = np.where(mono > mono.max() * 10 ** (-45 / 20))[0]
    end = min(len(a), (loud[-1] + int(0.05 * SR)) if len(loud) else len(a))
    a = a[:end]
    fade = min(len(a), int(0.02 * SR))
    a[-fade:] *= np.linspace(1, 0, fade)[:, None]
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with wave.open(dst, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())
    return round(hit, 3), round(len(a) / SR, 3)


def library(log=print):
    """{id: {"file", "wav", "hit", "len", "desc"}} for every usable file in sounds\\."""
    if not os.path.isdir(SOUNDS_DIR):
        return {}
    desc = _descriptions()
    lib = {}
    for name in sorted(os.listdir(SOUNDS_DIR)):
        if not name.lower().endswith(EXTS):
            continue
        src = os.path.join(SOUNDS_DIR, name)
        st = os.stat(src)
        key = hashlib.sha1(f"{VERSION}|{name}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:12]
        wav = os.path.join(OUT_DIR, f"{_id(name)}_{key}.wav")
        meta = wav + ".json"
        try:
            if os.path.exists(meta) and os.path.exists(wav):
                m = read_json(meta)
            else:
                hit, length = _prepare(src, wav)
                m = {"hit": hit, "len": length}
                write_json(meta, m)
        except Exception as e:
            log(f"  meme sound '{name}' skipped ({e})")
            continue
        lib[_id(name)] = {"file": name, "wav": wav, "hit": m["hit"], "len": m["len"],
                          "desc": desc.get(_id(name), "")}
    return lib


def prompt_list(lib):
    lines = []
    for k, v in lib.items():
        d = v["desc"] or "(use the meme this name refers to)"
        lines.append(f'    {k} ({v["len"]:.1f}s): {d}')
    return "\n".join(lines)


def resolve(name, lib):
    """'meme:<id>' -> (wav, hit, length) or None."""
    if not name.startswith("meme:"):
        return None
    v = lib.get(name[5:])
    return (v["wav"], v["hit"], v["len"]) if v else None


def clip_cues(clip, s, e, lib, max_n=2):
    """The AI's meme picks for a clip, as (source_time_of_hit, 'meme:<id>')."""
    out = []
    for m in clip.get("memes") or []:
        try:
            t = float(m.get("t"))
        except (TypeError, ValueError, AttributeError):
            continue
        k = _id(str(m.get("name", "")))
        if k not in lib or not (s + 0.8 <= t <= e - 0.3):
            continue
        if any(abs(t - x[0]) < 5.0 for x in out):
            continue
        out.append((t, "meme:" + k))
        if len(out) >= max_n:
            break
    return out
