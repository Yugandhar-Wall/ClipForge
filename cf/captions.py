"""ASS subtitle building: word-by-word captions + text overlays (hooks, cards, labels).

All times here are OUTPUT times (seconds in the finished short). render.py slices
the event list per segment.
"""
import re

FONT = "Montserrat ExtraBold"

# ASS colours are &HAABBGGRR
COLORS = {
    "yellow": "&H0000E5FF",
    "green": "&H0041F25B",
    "cyan": "&H00FFE100",
    "pink": "&H00B469FF",
    "orange": "&H00008CFF",
    "white": "&H00FFFFFF",
}
AUTO_CYCLE = ["yellow", "green", "cyan", "pink", "orange"]

HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{FONT},86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,7,3,5,60,60,0,1
Style: Hook,{FONT},72,&H00000000,&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,18,0,5,70,70,0,1
Style: Tease,{FONT},96,&H0000E5FF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,9,4,5,60,60,0,1
Style: Card,{FONT},260,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,14,6,5,40,40,0,1
Style: Label,{FONT},50,&H00FFFFFF,&H00FFFFFF,&H00202020,&H00202020,-1,0,0,0,100,100,1,0,3,12,0,5,40,40,0,1
Style: Banner,{FONT},70,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,2,0,1,7,3,5,60,60,0,1
Style: Cta,{FONT},64,&H00FFFFFF,&H00FFFFFF,&H001E1EE6,&H001E1EE6,-1,0,0,0,100,100,0,0,3,20,0,5,70,70,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# emoji / symbols the bundled font has no glyph for (would render as boxes)
_NO_GLYPH = re.compile("[\U00010000-\U0010FFFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]")


def clean(text, upper=True):
    t = _NO_GLYPH.sub("", text or "")
    t = t.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t.upper() if upper else t


def ts(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def event(t0, t1, style, text, x=540, y=960, tags="", layer=1):
    """Generic positioned overlay event (output time)."""
    return {"t0": t0, "t1": t1, "line": (layer, style, f"{{\\pos({x},{y}){tags}}}{text}")}


def caption_events(words, y, highlight="yellow", words_per_line=3, upper=True):
    """words: [{'w','s','e'}] already in OUTPUT time."""
    hi = COLORS.get(highlight, COLORS["yellow"])
    groups, cur = [], []
    for w in words:
        jump = ("src" in w and "src" in cur[-1] if cur else False) and \
            abs((w["src"] - cur[-1]["src"]) - (w["s"] - cur[-1]["s"])) > 0.3
        if cur and (len(cur) >= words_per_line or w["s"] - cur[-1]["e"] > 0.7 or jump
                    or cur[-1]["w"].endswith((".", "?", "!"))):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    out = []
    for gi, g in enumerate(groups):
        g_end = g[-1]["e"] + 0.15
        if gi + 1 < len(groups):  # never overlap the next line
            g_end = min(g_end, groups[gi + 1][0]["s"])
        for i, w in enumerate(g):
            start = w["s"]
            end = g[i + 1]["s"] if i + 1 < len(g) else g_end
            if end <= start:
                end = start + 0.08
            parts = []
            for j, x in enumerate(g):
                t = clean(x["w"], upper)
                if not t:
                    continue
                if j == i:
                    parts.append(f"{{\\c{hi}\\fscx112\\fscy112}}{t}{{\\c&H00FFFFFF&\\fscx100\\fscy100}}")
                else:
                    parts.append(t)
            if not parts:
                continue
            pop = "\\t(0,90,\\fscx104\\fscy104)\\t(90,160,\\fscx100\\fscy100)" if i == 0 else ""
            out.append({"t0": start, "t1": end,
                        "line": (0, "Cap", f"{{\\pos(540,{y}){pop}}}{' '.join(parts)}")})
    return out


def hook_event(text, t0, t1, y):
    return event(t0, t1, "Hook", clean(text), y=y,
                 tags="\\fad(120,250)\\t(0,180,\\fscx108\\fscy108)\\t(180,320,\\fscx100\\fscy100)")


_DEVANAGARI = re.compile("[\u0900-\u097F]")
DEVANAGARI_FONT = "Noto Sans Devanagari"


def _font_fix(text):
    """Montserrat has no Hindi letters: switch any line containing them to Noto Devanagari."""
    if _DEVANAGARI.search(text):
        i = text.find("}")
        # \\fsp0: letter spacing breaks Hindi shaping (vowel marks detach from letters)
        tag = "{\\fn" + DEVANAGARI_FONT + "\\fsp0}"
        return text[:i + 1] + tag + text[i + 1:] if i >= 0 else tag + text
    return text


def ass_for_window(events, w0, w1):
    """Events overlapping [w0, w1) shifted to segment-local time."""
    lines = []
    for e in events:
        a, b = max(e["t0"], w0), min(e["t1"], w1)
        if b - a < 0.02:
            continue
        layer, style, text = e["line"]
        # an overlay that started earlier must not replay its intro animation
        if e["t0"] < w0 - 0.05:
            text = re.sub(r"\\fad\(\d+,(\d+)\)", r"\\fad(0,\1)", text)
            text = re.sub(r"\\t\([^)]*\)", "", text)
        text = _font_fix(text)
        lines.append(f"Dialogue: {layer},{ts(a - w0)},{ts(b - w0)},{style},,0,0,0,,{text}")
    return HEADER + "\n".join(lines) + "\n"
