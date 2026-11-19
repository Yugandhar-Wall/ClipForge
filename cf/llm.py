"""OpenRouter client that automatically uses the cheapest model able to do the job.

How "cheapest that is still good" is chosen:
  1. Download OpenRouter's public model list (prices are USD per token).
  2. Keep chat models that take and return text, fit this transcript in their
     context window and are not tiny (< 7B when the size is in the name).
  3. QUALITY FLOOR: the first group tried is current models (released in the
     last ~15 months) from families known to follow instructions and return
     clean JSON: Gemini Flash / Flash-Lite, GPT mini / nano / gpt-oss, DeepSeek,
     Qwen3+, Llama 4, Mistral Small/Medium, Grok fast/mini, GLM, Kimi, MiniMax.
     Everything else (obscure or old cheap models) is only a fallback.
  4. Within each group, sort by the estimated cost of THIS request.
  5. Try in order. If a model is rate-limited, down, or its answer fails the
     quality check (valid timestamps, real titles/hooks/captions, enough clips),
     move to the next. The model that worked and its cost are reported.
"""
import json
import re
import time

import requests

API = "https://openrouter.ai/api/v1"
_SKIP = re.compile(r"embed|guard|rerank|moderat|tts|whisper|audio|image|vision-only|lyria|search", re.I)
_SIZE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)b\b", re.I)
_catalog = {"t": 0, "data": []}
TRUSTED = re.compile(
    r"^(google/gemini-[\d.]+-flash|openai/gpt-[\d.o]+(-\w+)?-(mini|nano)|openai/gpt-oss-(120|20)b|"
    r"deepseek/deepseek-(chat|v\d)|qwen/qwen3|meta-llama/llama-4|mistralai/mistral-(small|medium)|"
    r"x-ai/grok-[\w.-]*(fast|mini)|z-ai/glm-|moonshotai/kimi|minimax/minimax)", re.I)
RECENT_DAYS = 460


class LLMError(RuntimeError):
    pass


def _headers(key):
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost", "X-Title": "ClipForge"}


def catalog():
    if time.time() - _catalog["t"] < 3600 and _catalog["data"]:
        return _catalog["data"]
    r = requests.get(f"{API}/models", timeout=30)
    r.raise_for_status()
    _catalog.update(t=time.time(), data=r.json().get("data", []))
    return _catalog["data"]


def _price(m, k):
    try:
        return float((m.get("pricing") or {}).get(k) or 0)
    except (TypeError, ValueError):
        return -1.0


def rank_models(in_tokens, out_tokens, allow_free=True, min_billion=7):
    ranked = []
    for m in catalog():
        mid = m.get("id", "")
        arch = m.get("architecture") or {}
        ins = arch.get("input_modalities") or ["text"]
        outs = arch.get("output_modalities") or ["text"]
        if "text" not in ins or outs != ["text"]:
            continue
        if mid.startswith("openrouter/") or _SKIP.search(mid):
            continue
        p_in, p_out = _price(m, "prompt"), _price(m, "completion")
        if p_in < 0 or p_out < 0:
            continue
        free = p_in == 0 and p_out == 0
        if free and not allow_free:
            continue
        ctx = int(m.get("context_length") or 0)
        if ctx < (in_tokens + out_tokens) * 1.15:
            continue
        sizes = [float(x) for x in _SIZE.findall(mid)]
        if sizes and max(sizes) < min_billion:
            continue
        params = m.get("supported_parameters") or []
        reasoning = "reasoning" in params
        # reasoning models spend extra (hidden) output tokens thinking; budget for it
        cost = p_in * in_tokens + p_out * (out_tokens + (2500 if reasoning else 0))
        created = m.get("created") or 0
        recent = not created or (time.time() - created) < RECENT_DAYS * 86400
        tier = 0 if (TRUSTED.search(mid) and recent) else 1
        json_ok = "response_format" in params
        ranked.append((tier, cost, 0 if json_ok else 1, mid, m))
    ranked.sort(key=lambda x: (x[0], x[1], x[2]))
    return [(r[3], r[1], r[4]) for r in ranked]


def _try_catalog():
    try:
        catalog()
        return True
    except Exception:
        return False


def extract_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass
    raise ValueError("no JSON object in reply")


def chat_json(key, messages, in_tokens, out_tokens=3000, model="auto", allow_free=True,
              validate=None, log=print, max_tries=8):
    """Returns (parsed_json, model_id, est_cost_usd)."""
    if not key:
        raise LLMError("Add your OpenRouter API key first.")
    if model and model != "auto":
        meta = next((m for m in catalog() if m.get("id") == model), None) if _try_catalog() else None
        candidates = [(model, None, meta or {"supported_parameters": ["response_format"]})]
    else:
        candidates = rank_models(in_tokens, out_tokens, allow_free)
        if not candidates:
            raise LLMError("No OpenRouter model has a large enough context for this transcript.")
    errors = []
    for mid, cost, meta in candidates[:max_tries]:
        params = meta.get("supported_parameters") or []
        body = {"model": mid, "messages": messages, "temperature": 0.4, "max_tokens": out_tokens}
        if "response_format" in params:
            body["response_format"] = {"type": "json_object"}
        if "reasoning" in params:
            # keep thinking short (cheap) and out of the reply; leave room for it
            body["reasoning"] = {"effort": "low", "exclude": True}
            body["max_tokens"] = out_tokens + 6000
        label = "free" if cost == 0 else (f"~${cost:.4f}" if cost is not None else "")
        log(f"Asking {mid} {label}...")
        try:
            r = requests.post(f"{API}/chat/completions", headers=_headers(key),
                              json=body, timeout=300)
        except requests.RequestException as e:
            errors.append(f"{mid}: {e}")
            continue
        if r.status_code == 401:
            raise LLMError("OpenRouter rejected the API key (401). Check it in Settings.")
        if r.status_code != 200:
            msg = r.text[:200]
            errors.append(f"{mid}: HTTP {r.status_code} {msg}")
            log(f"  {mid} unavailable (HTTP {r.status_code}), trying next model.")
            if r.status_code == 402 and cost:
                raise LLMError("OpenRouter says your balance is too low for paid models (402). "
                               "Add credit, or enable free models.")
            continue
        try:
            data = r.json()
            usage = data.get("usage") or {}
            if usage.get("cost") is not None:
                cost = float(usage["cost"])
            if "error" in data:
                raise ValueError(str(data["error"])[:200])
            content = data["choices"][0]["message"].get("content") or ""
            if isinstance(content, list):  # some providers return content parts
                content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
            parsed = extract_json(content)
            if validate:
                validate(parsed)
            return parsed, mid, cost
        except Exception as e:
            errors.append(f"{mid}: bad reply ({e})")
            log(f"  {mid} gave an unusable answer ({e}), trying next model.")
    raise LLMError("All models failed:\n" + "\n".join(errors[-8:]))
