"""Long video in -> finished vertical shorts + titles/captions out.

process() runs the steps below it in order: gameplay check, download, transcript + reaction
signals, campaign rules, AI clip picking, selection, layout, editing into formats, Top-N,
motion graphics, rendering, and the .txt/.json files next to the clips.
"""
import json
import math
import os
import re
import time

from . import gameplay as GP
from . import memes as MM
from . import motion as M
from . import rules as R
from .audio import extract_audio
from .brand import fitted_logo, has_logo
from .captions import AUTO_CYCLE
from .download import download
from .formats import FORMATS, build, build_top_n
from .hype import loud_moments
from .llm import chat_json, est_tokens
from .render import H, layout_geometry, render_timeline
from .settings import DEFAULTS
from .signals import annotate, find_signals, hot_spots, vocal_energy
from .tools import CACHE_DIR, OUTPUT_DIR, check_stop, fmt_ts, parse_ts, probe, slug
from .transcribe import file_key, transcribe_auto

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

SYSTEM = """You are the editor behind the biggest streamer-clip channels. You turn long gaming streams and streamer content into Shorts/TikToks/Reels that strangers watch to the end, rewatch and send to friends.

WHY PEOPLE WATCH A CLIP - judge every moment by this, not by "is it funny?":
- Hook: in the first 2-3 seconds a line, a scream or a situation opens a question the viewer needs answered ("why is he running?", "is he about to lose everything?").
- Stakes: something can be won or lost - money, a fight, a run, a bet, pride, a friendship on stream. No stakes, no tension.
- A real emotional peak from the streamer: rage, panic, shock, disbelief, pure hype, embarrassment, cringe, laughing hard AT something. Viewers feel what the streamer feels: the bigger and more honest the reaction, the better.
- Payoff: the tension resolves inside the clip (the escape works or fails spectacularly, the plan backfires, the comeback lands) and the reaction lands on it.
- Zero context: a stranger who never saw this stream understands it within a second.
- A reason to share or comment: "this is so me", a quotable line, an absurd situation, a hot take or argument, someone getting what they deserve, an underdog win, a rivalry, something people will argue about.

THE STREAMER IS THE SIGNAL. Read what the streamer says and how they say it: shouting, swearing, slang ("ain't no way", "bro is cooked", "chat is this real", "nah nah nah"), exclamations, voice cracks, repeated words, rapid panicked talk, a sudden stunned silence. Live chat, when present, is a weak hint: it spams constantly (greetings, W/L, emotes, copypasta), so it can only confirm a moment the streamer already shows - never pick a moment because of chat.

REJECT (score under 40), however loud it is or however much chat reacted:
- laughing or yelling with no clear cause in the transcript; inside jokes and callbacks that need earlier context
- reading donations, subs or raids; greeting chat; chatting about nothing; sponsor reads; "like and subscribe"
- menus, loading, setup, tutorials, long explanations, AFK, dead air
- a moment whose payoff hasn't happened yet or happens outside the clip

The transcript is annotated by an automatic detector (hints, not the answer):
  <<exclaim:...>> exclamation/slang/swearing   <<shout:+N dB voice>> said much louder than the streamer usually talks
  <<laugh>> laughter   <<fast>> rapid excited talking   <<loud>> audio spike (scream, explosion, crash)
  ...(N s silence)... a sudden pause mid-conversation   <<chat:...>> live-chat burst (weak hint)
A quiet devastating line can beat a scream; a scream with no story is weak.
Start each clip 1-4 s before the setup line (never mid-sentence) so the hook is in the first seconds, and end 0.5-2 s after the reaction lands. No slow lead-in: the first 3 seconds decide whether anyone stays.
Reply with ONLY a JSON object, no prose."""

USER_TMPL = """Video: {hint}
Window: {w0:.0f}s to {w1:.0f}s of the video.
{task} Each clip must be {min_len}-{max_len} seconds long, clips must not overlap, and start/end must be real times from the transcript (seconds, decimals allowed).

For each clip write:
- "start", "end": seconds
- "title": viral YouTube Shorts title, max 70 chars, curiosity-driven but TRUE to what happens (no fake claims), may use 1 emoji
- "hook": on-screen text for the first 3 seconds, max 7 words, makes people stay (tease the payoff, don't spoil it)
- "caption": TikTok/Instagram caption, 1-2 short sentences + 4-6 relevant hashtags
- "scores": {{"hook": 0-25, "emotion": 0-25, "payoff": 0-20, "context": 0-15, "share": 0-15}}: hook = how strongly the first 3 s make people stay; emotion = how big and genuine the streamer's reaction is; payoff = whether the tension resolves inside the clip; context = how well a stranger understands it (15 = no context needed); share = how likely people send it to a friend or comment. Be strict: an average stream moment totals 40-60, only real standouts pass 80
- "trigger": the main reason people will watch, 2-5 words (e.g. "rage after a betrayal", "near-miss escape", "absurd glitch", "underdog clutch")
- "reason": one short sentence: what happens and why it hooks
- "payoff": the single peak second (absolute) - the crash/kill/scream/punchline everything builds to
- "format": the edit style that fits this moment best, one of:
{formats}
- "tease": max 6 words shown with the payoff in cold_open / freeze_open / wait_for_it (a curiosity gap, e.g. "HOW DID THIS HAPPEN?!", "HE HAD NO IDEA..."); never spoil the ending
- "cta": a short question shown at the end to get comments, max 8 words (e.g. "WOULD YOU HAVE SURVIVED THAT?")
- "label": 2-5 words naming the moment, used in "Top N" countdowns (e.g. "THE OCEAN ESCAPE")
- "keywords": 45-50 keywords for THIS clip (lowercase, 1-3 words each). Draw them only from what is said or shown in this clip: people, game or place names, the topic, the action, the reaction words. No hashtags, no duplicates, nothing the clip doesn't contain.
- "sfx": 1-4 sound effects as {{"t": <absolute seconds>, "name": <name>}} placed exactly on the beat they punctuate (a reaction, a crash, a punchline, the start of a silence)
Sound effect names: {sfx}
{creator_note}{notes}
Use a mix of formats across the clips - not all the same.
{language}

JSON format:
{{"clips":[{{"start":12.3,"end":45.6,"payoff":38.2,"format":"cold_open","title":"...","hook":"...","tease":"...","cta":"...","label":"...","caption":"...","scores":{{"hook":22,"emotion":21,"payoff":17,"context":13,"share":12}},"trigger":"...","reason":"...","keywords":["..."],"sfx":[{{"t":20.1,"name":"impact"}}]}}]}}

HOT SPOTS found by the detector (start second, strength, kinds) - check these first, but read the transcript around them:
{hot}

TRANSCRIPT:
{transcript}"""


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


SCORE_PARTS = {"hook": 25, "emotion": 25, "payoff": 20, "context": 15, "share": 15}


def _total_score(c):
    """The clip's score = the sum of the rubric parts (each clamped to its maximum). Falls back
    to a plain "score" when a model didn't return the parts."""
    parts = c.get("scores")
    if isinstance(parts, dict):
        total, out_of = 0.0, 0
        for k, cap in SCORE_PARTS.items():
            try:
                total += min(cap, max(0.0, float(parts.get(k))))
                out_of += cap
            except (TypeError, ValueError):
                pass
        if out_of >= 60:          # at least 3 parts: scale what's there to 100
            return int(round(total * 100 / out_of))
    try:
        return int(round(float(c.get("score") or 0)))
    except (TypeError, ValueError):
        return 0


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


# ---------------------------------------------------------------- output language
LANG_NAMES = {"en": "English", "es": "Spanish", "pt": "Portuguese", "fr": "French", "de": "German",
              "it": "Italian", "nl": "Dutch", "pl": "Polish", "tr": "Turkish", "id": "Indonesian",
              "tl": "Filipino", "ru": "Russian", "ja": "Japanese", "ko": "Korean", "ar": "Arabic",
              "zh": "Chinese"}
LATIN = {"en", "es", "pt", "fr", "de", "it", "nl", "pl", "tr", "id", "tl", "hi", "ur"}   # hi/ur: Hinglish
_DEVANAGARI = re.compile("[\u0900-\u097F]")
_NON_LATIN = re.compile("[\u0400-\u04FF\u0590-\u06FF\u0900-\u0DFF\u0E00-\u0E7F\u3040-\u30FF"
                        "\u3400-\u9FFF\uAC00-\uD7AF]")


def _output_language(cfg, transcript_language, words):
    """The language the AI must write titles, hooks and captions in: the one set in Settings,
    else the transcript's (else guessed from the script of the words). Returns
    (code, instruction for the prompt). Hindi/Urdu streams get Hinglish (Latin letters)."""
    code = str(cfg.get("language") or transcript_language or "").strip().lower()[:2]
    if not code:
        dev = sum(1 for w in words if _DEVANAGARI.search(w["w"]))
        code = "hi" if words and dev > 0.3 * len(words) else "en"
    if code in ("hi", "ur"):
        return code, ('LANGUAGE: the streamer speaks Hindi/Urdu. Write title, hook, tease, cta, label, caption '
                      'and keywords in Hinglish (Hindi written in English letters, e.g. "Bhai ne kya kar diya"). '
                      'Never use Devanagari or Urdu script.')
    name = LANG_NAMES.get(code, f"the transcript's language ({code})")
    return code, (f"LANGUAGE: the video is in {name}. Write title, hook, tease, cta, label, caption and "
                  f"keywords in {name} only - no other language or script.")


def _wrong_script(text, code):
    """True when text meant to be in a Latin-script language (or Hinglish) contains another script,
    e.g. an English video's title written in Hindi."""
    return code in LATIN and bool(_NON_LATIN.search(text or ""))


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
                f"WHY: {(meta['trigger'] + ' - ') if meta.get('trigger') else ''}{meta['reason']}\n")
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
A <<chat:...>> tag marks a live-chat burst; whatever caused it happened a few seconds BEFORE the tag. Chat is a weak hint: it can confirm a moment the streamer's own words and voice already show, never justify one on its own.
{seen}If a moment is still unfolding in the last seconds of this part (its reaction hasn't landed yet), leave it out: the next check will see all of it.
{chat}
"""


def process(source, cfg, log=print, live=None):
    """Clip one video. Returns a summary dict (clips, models, cost, out_dir, ...).

    live: None for a normal run. For the live monitor, a dict:
    out_dir, prefix, t0 (stream second this file starts at), min_score, max_clips,
    chat_signals, chat_note, taken [(s, e) already clipped, in this file's seconds],
    reviewed (seconds at the start already checked last time), more_coming (bool)."""
    cfg = {**DEFAULTS, **(cfg or {})}
    if cfg["layout"] == "split":
        cfg["split_on"] = True
    if cfg["layout"] == "facecam" and not cfg["split_on"]:
        fc = cfg.get("facecam") or {}
        if not (fc.get("cam") and fc.get("game")):     # checked before any download / transcription
            raise ValueError("The facecam layout needs both boxes: load a frame and draw the "
                             "facecam and gameplay boxes first.")
    if live:
        cfg["top_n_on"] = False
        cfg["n_clips"] = max(1, int(live.get("max_clips") or 2))
        cfg["range_start"] = cfg["range_end"] = ""
    t0 = time.time()
    picker = _gameplay_picker(cfg, log)
    source, offset = _fetch_source(source, cfg, log)
    if not os.path.exists(source):
        raise FileNotFoundError(source)
    info = probe(source)
    total = info["duration"]
    log(f"Video: {os.path.basename(source)} | {fmt_ts(total)} | {info['width']}x{info['height']}")

    r0, r1 = _source_range(cfg, total, log)
    media = _listen(source, info, cfg, r0, r1, log, live)
    check_stop()
    cfg["_lang"] = _output_language(cfg, media["language"], media["words"])   # (code, prompt line)
    words, signals = media["words"], media["signals"]
    usage = {"models": set(), "cost": media["cost"]}     # every AI model used + total est. cost
    if media["engine"].startswith("cloud:"):
        usage["models"].add(media["engine"][len("cloud:"):])

    n_clips = int(cfg["n_clips"])
    min_len, max_len = int(cfg["min_len"]), int(cfg["max_len"])
    rules = None
    if (cfg.get("rules_text") or "").strip():
        rules, min_len, max_len = _load_campaign(cfg, min_len, max_len, log, usage["models"])
    meme_lib, extras_note = _extras(cfg, log)

    def nothing(why, deferred=0, deferred_start=None):
        """Live mode: this part of the stream gives no clip."""
        log(why)
        return {"source": source, "models": sorted(usage["models"]), "est_cost_usd": round(usage["cost"], 5),
                "seconds": round(time.time() - t0), "clips": [], "out_dir": live.get("out_dir", ""),
                "deferred": deferred, "deferred_start": deferred_start}

    if live and len(words) < 25:
        return nothing("Barely any speech in this part - nothing to clip.")
    need_text = len(words) < 25
    if need_text:
        log("Barely any speech found - picking clips from the loudest moments instead.")
        candidates = _loudest_candidates(media["loud"], n_clips, min_len)
    else:
        prompt_notes = (R.prompt_block(rules) if rules else "") + extras_note
        candidates = _ask_for_clips(media["segments"], signals, cfg, r0, r1, n_clips, min_len, max_len,
                                    prompt_notes, live, log, usage)

    chosen, deferred, deferred_start = _select(candidates, words, r0, r1, min_len, max_len,
                                               min(40, n_clips * 2) if rules else n_clips, live)
    if not chosen and live:
        return nothing("No viral moment in this part." + (" (1 moment is still unfolding; the next "
                                                          "check will catch it.)" if deferred else ""),
                       deferred, deferred_start)
    if not chosen:
        raise RuntimeError("No usable clips were found in this video.")
    if need_text:
        _write_titles_without_transcript(chosen, cfg, log, usage["models"])
    if rules:
        try:
            chosen, cost = R.filter_clips(chosen, rules, words, cfg, log, usage["models"])
        except R.NoCompliantClips:
            if live:
                return nothing("A moment was found but it breaks the campaign rules - skipped.")
            raise
        usage["cost"] += cost
        chosen = chosen[:n_clips]

    if live:
        out_dir, prefix, offset = live["out_dir"], live.get("prefix") or "", float(live.get("t0") or 0)
    else:
        out_dir, prefix = os.path.join(OUTPUT_DIR, slug(os.path.splitext(os.path.basename(source))[0], 60),
                                       time.strftime("%Y-%m-%d_%H%M%S")), ""
    os.makedirs(out_dir, exist_ok=True)
    vf, geo, logo = _layout(cfg, info, picker is not None, log)
    chosen.sort(key=_score, reverse=True)
    for i, c in enumerate(chosen):
        c["payoff"] = _payoff(c, c["s"], c["e"], signals)
        c["_highlight"] = (AUTO_CYCLE[i % len(AUTO_CYCLE)] if cfg["highlight"] == "auto"
                           else cfg["highlight"])
    _assign_formats(chosen, cfg["formats"])
    jobs = _timelines(chosen, cfg, words, signals, media["loud"], meme_lib, geo, rules, prefix, log)
    top = (_top_n(chosen, cfg, words, geo, rules, log, usage["models"])
           if cfg["top_n_on"] and len(chosen) >= 3 else None)
    if cfg.get("motion_on"):
        M.apply([(c, tl) for c, _, tl, _, _ in jobs] + ([(None, top[2])] if top else []),
                cfg, signals, geo, log)

    def render(tl, name):
        """Render one timeline into out_dir; returns (file, name of the gameplay video under it)."""
        check_stop()
        out = os.path.join(out_dir, name + ".mp4")
        gp = picker.pick(tl.length) if picker else None
        render_timeline(source, tl.segments, tl.events, tl.sfx, out, vf, info["has_audio"],
                        float(cfg["sfx_gain"]), cfg["progress_on"], workdir=out_dir, logo=logo,
                        overlays=tl.overlays, memes=meme_lib, meme_gain=float(cfg["meme_gain"]),
                        gameplay=gp)
        return out, os.path.basename(gp["path"]) if gp else ""

    campaign = {"campaign": "passed the automatic rule review (speech + text)" if rules else "",
                "manual_checks": rules["manual_checks"] if rules else []}
    results = []
    for c, suffix, tl, name, i in jobs:
        s, e = c["s"], c["e"]
        log(f"Rendering {i}/{len(chosen)} [{c['format']}{suffix}]: {fmt_ts(s)}-{fmt_ts(e)} "
            f"-> {tl.length:.0f}s, score {c.get('score')}")
        out, gameplay = render(tl, name)
        meta = {"file": out, "format": c["format"] + suffix, "start": round(s, 2),
                "end": round(e, 2), "length": round(tl.length, 1), "score": c.get("score"),
                "title": c.get("title", "") + (" (Part 2)" if suffix == "_part2" else ""),
                "hook": c.get("hook", ""), "tease": c.get("tease", ""), "cta": c.get("cta", ""),
                "caption": c.get("caption", ""), "reason": c.get("reason", ""),
                "trigger": c.get("trigger", ""),
                "keywords": c.get("keywords") or [],
                "source": f"SOURCE: {fmt_ts(s + offset)} - {fmt_ts(e + offset)}"
                          + (" (time since the recording started)" if live else
                             " (times in the original video)" if offset else ""),
                **campaign, "gameplay": gameplay}
        _write_meta(os.path.join(out_dir, name + ".txt"), meta)
        results.append(meta)
    if top:
        best, pack, tl = top
        n = len(best)
        name = f"top_{n}_{slug(pack['title'], 40)}"
        log(f"Rendering Top {n} countdown -> {tl.length:.0f}s")
        out, gameplay = render(tl, name)
        meta = {"file": out, "format": f"top_{n}", "start": None, "end": None,
                "length": round(tl.length, 1), "score": None, "title": pack["title"],
                "hook": pack["hook"], "cta": pack["cta"], "caption": pack["caption"],
                "reason": "countdown of: " + ", ".join(m.get("label") or m.get("title", "") for m in best),
                "source": "SOURCES: " + ", ".join(f"{fmt_ts(m['s'])}" for m in best),
                **campaign, "gameplay": gameplay}
        _write_meta(os.path.join(out_dir, name + ".txt"), meta)
        results.append(meta)

    summary = {"source": source, "models": sorted(usage["models"]), "est_cost_usd": round(usage["cost"], 5),
               "seconds": round(time.time() - t0), "clips": results}
    if rules:
        summary["campaign"] = {k: rules[k] for k in rules if k != "text"}
        with open(os.path.join(out_dir, "campaign_rules.txt"), "w", encoding="utf-8") as f:
            f.write(rules["text"] + "\n\n---\nCHECK MANUALLY BEFORE POSTING:\n"
                    + "".join(f"[ ] {x}\n" for x in campaign["manual_checks"]))
    with open(os.path.join(out_dir, f"{prefix}clips.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    summary["deferred"], summary["deferred_start"] = deferred, deferred_start
    log(f"Done in {summary['seconds']}s. {len(results)} clips in {out_dir}"
        + (f" | model: {', '.join(summary['models'])} | est. cost ${usage['cost']:.4f}" if usage["models"] else ""))
    summary["out_dir"] = out_dir
    return summary


# ---------------------------------------------------------------- process() steps, in order
def _gameplay_picker(cfg, log):
    """Split-screen gameplay. Checked first, so a missing gameplay folder doesn't waste a
    download + transcription."""
    if not cfg.get("split_on"):
        return None
    game = cfg.get("split_game") or GP.ANY
    picker = GP.Picker(game)
    log(f"Split-screen: content on top, {('any game' if game == GP.ANY else GP.nice(game))} "
        f"gameplay underneath ({len(picker.dur)} video(s), muted).")
    if picker.skipped:
        log(f"  Skipped unusable gameplay file(s): {', '.join(picker.skipped)}")
    return picker


def _fetch_source(source, cfg, log):
    """A file path (Gradio file objects too) or a link to download. Returns (path, offset), where
    offset is the original video's second the file starts at. Downloading only the From/To window
    uses up the range, so it is cleared from cfg."""
    source = str(getattr(source, "name", None) or source)
    if not re.match(r"https?://", source.strip()):
        return source, 0.0
    a, b = parse_ts(cfg.get("range_start")), parse_ts(cfg.get("range_end"))
    if a is not None and b is not None and b <= a:
        raise ValueError(f"'To' ({fmt_ts(b)}) must be later than 'From' ({fmt_ts(a)}).")
    if a is None and b is None:
        return download(source.strip(), log), 0.0
    path = download(source.strip(), log, start=a or 0.0, end=b)   # the file starts exactly at From
    cfg["range_start"] = cfg["range_end"] = ""
    return path, a or 0.0


def _listen(source, info, cfg, r0, r1, log, live):
    """Transcript (in the source's own seconds), loud spikes and reaction signals for [r0, r1].
    Returns {segments, words, loud, signals, engine, cost}."""
    part = (r0, r1) != (0.0, info["duration"])
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
    # the wav starts at r0: shift every time back to the source's own clock
    segments = [{**sg, "s": sg["s"] + r0, "e": sg["e"] + r0,
                 "words": [{**w, "s": w["s"] + r0, "e": w["e"] + r0} for w in sg["words"]]}
                for sg in tr["segments"]]
    words = [w for s in segments for w in s["words"]]
    loud = [{**m, "t": m["t"] + r0} for m in loud_moments(wav)]
    signals = find_signals(segments, loud, info["duration"], voice=vocal_energy(segments, wav, offset=r0))
    if live and live.get("chat_signals"):
        signals = sorted(signals + list(live["chat_signals"]), key=lambda x: x["t"])
    kinds = {}
    for x in signals:
        kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
    log(f"{len(words)} words transcribed. Reaction signals: "
        + (", ".join(f"{v} {k}" for k, v in kinds.items()) or "none"))
    return {"segments": segments, "words": words, "loud": loud, "signals": signals,
            "language": tr.get("language") or "",
            "engine": tr.get("engine", ""), "cost": float(tr.get("cost") or 0)}


def _load_campaign(cfg, min_len, max_len, log, models):
    """Parse the campaign rules and apply their hard limits. Returns (rules, min_len, max_len)."""
    rules = R.parse_rules(cfg["rules_text"], cfg, log)
    if rules.get("model"):
        models.add(rules["model"])
    log(f"Campaign rules: {R.describe(rules)}")
    if rules["manual_checks"]:
        log(f"  {len(rules['manual_checks'])} rule(s) need a manual check - listed in each clip's .txt")
    min_len, max_len = R.length_window(min_len, max_len, rules, log)
    R.apply_disallowed(cfg, rules, log)
    return rules, min_len, max_len


def _extras(cfg, log):
    """Meme sounds and reaction stickers: (meme library, extra instructions for the clip prompt)."""
    note, meme_lib = "", {}
    if cfg.get("meme_on"):
        meme_lib = MM.library(log)
        if meme_lib:
            log(f"Meme sounds: {len(meme_lib)} in the sounds folder.")
            note += ('\n- "memes": 0-2 trending meme sounds as {"t": <absolute second the sound\'s '
                     'punch must land>, "name": <name>}. Only where it genuinely lands, the way an '
                     'editor would (e.g. fahhh the instant a ball flies just past someone); none is '
                     'better than a forced one; not on top of the creator\'s own punchline words. '
                     'Available:\n' + MM.prompt_list(meme_lib))
        else:
            log("Meme sounds are ON but the sounds folder has no audio files - skipping them.")
    if cfg.get("motion_on") and "stickers" in (cfg.get("motion_elements") or []):
        note += ('\n- "stickers": 0-3 animated reaction stickers as {"t": <absolute seconds>, '
                 '"emoji": <one emoji>, "text": <1-2 word reaction, e.g. "NO WAY", "BRUH", '
                 '"LMAO", "HE\'S COOKED">} on the funniest or most shocking beats, at least 4 s '
                 'apart, not in the first 3 s of the clip')
    return meme_lib, note


def _loudest_candidates(loud, n_clips, min_len):
    """Little speech: clip around the biggest audio spikes (titles are written afterwards)."""
    return [{"start": m["t"] - min_len * 0.6, "end": m["t"] + min_len * 0.4,
             "score": int(min(99, 50 + m["db_over"] * 3)), "title": "",
             "hook": "", "caption": "", "sfx": [{"t": m["t"], "name": "impact"}]}
            for m in sorted(loud, key=lambda m: -m["db_over"])[: n_clips * 2]]


def _ask_for_clips(segments, signals, cfg, r0, r1, n_clips, min_len, max_len, notes, live, log, usage):
    """The AI reads the annotated transcript window by window and proposes clips."""
    wins = _windows(segments, r1, start=r0)
    if len(wins) > 1:
        log(f"Long video: analysing in {len(wins)} parts.")
    sfx_desc = "; ".join(f"{k} = {v}" for k, v in SFX_NAMES.items())
    allowed = [f for f in cfg["formats"] if f in FORMATS] or ["standard"]
    fmts = "\n".join(f"    {f}: {FORMATS[f]}" for f in allowed)
    creator = (cfg.get("creator") or "").strip()
    creator_note = (f"The creator in this video is {creator}. Use the name in some titles/hooks "
                    f"(e.g. \"Watch {creator}...\"), always in spotlight hooks.") if creator else ""
    candidates = []
    for w0, w1 in wins:
        check_stop()
        k = max(2, math.ceil(n_clips * (w1 - w0) / (r1 - r0)) + 1)
        text = annotate(segments, signals, w0, w1)
        if len(text) < 200:
            continue
        spots = hot_spots([x for x in signals if w0 <= x["t"] < w1], top=max(12, 3 * k))
        hot = "\n".join(f"- {sp['t']:.0f}s  strength {sp['score']}  ({', '.join(sorted(sp['kinds']))})"
                        for sp in spots) or "none"
        task = f"Find the {k} best clips inside this window."
        if live:
            k = n_clips
            task = _live_task(live, k)
        prompt = USER_TMPL.format(hint=cfg["content_hint"] or "gaming video", w0=w0, w1=w1, task=task,
                                  min_len=min_len, max_len=max_len, sfx=sfx_desc, formats=fmts,
                                  creator_note=creator_note, notes=notes, language=cfg["_lang"][1],
                                  hot=hot, transcript=text)
        data, model, cost = chat_json(cfg["api_key"], [
            {"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            est_tokens(prompt) + 400, out_tokens=min(12000, 950 * k + 500), model=cfg["model"],
            allow_free=cfg["allow_free"], validate=_validator(w0, w1, min_len, max_len, live, cfg["_lang"][0]),
            log=log)
        usage["models"].add(model)
        usage["cost"] += cost or 0
        log(f"  {model} suggested {len(data['clips'])} clips.")
        candidates += data["clips"]
    return candidates


def _live_task(live, k):
    seen = ""
    if live.get("reviewed"):
        seen += (f"The first {live['reviewed']:.0f}s were already checked last time; pick "
                 f"something there only if it continues past {live['reviewed']:.0f}s. ")
    if live.get("taken"):
        seen += ("Already clipped, never pick again: "
                 + ", ".join(f"{a:.0f}-{b:.0f}s" for a, b in live["taken"]) + ". ")
    return LIVE_TASK.format(min_score=int(live.get("min_score") or 70), k=k,
                            seen=seen + ("\n" if seen else ""), chat=live.get("chat_note") or "")


def _validator(w0, w1, min_len, max_len, live, lang="en"):
    """Quality gate for the AI's answer: tidies usable clips, and rejects lazy/broken answers -
    including text in the wrong language or script - so the next model is tried."""
    def validate(d):
        clips = d.get("clips") if isinstance(d, dict) else None
        if live and isinstance(clips, list) and not clips:
            return          # "nothing viral here" is a valid live answer
        if not clips or not isinstance(clips, list):
            raise ValueError("no clips list")
        ok, wrong_lang = [], 0
        for c in clips:
            if not isinstance(c, dict):
                continue
            c["score"] = _total_score(c)
            for key in ("title", "hook", "caption", "tease", "cta", "label", "format", "reason", "trigger"):
                v = c.get(key)
                if isinstance(v, list):
                    c[key] = " ".join(str(x) for x in v)
                elif v is not None and not isinstance(v, str):
                    c[key] = str(v)
            for key in ("sfx", "stickers", "memes"):
                if not isinstance(c.get(key), list):
                    c[key] = []
            c["keywords"] = [k for k in _clean_keywords(c.get("keywords")) if not _wrong_script(k, lang)]
            if _wrong_script(" ".join(str(c.get(k) or "") for k in ("title", "hook", "caption", "tease",
                                                                     "cta", "label")), lang):
                wrong_lang += 1
                continue
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
        if wrong_lang and not ok:
            raise ValueError("titles/captions in the wrong language")
        need = 0 if live else (1 if (w1 - w0) < 5 * max_len else 2)
        if len(ok) < need:
            raise ValueError(f"only {len(ok)} usable clips (need {need})")
        d["clips"] = ok
    return validate


def _select(candidates, words, r0, r1, min_len, max_len, limit, live):
    """Best first: snap to word boundaries, drop overlaps (and, live, weak or already-clipped
    moments). A live moment running into the end of this part is deferred: the next check sees
    all of it. Returns (chosen, deferred count, earliest deferred start)."""
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
                deferred += 1
                deferred_start = s if deferred_start is None else min(deferred_start, s)
                continue
        chosen.append({**c, "s": s, "e": e})
        if len(chosen) >= limit:
            break
    return chosen, deferred, deferred_start


def _layout(cfg, info, split, log):
    """Frame layout. Returns (filter template, (caption_y, hook_y), logo or None).
    A sponsor logo must never cover the content, so with a logo the crop layout becomes blur
    (the logo sits on the blurred band); split-screen and facecam put it on the gameplay."""
    with_logo = has_logo(cfg)
    layout = "split" if split else cfg["layout"]
    if with_logo and layout == "crop":
        log("Logo is set: using the blur layout so the logo sits on the blurred band, not the gameplay.")
        layout = "blur"
    vf, cap_y, hook_y, logo_y = layout_geometry(layout, info["width"], info["height"],
                                                float(cfg["zoom"]), logo=with_logo,
                                                boxes=cfg.get("facecam"))
    logo = None
    if with_logo:
        png, _, lh = fitted_logo(cfg["logo_path"], float(cfg.get("logo_size") or 1.0))
        logo = {"png": png, "cy": min(logo_y + lh // 2, H - lh // 2 - 24),
                "anim": cfg.get("logo_anim") or "float"}
    return vf, (cap_y, hook_y), logo


def _timelines(chosen, cfg, words, signals, loud, meme_lib, geo, rules, prefix, log):
    """Edit every chosen clip into its format. Returns [(clip, suffix, Timeline, file name, number)]."""
    jobs = []
    for i, c in enumerate(chosen, start=1):
        s, e = c["s"], c["e"]
        cues = _cues(c, s, e, loud) if cfg["sfx_on"] else []
        punches = [x["t"] for x in sorted(signals, key=lambda x: -x["strength"])
                   if s + 1 < x["t"] < e - 1 and x["kind"] in ("exclaim", "loud") and x["strength"] >= 3]
        mcues = MM.clip_cues(c, s, e, meme_lib) if meme_lib else []
        if mcues:
            cues = sorted([x for x in cues if all(abs(x[0] - m[0]) > 1.5 for m in mcues)] + mcues)
        videos = build(c["format"], c, words, cfg, geo, cues, punches)
        if rules and not all(R.length_ok(tl.length, rules) for _, tl in videos):
            if c["format"] != "standard":
                log(f"  clip {i}: '{c['format']}' would break the campaign length limit - using standard.")
                c["format"] = "standard"
                videos = build("standard", c, words, cfg, geo, cues, punches)
            if not all(R.length_ok(tl.length, rules) for _, tl in videos):
                log(f"  clip {i} skipped: {videos[0][1].length:.0f}s is outside the campaign length limit.")
                continue
        for suffix, tl in videos:
            _settle_sfx(tl, cfg["sfx_on"])
            name = f"{prefix}clip_{i:02d}_{c['format']}_{slug(c.get('title'), 34)}{suffix}"
            jobs.append((c, suffix, tl, name, i))
    return jobs


def _top_n(chosen, cfg, words, geo, rules, log, models):
    """The Top-N countdown: (moments, packaging, Timeline), or None when it can't be made."""
    n = max(3, min(int(cfg["top_n"]), len(chosen)))
    best = chosen[:n]
    pack = _top_n_package(best, cfg, log, models, rules)
    if pack is None:
        log("Top-N countdown skipped: its title/caption couldn't be made rule-compliant.")
        return None
    tl = build_top_n(best, words, cfg, geo, pack["hook"], pack["cta"])
    while rules and rules["max_len"] and tl.length > rules["max_len"] + 0.5 and len(best) > 3:
        best = best[:-1]
        tl = build_top_n(best, words, cfg, geo, pack["hook"], pack["cta"])
    if rules and not R.length_ok(tl.length, rules):
        log(f"Top-N countdown skipped: {tl.length:.0f}s is outside the campaign length limit.")
        return None
    if not cfg["sfx_on"]:
        tl.sfx = []
    return best, pack, tl


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
              f"'caption' (1-2 sentences + 4-6 hashtags). Reply JSON with those 4 keys.\n"
              + _lang_line(cfg))
    if rules:
        prompt += "\nAll text must follow these campaign rules:\n" + R.prompt_block(rules)
    try:
        data, model, _ = chat_json(cfg["api_key"], [{"role": "user", "content": prompt}],
                                   500 + (len(rules["text"]) // 3 if rules else 0), 600,
                                   cfg["model"], cfg["allow_free"], log=log,
                                   validate=_pack_validator(cfg))
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


def _lang_line(cfg):
    return (cfg.get("_lang") or (None, ""))[1]


def _pack_validator(cfg):
    def validate(d):
        if not isinstance(d, dict) or not d.get("title"):
            raise ValueError("no title")
        if _wrong_script(" ".join(str(d.get(k) or "") for k in ("title", "hook", "cta", "caption")),
                         (cfg.get("_lang") or ("en",))[0]):
            raise ValueError("wrote in the wrong language")
    return validate


def _settle_sfx(tl, sfx_on):
    """Meme sounds win: drop regular effects within 1.2 s of one (they'd clash); with sound
    effects off, only the meme sounds stay."""
    memes = [t for t, n in tl.sfx if n.startswith("meme:")]
    tl.sfx = [(t, n) for t, n in tl.sfx
              if n.startswith("meme:") or (sfx_on and all(abs(t - m) > 1.2 for m in memes))]


def _write_titles_without_transcript(chosen, cfg, log, models_used):
    prompt = (f"Video: {cfg['content_hint']}. These clips were cut at the loudest moments "
              f"(there is little speech). For each clip id write a viral but honest 'title' "
              f"(max 70 chars), 'hook' (max 6 words) and 'caption' (1 sentence + 4-6 hashtags). "
              f"Keep them generic enough to be true without seeing the clip. "
              f'Reply JSON: {{"clips":[{{"id":1,"title":"...","hook":"...","caption":"..."}}]}}\n'
              f"Clip ids: {list(range(1, len(chosen) + 1))}\n" + _lang_line(cfg))
    lang = (cfg.get("_lang") or ("en",))[0]
    try:
        data, model, _ = chat_json(cfg["api_key"], [{"role": "user", "content": prompt}], 600,
                                   1500, cfg["model"], cfg["allow_free"], log=log)
        models_used.add(model)
        by_id = {int(c.get("id", 0)): c for c in data.get("clips", []) if isinstance(c, dict)}
        for i, c in enumerate(chosen, start=1):
            for k in ("title", "hook", "caption"):
                v = str((by_id.get(i) or {}).get(k, "") or "")
                c[k] = v if v and not _wrong_script(v, lang) else c.get(k, "")
    except Exception as e:
        log(f"Could not generate titles ({e}); clips will be rendered without them.")
