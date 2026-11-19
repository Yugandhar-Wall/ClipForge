"""Finds loud spikes in the audio (screams, explosions, crowd/stream hype).

Gameplay highlights are often loud rather than wordy, so these timestamps are
given to the LLM alongside the transcript and used to place impact SFX.
"""
import numpy as np

from .audio import read_wav


def loud_moments(wav_path, window=0.5, top=40, min_gap=8.0):
    samples, sr, _ = read_wav(wav_path)
    data = samples.astype(np.float32)
    if data.size == 0:
        return []
    hop = int(sr * window)
    n = len(data) // hop
    if n < 4:
        return []
    frames = data[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-6
    db = 20 * np.log10(rms / 32768)
    # compare each window with its local surroundings (60 s) so quiet and loud
    # sections of a long stream are judged fairly
    k = max(1, int(60 / window))
    pad = np.pad(db, (k, k), mode="edge")
    local = np.convolve(pad, np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
    excess = db - local
    order = np.argsort(-excess)
    picked = []
    for i in order:
        if excess[i] < 6:  # at least 6 dB above its surroundings
            break
        t = i * window
        if all(abs(t - p["t"]) >= min_gap for p in picked):
            picked.append({"t": round(t, 1), "db_over": round(float(excess[i]), 1)})
        if len(picked) >= top:
            break
    return sorted(picked, key=lambda p: p["t"])
