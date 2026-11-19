"""Audio shared by transcription (local + cloud) and the loudness detector."""
import wave

import numpy as np

from .tools import run_ffmpeg

SPEECH_SR = 16000
# Whisper normally "cleans up" speech and drops interjections. Priming it with a messy,
# exclamation-heavy example makes it keep "oh no", "nooo", "what?!" (local and cloud).
STT_PROMPT = "Uh, oh no! Nooo, no no no... What?! Bro, are you kidding me? Haha, let's go! Oh shit. Wait... huh?"


def extract_audio(video, wav_out, start=None, end=None):
    """16 kHz mono wav. start/end in seconds of the source; the wav then starts at `start`."""
    cut = []
    if start:
        cut += ["-ss", f"{start:.3f}"]
    if end is not None:
        cut += ["-t", f"{end - (start or 0):.3f}"]
    run_ffmpeg(cut[:2] + ["-i", video] + cut[2:] +
               ["-vn", "-ac", "1", "-ar", str(SPEECH_SR), "-c:a", "pcm_s16le", wav_out])


def read_wav(path):
    """(int16 samples, sample rate, channels)."""
    with wave.open(path, "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        return data, w.getframerate(), w.getnchannels()


def read_speech(path):
    """The wav from extract_audio as float32 samples in -1..1."""
    data, sr, ch = read_wav(path)
    if sr != SPEECH_SR or ch != 1:
        raise RuntimeError("expected a 16 kHz mono wav")
    return data.astype(np.float32) / 32768.0
