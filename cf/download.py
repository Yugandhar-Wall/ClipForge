"""Downloading videos with yt-dlp (whole, or only a From/To window)."""
import os

from .tools import CACHE_DIR, check_stop, ffmpeg_exe, fmt_ts, hls_input_opts, js_runtimes, pause, stoppable, strip_ansi


def download(url, log, start=None, end=None, attempts=4):
    """Download a video with yt-dlp. With start/end (seconds) only that part is fetched:
    it is cut frame-accurately (re-encoded at the cut), so the file's 0:00 is exactly `start`."""
    import yt_dlp
    from yt_dlp.utils import download_range_func
    dl_dir = os.path.join(CACHE_DIR, "downloads")
    os.makedirs(dl_dir, exist_ok=True)
    part = start is not None or end is not None
    tag = f" part {int(start or 0)}-{int(end) if end is not None else 'end'}" if part else ""
    opts = {"outtmpl": os.path.join(dl_dir, "%(title).70s [%(id)s]" + tag + ".%(ext)s"),
            "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
            "merge_output_format": "mp4", "ffmpeg_location": ffmpeg_exe(),
            "quiet": os.environ.get("CF_DL_DEBUG") != "1", "noprogress": True,
            # slow / flaky connections: wait longer, retry a lot, resume partial files
            "socket_timeout": 60, "retries": 20, "fragment_retries": 20,
            "extractor_retries": 5, "file_access_retries": 5, "continuedl": True,
            "retry_sleep_functions": {"http": lambda n: min(30, 2 ** n),
                                      "fragment": lambda n: min(30, 2 ** n)},
            # YouTube now needs a JavaScript runtime to unlock all formats; use Node.js if present
            "js_runtimes": js_runtimes(),
            "progress_hooks": [lambda d: check_stop()]}         # Stop aborts the download
    if part:
        opts.update({
            "download_ranges": download_range_func(None, [(start or 0, end if end is not None
                                                           else float("inf"))]),
            "force_keyframes_at_cuts": True,
            "external_downloader_args": {
                "ffmpeg_i": ["-reconnect", "1", "-reconnect_streamed", "1",
                             "-reconnect_on_network_error", "1", "-reconnect_delay_max", "30",
                             "-rw_timeout", "60000000"] + hls_input_opts(),
                "ffmpeg_o": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]}})
        log(f"Downloading only {fmt_ts(start or 0)} to "
            f"{fmt_ts(end) if end is not None else 'the end'} of {url} "
            "(cut exactly at your times, so this re-encodes - long windows take a while)...")
    else:
        log(f"Downloading {url} ...")
    def fetch():
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=True)
            return y.prepare_filename(info)

    last = None
    for n in range(1, attempts + 1):
        try:
            path = stoppable(fetch)
            if not os.path.exists(path):
                path = os.path.splitext(path)[0] + ".mp4"
            if not os.path.exists(path):
                raise RuntimeError("the download finished but the file is missing")
            log(f"Downloaded: {os.path.basename(path)}")
            return path
        except Exception as e:
            last = e
            msg = strip_ansi(e).strip().splitlines()[-1][:200]
            if "Private video" in msg or "Video unavailable" in msg or "Unsupported URL" in msg:
                break
            if n < attempts:
                log(f"  Download problem ({msg}). Retrying in {10 * n}s ({n}/{attempts - 1})...")
                pause(10 * n)
    msg = strip_ansi(last).strip()
    raise RuntimeError(f"Couldn't download the video after {attempts} tries: {msg}\n"
                       "Check the link and your internet connection and try again, or download the "
                       "video another way and paste its file path instead.") from last
