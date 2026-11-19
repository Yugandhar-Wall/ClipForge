"""Speech-to-text: cloud Whisper (cloud_stt.py) with local faster-whisper as the fallback.
Local is free and runs on your PC; both give word timestamps."""
import hashlib
import os

from .audio import STT_PROMPT, read_speech
from .cloud_stt import MODELS as CLOUD_MODELS, CloudSTTError, transcribe_cloud
from .tools import CACHE_DIR, check_stop, read_json, write_json

_MODELS = {}
LOCAL_MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo"]


def file_key(path):
    st = os.stat(path)
    h = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}".encode())
    return h.hexdigest()[:16]


def _device():
    # GPU only when asked for: on Windows it also needs NVIDIA cuBLAS/cuDNN DLLs,
    # and a half-installed CUDA crashes mid-transcription.
    if os.environ.get("CLIPFORGE_GPU") != "1":
        return "cpu", "int8"
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def transcribe(wav, model_size="small", language=None, log=print, cache_key=None):
    cache = os.path.join(CACHE_DIR, f"{cache_key}_{model_size}_v2.json") if cache_key else None
    if cache and os.path.exists(cache):
        log("Transcript found in cache, skipping transcription.")
        return read_json(cache)

    from faster_whisper import WhisperModel
    device, compute = _device()
    key = (model_size, device)
    if key not in _MODELS:
        log(f"Loading Whisper '{model_size}' on {device} (first run downloads the model)...")
        _MODELS[key] = WhisperModel(model_size, device=device, compute_type=compute,
                                    cpu_threads=max(1, (os.cpu_count() or 4) - 1))
    model = _MODELS[key]

    # Hand Whisper the samples ourselves: faster-whisper's own loader breaks with
    # newer PyAV releases (av.open(metadata_errors=...) was removed in PyAV 19).
    audio = read_speech(wav)
    segments, info = model.transcribe(
        audio, language=language or None, word_timestamps=True,
        # hotwords prime every 30 s window (initial_prompt would only cover the first one)
        hotwords=STT_PROMPT,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 400, "threshold": 0.4},
        condition_on_previous_text=False)
    log(f"Detected language: {info.language} ({info.language_probability:.0%}). Transcribing "
        f"{info.duration/60:.1f} min of audio...")
    out = {"language": info.language, "segments": []}
    last_report = 0
    for seg in segments:
        check_stop()
        words = [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)}
                 for w in (seg.words or []) if w.word.strip()]
        out["segments"].append({"s": round(seg.start, 2), "e": round(seg.end, 2),
                                "text": seg.text.strip(), "words": words})
        if info.duration and seg.end - last_report > 120:
            last_report = seg.end
            log(f"  transcribed {seg.end/60:.1f}/{info.duration/60:.1f} min")
    if cache:
        write_json(cache, out)
    return out


def transcribe_auto(wav, cfg, log=print, cache_key=None):
    """Cloud (OpenRouter) when chosen, with automatic fallback: the other cloud model, then
    local Whisper. The returned dict may carry "cost" (USD) and "engine"."""
    choice = cfg.get("whisper_model") or "small"
    lang = cfg.get("language") or None
    if choice.startswith("cloud:"):
        first = choice[len("cloud:"):]
        for model in [first] + [m for m in CLOUD_MODELS if m != first]:
            try:
                return transcribe_cloud(wav, cfg.get("api_key"), model, lang, log, cache_key)
            except CloudSTTError as e:
                log(f"Cloud transcription with {model} failed: {e}")
            except Exception as e:  # anything unexpected: still don't lose the run
                log(f"Cloud transcription with {model} failed ({type(e).__name__}: {e})")
        local = cfg.get("fallback_model") or "small"
        log(f"Falling back to local Whisper '{local}' (slower, free).")
    else:
        local = choice
    out = transcribe(wav, local, lang, log=log, cache_key=cache_key)
    out.setdefault("engine", f"local:{local}")
    return out
