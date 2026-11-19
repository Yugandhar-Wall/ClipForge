"""Cloud speech-to-text through OpenRouter's /audio/transcriptions endpoint.

Why it is built this way:
- Upstream providers time out after 60 s per request, so the audio is cut into ~5 min pieces,
  and each cut is placed in the quietest moment nearby so no word is split in half.
- Pieces are sent as 48 kbps MP3 (Whisper doesn't need more for speech): about 40 MB of upload
  for a 2-hour stream instead of ~230 MB of WAV.
- Silent pieces are not sent at all (cloud Whisper invents text in long silences), and words
  that land on near-silent audio are dropped for the same reason.
- Word timestamps are required (captions and cuts depend on them). If the provider returns no
  words, this raises CloudSTTError and the caller falls back to local Whisper.
- Every finished piece is cached, so a re-run after a network error only sends what's missing.
"""
import base64
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests

from .audio import SPEECH_SR, STT_PROMPT, read_speech
from .llm import API, headers
from .tools import CACHE_DIR, check_stop, ffmpeg_exe, in_scope, read_json, run_cmd, stoppable, write_json

URL = f"{API}/audio/transcriptions"
MODELS = ["openai/whisper-large-v3", "openai/whisper-large-v3-turbo"]
CHUNK = 300.0          # target seconds per request
FRAME = 0.1            # RMS frame for silence detection
SILENT_DB = -50.0      # piece/word quieter than this = silence
WORKERS = 4


_LANGS = {"english": "en", "hindi": "hi", "spanish": "es", "portuguese": "pt", "french": "fr",
          "german": "de", "italian": "it", "russian": "ru", "japanese": "ja", "korean": "ko",
          "arabic": "ar", "turkish": "tr", "indonesian": "id", "urdu": "ur", "dutch": "nl",
          "polish": "pl", "chinese": "zh", "filipino": "tl", "tagalog": "tl"}


class CloudSTTError(RuntimeError):
    pass


def _rms_db(audio, sr=SPEECH_SR):
    n = int(sr * FRAME)
    k = len(audio) // n
    if k == 0:
        return np.array([-120.0])
    fr = audio[: k * n].reshape(k, n)
    return 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-9)


def plan_chunks(db, total):
    """[(start, end)] cut at the quietest 0.6 s near every CHUNK seconds."""
    cuts, pos = [0.0], 0.0
    win = max(1, int(0.6 / FRAME))
    smooth = np.convolve(db, np.ones(win) / win, mode="same")
    while total - pos > CHUNK + 30:
        lo, hi = int((pos + CHUNK - 45) / FRAME), int((pos + CHUNK + 15) / FRAME)
        i = lo + int(np.argmin(smooth[lo:hi]))
        pos = i * FRAME
        cuts.append(pos)
    cuts.append(total)
    return list(zip(cuts[:-1], cuts[1:]))


def _encode(wav, s, e):
    """Piece of the wav as MP3 bytes (FLAC if this ffmpeg has no MP3 encoder)."""
    base = [ffmpeg_exe(), "-v", "error", "-ss", f"{s:.2f}", "-t", f"{e - s:.2f}", "-i", wav, "-ac", "1"]
    for args, fmt in ((["-c:a", "libmp3lame", "-b:a", "48k", "-f", "mp3"], "mp3"),
                      (["-c:a", "flac", "-f", "flac"], "flac")):
        p = run_cmd(base + args + ["-"], binary=True)
        if p.returncode == 0 and p.stdout:
            return p.stdout, fmt
    raise CloudSTTError("ffmpeg couldn't encode audio for upload")


def _request(key, model, data, fmt, language):
    body = {"model": model, "input_audio": {"data": base64.b64encode(data).decode(), "format": fmt},
            "response_format": "verbose_json", "timestamp_granularities": ["word", "segment"],
            "temperature": 0, "provider": {"options": {"groq": {"prompt": STT_PROMPT}}}}
    if language:
        body["language"] = language
    last = None
    for attempt in range(4):
        try:
            r = stoppable(requests.post, URL, json=body, headers=headers(key), timeout=180)
        except requests.RequestException as e:
            last = f"network error: {e}"
        else:
            if r.status_code == 200:
                return r.json()
            msg = r.text[:300]
            if r.status_code in (400, 401, 402, 403, 404):
                raise CloudSTTError(f"HTTP {r.status_code}: {msg}")
            last = f"HTTP {r.status_code}: {msg}"
        time.sleep(2 * (attempt + 1))
    raise CloudSTTError(last or "request failed")


_TOKEN = re.compile(r"[^\w']+")


def _norm(w):
    return _TOKEN.sub("", w.lower())


def _punctuate(words, text):
    """Cloud word lists usually lose punctuation ("what" instead of "What?!"); copy it back from
    the segment text so captions and the reaction detector see it."""
    toks = text.split()
    j = 0
    for w in words:
        n = _norm(w["w"])
        for k in range(j, min(j + 4, len(toks))):
            if _norm(toks[k]) == n and n:
                w["w"] = toks[k]
                j = k + 1
                break


def _to_segments(resp, offset, db):
    words = []
    for x in resp.get("words") or []:
        t = str(x.get("word", "")).strip()
        try:
            s, e = float(x["start"]) + offset, float(x["end"]) + offset
        except (KeyError, TypeError, ValueError):
            continue
        if t and e >= s:
            words.append({"w": t, "s": round(s, 2), "e": round(max(e, s + 0.05), 2)})
    segs_in = []
    for x in resp.get("segments") or []:
        try:
            segs_in.append({"s": float(x["start"]) + offset, "e": float(x["end"]) + offset,
                            "text": str(x.get("text", "")).strip()})
        except (KeyError, TypeError, ValueError):
            continue
    text = str(resp.get("text") or "").strip()
    if not words:
        if text and len(text.split()) > 2:
            raise CloudSTTError("the provider returned text but no word timestamps")
        return []

    # drop words sitting on near-silent audio (invented text)
    def loud(w):
        a, b = int(w["s"] / FRAME), max(int(w["s"] / FRAME) + 1, int(w["e"] / FRAME))
        seg = db[a:b]
        return len(seg) == 0 or float(seg.max()) > SILENT_DB
    words = [w for w in words if loud(w)]

    out = []
    if segs_in:
        for sg in segs_in:
            ws = [w for w in words if sg["s"] - 0.05 <= (w["s"] + w["e"]) / 2 <= sg["e"] + 0.05]
            if ws:
                _punctuate(ws, sg["text"])
                out.append({"s": ws[0]["s"], "e": ws[-1]["e"], "text": sg["text"], "words": ws})
        placed = {id(w) for sg in out for w in sg["words"]}
        rest = [w for w in words if id(w) not in placed]
    else:
        rest = words
    # words without a segment: group them by pauses
    cur = []
    for w in rest:
        if cur and (w["s"] - cur[-1]["e"] > 0.8 or w["e"] - cur[0]["s"] > 12):
            out.append({"s": cur[0]["s"], "e": cur[-1]["e"], "text": " ".join(x["w"] for x in cur), "words": cur})
            cur = []
        cur.append(w)
    if cur:
        out.append({"s": cur[0]["s"], "e": cur[-1]["e"], "text": " ".join(x["w"] for x in cur), "words": cur})
    out.sort(key=lambda sg: sg["s"])
    return out


def transcribe_cloud(wav, api_key, model=MODELS[0], language=None, log=print, cache_key=None):
    """Returns {"language", "segments": [{s, e, text, words: [{w, s, e}]}], "cost", "engine"}."""
    if not api_key:
        raise CloudSTTError("no OpenRouter API key")
    tag = model.split("/")[-1]
    final = os.path.join(CACHE_DIR, f"{cache_key}_cloud-{tag}_v2.json") if cache_key else None
    if final and os.path.exists(final):
        log("Transcript found in cache, skipping transcription.")
        return read_json(final)
    os.makedirs(CACHE_DIR, exist_ok=True)

    audio = read_speech(wav)
    total = len(audio) / SPEECH_SR
    db = _rms_db(audio)
    chunks = plan_chunks(db, total)
    todo = []
    for i, (s, e) in enumerate(chunks):
        part = db[int(s / FRAME):int(e / FRAME)]
        if len(part) == 0 or np.percentile(part, 95) < SILENT_DB:
            continue  # silent piece: nothing to transcribe
        todo.append((i, s, e))
    log(f"Cloud transcription ({model}): {total / 60:.1f} min in {len(todo)} pieces"
        f"{f' ({len(chunks) - len(todo)} silent skipped)' if len(chunks) > len(todo) else ''}...")

    def one(job):
        check_stop()
        i, s, e = job
        pc = os.path.join(CACHE_DIR, f"{cache_key or 'nokey'}_cloud-{tag}_{i:03d}_{int(s)}.json")
        if os.path.exists(pc):
            return i, read_json(pc), True
        data, fmt = _encode(wav, s, e)
        resp = _request(api_key, model, data, fmt, language)
        if not resp.get("words") and len(str(resp.get("text") or "").split()) > 2:
            raise CloudSTTError("the provider returned text but no word timestamps")
        if cache_key:
            write_json(pc, resp)
        return i, resp, False

    results, done, cost, t0 = {}, 0, 0.0, time.time()
    with ThreadPoolExecutor(WORKERS) as ex:
        for i, resp, cached in ex.map(in_scope(one), todo):
            results[i] = resp
            done += 1
            if not cached:
                cost += float((resp.get("usage") or {}).get("cost") or 0)
            if done % 4 == 0 or done == len(todo):
                log(f"  {done}/{len(todo)} pieces done ({time.time() - t0:.0f}s)")

    out = {"language": language or "", "segments": [], "cost": round(cost, 5),
           "engine": f"cloud:{model}"}
    for i, s, e in todo:
        resp = results[i]
        out["segments"] += _to_segments(resp, s, db)
        if not out["language"] and resp.get("language"):
            out["language"] = str(resp["language"])
    if not out["segments"] and todo:
        raise CloudSTTError("no speech came back from the provider")
    lang = out["language"].lower()
    out["language"] = _LANGS.get(lang, lang if len(lang) <= 3 else "")
    nwords = sum(len(sg["words"]) for sg in out["segments"])
    log(f"  {nwords} words, language '{out['language'] or '?'}', cost ${cost:.4f}")
    if final:
        write_json(final, out)
    return out
