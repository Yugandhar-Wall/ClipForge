"""Live-recording diagnosis: tries every way of recording a live stream for ~15 s each and
reports which one works. Run through diagnose_live.bat; writes live_diag.txt.
Usage: python -m cf.live_diag <live link>"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cf.pipeline import _js_runtimes  # noqa: E402
from cf.tools import ffmpeg_exe, hls_input_opts  # noqa: E402

TEST_S = 15
FLAGS = 0x08000000 if sys.platform == "win32" else 0


def p(*a):
    print(*a, flush=True)


def tail(b, n=12):
    lines = [ln for ln in b.decode("utf-8", "replace").splitlines() if ln.strip()]
    return "\n      ".join(ln[:220] for ln in lines[-n:]) or "(nothing)"


def run_pipe(name, url, fmt, extra=()):
    node = (_js_runtimes().get("node") or {}).get("path")
    dl = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-playlist", "--no-part", "-f", fmt,
          "--hls-use-mpegts", "-o", "-", *extra]
    if node:
        dl += ["--js-runtimes", f"node:{node}"]
    dl.append(url)
    work = tempfile.mkdtemp(prefix="cfdiag_")
    ff = [ffmpeg_exe(), "-hide_banner", "-loglevel", "warning", "-y", "-i", "pipe:0", "-map", "0:v:0",
          "-map", "0:a:0?", "-c", "copy", "-f", "segment", "-segment_time", "4", "-segment_format", "mpegts",
          os.path.join(work, "s_%04d.ts")]
    a = subprocess.Popen(dl, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=FLAGS)
    b = subprocess.Popen(ff, stdin=a.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=FLAGS)
    a.stdout.close()
    return finish(name, [a, b], work, {"yt-dlp": a, "ffmpeg": b})


def run_ffmpeg(name, inputs, in_opts):
    work = tempfile.mkdtemp(prefix="cfdiag_")
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "info", "-y"]
    for u, headers in inputs:
        cmd += list(in_opts)
        if headers:
            cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
        cmd += ["-i", u]
    last = len(inputs) - 1
    cmd += ["-map", "0:v:0", "-map", f"{last}:a:0?", "-c", "copy", "-f", "segment", "-segment_time", "4",
            "-segment_format", "mpegts", os.path.join(work, "s_%04d.ts")]
    b = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                         creationflags=FLAGS)
    return finish(name, [b], work, {"ffmpeg": b})


def run_native(name, url, fmt):
    """yt-dlp's own downloader only (no ffmpeg reading YouTube), saving to a file."""
    node = (_js_runtimes().get("node") or {}).get("path")
    work = tempfile.mkdtemp(prefix="cfdiag_")
    dl = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-playlist", "--no-part", "-f", fmt,
          "--hls-use-mpegts", "-o", os.path.join(work, "s_0001.%(ext)s")]
    if node:
        dl += ["--js-runtimes", f"node:{node}"]
    dl.append(url)
    a = subprocess.Popen(dl, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=FLAGS)
    return finish(name, [a], work, {"yt-dlp": a})


def finish(name, procs, work, named):
    errs = {k: bytearray() for k in named}
    ts = [threading.Thread(target=lambda k=k, pr=pr: errs[k].extend(pr.stderr.read() or b""), daemon=True)
          for k, pr in named.items()]
    for t in ts:
        t.start()
    end = time.time() + TEST_S
    while time.time() < end and any(pr.poll() is None for pr in procs):
        time.sleep(0.5)
    for pr in procs:
        if pr.poll() is None:
            try:
                pr.terminate()
                pr.wait(8)
            except Exception:
                pr.kill()
    for t in ts:
        t.join(5)
    files = sorted(os.listdir(work))
    size = sum(os.path.getsize(os.path.join(work, f)) for f in files)
    ok = size > 200_000
    p(f"\n[{'WORKS' if ok else 'FAILS'}] {name}: {len(files)} piece(s), {size / 1e6:.1f} MB in {TEST_S}s")
    for k, e in errs.items():
        p(f"   {k} said:\n      {tail(bytes(e))}")
    for f in files:
        try:
            os.remove(os.path.join(work, f))
        except OSError:
            pass
    return ok


def main(url):
    import yt_dlp
    p("=== ClipForge live diagnosis", time.strftime("%Y-%m-%d %H:%M"))
    p("link:", url)
    p("python:", sys.version.split()[0], "| yt-dlp:", yt_dlp.version.__version__)
    ff = ffmpeg_exe()
    out = subprocess.run([ff, "-version"], capture_output=True, text=True, creationflags=FLAGS).stdout
    p("ffmpeg:", ff, "|", out.splitlines()[0] if out else "?")
    p("js runtimes:", json.dumps(_js_runtimes()))
    p("hls options ClipForge adds:", hls_input_opts())
    try:
        import yt_dlp_ejs
        p("yt-dlp-ejs:", getattr(yt_dlp_ejs, "version", "installed"))
    except Exception as e:
        p("yt-dlp-ejs: MISSING", e)

    p("\n=== formats YouTube offers right now")
    opts = {"quiet": True, "no_warnings": False, "skip_download": True, "noplaylist": True,
            "js_runtimes": _js_runtimes()}
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=False, process=False)
            info = y.process_ie_result(info, download=False) if info.get("_type") != "playlist" else info
    except Exception as e:
        p("COULD NOT READ THE STREAM:", e)
        return
    p("live_status:", info.get("live_status"), "| title:", info.get("title"))
    fmts = info.get("formats") or []
    for f in fmts:
        p(f"  {str(f.get('format_id')):>8}  {str(f.get('protocol')):18} {str(f.get('ext')):5} "
          f"v={str(f.get('vcodec'))[:14]:14} a={str(f.get('acodec'))[:12]:12} h={f.get('height')} "
          f"tbr={f.get('tbr')} url=...{(f.get('url') or '')[-60:]}")
    combined = [f for f in fmts if f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none")]
    vonly = [f for f in fmts if f.get("vcodec") not in (None, "none") and f.get("acodec") == "none"]
    aonly = [f for f in fmts if f.get("acodec") not in (None, "none") and f.get("vcodec") == "none"]
    unknown = [f for f in fmts if f.get("vcodec") is None and f.get("acodec") is None]
    key = lambda f: (f.get("height") or 0) if (f.get("height") or 0) <= 1080 else -1
    p(f"combined: {len(combined)} | video-only: {len(vonly)} | audio-only: {len(aonly)} | unlabelled: {len(unknown)}")

    p("\n=== recording tests (about 15 s each)")
    results = {}
    results["pipe b"] = run_pipe("yt-dlp -f b (combined) -> ffmpeg", url, "b")
    results["pipe default"] = run_pipe("yt-dlp default format -> ffmpeg", url, "bv*+ba/b")
    best = max(combined or unknown or vonly or [{}], key=key)
    if best.get("format_id"):
        results["pipe id"] = run_pipe(f"yt-dlp -f {best['format_id']} -> ffmpeg", url, str(best["format_id"]))
    hdr = lambda f: dict(f.get("http_headers") or {})
    if best.get("url"):
        inputs = [(best["url"], hdr(best))]
        if best in vonly and aonly:
            ab = max(aonly, key=lambda f: f.get("tbr") or 0)
            inputs.append((ab["url"], hdr(ab)))
        results["ffmpeg plain"] = run_ffmpeg("ffmpeg reads the feed directly", inputs, [])
        results["ffmpeg ext"] = run_ffmpeg("ffmpeg direct + allowed_extensions ALL", inputs,
                                           ["-allowed_extensions", "ALL"])
        results["ffmpeg picky"] = run_ffmpeg("ffmpeg direct + extension_picky 0", inputs,
                                             ["-allowed_extensions", "ALL", "-extension_picky", "0"])
    if vonly:
        vb = max(vonly, key=key)
        results["native video"] = run_native(f"yt-dlp's own downloader, video only ({vb['format_id']})", url, str(vb["format_id"]))
    if aonly:
        ab2 = max(aonly, key=lambda f: f.get("tbr") or 0)
        results["native audio"] = run_native(f"yt-dlp's own downloader, audio only ({ab2['format_id']})", url, str(ab2["format_id"]))
    hopts = " ".join(hls_input_opts())
    results["pipe merge"] = run_pipe("yt-dlp joins video+audio itself -> ffmpeg", url, "bv*+ba/b",
                                     ["--downloader-args", f"ffmpeg_i:{hopts}"] if hopts else [])
    p("\n=== summary")
    for k, v in results.items():
        p(f"  {'WORKS' if v else 'fails'}  {k}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python -m cf.live_diag <live link>")
    main(sys.argv[1])
