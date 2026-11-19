"""Sponsor logo: stored copy, fitted versions, and the animated overlay filter."""
import hashlib
import os

from .tools import CACHE_DIR, ROOT

ASSETS_DIR = os.path.join(ROOT, "assets")
LOGO_BOX = (440, 150)       # max width x height at size 1.0 (fits inside the box, keeps aspect)
ANIMATIONS = ["float", "pulse", "none"]


def store_logo(src):
    """Copy an uploaded image into assets/ as a clean RGBA PNG (transparent margins trimmed).
    Returns the stored path."""
    from PIL import Image
    os.makedirs(ASSETS_DIR, exist_ok=True)
    im = Image.open(src)
    im.load()
    im = im.convert("RGBA")
    box = im.getchannel("A").getbbox()
    if box:
        im = im.crop(box)
    if max(im.size) > 1600:
        im.thumbnail((1600, 1600), Image.LANCZOS)
    out = os.path.join(ASSETS_DIR, "logo.png")
    im.save(out)
    return out


def fitted_logo(path, scale=1.0):
    """Logo resized to fit LOGO_BOX * scale. Returns (png_path, w, h); cached."""
    from PIL import Image
    bw, bh = int(LOGO_BOX[0] * scale), int(LOGO_BOX[1] * scale)
    with open(path, "rb") as f:
        key = hashlib.md5(f.read()).hexdigest()[:10]
    out = os.path.join(CACHE_DIR, f"logo_{key}_{bw}x{bh}.png")
    if not os.path.exists(out):
        os.makedirs(CACHE_DIR, exist_ok=True)
        im = Image.open(path).convert("RGBA")
        r = min(bw / im.width, bh / im.height)
        w, h = max(2, int(im.width * r / 2) * 2), max(2, int(im.height * r / 2) * 2)
        im.resize((w, h), Image.LANCZOS).save(out)
    with Image.open(out) as im:
        return out, im.width, im.height


def logo_filters(base, logo_in, out, cy, off, anim):
    """Filter-graph lines overlaying the logo centred at y=cy. Animation runs on the clip's
    clock (t + off) so it stays smooth across the separately rendered segments."""
    T = f"(t+{off:.3f})"
    lines = []
    if anim == "pulse":  # a quick pop (+12%) every 3 s
        p = f"mod({T},3)"
        lines.append(f"{logo_in}format=rgba,scale=w='trunc(iw*(1+0.12*sin(PI*min(1,{p}/0.45))"
                     f"*lt({p},0.45))/2)*2':h=-2:eval=frame[lg]")
        y = f"{cy}-h/2"
    else:
        lines.append(f"{logo_in}format=rgba[lg]")
        y = f"{cy}-h/2+10*sin(2*PI*{T}/2.4)" if anim == "float" else f"{cy}-h/2"
    lines.append(f"{base}[lg]overlay=x='(W-w)/2':y='{y}':eval=frame:eof_action=repeat{out}")
    return lines


def has_logo(cfg):
    p = (cfg or {}).get("logo_path") or ""
    return bool(p) and os.path.exists(p)


def remove_logo():
    p = os.path.join(ASSETS_DIR, "logo.png")
    if os.path.exists(p):
        os.remove(p)
