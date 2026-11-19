"""Long video in -> finished vertical shorts + titles/captions out."""
import json
import math
import os
import re
import time

from .captions import AUTO_CYCLE
from .formats import FORMATS, build, build_top_n
from .hype import loud_moments
from . import gameplay as GP
from . import memes as MM
from . import motion as M
from . import rules as R
from .brand import fitted_logo, has_logo
from .llm import chat_json
from .render import H, layout_geometry, render_timeline
from .signals import annotate, find_signals, hot_spots
from .tools import CACHE_DIR, OUTPUT_DIR, ROOT, fmt_ts, hls_input_opts, parse_ts, probe, slug
from .transcribe import extract_audio, file_key, transcribe_auto

SFX_NAMES = {
    "impact": "hard hit / crash / kill / big reveal",
    "deep_boom": "dramatic or 'oh no' moment, huge fail",
    "bass_drop": "hype moment starts, epic play",
    "whoosh": "fast movement, transition, car zooming past",
    "swoosh_short": "quick cut or quick action",
    "pop": "funny line, punchline, small surprise",
    "ding": "success, money, 'correct', achievement",
    "glitch": "something weird/broken/bug happens",
    "riser": "tension build-up; set t = the payoff moment, it plays during the 2s before it",
    "rewind": "replay / 'wait what?' moment",
    "record_scratch": "sudden awkward stop, 'wait... what?', things going very wrong",
}

DEFAULTS = {
    "api_key": "", "model": "auto", "allow_free": False, "n_clips": 6,
    "min_len": 20, "max_len": 59, "whisper_model": "cloud:openai/whisper-large-v3",
    "fallback_model": "small", "language": "",
    "yt_on": False, "yt_mode": "review", "yt_interval": 10, "yt_first_delay": 10,
    "yt_category": "20", "telegram_token": "", "telegram_chat": "",
    "share_on": False, "share_user": "klipr", "share_hash": "",
    "layout": "blur", "split_on": False, "split_game": "any", "zoom": 1.25, "sfx_on": True, "sfx_gain": 0.55,
    "highlight": "auto", "words_per_line": 3, "content_hint": "GTA 6 gameplay",
    "formats": list(FORMATS), "top_n_on": True, "top_n": 5, "creator": "",
    "cta_on": True, "progress_on": True, "punch_on": True,
    "logo_path": "", "logo_anim": "float", "logo_size": 1.0, "rules_text": "",
    "motion_on": False, "motion_elements": list(M.ELEMENTS),
    "meme_on": False, "meme_gain": 0.8, "range_start": "", "range_end": "",
    "live_interval": 5, "live_min_score": 70, "live_max_clips": 2, "live_upload": True,
    "live_keep": False, "live_chat_on": True,
}

SYSTEM = """You are a top short-form editor who turns long gaming streams (GTA, playthroughs, streamer content) into Shorts/TikToks/Reels that people watch to the end and share.

Judge moments by UNDERSTANDING what is happening in the transcript, not by volume. Great clips usually have:
- a clear mini-story: setup -> escalation -> payoff/reaction, understandable with zero context
- a genuine reaction: shock ("oh no", "no way", "what the..."), swearing, panic, laughter, rage, disbelief, hype ("let's go")
- a dramatic beat: a sudden silence after something happens, a stunned pause, a long "...", then the reaction
- funny banter, roasts, quotable lines, chat interactions, fails, glitches, near-misses, chases, crashes, wins
Skip: menus, loading, ads/sponsor reads, "subscribe" talk, long explanations, dead air with nothing happening.

The transcript is annotated by an automatic detector:
  <<exclaim:...>> exclamation/swearing   <<laugh>> laughter   <<fast>> rapid excited talking
  <<loud>> audio spike (scream, explosion, crash)   ...(N s silence)... a sudden pause mid-conversation
These are hints, not the answer: a quiet clever line can beat a scream, and a scream with no context is weak.
Start each clip 1-4 s before the setup line (never mid-sentence), end 0.5-2 s after the reaction lands.
Reply with ONLY a JSON object, no prose."""

USER_TMPL = """Video: {hint}
Window: {w0:.0f}s to {w1:.0f}s of the video.
{task} Each clip must be {min_len}-{max_len} seconds long, clips must not overlap, and start/end must be real times from the transcript (seconds, decimals allowed).

For each clip write:
- "start", "end": seconds
- "title": viral YouTube Shorts title, max 70 chars, curiosity-driven but TRUE to what happens (no fake claims), may use 1 emoji
- "hook": on-screen text for the first 3 seconds, max 7 words, makes people stay (tease the payoff, don't spoil it)
- "caption": TikTok/Instagram caption, 1-2 short sentences + 4-6 relevant hashtags
- "score": 1-100 how likely it goes viral (be strict; average moments are 40-60)
- "reason": one short sentence: what happens and why people will watch it
- "payoff": the single peak second (absolute) - the crash/kill/scream/punchline everything builds to
- "format": the edit style that fits this moment best, one of:
{formats}
- "tease": max 6 words shown with the payoff in cold_open / freeze_open / wait_for_it (a curiosity gap, e.g. "HOW DID THIS HAPPEN?!", "HE HAD NO IDEA..."); never spoil the ending
- "cta": a short question shown at the end to get comments, max 8 words (e.g. "WOULD YOU HAVE SURVIVED THAT?")
- "label": 2-5 words naming the moment, used in "Top N" countdowns (e.g. "THE OCEAN ESCAPE")
- "keywords": 45-50 keywords for THIS clip (lowercase, 1-3 words each). Draw them only from what is said or shown in this clip: people, game or place names, the topic, the action, the reaction words. No hashtags, no duplicates, nothing the clip doesn't contain.
- "sfx": 1-4 sound effects as {{"t": <absolute seconds>, "name": <name>}} placed exactly on the beat they punctuate (a reaction, a crash, a punchline, the start of a silence)
Sound effect names: {sfx}
{creator_note}{rules_note}{motion_note}
Use a mix of formats across the clips - not all the same.
Language: write title, hook, tease, cta, label and caption in the language the audience speaks (the transcript's language). For Hindi/Urdu transcripts use Hinglish (Hindi written in English letters, e.g. "Bhai ne kya kar diya 😭") - never Devanagari for on-screen text.

JSON format:
{{"clips":[{{"start":12.3,"end":45.6,"payoff":38.2,"format":"cold_open","title":"...","hook":"...","tease":"...","cta":"...","label":"...","caption":"...","score":87,"reason":"...","keywords":["..."],"sfx":[{{"t":20.1,"name":"impact"}}]}}]}}

HOT SPOTS found by the detector (start second, strength, kinds) - check these first, but read the transcript around them:
{hot}

TRANSCRIPT:
{transcript}"""


def _js_runtimes():
    """Node.js for yt-dlp's YouTube challenge solver (deno stays the fallback)."""
    import shutil
    paths = [shutil.which("node")]
    try:
        with open(os.path.join(ROOT, "motion", "ready.json"), encoding="utf-8-sig") as f:
            paths.append(json.load(f).get("node"))
    except Exception:
        pass
    for p in paths:
        if p and os.path.exists(p):
            return {"node": {"path": p}, "deno": {}}
    return {"deno": {}}


def _download(url, log, start=None, end=None, attempts=4):
    """Download a video with yt-dlp. With start/end (seconds) only that part is fetched:
    it is cut frame-accurately (re-encoded at the cut), so the file's 0:00 is exactly `start`."""
    import yt_dlp
    from yt_dlp.utils import download_range_func
    from .tools import ffmpeg_exe
    dl_dir = os.path.join(CACHE_DIR, "downloads")
    os.makedirs(dl_dir, exist_ok=True)
    part = start is not None or end is not None
    tag = f" part {int(start or 0)}-{int(end) if end is not None else 'end'}" if part else ""
    opts = {"outtmpl": os.path.join(dl_dir, "%(title).70s [%(id)s]" + tag + ".%(ext)s"),
            "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
            "merge_output_format": "mp4", "ffmpeg_location": ffmpeg_exe(),
            "quiet": os.environ.get("CF_DL_DEBUG") != "1", "noprogress": True,
            # slow / flaky connections: wait longer, retry a lot, resume partial files
            "socket_timeout": 60, "retries": 20, "fragment_retries": 20,
            "extractor_retries": 5, "file_access_retries": 5, "continuedl": True,
            "retry_sleep_functions": {"http": lambda n: min(30, 2 ** n),
                                      "fragment": lambda n: min(30, 2 ** n)},
            # YouTube now needs a JavaScript runtime to unlock all formats; use Node.js if present
            "js_runtimes": _js_runtimes()}
    if part:
        opts.update({
            "download_ranges": download_range_func(None, [(start or 0, end if end is not None
                                                           else float("inf"))]),
            "force_keyframes_at_cuts": True,
            "external_downloader_args": {
                "ffmpeg_i": ["-reconnect", "1", "-reconnect_streamed", "1",
                             "-reconnect_on_network_error", "1", "-reconnect_delay_max", "30",
                             "-rw_timeout", "60000000"] + hls_input_opts(),
                "ffmpeg_o": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]}})
        log(f"Downloading only {fmt_ts(start or 0)} to "
            f"{fmt_ts(end) if end is not None else 'the end'} of {url} "
            "(cut exactly at your times, so this re-encodes - long windows take a while)...")
    else:
        log(f"Downloading {url} ...")
    last = None
    for n in range(1, attempts + 1):
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(url, download=True)
                path = y.prepare_filename(info)
            if not os.path.exists(path):
                path = os.path.splitext(path)[0] + ".mp4"
            if not os.path.exists(path):
                raise RuntimeError("the download finished but the file is missing")
            log(f"Downloaded: {os.path.basename(path)}")
            return path
        except Exception as e:
            last = e
            msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).strip().splitlines()[-1][:200]
            if "Private video" in msg or "Video unavailable" in msg or "Unsupported URL" in msg:
                break
            if n < attempts:
                log(f"  Download problem ({msg}). Retrying in {10 * n}s ({n}/{attempts - 1})...")
                time.sleep(10 * n)
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(last)).strip()
    raise RuntimeError(f"Couldn't download the video after {attempts} tries: {msg}\n"
                       "Check the link and your internet connection and try again, or download the "
                       "video another way and paste its file path instead.") from last


def est_tokens(text):
    """Latin text ~3.5 chars/token; Devanagari and other scripts ~1 token per char."""
    ascii_n = sum(1 for ch in text if ord(ch) < 128)
    return int(ascii_n / 3.5 + (len(text) - ascii_n) * 1.0) + 1


def _windows(segments, total, budget_tokens=45000, start=0.0):
    """Split the transcript into windows the model can read in one go."""
    est = sum(est_tokens(s["text"]) + 4 for s in segments)
    n = max(1, math.ceil(est / budget_tokens))
    span = (total - start) / n
    return [(start + i * span, min(total, start + (i + 1) * span)) for i in range(n)]


def _source_range(cfg, total, log):
    """The part of the source the user allowed (From/To). Returns (start, end) in seconds."""
    a, b = parse_ts(cfg.get("range_start")), parse_ts(cfg.get("range_end"))
    if a is None and b is None:
        return 0.0, total
    a = a or 0.0
    b = total if b is None else min(b, total)
    if a >= total:
        raise ValueError(f"'From' ({fmt_ts(a)}) is after the end of the video ({fmt_ts(total)}).")
    if b <= a:
        raise ValueError(f"'To' ({fmt_ts(b)}) must be later than 'From' ({fmt_ts(a)}).")
    if b - a < 8:
        raise ValueError("The From/To window is under 8 seconds - too short to clip.")
    log(f"Using only {fmt_ts(a)} to {fmt_ts(b)} of the video ({fmt_ts(b - a)}). "
        "No clip will start before or end after this window.")
    return a, b


def _snap(clip, words, total, min_len, max_len, lo=0.0):
    s, e = float(clip["start"]), float(clip["end"])
    s, e = max(lo, s), min(total, e)
    if words:
        # start on a word boundary at/just before s, end on the word that finishes after e
        before = [w for w in words if w["s"] <= s + 0.25]
        if before and s - before[-1]["s"] < 1.5:
            s = before[-1]["s"]
        after = [w for w in words if w["e"] >= e - 0.25]
        if after and after[0]["e"] - e < 2.0:
            e = after[0]["e"]
    s = max(lo, s - 0.15)
    e = min(total, e + 0.4)
    if e - s < min_len:
        e = min(total, s + min_len)
        s = max(lo, e - min_len)
    if e - s > max_len:
        e = s + max_len
    return s, e


def _score(c):
    try:
        return float(c.get("score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _overlap(a, b):
    return min(a[1], b[1]) - max(a[0], b[0]) > 2.0


def _cues(clip, s, e, loud):
    """Sound-effect cues in SOURCE time: the model's picks + impacts on big unflagged spikes."""
    out = []
    for cue in (clip.get("sfx") or [])[:4]:
        try:
            t = float(cue.get("t"))
        except (TypeError, ValueError, AttributeError):
            continue
        name = str(cue.get("name", "")).strip().lower()
        if name not in SFX_NAMES:
            continue
        if name == "riser":  # the model gives the payoff; the 2 s riser must END there
            t -= 2.0
        if s + 0.4 <= t < e - 0.4:
            out.append((t, name))
    added = 0
    for m in sorted((m for m in loud if s + 0.6 <= m["t"] < e - 0.6), key=lambda m: -m["db_over"]):
        if all(abs(m["t"] - x[0]) > 2.0 for x in out) and added < 2:
            out.append((m["t"], "impact"))
            added += 1
    out.sort()
    pruned = []
    for t, n in out:
        if not pruned or t - pruned[-1][0] >= 1.2:
            pruned.append((t, n))
    return pruned


def _payoff(c, s, e, signals):
    try:
        p = float(c.get("payoff"))
        if s + 1.0 <= p <= e - 0.3:
            return p
    except (TypeError, ValueError):
        pass
    inside = [x for x in signals if s + 1.0 <= x["t"] <= e - 0.3 and x["kind"] != "fast"]
    if inside:
        return max(inside, key=lambda x: x["strength"])["t"]
    return s + (e - s) * 0.7


def _assign_formats(chosen, allowed):
    """Use the model's pick when allowed, but keep variety: no format on more than ~40%."""
    allowed = [f for f in allowed if f in FORMATS] or ["standard"]
    cap = max(1, math.ceil(len(chosen) * 0.4))
    used = {f: 0 for f in allowed}
    for c in chosen:
        want = str(c.get("format", "")).strip().lower()
        ok = [f for f in allowed if f != "two_part" or c["e"] - c["s"] >= 30]
        if want not in ok or used.get(want, 0) >= cap:
            want = min(ok, key=lambda f: (used[f], f != "cold_open", f != "standard"))
        used[want] += 1
        c["format"] = want


def _clean_keywords(raw, cap=50):
    """Model keywords -> unique, lowercase, non-empty strings (accepts a list or a comma string)."""
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for item in raw:
        for k in str(item).split(","):
            k = " ".join(k.replace("#", " ").lower().split()).strip(" .;")
            if k and k not in seen:
                seen.add(k)
                out.append(k)
    return out[:cap]


def _write_meta(path, meta):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"TITLE:\n{meta['title']}\n\nCAPTION:\n{meta['caption']}\n\n"
                f"FORMAT: {meta['format']}\nHOOK TEXT: {meta['hook']}\nTEASE: {meta.get('tease', '')}\n"
                f"END QUESTION: {meta.get('cta', '')}\n\n{meta['source']}   SCORE: {meta['score']}\n"
                f"WHY: {meta['reason']}\n")
        kws = meta.get("keywords") or []
        f.write(f"\nKEYWORDS ({len(kws)}):\n{', '.join(kws) if kws else 'none - no transcript for this clip'}\n")
        if meta.get("gameplay"):
            f.write(f"\nBACKGROUND GAMEPLAY: {meta['gameplay']}\n")
        if meta.get("campaign"):
            f.write(f"\nCAMPAIGN RULES: {meta['campaign']}\n")
        if meta.get("manual_checks"):
            f.write("CHECK MANUALLY BEFORE POSTING (the software can't verify these):\n"
                    + "".join(f"  [ ] {x}\n" for x in meta["manual_checks"]))


LIVE_TASK = """This is the newest part of a LIVE stream that is checked every few minutes while it happens.
Return ONLY moments that would genuinely go viral as a Short (score {min_score} or higher), at most {k}. Most parts of a stream have NO such moment: then reply {{"clips": []}} and nothing else is needed. Never pad the answer with average moments.
A <<chat:...>> tag means live chat exploded there; the moment that caused it is in the few seconds BEFORE the tag. A moment that both reads well in the transcript and made chat explode is the strongest pick.
{seen}If a moment is still unfolding in the last seconds of this part (its reaction hasn't landed yet), leave it out: the next check will see all of it.
{chat}
"""


def process(source, cfg, log=print, live=None):
    """live: None for a normal run. For the live monitor, a dict:
    out_dir, prefix, t0 (stream second this file starts at), min_score, max_clips,
    chat_signals, chat_note, taken [(s, e) already clipped, in this file's seconds],
    reviewed (seconds at the start already checked last time), more_coming (bool)."""
    cfg = {**DEFAULTS, **(cfg or {})}
    if live:
        cfg["top_n_on"] = False
        cfg["n_clips"] = max(1, int(live.get("max_clips") or 2))
        cfg["range_start"] = cfg["range_end"] = ""
    t0 = time.time()
    picker = None
    if cfg.get("split_on"):
        # checked first, so a missing gameplay folder doesn't waste a download + transcription
        picker = GP.Picker(cfg.get("split_game") or GP.ANY)
        game = cfg.get("split_game") or GP.ANY
        log(f"Split-screen: content on top, {('any game' if game == GP.ANY else GP.nice(game))} "
            f"gameplay underneath ({len(picker.dur)} video(s), muted).")
        if picker.skipped:
            log(f"  Skipped unusable gameplay file(s): {', '.join(picker.skipped)}")
    source = str(getattr(source, "name", None) or source)  # accept Gradio file objects too
    offset = 0.0
    if re.match(r"https?://", source.strip()):
        a, b = parse_ts(cfg.get("range_start")), parse_ts(cfg.get("range_end"))
        if a is not None and b is not None and b <= a:
            raise ValueError(f"'To' ({fmt_ts(b)}) must be later than 'From' ({fmt_ts(a)}).")
        if a is not None or b is not None:
            # fetch only the window; the file then starts exactly at From
            source = _download(source.strip(), log, start=a or 0.0, end=b)
            offset = a or 0.0
            cfg["range_start"] = cfg["range_end"] = ""
        else:
            source = _download(source.strip(), log)
    if not os.path.exists(source):
        raise FileNotFoundError(source)
    info = probe(source)
    total = info["duration"]
    log(f"Video: {os.path.basename(source)} | {fmt_ts(total)} | {info['width']}x{info['height']}")

    r0, r1 = _source_range(cfg, total, log)
    part = (r0, r1) != (0.0, total)
    key = file_key(source)
    akey = f"{key}_{int(r0 * 1000)}_{int(r1 * 1000)}" if part else key
    os.makedirs(CACHE_DIR, exist_ok=True)
    wav = os.path.join(CACHE_DIR, f"{akey}.wav")
    if not info["has_audio"]:
        raise RuntimeError("This video has no audio track - nothing to transcribe.")
    if not os.path.exists(wav):
        log("Extracting audio...")
        if part:
            extract_audio(source, wav, r0, r1)
        else:
            extract_audio(source, wav)
    tr = transcribe_auto(wav, cfg, log=log, cache_key=akey)
    stt_cost = float(tr.get("cost") or 0)
    # the wav starts at r0: shift every time back to the source's own clock
    segments = [{**sg, "s": sg["s"] + r0, "e": sg["e"] + r0,
                 "words": [{**w, "s": w["s"] + r0, "e": w["e"] + r0} for w in sg["words"]]}
                for sg in tr["segments"]]
    words = [w for s in segments for w in s["words"]]
    loud = [{**m, "t": m["t"] + r0} for m in loud_moments(wav)]
    signals = find_signals(segments, loud, total)
    if live and live.get("chat_signals"):
        signals = sorted(signals + list(live["chat_signals"]), key=lambda x: x["t"])
    kinds = {}
    for x in signals:
        kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
    log(f"{len(words)} words transcribed. Reaction signals: "
        + (", ".join(f"{v} {k}" for k, v in kinds.items()) or "none"))

    n_clips = int(cfg["n_clips"])
    min_len, max_len = int(cfg["min_len"]), int(cfg["max_len"])
    rules, rules_note, models_used, cost_total = None, "", set(), stt_cost
    if tr.get("engine", "").startswith("cloud:"):
        models_used.add(tr["engine"][len("cloud:"):])
    if (cfg.get("rules_text") or "").strip():
        rules = R.parse_rules(cfg["rules_text"], cfg, log)
        if rules.get("model"):
            models_used.add(rules["model"])
        log(f"Campaign rules: {R.describe(rules)}")
        if rules["manual_checks"]:
            log(f"  {len(rules['manual_checks'])} rule(s) need a manual check - listed in each clip's .txt")
        min_len, max_len = _rule_lengths(min_len, max_len, rules, log)
        _apply_disallowed(cfg, rules, log)
        rules_note = R.prompt_block(rules)
    sfx_desc = "; ".join(f"{k} = {v}" for k, v in SFX_NAMES.items())
    motion_note = ""
    meme_lib = {}
    if cfg.get("meme_on"):
        meme_lib = MM.library(log)
        if meme_lib:
            log(f"Meme sounds: {len(meme_lib)} in the sounds folder.")
            motion_note += ('\n- "memes": 0-2 trending meme sounds as {"t": <absolute second the sound\'s '
                            'punch must land>, "name": <name>}. Only where it genuinely lands, the way an '
                            'editor would (e.g. fahhh the instant a ball flies just past someone); none is '
                            'better than a forced one; not on top of the creator\'s own punchline words. '
                            'Available:\n' + MM.prompt_list(meme_lib))
        else:
            log("Meme sounds are ON but the sounds folder has no audio files - skipping them.")
    if cfg.get("motion_on") and "stickers" in (cfg.get("motion_elements") or []):
        motion_note += ('\n- "stickers": 0-3 animated reaction stickers as {"t": <absolute seconds>, '
                       '"emoji": <one emoji>, "text": <1-2 word reaction, e.g. "NO WAY", "BRUH", '
                       '"LMAO", "HE\'S COOKED">} on the funniest or most shocking beats, at least 4 s '
                       'apart, not in the first 3 s of the clip')
    candidates = []

    def nothing(why):
        log(why)
        return {"source": source, "models": sorted(models_used), "est_cost_usd": round(cost_total, 5),
                "seconds": round(time.time() - t0), "clips": [], "out_dir": live.get("out_dir", ""),
                "deferred": 0, "deferred_start": None}

    if live and len(words) < 25:
        return nothing("Barely any speech in this part - nothing to clip.")
    if len(words) < 25:
        log("Barely any speech found - picking clips from the loudest moments instead.")
        for m in sorted(loud, key=lambda m: -m["db_over"])[: n_clips * 2]:
            candidates.append({"start": m["t"] - min_len * 0.6, "end": m["t"] + min_len * 0.4,
                               "score": int(min(99, 50 + m["db_over"] * 3)), "title": "",
                               "hook": "", "caption": "", "sfx": [{"t": m["t"], "name": "impact"}]})
        need_text = True
    else:
        need_text = False
        wins = _windows(segments, r1, start=r0)
        if len(wins) > 1:
            log(f"Long video: analysing in {len(wins)} parts.")
        for w0, w1 in wins:
            k = max(2, math.ceil(n_clips * (w1 - w0) / (r1 - r0)) + 1)
            text = annotate(segments, signals, w0, w1)
            if len(text) < 200:
                continue
            spots = hot_spots([x for x in signals if w0 <= x["t"] < w1], top=max(12, 3 * k))
            hot = "\n".join(f"- {sp['t']:.0f}s  strength {sp['score']}  ({', '.join(sorted(sp['kinds']))})"
                            for sp in spots) or "none"
            allowed = [f for f in cfg["formats"] if f in FORMATS] or ["standard"]
            fmts = "\n".join(f"    {f}: {FORMATS[f]}" for f in allowed)
            creator = (cfg.get("creator") or "").strip()
            creator_note = (f"The creator in this video is {creator}. Use the name in some titles/hooks "
                            f"(e.g. \"Watch {creator}...\"), always in spotlight hooks.") if creator else ""
            task = f"Find the {k} best clips inside this window."
            if live:
                k = n_clips
                seen = ""
                if live.get("reviewed"):
                    seen += (f"The first {live['reviewed']:.0f}s were already checked last time; pick "
                             f"something there only if it continues past {live['reviewed']:.0f}s. ")
                if live.get("taken"):
                    seen += ("Already clipped, never pick again: "
                             + ", ".join(f"{a:.0f}-{b:.0f}s" for a, b in live["taken"]) + ". ")
                task = LIVE_TASK.format(min_score=int(live.get("min_score") or 70), k=k,
                                        seen=seen + ("\n" if seen else ""),
                                        chat=live.get("chat_note") or "")
            prompt = USER_TMPL.format(hint=cfg["content_hint"] or "gaming video", w0=w0, w1=w1, k=k, task=task,
                                      min_len=min_len, max_len=max_len, sfx=sfx_desc, formats=fmts,
                                      creator_note=creator_note, rules_note=rules_note,
                                      motion_note=motion_note, hot=hot, transcript=text)

            def validate(d, w0=w0, w1=w1, k=k):
                """Quality gate: reject lazy/broken answers so the next model is tried."""
                clips = d.get("clips") if isinstance(d, dict) else None
                if live and isinstance(clips, list) and not clips:
                    return          # "nothing viral here" is a valid live answer
                if not clips or not isinstance(clips, list):
                    raise ValueError("no clips list")
                ok = []
                for c in clips:
                    if not isinstance(c, dict):
                        continue
                    for key in ("title", "hook", "caption", "tease", "cta", "label", "format", "reason"):
                        v = c.get(key)
                        if isinstance(v, list):
                            c[key] = " ".join(str(x) for x in v)
                        elif v is not None and not isinstance(v, str):
                            c[key] = str(v)
                    if not isinstance(c.get("sfx"), list):
                        c["sfx"] = []
                    if not isinstance(c.get("stickers"), list):
                        c["stickers"] = []
                    if not isinstance(c.get("memes"), list):
                        c["memes"] = []
                    c["keywords"] = _clean_keywords(c.get("keywords"))
                    try:
                        c["start"], c["end"] = float(c["start"]), float(c["end"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    dur = c["end"] - c["start"]
                    if not (w0 - 5 <= c["start"] and c["end"] <= w1 + max_len):
                        continue
                    if not (min_len * 0.5 <= dur <= max_len * 1.5):
                        continue
                    if len(str(c.get("title", "")).strip()) < 8 or not str(c.get("hook", "")).strip():
                        continue
                    if "#" not in str(c.get("caption", "")):
                        continue
                    ok.append(c)
                need = 0 if live else (1 if (w1 - w0) < 5 * max_len else 2)
                if len(ok) < need:
                    raise ValueError(f"only {len(ok)} usable clips (need {need})")
                d["clips"] = ok

            in_tok = est_tokens(prompt) + 400
            data, model, cost = chat_json(cfg["api_key"], [
                {"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                in_tok, out_tokens=min(8000, 600 * k + 500), model=cfg["model"],
                allow_free=cfg["allow_free"], validate=validate, log=log)
            models_used.add(model)
            cost_total += cost or 0
            log(f"  {model} suggested {len(data['clips'])} clips.")
            candidates += data["clips"]

    # snap, de-overlap, rank
    chosen, deferred, deferred_start = [], 0, None
    for c in sorted(candidates, key=lambda c: -_score(c)):
        s, e = _snap(c, words, r1, min_len, max_len, lo=r0)
        if e - s < min(min_len, r1 - r0) * 0.8 or s < r0 - 0.01 or e > r1 + 0.01:
            continue
        if any(_overlap((s, e), (x["s"], x["e"])) for x in chosen):
            continue
        if live:
            if _score(c) < float(live.get("min_score") or 70):
                continue
            if any(_overlap((s, e), t) for t in live.get("taken") or []):
                continue
            if live.get("more_coming") and e >= r1 - 2.0:
                deferred += 1   # runs into the end of this part: the next check sees all of it
                deferred_start = s if deferred_start is None else min(deferred_start, s)
                continue
        chosen.append({**c, "s": s, "e": e})
        if len(chosen) >= (min(40, n_clips * 2) if rules else n_clips):
            break
    if not chosen and live:
        r = nothing("No viral moment in this part." + (" (1 moment is still unfolding; the next "
                                                       "check will catch it.)" if deferred else ""))
        r["deferred"], r["deferred_start"] = deferred, deferred_start
        return r
    if not chosen:
        raise RuntimeError("No usable clips were found in this video.")

    if need_text:
        _write_titles_without_transcript(chosen, cfg, log, models_used)
    if rules:
        try:
            chosen, cost = _filter_by_rules(chosen, rules, words, cfg, log, models_used)
        except RuntimeError as e:
            if live and "complies with the campaign rules" in str(e):
                return nothing("A moment was found but it breaks the campaign rules - skipped.")
            raise
        cost_total += cost
        chosen = chosen[:n_clips]

    prefix = ""
    out_dir = os.path.join(OUTPUT_DIR, slug(os.path.splitext(os.path.basename(source))[0], 60),
                           time.strftime("%Y-%m-%d_%H%M%S"))
    if live:
        out_dir, prefix, offset = live["out_dir"], live.get("prefix") or "", float(live.get("t0") or 0)
    os.makedirs(out_dir, exist_ok=True)
    logo = None
    layout = "split" if picker else cfg["layout"]
    if has_logo(cfg) and picker:
        vf, cap_y, hook_y, logo_y = layout_geometry("split", info["width"], info["height"],
                                                    float(cfg["zoom"]), logo=True)
        png, lw, lh = fitted_logo(cfg["logo_path"], float(cfg.get("logo_size") or 1.0))
        logo = {"png": png, "cy": min(logo_y + lh // 2, H - lh // 2 - 24),
                "anim": cfg.get("logo_anim") or "float"}
    elif has_logo(cfg):
        if cfg["layout"] == "crop":
            log("Logo is set: using the blur layout so the logo sits on the blurred band, not the gameplay.")
        vf, cap_y, hook_y, logo_y = layout_geometry("blur", info["width"], info["height"],
                                                    float(cfg["zoom"]), logo=True)
        png, lw, lh = fitted_logo(cfg["logo_path"], float(cfg.get("logo_size") or 1.0))
        cy = min(logo_y + lh // 2, H - lh // 2 - 24)
        logo = {"png": png, "cy": cy, "anim": cfg.get("logo_anim") or "float"}
    else:
        vf, cap_y, hook_y, _ = layout_geometry(layout, info["width"], info["height"],
                                               float(cfg["zoom"]))
    manual = rules["manual_checks"] if rules else []
    campaign = "passed the automatic rule review (speech + text)" if rules else ""
    chosen.sort(key=_score, reverse=True)
    for i, c in enumerate(chosen):
        c["payoff"] = _payoff(c, c["s"], c["e"], signals)
        c["_highlight"] = (AUTO_CYCLE[i % len(AUTO_CYCLE)] if cfg["highlight"] == "auto"
                           else cfg["highlight"])
    _assign_formats(chosen, cfg["formats"])

    jobs = []  # (clip, suffix, Timeline, name)
    for i, c in enumerate(chosen, start=1):
        s, e = c["s"], c["e"]
        cues = _cues(c, s, e, loud) if cfg["sfx_on"] else []
        punches = [x["t"] for x in sorted(signals, key=lambda x: -x["strength"])
                   if s + 1 < x["t"] < e - 1 and x["kind"] in ("exclaim", "loud") and x["strength"] >= 3]
        mcues = MM.clip_cues(c, s, e, meme_lib) if meme_lib else []
        if mcues:
            cues = sorted([x for x in cues if all(abs(x[0] - m[0]) > 1.5 for m in mcues)] + mcues)
        videos = build(c["format"], c, words, cfg, (cap_y, hook_y), cues, punches)
        if rules and not all(_len_ok(tl.length, rules) for _, tl in videos):
            if c["format"] != "standard":
                log(f"  clip {i}: '{c['format']}' would break the campaign length limit - using standard.")
                c["format"] = "standard"
                videos = build("standard", c, words, cfg, (cap_y, hook_y), cues, punches)
            if not all(_len_ok(tl.length, rules) for _, tl in videos):
                log(f"  clip {i} skipped: {videos[0][1].length:.0f}s is outside the campaign length limit.")
                continue
        for suffix, tl in videos:
            _settle_sfx(tl, cfg["sfx_on"])
            name = f"{prefix}clip_{i:02d}_{c['format']}_{slug(c.get('title'), 34)}{suffix}"
            jobs.append((c, suffix, tl, name, i))

    top = None
    if cfg["top_n_on"] and len(chosen) >= 3:
        n = max(3, min(int(cfg["top_n"]), len(chosen)))
        best = chosen[:n]
        pack = _top_n_package(best, cfg, log, models_used, rules)
        if pack is None:
            log("Top-N countdown skipped: its title/caption couldn't be made rule-compliant.")
        else:
            tl = build_top_n(best, words, cfg, (cap_y, hook_y), pack["hook"], pack["cta"])
            while rules and rules["max_len"] and tl.length > rules["max_len"] + 0.5 and len(best) > 3:
                best = best[:-1]
                tl = build_top_n(best, words, cfg, (cap_y, hook_y), pack["hook"], pack["cta"])
            if rules and not _len_ok(tl.length, rules):
                log(f"Top-N countdown skipped: {tl.length:.0f}s is outside the campaign length limit.")
            else:
                if not cfg["sfx_on"]:
                    tl.sfx = []
                top = (best, pack, tl)

    if cfg.get("motion_on"):
        M.apply([(c, tl) for c, _, tl, _, _ in jobs] + ([(None, top[2])] if top else []),
                cfg, signals, (cap_y, hook_y), log)

    results = []
    for c, suffix, tl, name, i in jobs:
        s, e = c["s"], c["e"]
        out = os.path.join(out_dir, name + ".mp4")
        log(f"Rendering {i}/{len(chosen)} [{c['format']}{suffix}]: {fmt_ts(s)}-{fmt_ts(e)} "
            f"-> {tl.length:.0f}s, score {c.get('score')}")
        gp = picker.pick(tl.length) if picker else None
        render_timeline(source, tl.segments, tl.events, tl.sfx, out, vf, info["has_audio"],
                        float(cfg["sfx_gain"]), cfg["progress_on"], workdir=out_dir, logo=logo,
                        overlays=tl.overlays, memes=meme_lib, meme_gain=float(cfg["meme_gain"]),
                        gameplay=gp)
        meta = {"file": out, "format": c["format"] + suffix, "start": round(s, 2),
                "end": round(e, 2), "length": round(tl.length, 1), "score": c.get("score"),
                "title": c.get("title", "") + (" (Part 2)" if suffix == "_part2" else ""),
                "hook": c.get("hook", ""), "tease": c.get("tease", ""), "cta": c.get("cta", ""),
                "caption": c.get("caption", ""), "reason": c.get("reason", ""),
                "keywords": c.get("keywords") or [],
                "source": f"SOURCE: {fmt_ts(s + offset)} - {fmt_ts(e + offset)}"
                          + (" (time since the recording started)" if live else
                             " (times in the original video)" if offset else ""),
                "campaign": campaign, "manual_checks": manual,
                "gameplay": os.path.basename(gp["path"]) if gp else ""}
        _write_meta(os.path.join(out_dir, name + ".txt"), meta)
        results.append(meta)

    if top:
        best, pack, tl = top
        n = len(best)
        name = f"top_{n}_{slug(pack['title'], 40)}"
        out = os.path.join(out_dir, name + ".mp4")
        log(f"Rendering Top {n} countdown -> {tl.length:.0f}s")
        gp = picker.pick(tl.length) if picker else None
        render_timeline(source, tl.segments, tl.events, tl.sfx, out, vf, info["has_audio"],
                        float(cfg["sfx_gain"]), cfg["progress_on"], workdir=out_dir, logo=logo,
                        overlays=tl.overlays, memes=meme_lib, meme_gain=float(cfg["meme_gain"]),
                        gameplay=gp)
        meta = {"file": out, "format": f"top_{n}", "start": None, "end": None,
                "length": round(tl.length, 1), "score": None, "title": pack["title"],
                "hook": pack["hook"], "cta": pack["cta"], "caption": pack["caption"],
                "reason": "countdown of: " + ", ".join(m.get("label") or m.get("title", "") for m in best),
                "source": "SOURCES: " + ", ".join(f"{fmt_ts(m['s'])}" for m in best),
                "campaign": campaign, "manual_checks": manual,
                "gameplay": os.path.basename(gp["path"]) if gp else ""}
        _write_meta(os.path.join(out_dir, name + ".txt"), meta)
        results.append(meta)

    summary = {"source": source, "models": sorted(models_used), "est_cost_usd": round(cost_total, 5),
               "seconds": round(time.time() - t0), "clips": results}
    if rules:
        summary["campaign"] = {k: rules[k] for k in rules if k != "text"}
        with open(os.path.join(out_dir, "campaign_rules.txt"), "w", encoding="utf-8") as f:
            f.write(rules["text"] + "\n\n---\nCHECK MANUALLY BEFORE POSTING:\n"
                    + "".join(f"[ ] {x}\n" for x in manual))
    with open(os.path.join(out_dir, f"{prefix}clips.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    summary["deferred"], summary["deferred_start"] = deferred, deferred_start
    log(f"Done in {summary['seconds']}s. {len(results)} clips in {out_dir}"
        + (f" | model: {', '.join(summary['models'])} | est. cost ${cost_total:.4f}" if models_used else ""))
    summary["out_dir"] = out_dir
    return summary


def _top_n_package(best, cfg, log, models_used, rules=None):
    n = len(best)
    hint = cfg["content_hint"] or "gaming"
    default = {"title": f"Top {n} craziest moments - {hint}", "hook": f"TOP {n} CRAZIEST MOMENTS",
               "cta": "WHICH ONE WAS THE CRAZIEST?",
               "caption": f"Number 1 is unreal. #gaming #shorts #top{n}"}
    items = "\n".join(f"#{i + 1}: {m.get('label') or m.get('title')}" for i, m in enumerate(best))
    prompt = (f"Video: {hint}" + (f" (creator: {cfg['creator']})" if cfg.get("creator") else "") +
              f". I made a countdown Short of the top {n} moments:\n{items}\n"
              f"Write: 'title' (viral but honest, max 70 chars), 'hook' (on-screen opener, max 6 words, "
              f"e.g. 'TOP 5 CRAZIEST GTA 6 MOMENTS'), 'cta' (end question for comments, max 8 words), "
              f"'caption' (1-2 sentences + 4-6 hashtags). Reply JSON with those 4 keys.")
    if rules:
        prompt += "\nAll text must follow these campaign rules:\n" + R.prompt_block(rules)
    try:
        data, model, _ = chat_json(cfg["api_key"], [{"role": "user", "content": prompt}],
                                   500 + (len(rules["text"]) // 3 if rules else 0), 600,
                                   cfg["model"], cfg["allow_free"], log=log,
                                   validate=lambda d: d.get("title") or (_ for _ in ()).throw(ValueError("no title")))
        models_used.add(model)
        pack = {k: str(data.get(k) or default[k]) for k in default}
    except Exception as e:
        log(f"Top-{n} packaging fell back to defaults ({e}).")
        pack = dict(default)
    if rules:
        pack["caption"] = R.enforce_caption(rules, pack["caption"])
        if R.banned_hits(rules, pack.values()):
            return None
        said = [c.get("_said", "") for c in best]
        verdicts, _ = R.review_clips(rules, [{"s": 0, "e": 0, **pack, "_said": " ".join(said)}],
                                     [], cfg, log, models_used)
        if verdicts.get(0, ("fail",))[0] != "pass":
            return None
    return pack


def _settle_sfx(tl, sfx_on):
    """Meme sounds win: drop regular effects within 1.2 s of one (they'd clash); with sound
    effects off, only the meme sounds stay."""
    memes = [t for t, n in tl.sfx if n.startswith("meme:")]
    tl.sfx = [(t, n) for t, n in tl.sfx
              if n.startswith("meme:") or (sfx_on and all(abs(t - m) > 1.2 for m in memes))]


def _len_ok(length, rules, tol=0.5):
    lo, hi = rules.get("min_len"), rules.get("max_len")
    return (not lo or length >= lo - tol) and (not hi or length <= hi + tol)


def _rule_lengths(min_len, max_len, rules, log):
    """Clip length window that satisfies both the user's sliders and the campaign."""
    lo, hi = rules.get("min_len"), rules.get("max_len")
    new_min = max(min_len, int(math.ceil(lo))) if lo else min_len
    new_max = min(max_len, int(hi)) if hi else max_len
    if new_min > new_max:  # sliders conflict with the campaign: the campaign wins
        new_min = int(math.ceil(lo)) if lo else min(min_len, new_max)
        new_max = int(hi) if hi else max(max_len, new_min)
    if (new_min, new_max) != (min_len, max_len):
        log(f"  clip length set to {new_min}-{new_max}s to match the campaign rules.")
    return new_min, new_max


def _apply_disallowed(cfg, rules, log):
    off = rules["disallowed"]
    if not off:
        return
    fmts = [f for f in cfg["formats"] if f not in off]
    cfg["formats"] = fmts or ["standard"]
    for feat, key in (("countdown", "top_n_on"), ("sound_effects", "sfx_on"),
                      ("end_question", "cta_on"), ("zoom_effects", "punch_on"),
                      ("progress_bar", "progress_on")):
        if feat in off:
            cfg[key] = False
    log("  turned off for this campaign: " + ", ".join(off))


def _filter_by_rules(chosen, rules, words, cfg, log, models_used):
    """Banned words (speech + text) are checked here; then an independent AI review judges
    every remaining clip against the full rules. Only passing clips are kept."""
    keep = []
    for c in chosen:
        said = " ".join(w["w"] for w in words if c["s"] - 0.3 <= w["s"] <= c["e"] + 0.3)
        c["_said"] = said
        c["caption"] = R.enforce_caption(rules, c.get("caption", ""))
        hits = R.banned_hits(rules, [said, c.get("title"), c.get("hook"), c.get("tease"),
                                     c.get("cta"), c.get("label"), c.get("caption"),
                                     " ".join(c.get("keywords") or [])])
        if hits:
            log(f"  dropped {fmt_ts(c['s'])} '{c.get('title', '')[:40]}': banned word(s) {', '.join(hits)}")
            continue
        keep.append(c)
    if not keep:
        raise RuntimeError("No clip in this video complies with the campaign rules (banned words).")
    log(f"Checking {len(keep)} candidate clips against the campaign rules...")
    try:
        verdicts, cost = R.review_clips(rules, keep, words, cfg, log, models_used)
    except Exception as e:
        raise RuntimeError(f"Couldn't check the clips against the campaign rules ({e}). "
                           "Nothing was rendered, so no unchecked clip gets posted.") from e
    passed = []
    for i, c in enumerate(keep):
        verdict, why = verdicts.get(i, ("fail", ["no verdict"]))
        if verdict == "pass":
            passed.append(c)
        else:
            log(f"  dropped {fmt_ts(c['s'])} '{c.get('title', '')[:40]}': {'; '.join(why) or 'breaks a rule'}")
    log(f"  {len(passed)} of {len(keep)} clips passed the rule review.")
    if not passed:
        raise RuntimeError("No clip in this video fully complies with the campaign rules.")
    return passed, cost


def _write_titles_without_transcript(chosen, cfg, log, models_used):
    prompt = (f"Video: {cfg['content_hint']}. These clips were cut at the loudest moments "
              f"(there is little speech). For each clip id write a viral but honest 'title' "
              f"(max 70 chars), 'hook' (max 6 words) and 'caption' (1 sentence + 4-6 hashtags). "
              f"Keep them generic enough to be true without seeing the clip. "
              f'Reply JSON: {{"clips":[{{"id":1,"title":"...","hook":"...","caption":"..."}}]}}\n'
              f"Clip ids: {list(range(1, len(chosen) + 1))}")
    try:
        data, model, _ = chat_json(cfg["api_key"], [{"role": "user", "content": prompt}], 600,
                                   1500, cfg["model"], cfg["allow_free"], log=log)
        models_used.add(model)
        by_id = {int(c.get("id", 0)): c for c in data.get("clips", []) if isinstance(c, dict)}
        for i, c in enumerate(chosen, start=1):
            for k in ("title", "hook", "caption"):
                c[k] = (by_id.get(i) or {}).get(k, "") or c.get(k, "")
    except Exception as e:
        log(f"Could not generate titles ({e}); clips will be rendered without them.")
