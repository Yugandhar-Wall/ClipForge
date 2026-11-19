"""Procedurally synthesizes the ClipForge sound-effect pack.

Every sound here is generated from math (sine waves, filtered noise,
envelopes) - no samples, no third-party audio, so there is nothing to
license. Run:  python make_sfx.py   -> writes WAVs into ./sfx/
"""
import os
import wave

import numpy as np

SR = 44100
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sfx")
rng = np.random.default_rng(7)


def t_axis(sec):
    return np.arange(int(SR * sec)) / SR


def env_adsr(n, a=0.01, d=0.1, s=0.6, r=0.3):
    a, d, r = int(a * SR), int(d * SR), int(r * SR)
    s_len = max(n - a - d - r, 0)
    e = np.concatenate([
        np.linspace(0, 1, a, endpoint=False),
        np.linspace(1, s, d, endpoint=False),
        np.full(s_len, s),
        np.linspace(s, 0, r),
    ])
    return np.pad(e, (0, max(0, n - len(e))))[:n]


def biquad_bp(x, f0, q):
    """Band-pass filter whose centre frequency can change per block (f0 array)."""
    y = np.zeros_like(x)
    x1 = x2 = y1 = y2 = 0.0
    block = 256
    for i in range(0, len(x), block):
        fc = float(f0[min(i, len(f0) - 1)])
        w0 = 2 * np.pi * fc / SR
        alpha = np.sin(w0) / (2 * q)
        b0, b1, b2 = alpha, 0.0, -alpha
        a0, a1, a2 = 1 + alpha, -2 * np.cos(w0), 1 - alpha
        b0, b1, b2, a1, a2 = b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0
        for j in range(i, min(i + block, len(x))):
            xn = x[j]
            yn = b0 * xn + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, xn, y1, yn
            y[j] = yn
    return y


def lowpass(x, alpha):
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc += alpha * (v - acc)
        y[i] = acc
    return y


def sweep(f_start, f_end, sec, curve="exp"):
    t = t_axis(sec)
    if curve == "exp":
        f = f_start * (f_end / f_start) ** (t / sec)
    else:
        f = np.linspace(f_start, f_end, len(t))
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def norm(x, peak=0.89):
    m = np.max(np.abs(x)) or 1.0
    return x / m * peak


def fade(x, ms=8):
    n = int(SR * ms / 1000)
    x = x.copy()
    x[:n] *= np.linspace(0, 1, n)
    x[-n:] *= np.linspace(1, 0, n)
    return x


def save(name, x):
    os.makedirs(OUT, exist_ok=True)
    x = fade(norm(x))
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(os.path.join(OUT, name + ".wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ---------------------------------------------------------------- sounds
def whoosh(sec=0.7, f_lo=300, f_hi=3500):
    n = int(SR * sec)
    noise = rng.standard_normal(n)
    t = np.linspace(0, 1, n)
    # centre frequency rises then falls -> the "air passing by" feel
    f0 = f_lo + (f_hi - f_lo) * np.sin(np.pi * t) ** 1.5
    y = biquad_bp(noise, f0, q=1.4)
    e = np.sin(np.pi * t) ** 2
    return y * e


def impact():
    t = t_axis(1.4)
    body = sweep(95, 38, 1.4) * np.exp(-t * 3.2)
    click = rng.standard_normal(len(t)) * np.exp(-t * 60)
    thump = np.tanh(body * 2.5)
    return thump + 0.35 * lowpass(click, 0.25)


def deep_boom():
    """Original big cinematic boom: distorted pitch-dropping sub with a long tail."""
    t = t_axis(2.2)
    sub = sweep(70, 30, 2.2) * np.exp(-t * 1.6)
    harm = 0.4 * sweep(140, 60, 2.2) * np.exp(-t * 2.5)
    y = np.tanh((sub + harm) * 3.0)
    rumble = rng.standard_normal(len(t))
    for _ in range(4):  # 4 cascaded one-poles -> steep roll-off, no hiss
        rumble = lowpass(rumble, 0.02)
    rumble = norm(rumble, 0.5) * np.exp(-t * 2.0)
    return y + rumble


def pop():
    t = t_axis(0.12)
    return sweep(1100, 380, 0.12) * np.exp(-t * 40)


def riser(sec=2.0):
    t = t_axis(sec)
    n = len(t)
    noise = biquad_bp(rng.standard_normal(n), np.linspace(400, 6000, n), q=0.9)
    f = 180 * (8 ** (t / sec))
    saw = 2 * ((np.cumsum(f) / SR) % 1) - 1
    saw = lowpass(saw, 0.15)
    e = (t / sec) ** 2.2
    return (0.7 * noise + 0.5 * saw) * e


def ding():
    t = t_axis(1.6)
    f = 1318.5  # E6
    y = sum(a * np.sin(2 * np.pi * f * k * t) * np.exp(-t * d)
            for k, a, d in [(1, 1.0, 3.0), (2.76, 0.45, 5.5), (5.4, 0.25, 9.0), (8.9, 0.1, 14.0)])
    return y * env_adsr(len(t), a=0.002, d=0.05, s=1.0, r=0.2)


def glitch():
    out = []
    for _ in range(9):
        seg = int(SR * rng.uniform(0.02, 0.06))
        kind = rng.integers(3)
        if kind == 0:
            s = np.sign(np.sin(2 * np.pi * rng.uniform(200, 1800) * np.arange(seg) / SR))
        elif kind == 1:
            s = rng.standard_normal(seg)
            s = np.round(s * 3) / 3  # bit-crush
        else:
            s = np.zeros(seg)
        out.append(s * rng.uniform(0.4, 1.0))
    return np.concatenate(out)


def bass_drop():
    t = t_axis(1.6)
    y = sweep(160, 32, 1.6) * np.exp(-t * 1.4)
    return np.tanh(y * 2) + 0.5 * np.sin(2 * np.pi * 42 * t) * np.exp(-t * 2.2)


def tick():
    t = t_axis(0.05)
    return np.sin(2 * np.pi * 2400 * t) * np.exp(-t * 120)


def rewind():
    """Tape-rewind style stutter: rapid pitch-rising chirps."""
    parts = []
    for i in range(12):
        f = 300 + i * 120
        parts.append(sweep(f, f * 1.8, 0.05) * np.hanning(int(SR * 0.05)))
    return np.concatenate(parts)


def record_scratch():
    """Vinyl-scratch style stop: a back-and-forth pitch wobble that drops dead."""
    t = t_axis(0.55)
    n = len(t)
    wob = np.sin(2 * np.pi * 5.5 * t)  # hand pushing the record back and forth
    f = np.clip(900 + 700 * wob - 1200 * (t / 0.55) ** 2, 60, None)
    saw = 2 * ((np.cumsum(f) / SR) % 1) - 1
    noise = biquad_bp(rng.standard_normal(n), np.clip(f * 2.2, 200, 8000), q=2.0)
    e = np.minimum(1, t / 0.01) * np.where(t < 0.42, 1.0, np.exp(-(t - 0.42) * 40))
    return (0.55 * lowpass(saw, 0.3) + 0.8 * noise) * e


SOUNDS = {
    "whoosh": lambda: whoosh(),
    "swoosh_short": lambda: whoosh(0.32, 600, 5000),
    "impact": impact,
    "deep_boom": deep_boom,
    "pop": pop,
    "riser": riser,
    "ding": ding,
    "glitch": glitch,
    "bass_drop": bass_drop,
    "tick": tick,
    "rewind": rewind,
    "record_scratch": record_scratch,
}

if __name__ == "__main__":
    for name, fn in SOUNDS.items():
        save(name, fn())
        print("wrote", name)
