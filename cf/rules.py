"""Campaign rules: read them, turn the checkable ones into hard limits, and review clips.

Flow:
  1. read_rules_file()  - .txt/.md/.pdf/.docx -> plain text (the UI shows it for editing)
  2. parse_rules()      - AI turns the text into limits the software enforces by itself
                          (length, required tags, banned words, banned edits) plus a list of
                          rules only a human can check (what is on screen, posting rules...)
  3. the full text also goes into the clip-picking prompt
  4. review_clips()     - a separate AI pass judges every picked clip (its transcript and
                          all generated text) against the rules; anything not passing is dropped
  5. enforce_text()     - required hashtags/mentions/phrases are added to the caption,
                          banned words are checked in speech and in every generated text
"""
import hashlib
import json
import os
import re
import zipfile

from .llm import chat_json
from .tools import CACHE_DIR

MAX_RULES_CHARS = 15000

FEATURES = {
    "sound_effects": "added sound effects",
    "end_question": "a question shown at the end asking viewers to comment",
    "zoom_effects": "punch-in zooms / camera zoom effects",
    "progress_bar": "a progress bar at the top",
    "countdown": "a 'Top N' compilation that combines several moments into one video",
}


def read_rules_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise RuntimeError("Reading PDFs needs the 'pypdf' package: run setup.bat again.") from e
        text = "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    elif ext == ".docx":
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<w:tab/>", "\t", xml)
        text = re.sub(r"<[^>]+>", "", xml)
        text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&apos;", "'"))
    else:
        with open(path, "rb") as f:
            raw = f.read()
        for enc in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", "replace")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise RuntimeError("No text found in that file (a scanned PDF? paste the rules instead).")
    return text


PARSE_PROMPT = """These are the rules of a paid clipping campaign. I cut short vertical clips (TikTok/Reels/Shorts) from long videos automatically. Turn the rules into settings my software can enforce by itself, and list everything else as manual checks.

My software can: choose which moments are clipped (it reads the transcript, it can NOT see the picture), set clip length, write titles/captions/hashtags/on-screen hook text, add a sponsor logo, and use these edit styles and extras:
{features}

Reply with ONLY a JSON object:
{{
 "summary": "2-3 sentences: what the campaign wants",
 "min_len": seconds or null,   "max_len": seconds or null,     (final video length limits, only if stated)
 "required_hashtags": ["#tag"], "required_mentions": ["@name"],
 "required_caption_text": ["exact text that must be in the post caption, e.g. #ad or 'link in bio'"],
 "banned_words": ["words/phrases that must never be said in the clip or written in its text"],
 "content_focus": "what kind of moments/topics the clips must show (from the rules)",
 "content_avoid": "what must not appear or be talked about",
 "text_rules": "rules for titles, captions and on-screen text (language, tone, claims, credit lines...)",
 "disallowed": ["names from my list above that the rules forbid"],
 "manual_checks": ["each rule my software cannot check from speech or text: what is on screen, posting/account/payment rules, music, watermarks, etc. - short, one rule per item"]
}}
Use null / [] / "" when the rules say nothing. Do not invent rules.

RULES:
{rules}"""


def _features_text():
    from .formats import FORMATS
    lines = [f"- {k}: {v}" for k, v in FORMATS.items()]
    lines += [f"- {k}: {v}" for k, v in FEATURES.items()]
    return "\n".join(lines)


def _str_list(v):
    if isinstance(v, str):
        v = [x for x in re.split(r"[,\n]", v)]
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()]


def _num(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def parse_rules(text, cfg, log=print):
    """Returns the structured rules dict (cached per rules text)."""
    text = text.strip()[:MAX_RULES_CHARS]
    key = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
    cache = os.path.join(CACHE_DIR, f"rules_{key}.json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    log("Reading the campaign rules...")

    def validate(d):
        if not isinstance(d, dict) or not str(d.get("summary", "")).strip():
            raise ValueError("no summary")

    data, model, _ = chat_json(cfg["api_key"], [{"role": "user", "content": PARSE_PROMPT.format(
        features=_features_text(), rules=text)}], in_tokens=len(text) // 3 + 900, out_tokens=1500,
        model=cfg["model"], allow_free=cfg["allow_free"], validate=validate, log=log)
    from .formats import FORMATS
    known = set(FORMATS) | set(FEATURES)
    r = {
        "text": text,
        "summary": str(data.get("summary", "")).strip(),
        "min_len": _num(data.get("min_len")), "max_len": _num(data.get("max_len")),
        "required_hashtags": ["#" + t.lstrip("#") for t in _str_list(data.get("required_hashtags"))],
        "required_mentions": ["@" + t.lstrip("@") for t in _str_list(data.get("required_mentions"))],
        "required_caption_text": _str_list(data.get("required_caption_text")),
        "banned_words": _str_list(data.get("banned_words")),
        "content_focus": str(data.get("content_focus") or "").strip(),
        "content_avoid": str(data.get("content_avoid") or "").strip(),
        "text_rules": str(data.get("text_rules") or "").strip(),
        "disallowed": [x for x in (s.lower() for s in _str_list(data.get("disallowed"))) if x in known],
        "manual_checks": _str_list(data.get("manual_checks")),
        "model": model,
    }
    if r["min_len"] and r["max_len"] and r["min_len"] > r["max_len"]:
        r["min_len"], r["max_len"] = r["max_len"], r["min_len"]
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(r, f, indent=2, ensure_ascii=False)
    return r


def describe(r):
    """Short human summary for the log."""
    bits = []
    if r["min_len"] or r["max_len"]:
        bits.append(f"length {r['min_len'] or '-'}-{r['max_len'] or '-'}s")
    for k, label in (("required_hashtags", "tags"), ("required_mentions", "mentions"),
                     ("required_caption_text", "caption text"), ("banned_words", "banned words")):
        if r[k]:
            bits.append(f"{label}: {', '.join(r[k][:6])}")
    if r["disallowed"]:
        bits.append("not allowed: " + ", ".join(r["disallowed"]))
    return "; ".join(bits) or "no hard limits found"


def prompt_block(r):
    """Text added to the clip-picking prompt."""
    extra = []
    if r["content_focus"]:
        extra.append(f"Campaign wants: {r['content_focus']}")
    if r["content_avoid"]:
        extra.append(f"Must NOT contain: {r['content_avoid']}")
    if r["text_rules"]:
        extra.append(f"Text rules: {r['text_rules']}")
    if r["banned_words"]:
        extra.append("Never say or write: " + ", ".join(r["banned_words"]))
    return ("\nCAMPAIGN RULES - these clips are paid by a sponsor. Every clip AND every piece of text "
            "you write must fully comply with ALL rules below. Only pick moments that clearly comply; "
            "if a moment might break a rule, skip it. Fewer clips is better than one that breaks a rule. "
            "Rules about what is on screen can't be checked from the transcript: prefer moments that "
            "are clearly safe for them.\n" + "\n".join(extra) +
            "\nFULL RULES:\n<<<\n" + r["text"] + "\n>>>\n")


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s@#]", " ", str(s).lower())).strip()


def banned_hits(r, texts):
    hits = []
    hay = " " + " ".join(_norm(t) for t in texts if t) + " "
    for w in r["banned_words"]:
        n = _norm(w)
        if n and f" {n} " in hay:
            hits.append(w)
    return hits


def enforce_caption(r, caption):
    """Append required hashtags / mentions / phrases that are missing."""
    cap = (caption or "").strip()
    low = cap.lower()
    add = []
    for t in r["required_caption_text"] + r["required_mentions"] + r["required_hashtags"]:
        if t.lower() not in low and t.lower() not in " ".join(add).lower():
            add.append(t)
    return (cap + (" " if cap else "") + " ".join(add)).strip() if add else cap


REVIEW_PROMPT = """You check short clips for a paid clipping campaign before they are posted. Judge each clip strictly against EVERY campaign rule below, using its transcript (what is said) and all of its text (title, on-screen text, caption).

- "pass" only if nothing in the transcript or text breaks or risks breaking any rule.
- "fail" if anything breaks a rule, or the clip clearly doesn't fit what the campaign asks for.
- Rules about what is visible on screen cannot be judged from text: ignore those (a human checks them), unless the transcript itself shows the rule is broken.
- Spelling of required hashtags/mentions in the caption is fixed automatically - ignore those.

Reply with ONLY JSON: {{"clips":[{{"id":1,"verdict":"pass","broken":[]}},{{"id":2,"verdict":"fail","broken":["short reason"]}}]}}
Every id must appear exactly once.

CAMPAIGN RULES:
<<<
{rules}
>>>

CLIPS:
{clips}"""


def review_clips(r, clips, words, cfg, log=print, models_used=None):
    """clips: dicts with s, e and the text fields. Returns {index: (verdict, reasons)}."""
    blocks = []
    for i, c in enumerate(clips, start=1):
        said = c.get("_said") or " ".join(w["w"] for w in words if c["s"] - 0.3 <= w["s"] <= c["e"] + 0.3)
        blocks.append(f"--- id {i} ---\nTRANSCRIPT: {said}\nTITLE: {c.get('title', '')}\n"
                      f"ON-SCREEN TEXT: {c.get('hook', '')} | {c.get('tease', '')} | {c.get('cta', '')} | "
                      f"{c.get('label', '')}\nCAPTION: {c.get('caption', '')}")
    prompt = REVIEW_PROMPT.format(rules=r["text"], clips="\n".join(blocks))
    ids = set(range(1, len(clips) + 1))

    def validate(d):
        got = d.get("clips") if isinstance(d, dict) else None
        if not isinstance(got, list):
            raise ValueError("no clips list")
        seen = set()
        for x in got:
            try:
                seen.add(int(x.get("id")))
            except (TypeError, ValueError, AttributeError):
                continue
        if not ids <= seen:
            raise ValueError(f"verdicts missing for {sorted(ids - seen)}")

    from .pipeline import est_tokens
    data, model, cost = chat_json(cfg["api_key"], [{"role": "user", "content": prompt}],
                                  est_tokens(prompt) + 300, out_tokens=120 * len(clips) + 300,
                                  model=cfg["model"], allow_free=cfg["allow_free"],
                                  validate=validate, log=log)
    if models_used is not None:
        models_used.add(model)
    out = {}
    for x in data["clips"]:
        try:
            i = int(x.get("id"))
        except (TypeError, ValueError, AttributeError):
            continue
        verdict = str(x.get("verdict", "")).strip().lower()
        out[i - 1] = ("pass" if verdict == "pass" else "fail", _str_list(x.get("broken")))
    return out, cost or 0.0
