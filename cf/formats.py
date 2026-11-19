"""Viral short-form formats. Each builder turns a chosen moment into a timeline.

Formats (all researched patterns for gaming/stream clips):
  standard     mid-action start + hook text, word captions
  cold_open    payoff first (1-3 s) with a curiosity line, rewind, then the build-up;
               the video ends on the same payoff, so it loops naturally
  freeze_open  "you're probably wondering how we got here": play into the peak,
               freeze in black & white + record scratch + text, then rewind
  replay       full moment, then an instant replay of the peak in slow motion, zoomed
  wait_for_it  open loop: a "WAIT FOR IT..." banner until the payoff lands, then impact
  spotlight    "Watch <creator>..." hook + persistent creator credit label
  two_part     Part 1 ends on a cliffhanger, Part 2 starts with the payoff (2 videos)
  top_n        countdown compilation of the best N moments (#N ... #1)
Retention extras: progress bar, punch-in zooms on the strongest reactions,
comment-bait question at the end.
"""
from .captions import caption_events, clean, event, hook_event
from .render import Segment

FORMATS = {
    "standard": "plain clip, best when the moment is self-explanatory from second 1",
    "cold_open": "a big visual/emotional payoff (crash, kill, scream, fail) that makes people ask 'how did that happen?'",
    "freeze_open": "absurd or chaotic situations, 'how did we end up here' energy, comedic disasters",
    "replay": "a fast or skillful play people want to see again (insane drive, clutch, near-miss)",
    "wait_for_it": "a slow build to a sudden payoff (prank, unexpected event, jump scare)",
    "spotlight": "the streamer does something impressive or iconic; credits the creator",
    "two_part": "a longer story (35s+) with a natural cliffhanger in the middle",
}


class Timeline:
    def __init__(self):
        self.segments, self.events, self.sfx = [], [], []
        self.overlays = []  # animated overlays (motion.py), output time
        self.groups = []  # (out_start, Segment) for src->out mapping

    @property
    def length(self):
        return sum(s.dur for s in self.segments)

    def add(self, seg):
        start = self.length
        self.segments.append(seg)
        self.groups.append((start, seg))
        return start

    def out_time(self, src_t, after=0.0):
        """Map a source time to output time (first segment at/after `after` containing it)."""
        for start, seg in self.groups:
            if start + 1e-6 < after:
                continue
            if seg.src_s - 1e-6 <= src_t <= seg.src_e + 1e-6:
                return start + (src_t - seg.src_s) / seg.speed
        return None

    def words(self, words):
        out = []
        for start, seg in self.groups:
            if not seg.captions:
                continue
            for w in words:
                if seg.src_s - 0.05 <= w["s"] and w["e"] <= seg.src_e + 0.05:
                    out.append({"w": w["w"], "src": w["s"], "s": start + (w["s"] - seg.src_s) / seg.speed,
                                "e": start + (min(w["e"], seg.src_e) - seg.src_s) / seg.speed})
        return out


def _body(tl, s, e, punches, punch_on=True):
    """Add the main moment, splitting out short zoomed punch-ins on big reactions."""
    cuts = []
    if punch_on:
        for t in sorted(punches)[:2]:
            a, b = t - 0.15, t + 1.3
            if a > s + 1.0 and b < e - 0.5 and all(abs(a - x[0]) > 3 for x in cuts):
                cuts.append((a, b))
    start = tl.length
    cur = s
    for a, b in cuts:
        tl.add(Segment(cur, a))
        tl.add(Segment(a, b, zoom=1.18))
        cur = b
    tl.add(Segment(cur, e))
    return start


def _sfx_from_cues(tl, cues, after=0.0):
    for t, name in cues:
        o = tl.out_time(t, after)
        if o is not None:
            tl.sfx.append((o, name))


def _cta(tl, text, y):
    """Comment-bait question over the last 2.6 s, in the top area (clear of gameplay/UI)."""
    if text:
        L = tl.length
        tl.events.append(event(max(0, L - 2.6), L, "Cta", clean(text), y=y,
                               tags="\\fad(150,0)\\t(0,150,\\fscx110\\fscy110)\\t(150,280,\\fscx100\\fscy100)"))


def build(fmt, clip, words, cfg, geo, cues, punches):
    """Returns a list of (suffix, Timeline). Most formats give one video; two_part gives two."""
    s, e, p = clip["s"], clip["e"], clip["payoff"]
    cap_y, hook_y = geo
    hook = clip.get("hook") or ""
    tease = clip.get("tease") or hook or "WAIT FOR IT"
    cta = clip.get("cta") if cfg.get("cta_on", True) else ""
    hl = clip.get("_highlight", "yellow")
    wpl = int(cfg.get("words_per_line", 3))
    punch_on = cfg.get("punch_on", True)
    creator = (cfg.get("creator") or "").strip()
    tl = Timeline()

    def finish(t, with_cta=True):
        t.events = caption_events(t.words(words), cap_y, hl, wpl) + t.events
        if creator and fmt != "spotlight":
            t.events.append(event(0, t.length, "Label", clean("@" + creator.lstrip("@"), False),
                                  y=hook_y - 120, tags="\\alpha&H40&"))
        if with_cta:
            _cta(t, cta, hook_y)
        return t

    if fmt == "cold_open":
        t0 = max(s, p - 0.6)
        t1 = min(e, p + 2.2)
        tl.add(Segment(t0, t1))
        tl.events.append(event(0, t1 - t0, "Tease", clean(tease), y=hook_y,
                               tags="\\fad(80,0)\\t(0,150,\\fscx115\\fscy115)\\t(150,300,\\fscx100\\fscy100)"))
        tl.sfx += [(0.0, "deep_boom"), (t1 - t0 - 0.35, "rewind")]
        b0 = _body(tl, s, e, punches, punch_on)
        tl.events.append(event(b0, b0 + 1.6, "Label", "EARLIER...", y=hook_y, tags="\\fad(0,200)"))
        tl.sfx.append((b0, "glitch"))
        _sfx_from_cues(tl, cues, after=b0)
        return [("", finish(tl))]

    if fmt == "freeze_open":
        t0 = max(s, p - 1.4)
        tl.add(Segment(t0, p))
        fz = tl.add(Segment(p, p + 0.05, freeze=1.9, gray=True, captions=False))
        tl.events.append(event(fz, fz + 1.95, "Tease",
                               clean(clip.get("tease") or "YOU'RE PROBABLY WONDERING HOW WE GOT HERE"),
                               y=960, tags="\\fad(60,0)"))
        tl.sfx += [(fz, "record_scratch")]
        b0 = _body(tl, s, e, punches, punch_on)
        tl.events.append(event(b0, b0 + 1.6, "Label", "EARLIER...", y=hook_y, tags="\\fad(0,200)"))
        _sfx_from_cues(tl, cues, after=b0)
        return [("", finish(tl))]

    if fmt == "replay":
        _body(tl, s, e, punches, punch_on)
        if hook:
            tl.events.append(hook_event(hook, 0, 3.2, hook_y))
            tl.sfx.append((0.0, "swoosh_short"))
        _sfx_from_cues(tl, cues)
        r0 = max(s, p - 1.6)
        r1 = min(e, p + 1.0)
        rs = tl.add(Segment(r0, r1, speed=0.5, zoom=1.22, captions=False))
        tl.events.append(event(rs, rs + (r1 - r0) / 0.5, "Label", "REPLAY  x0.5", x=190, y=hook_y + 235,
                               tags="\\fad(100,100)"))
        tl.sfx += [(rs, "rewind"), (rs + (p - r0) / 0.5, "impact")]
        return [("", finish(tl))]

    if fmt == "wait_for_it":
        _body(tl, s, e, punches, punch_on)
        po = tl.out_time(p) or tl.length * 0.7
        banner = clean(clip.get("tease") or "WAIT FOR IT...")
        tl.events.append(event(0, max(1.0, po - 0.2), "Banner", banner, y=hook_y,
                               tags="\\t(0,500,\\alpha&H30&)\\t(500,1000,\\alpha&H00&)"))
        tl.sfx += [(max(0, po - 2.0), "riser"), (po, "deep_boom")]
        _sfx_from_cues(tl, [(t, n) for t, n in cues if abs(t - p) > 1.5 or n.startswith("meme:")])
        return [("", finish(tl))]

    if fmt == "spotlight":
        _body(tl, s, e, punches, punch_on)
        tl.events.append(hook_event(hook, 0, 3.4, hook_y))
        if creator:
            tl.events.append(event(0, tl.length, "Label", clean("@" + creator.lstrip("@"), False),
                                   y=hook_y + 110 if hook_y < 700 else hook_y - 110,
                                   tags="\\alpha&H20&"))
        tl.sfx.append((0.0, "bass_drop"))
        _sfx_from_cues(tl, cues)
        return [("", finish(tl))]

    if fmt == "two_part" and e - s >= 30:
        cut = p if s + 12 < p < e - 8 else s + (e - s) * 0.55
        t1 = Timeline()
        _body(t1, s, cut, punches, punch_on)
        t1.events.append(hook_event(hook, 0, 3.2, hook_y))
        fz = t1.add(Segment(cut, cut + 0.05, freeze=1.6, captions=False))
        t1.events.append(event(fz, fz + 1.65, "Cta", "PART 2 ON MY PAGE", y=hook_y))
        t1.events.append(event(0, t1.length, "Label", "PART 1", x=150, y=hook_y - 120))
        t1.sfx += [(0.0, "swoosh_short"), (fz, "riser")]
        _sfx_from_cues(t1, [(t, n) for t, n in cues if t < cut])
        t2 = Timeline()
        _body(t2, max(s, cut - 3.0), e, punches, punch_on)
        t2.events.append(event(0, t2.length, "Label", "PART 2", x=150, y=hook_y - 120))
        t2.events.append(hook_event(clip.get("tease") or "HERE'S WHAT HAPPENED", 0, 2.8, hook_y))
        t2.sfx.append((0.0, "whoosh"))
        _sfx_from_cues(t2, [(t, n) for t, n in cues if t >= cut - 3])
        return [("_part1", finish(t1, with_cta=False)), ("_part2", finish(t2))]

    # standard (also the fallback)
    _body(tl, s, e, punches, punch_on)
    if hook:
        tl.events.append(hook_event(hook, 0, 3.2, hook_y))
        tl.sfx.append((0.0, "swoosh_short"))
    _sfx_from_cues(tl, cues)
    return [("", finish(tl))]


def build_top_n(moments, words, cfg, geo, title, cta):
    """moments: best-first list of clip dicts (s, e, payoff, label). Plays #N ... #1."""
    cap_y, hook_y = geo
    tl = Timeline()
    n = len(moments)
    for rank_idx, m in enumerate(reversed(moments)):
        rank = n - rank_idx
        p = m["payoff"]
        a = max(m["s"], p - 6.5)
        b = min(m["e"], p + 2.8)
        if b - a < 4:
            a, b = m["s"], min(m["e"], m["s"] + 9)
        st = tl.add(Segment(a, b))
        tl.events.append(event(st, st + 1.7, "Card", f"#{rank}", y=hook_y + 40,
                               tags="\\fad(0,250)\\t(0,140,\\fscx120\\fscy120)\\t(140,260,\\fscx100\\fscy100)"))
        if m.get("label"):
            tl.events.append(event(st + 0.2, st + 2.6, "Hook", clean(m["label"]), y=hook_y + 210,
                                   tags="\\fad(100,200)"))
        tl.sfx.append((st, "whoosh" if rank_idx else "bass_drop"))
        po = tl.out_time(p, after=st)
        if po is not None and po - st > 2.8:
            tl.sfx.append((po, "impact"))
        if rank == 1 and st >= 2.2:
            tl.sfx.append((st - 2.0, "riser"))  # build into #1
    if title:
        tl.events.append(event(0, 2.4, "Banner", clean(title), y=hook_y - 140, tags="\\fad(100,200)"))
    tl.events = caption_events(tl.words(words), cap_y, "yellow", int(cfg.get("words_per_line", 3))) + tl.events
    if cfg.get("cta_on", True):
        _cta(tl, cta or "WHICH ONE WAS THE CRAZIEST?", hook_y)
    return tl
