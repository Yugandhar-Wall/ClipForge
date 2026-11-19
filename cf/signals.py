"""Reads the streamer's own words and voice for reaction signals: the moments viewers watch.

The streamer is the main source. This marks:
  exclaim   - "oh no", "no way", "ain't no way", "he's cooked", swearing, "nooo", repeated words
  laugh     - haha / lol / (laughs)
  shout     - a line said much louder than the streamer's normal speaking level
  pause     - a sudden silence in the middle of talking (stunned / dramatic beat)
  fast      - talking much faster than usual (panic, excitement, chase)
  loud      - audio spike of any kind (from hype.py: screams, crashes, explosions)
Live chat bursts (chat.py) are added on top as a weaker hint. Each signal has a strength; the
LLM sees them inline in the transcript and as a ranked "hot spots" list, then judges with the
actual words.
"""
import bisect
import re

import numpy as np

from .audio import read_speech

STRONG = [
    r"oh no+", r"oh my (god|gosh|lord)", r"\bomg\b", r"no+ way", r"what the (hell|heck|fuck|f)",
    r"\bwtf\b", r"\bholy\b", r"let'?s go+", r"are you (kidding|serious)", r"you'?re kidding",
    r"\bno{3,}\b", r"\ba+h{2,}\b", r"\bah+\b!", r"oh (shit|crap|god)", r"\bshit\b", r"\bfuck",
    r"\bjesus\b", r"i'?m dead", r"\bbruh\b", r"get out",
    r"what just happened", r"did you see that", r"\bclip (it|that)\b",
    r"\bnah+\b", r"\bbro what\b", r"\bwhy+\b\?!", r"\boh teri\b", r"\barre+\b", r"\bkya\b.*\?!",
    # how streamers react now
    r"ain'?t no way", r"\bno shot\b", r"\bon god\b", r"\b(he|she|i|we|you)'?(s|m|re)? (so )?cooked\b",
    r"chat,? (is|was) (this|that) real", r"what is (bro|he|she|this guy) doing", r"\bbro (really|actually|just)\b",
    r"\bdeadass\b", r"\bcrash(ing)? ?out\b", r"\bi'?m (crying|weak|dying|shaking)\b", r"\bhell nah+\b",
    r"\bhold (up|on)\b!", r"\brun it back\b", r"\bthat'?s (crazy|insane|wild)\b", r"\bno way bro\b",
    r"\bwhat (did|do) (i|you|he) just\b", r"\bi can'?t believe\b", r"\bare you serious\b", r"\bgo+ go+ go+\b",
    # Hindi (Devanagari) - Whisper writes Hindi speech in Devanagari
    "अरे+", "ओ+ह? तेरी", "बाप रे", "हे भगवान", "अबे", "भागो", "मर गया", "मर गए", "गया काम से",
    "नही+ं?!", "क्या\\?!", "ओ+ह? नो", "ओ+ माय गॉड", "लेट्स गो", "भाई+ ?!", "चलो चलो",
]
WEAK = ["भाई", "यार", "क्या", "रुको", "देखो", "वाह",
        r"\bwhat\b\?", r"\bwait\b", r"\bbro\b", r"\bdude\b", r"\byo+\b", r"\bhuh\b", r"\bwow\b",
        r"\boh\b", r"\byes+\b!", r"\bgo\b!", r"\bbhai\b", r"\brun\b", r"\bhelp\b",
        r"\bdamn\b", r"\bcrazy\b", r"\binsane\b", r"\bchat\b", r"\bfr\b", r"\bno cap\b",
        r"\block(ed)? in\b", r"\bgoated\b", r"\blet him cook\b", r"\bsus\b", r"\bwild\b", r"\bbro\b\?"]
LAUGH = [r"\b(ha){2,}", r"\blo+l\b", r"\blmao\b", r"\(laugh", r"\[laugh", r"\bhehe", "(हा){2,}", "हाहा"]

_strong = re.compile("|".join(STRONG), re.I)
_weak = re.compile("|".join(WEAK), re.I)
_laugh = re.compile("|".join(LAUGH), re.I)


_STOP = {"the", "a", "an", "and", "to", "i", "it", "uh", "um", "like", "you", "of", "in", "we",
         "that", "so", "is", "he", "she", "they", "this", "my", "on", "with", "for"}


def _repeat(words):
    """'no no no', 'go go go' -> panic/hype."""
    ws = [re.sub(r"[^\w']", "", w["w"].lower()) for w in words]
    for i in range(len(ws) - 2):
        if ws[i] and ws[i] not in _STOP and ws[i] == ws[i + 1] == ws[i + 2]:
            return True
    return False


def vocal_energy(segments, wav, offset=0.0):
    """'shout' signals: lines said 6+ dB louder than the streamer's median speaking level.
    Segment times are in source seconds; the wav starts at `offset`."""
    try:
        audio = read_speech(wav)
    except Exception:
        return []
    sr = 16000
    levels = []
    for seg in segments:
        a = int((seg["s"] - offset) * sr)
        b = int((seg["e"] - offset) * sr)
        part = audio[max(0, a):max(0, b)]
        if len(part) < sr * 0.3:
            levels.append(None)
            continue
        # loudest 0.25 s inside the line: a shout is short, the rest of the line may be quiet
        n = int(sr * 0.25)
        frames = part[: len(part) // n * n].reshape(-1, n) if len(part) >= n else part[None, :]
        rms = np.sqrt((frames ** 2).mean(axis=1)).max()
        levels.append(20 * np.log10(rms + 1e-9))
    known = [x for x in levels if x is not None]
    if len(known) < 8:
        return []
    base = float(np.median(known))
    out = []
    for seg, lv in zip(segments, levels):
        if lv is not None and lv - base >= 6:
            out.append({"t": seg["s"], "kind": "shout", "strength": round(min(4.0, 1.0 + (lv - base - 6) / 2), 1),
                        "note": f"+{lv - base:.0f}dB voice"})
    return out


def find_signals(segments, loud, total, voice=None):
    """voice: optional 'shout' signals from vocal_energy()."""
    sig = list(voice or [])  # dicts: t, kind, strength, note
    for seg in segments:
        text = seg["text"]
        st = 0.0
        notes = []
        for m in _strong.finditer(text):
            st += 2.0
            notes.append(m.group(0).strip())
        weak = len(_weak.findall(text))
        st += 0.6 * min(weak, 3)
        if "!" in text:
            st += 0.8
        if _repeat(seg["words"]):
            st += 1.5
            notes.append("repeated words")
        if st >= 1.4:
            sig.append({"t": seg["s"], "kind": "exclaim", "strength": round(min(st, 6), 1),
                        "note": ", ".join(dict.fromkeys(notes))[:60]})
        if _laugh.search(text):
            sig.append({"t": seg["s"], "kind": "laugh", "strength": 2.0, "note": ""})

    words = [w for s in segments for w in s["words"]]
    # pauses: silence between words while the person was clearly mid-conversation
    starts = [w["s"] for w in words]
    for a, b in zip(words, words[1:]):
        gap = b["s"] - a["e"]
        if 1.8 <= gap <= 12:
            before = bisect.bisect_right(starts, a["e"]) - bisect.bisect_left(starts, a["e"] - 8)
            after = bisect.bisect_right(starts, b["s"] + 8) - bisect.bisect_left(starts, b["s"])
            if before >= 6 and after >= 3:
                sig.append({"t": round(a["e"], 1), "kind": "pause",
                            "strength": round(min(1.0 + gap / 2, 4.0), 1),
                            "note": f"{gap:.1f}s silence"})

    # fast talking compared with this video's normal pace
    if words and total > 30:
        bins = np.zeros(int(total // 2) + 1)
        for w in words:
            bins[min(int(w["s"] // 2), len(bins) - 1)] += 1
        rate = np.convolve(bins, np.ones(3) / 3, mode="same")  # words per 6s / 3
        talking = rate[rate > 0.5]
        if talking.size > 20:
            base = np.median(talking)
            hot = np.where(rate > base * 1.7)[0]
            last = -99
            for i in hot:
                t = i * 2.0
                if t - last > 15:
                    sig.append({"t": float(t), "kind": "fast",
                                "strength": round(float(min(rate[i] / base, 3.0)), 1),
                                "note": "rapid talking"})
                    last = t

    for m in loud:
        sig.append({"t": m["t"], "kind": "loud", "strength": round(min(m["db_over"] / 3, 4), 1),
                    "note": f"+{m['db_over']}dB"})
    return sorted(sig, key=lambda s: s["t"])


def hot_spots(signals, window=20.0, top=25):
    """Merge nearby signals; places where several kinds stack up are the best bets."""
    spots = []
    for s in signals:
        if spots and s["t"] - spots[-1]["end"] <= window / 2 and s["t"] - spots[-1]["t"] <= window:
            sp = spots[-1]
            sp["end"] = s["t"]
            sp["score"] += s["strength"]
            sp["kinds"].add(s["kind"])
        else:
            spots.append({"t": s["t"], "end": s["t"], "score": s["strength"], "kinds": {s["kind"]}})
    for sp in spots:
        sp["score"] = round(sp["score"] * (1 + 0.35 * (len(sp["kinds"]) - 1)), 1)
    return sorted(spots, key=lambda x: -x["score"])[:top]


def annotate(segments, signals, w0, w1):
    """Transcript lines with inline tags, e.g. [812.4] oh no no no  <<exclaim:oh no | loud>>"""
    pauses = [s for s in signals if s["kind"] == "pause"]
    lines = []
    seg_list = [s for s in segments if w0 <= s["s"] < w1]
    for i, seg in enumerate(seg_list):
        end = seg_list[i + 1]["s"] if i + 1 < len(seg_list) else seg["e"] + 1
        tags = []
        for s in signals:
            if s["kind"] != "pause" and seg["s"] - 0.5 <= s["t"] < end:
                tags.append(s["kind"] + (f":{s['note']}" if s["note"] and s["kind"] in ("exclaim", "chat", "shout") else ""))
        line = f"[{seg['s']:.1f}] {seg['text']}"
        if tags:
            line += "  <<" + " | ".join(dict.fromkeys(tags)) + ">>"
        lines.append(line)
        for p in pauses:
            if seg["e"] - 0.6 <= p["t"] < end:
                lines.append(f"[{p['t']:.1f}] ...({p['note']})...")
    return "\n".join(lines)
