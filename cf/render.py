"""Timeline renderer: each short is a list of segments rendered separately, then joined.

A segment is a piece of the source video with an effect:
  speed  < 1 -> slow motion (audio slowed without pitch change)
  freeze > 0 -> hold the last frame for N seconds (audio goes silent)
  zoom   > 1 -> punch-in
  gray       -> black & white (freeze-frame look)
Overlays (captions, hooks, number cards, labels) and sound effects are given in
OUTPUT time for the whole short; each segment gets the slice that falls inside it.
"""
import os
import wave
import shutil
from dataclasses import dataclass

from .brand import logo_filters
from .captions import ass_for_window
from .memes import resolve as resolve_meme
from .tools import FONTS_DIR, SFX_DIR, run_ffmpeg

W, H = 1080, 1920


@dataclass
class Segment:
    src_s: float
    src_e: float
    speed: float = 1.0
    freeze: float = 0.0
    zoom: float = 1.0
    gray: bool = False
    captions: bool = True

    @property
    def dur(self):
        return (self.src_e - self.src_s) / self.speed + self.freeze


def layout_geometry(layout, src_w, src_h, zoom=1.25, logo=False):
    """Returns (filter template using {inp}, caption_y, hook_y, logo_y).
    logo_y is None without a logo. With a logo the blurred layout is always used (the logo
    must sit on the blurred band, never on the gameplay): captions move up against the
    gameplay and the logo goes directly under them."""
    aspect = (src_w / src_h) if src_w and src_h else 16 / 9
    if layout == "split":
        # content fills the top half; the gameplay video is stacked under it at render time.
        # Captions sit on the seam, the hook over the content, a logo on the gameplay half.
        vf = (f"{{inp}}scale={W}:{SPLIT_H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{SPLIT_H},setsar=1[top]")
        return vf, SPLIT_H, 300, (SPLIT_H + 190 if logo else None)
    if not logo and (layout == "crop" or aspect < 0.9):
        vf = (f"{{inp}}scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H},setsar=1[base]")
        return vf, 1480, 330, None
    fg_w = int(round(W * zoom / 2)) * 2
    fg_h = int(round(fg_w / aspect / 2)) * 2
    if logo and fg_h > LOGO_MAX_FG_H:  # tall sources: shrink so the bottom band has room
        fg_w = int(round(fg_w * LOGO_MAX_FG_H / fg_h / 2)) * 2
        fg_h = LOGO_MAX_FG_H
    fg_h_vis = min(fg_h, H)
    fg_w_vis = min(fg_w, W)
    vf = (f"{{inp}}split[a][b];"
          f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"boxblur=24:2,eq=brightness=-0.10:saturation=1.15[bg];"
          f"[b]scale={fg_w}:{fg_h},crop={fg_w_vis}:{fg_h_vis}[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]")
    hook_y = max(250, H // 2 - fg_h_vis // 2 - 170)
    fg_bottom = H // 2 + fg_h_vis // 2
    if not logo:
        return vf, min(H - 260, fg_bottom + 190), hook_y, None
    cap_y = fg_bottom + 100
    return vf, cap_y, hook_y, cap_y + 115   # logo centre; caller adds half the logo height


LOGO_MAX_FG_H = 1000
SPLIT_H = H // 2   # height of each half in the split-screen layout


_WAV_LEN = {}


def _wav_len(p):
    if p not in _WAV_LEN:
        try:
            with wave.open(p, "rb") as w:
                _WAV_LEN[p] = w.getnframes() / w.getframerate()
        except Exception:
            _WAV_LEN[p] = 2.0
    return _WAV_LEN[p]


def _render_segment(src, seg, off, total, events, sfx, out, layout_vf, has_audio, sfx_gain,
                    progress_bar, workdir, idx, logo=None, overlays=None, memes=None, meme_gain=0.8,
                    gameplay=None):
    dur = seg.dur
    src_len = max(0.04, seg.src_e - seg.src_s)
    ass_name = f"seg{idx:02d}.ass"
    with open(os.path.join(workdir, ass_name), "w", encoding="utf-8") as f:
        f.write(ass_for_window(events, off, off + dur))

    args = ["-ss", f"{seg.src_s:.3f}", "-t", f"{src_len:.3f}", "-i", os.path.abspath(src)]
    # sound effects: (local start, path, gain, skip into the file); a sound that started in an
    # earlier segment keeps playing here instead of being cut at the segment boundary
    sfx_inputs, ducks = [], []
    for t, name in sfx:
        meme = resolve_meme(name, memes or {})
        if meme:
            p, hit, length = meme
            start, gain = t - off - hit, meme_gain
        else:
            p = os.path.join(SFX_DIR, f"{name}.wav")
            if not os.path.exists(p):
                continue
            start, gain, length = t - off, sfx_gain, _wav_len(p)
        if start >= dur - 0.05 or start + length <= 0.02:
            continue
        sfx_inputs.append((max(0.0, start), p, gain, max(0.0, -start)))
        if meme:  # duck the game audio while the meme plays
            ducks.append((max(0.0, start), min(dur, start + length)))
    for _, p, _, _ in sfx_inputs:
        args += ["-i", p]
    n_in = 1 + len(sfx_inputs)
    if not has_audio:
        args += ["-f", "lavfi", "-t", f"{src_len:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
        main_a = f"[{n_in}:a]"
    else:
        main_a = "[0:a]"
    logo_idx = None
    if logo:
        logo_idx = n_in + (0 if has_audio else 1)
        args += ["-loop", "1", "-framerate", "30", "-t", f"{dur + 0.5:.3f}", "-i", logo["png"]]
    # animated overlays (transparent PNG sequences) that touch this segment
    ovs = [o for o in (overlays or []) if o["t1"] > off + 0.02 and o["t0"] < off + dur - 0.02]
    ov_idx = n_in + (0 if has_audio else 1) + (1 if logo else 0)
    for o in ovs:
        args += ["-framerate", "30", "-i", os.path.join(o["dir"], "frame_%06d.png")]
    gp_idx = ov_idx + len(ovs)
    if gameplay:
        # keeps running through freezes and slow-mo of the content: it's only the background
        gp_t = (gameplay["start"] + off) % max(1.0, gameplay.get("length") or 1e9)
        args += ["-stream_loop", "-1", "-ss", f"{gp_t:.3f}", "-t", f"{dur + 0.5:.3f}",
                 "-i", os.path.abspath(gameplay["path"])]

    v = [f"[0:v]setpts=(PTS-STARTPTS)/{seg.speed},fps=30[src]",
         layout_vf.format(inp="[src]")]
    chain = "[base]"
    post = []
    ph = SPLIT_H if gameplay else H   # zoom / grey / freeze act on the content only
    if seg.zoom > 1.001:
        zw, zh = int(W * seg.zoom / 2) * 2, int(ph * seg.zoom / 2) * 2
        post.append(f"scale={zw}:{zh},crop={W}:{ph}")
    if seg.gray:
        post.append("hue=s=0,eq=contrast=1.1")
    if seg.freeze > 0:
        post.append(f"tpad=stop_mode=clone:stop_duration={seg.freeze:.3f}")
    if gameplay:
        v.append(f"[top]{','.join(post) or 'null'}[topx]")
        v.append(f"[{gp_idx}:v]setpts=PTS-STARTPTS,fps=30,"
                 f"scale={W}:{H - SPLIT_H}:force_original_aspect_ratio=increase,"
                 f"crop={W}:{H - SPLIT_H},setsar=1[gp]")
        v.append("[topx][gp]vstack=inputs=2,setsar=1[base]")
        post = []
    if logo_idx is not None:
        v.append(f"{chain}{','.join(post) or 'null'}[pre]")
        v += logo_filters("[pre]", f"[{logo_idx}:v]", "[lgo]", logo["cy"], off, logo["anim"])
        chain, post = "[lgo]", []
    if ovs:
        v.append(f"{chain}{','.join(post) or 'null'}[mo0]")
        chain, post = "[mo0]", []
        for j, o in enumerate(ovs):
            span = o["t1"] - o["t0"]
            hold = max(0.0, span - o["alen"]) + 0.1
            skip, delay = max(0.0, off - o["t0"]), max(0.0, o["t0"] - off)
            fade = (f",fade=t=out:st={span - 0.18:.3f}:d=0.18:alpha=1"
                    if o.get("fade") and span > 0.5 else "")
            v.append(f"[{ov_idx + j}:v]format=rgba,tpad=stop_mode=clone:stop_duration={hold:.3f}"
                     f"{fade},trim=start={skip:.3f}:end={span:.3f},"
                     f"setpts=PTS-STARTPTS+{delay:.3f}/TB[ov{j}]")
            v.append(f"{chain}[ov{j}]overlay={o.get('x', 0)}:{o.get('y', 0)}:eof_action=pass"
                     f":format=auto[mo{j + 1}]")
            chain = f"[mo{j + 1}]"
    post.append(f"ass={ass_name}:fontsdir=fonts")
    post.append("format=yuv420p")
    v.append(f"{chain}{','.join(post)}[v1]")
    vout = "[v1]"
    if progress_bar and total > 0:
        v.append(f"color=c=0xFFD400:s={W}x14:r=30[bar]")
        v.append(f"[v1][bar]overlay=x='-W+W*min(1,(t+{off:.3f})/{total:.3f})':y=0:"
                 f"eval=frame:shortest=1[v2]")
        vout = "[v2]"

    a = [f"{main_a}asetpts=PTS-STARTPTS"]
    if abs(seg.speed - 1) > 0.001:
        a[0] += f",atempo={max(0.5, min(2.0, seg.speed))}"
    a[0] += ",aresample=44100,aformat=channel_layouts=stereo"
    if seg.freeze > 0:
        a[0] += f",apad=pad_dur={seg.freeze:.3f}"
    for d0, d1 in ducks:
        a[0] += f",volume=0.45:enable='between(t,{d0:.3f},{d1:.3f})'"
    a[0] += "[a0]"
    mix = "[a0]"
    for i, (t, _, gain, skip) in enumerate(sfx_inputs, start=1):
        ms = int(t * 1000)
        trim = f"atrim=start={skip:.3f},asetpts=PTS-STARTPTS," if skip > 0 else ""
        a.append(f"[{i}:a]{trim}aresample=44100,aformat=channel_layouts=stereo,"
                 f"adelay={ms}|{ms},volume={gain}[s{i}]")
        mix += f"[s{i}]"
    if sfx_inputs:
        a.append(f"{mix}amix=inputs={len(sfx_inputs) + 1}:duration=first:normalize=0[am]")
        mix = "[am]"
    a.append(f"{mix}apad,atrim=0:{dur:.3f},asetpts=PTS-STARTPTS[aout]")

    args += ["-filter_complex", ";".join(v + a), "-map", vout, "-map", "[aout]",
             "-t", f"{dur:.3f}", "-r", "30",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-g", "60", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", out]
    run_ffmpeg(args, cwd=workdir)


def render_timeline(src, segments, events, sfx, out_path, layout_vf, has_audio=True,
                    sfx_gain=0.55, progress_bar=True, workdir=None, logo=None, overlays=None,
                    memes=None, meme_gain=0.8, gameplay=None):
    """segments: [Segment]; events: caption/overlay events (output time);
    sfx: [(output_seconds, name)]; logo: None or {"png", "cy", "anim"};
    gameplay: None or {"path", "start", "length"} for the split-screen layout (muted)."""
    workdir = workdir or os.path.dirname(out_path)
    tmp = os.path.join(workdir, "_parts_" + os.path.splitext(os.path.basename(out_path))[0])
    os.makedirs(tmp, exist_ok=True)
    if not os.path.isdir(os.path.join(tmp, "fonts")):
        shutil.copytree(FONTS_DIR, os.path.join(tmp, "fonts"))
    total = sum(s.dur for s in segments)
    parts, off = [], 0.0
    for i, seg in enumerate(segments):
        part = os.path.join(tmp, f"part{i:02d}.mp4")
        _render_segment(src, seg, off, total, events, sfx, part, layout_vf, has_audio,
                        sfx_gain, progress_bar, tmp, i, logo, overlays, memes, meme_gain,
                        gameplay)
        parts.append(part)
        off += seg.dur
    with open(os.path.join(tmp, "list.txt"), "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{os.path.basename(p)}'\n")
    # join; loudness-normalise the whole short in one pass (-14 LUFS, social standard)
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", "list.txt", "-c:v", "copy",
                "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                os.path.abspath(out_path)], cwd=tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    return out_path, total
