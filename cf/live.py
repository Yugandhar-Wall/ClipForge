"""Live-stream monitor: watch a stream while it happens and clip the viral moments.

What a session does, start to finish
1. resolve()   - asks yt-dlp for the stream's live video address (waits if it hasn't started).
2. Recorder    - ffmpeg copies the stream into short .ts pieces (30 s each) in cache/live/...
3. Chat reader - collects live chat messages (YouTube, Twitch) with their clock time.
4. Every `live_interval` minutes: the new pieces (+ a short overlap with the last check, so a
   moment that straddles two checks is never cut in half) are joined into one file and run
   through the normal clip pipeline in live mode: transcript + chat bursts -> the AI either
   picks moments scoring `live_min_score`+ or says "nothing here".
5. Clips are rendered, queued for YouTube as PRIVATE, and announced on Telegram.
6. Checked pieces are deleted (only the overlap is kept), so disk use stays at a few minutes
   of video however long the stream runs. "Keep the full recording" moves them to the output
   folder instead.
"""
import atexit
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque

from . import chat as CH
from . import publish as PUB
from .pipeline import process
from .tools import (CACHE_DIR, NO_WINDOW, OUTPUT_DIR, ffmpeg_exe, fmt_ts, hls_input_opts, is_hls,
                    js_runtimes, node_exe, probe, run_ffmpeg, slug, strip_ansi)
from .transcribe import file_key

LIVE_DIR = os.path.join(CACHE_DIR, "live")
_WAIT_WORDS = ("will begin", "offline", "not currently live", "premieres in", "is upcoming",
               "not live", "starts in", "has not started", "waiting for")


class LiveError(RuntimeError):
    pass


class NotLiveYet(Exception):
    pass


class _Quiet:
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


def resolve(url):
    """Ask yt-dlp where the live video is. Returns
    {live, status, inputs: [(url, headers)], title, channel, id}. Raises NotLiveYet."""
    import yt_dlp
    base = {"quiet": True, "no_warnings": True, "noprogress": True, "skip_download": True,
            "noplaylist": True, "socket_timeout": 30, "extractor_retries": 2,
            "js_runtimes": js_runtimes(), "logger": _Quiet()}
    info, last = None, None
    for fmt in ("b[protocol^=m3u8][height<=1080]/b[height<=1080]/b", "bv*[height<=1080]+ba/bv*+ba/b"):
        try:
            with yt_dlp.YoutubeDL({**base, "format": fmt}) as y:
                info = y.extract_info(url, download=False)
            break
        except Exception as e:
            msg = strip_ansi(e).strip()
            last = msg
            if "Requested format is not available" in msg:
                continue
            if any(w in msg.lower() for w in _WAIT_WORDS):
                raise NotLiveYet(msg.splitlines()[-1][:160]) from None
            raise LiveError(f"Couldn't open that link: {msg.splitlines()[-1][:300]}") from None
    if info is None:
        raise LiveError(f"Couldn't open that link: {last}")
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise NotLiveYet("the channel is not live")
        info = next((e for e in entries if e.get("is_live")), entries[0])
    status = info.get("live_status") or ("is_live" if info.get("is_live") else "not_live")
    if status == "is_upcoming":
        raise NotLiveYet("the stream hasn't started yet")
    fmts = info.get("requested_formats") or [info]
    return {"live": status == "is_live", "status": status,
            "inputs": [(f["url"], dict(f.get("http_headers") or {})) for f in fmts if f.get("url")],
            "title": info.get("title") or "", "id": info.get("id") or "",
            "channel": info.get("channel") or info.get("uploader") or ""}


class LiveSession:
    def __init__(self, url, cfg, seg_len=30, overlap=None, interval_s=None, resolver=None,
                 chat_reader=None, wait_s=60):
        self.url = (url or "").strip()
        self.cfg = dict(cfg)
        self.interval = float(interval_s or max(1.0, float(cfg.get("live_interval") or 5)) * 60)
        self.seg_len = int(seg_len)
        max_len = int(cfg.get("max_len") or 59)
        self.overlap = float(overlap if overlap is not None else min(150, max(60, max_len + 20)))
        self._resolve = resolver or resolve
        self._chat_reader = chat_reader        # tests: callable(buf) -> reader or None
        self._wait_s = wait_s
        self.stall_s = max(90, 3 * self.seg_len)   # no new video for this long = the feed is stuck
        self._cur = {}
        self._reclog = ""
        self.lines = deque(maxlen=600)
        self.on_line = None                    # optional callback(line), used by cli.py
        self.state, self.error = "starting", ""
        self.title = self.channel = ""
        self.work = self.out_dir = ""
        self.started = time.time()
        self.ended = None
        self.checks = 0
        self.clips = []                        # result dicts of every clip made
        self.uploaded = 0
        self.cost = 0.0
        self.taken = []                        # (start, end) in stream seconds already clipped
        self.next_check = None
        self._segs = {}                        # index -> {path, dur, end_wall, start}
        self._pos = 0.0                        # recorded seconds so far (sum of pieces)
        self._hi = -1
        self._done_idx = -1                    # last piece already checked
        self._fails = 0
        self._proc = None
        self._feeder = None                    # yt-dlp process feeding the recorder (pipe mode)
        self._modes = ["pipe", "direct"]       # pipe: yt-dlp downloads, ffmpeg only cuts pieces
        self._rec_done = threading.Event()
        self._stop = threading.Event()
        self._rec_error = ""
        self.chatbuf = None
        self._chat = None
        self._chat_base = None
        self._told_yt = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    # ------------------------------------------------------------ public
    def start(self):
        self._thread.start()
        return self

    def stop(self):
        if not self._stop.is_set():
            self.log("Stopping: finishing the last check...")
        self._stop.set()

    def join(self, timeout=None):
        self._thread.join(timeout)

    @property
    def running(self):
        return self._thread.is_alive()

    def status(self):
        disk = 0
        try:
            if self.work and os.path.isdir(self.work):
                disk = sum(os.path.getsize(os.path.join(self.work, f)) for f in os.listdir(self.work))
        except OSError:
            pass
        return {"state": self.state, "error": self.error, "title": self.title, "channel": self.channel,
                "elapsed": (self.ended or time.time()) - self.started, "recorded": self._pos,
                "checks": self.checks,
                "clips": len(self.clips), "uploaded": self.uploaded, "disk_mb": disk / 1e6,
                "next_in": max(0, self.next_check - time.time()) if self.next_check and self.state == "recording" else None,
                "chat": (self.chatbuf.total if self.chatbuf and (self.chatbuf.total or
                                                                 (self._chat and self._chat.is_alive())) else None),
                "chat_source": getattr(self._chat, "name_", "") if self._chat else "",
                "out_dir": self.out_dir, "cost": self.cost}

    def log(self, msg):
        line = f"{time.strftime('%H:%M:%S')}  {msg}"
        self.lines.append(line)
        if self.on_line:
            try:
                self.on_line(line)
            except Exception:
                pass
        if self.out_dir:
            try:
                with open(os.path.join(self.out_dir, "live_log.txt"), "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass

    # ------------------------------------------------------------ main loop
    def _run(self):
        try:
            info = self._wait_live()
            if info is None:
                return
            self._setup(info)
            rec = threading.Thread(target=self._record, args=(info,), daemon=True)
            rec.start()
            self._start_chat()
            self.state = "recording"
            every = f"{self.interval / 60:.0f} min" if self.interval >= 60 else f"{self.interval:.0f} s"
            self.log(f"Recording '{self.title or self.url}'. First check in {every}.")
            self._notify(f"🔴 ClipForge is watching: {self.title or self.url}\n"
                         f"Checking every {every} for viral moments.")
            self.next_check = time.time() + self.interval
            while not self._stop.is_set() and not self._rec_done.is_set():
                if time.time() >= self.next_check:
                    began = time.time()
                    more = self._checkpoint(final=False)
                    self.next_check = time.time() if more else max(time.time(), began + self.interval)
                self._space_guard()
                self._stop.wait(1.0)
            self.state = "stopping"
            self._stop.set()
            self._quit_proc()
            rec.join(30)
            if self._rec_error:
                self.log(self._rec_error)
            while self._checkpoint(final=True):
                pass
        except Exception as e:
            self.error = str(e)
            self.log(f"ERROR: {e}")
            self._notify(f"❌ ClipForge live monitor stopped: {str(e)[:300]}")
        finally:
            self._stop.set()
            self._quit_proc()
            if self._chat:
                self._chat.stop()
                self._chat.join(6)
            self._finish_recording()
            if self.work:
                shutil.rmtree(self.work, ignore_errors=True)
            if self.checks or self.clips:
                self.log(f"Finished: {self.checks} check(s), {len(self.clips)} clip(s) made, "
                         f"{self.uploaded} uploaded." + (f" Clips are in {self.out_dir}" if self.clips else ""))
                self._notify(f"⏹ Stopped watching {self.title or 'the stream'}: {self.checks} checks, "
                             f"{len(self.clips)} clips made, {self.uploaded} uploaded as private.")
            self.ended = time.time()
            self.state = "error" if self.error else "stopped"

    def _wait_live(self):
        told, deadline = False, time.time() + 6 * 3600
        while not self._stop.is_set():
            why = ""
            try:
                info = self._resolve(self.url)
            except NotLiveYet as e:
                info, why = None, str(e)
            if info and info["live"]:
                if not info["inputs"]:
                    raise LiveError("The stream is live but no video address was found.")
                return info
            if info:
                raise LiveError("That link isn't live right now (it looks like a finished video). "
                                "Use the Create page for finished videos.")
            if not told:
                self.state, told = "waiting", True
                self.log(f"The stream isn't live yet ({why}). Waiting; I check every minute.")
            if time.time() > deadline:
                raise LiveError("Waited 6 hours and the stream never started.")
            self._stop.wait(self._wait_s)
        return None

    def _setup(self, info):
        self.title, self.channel = info.get("title") or "", info.get("channel") or ""
        shutil.rmtree(LIVE_DIR, ignore_errors=True)          # leftovers of a crashed session
        stamp = time.strftime("%Y-%m-%d_%H%M%S")
        self.work = os.path.join(LIVE_DIR, stamp)
        os.makedirs(self.work, exist_ok=True)
        out = os.path.join(OUTPUT_DIR, "live_" + slug(self.channel or self.title or "stream", 40), stamp)
        os.makedirs(out, exist_ok=True)
        self.out_dir = out
        self._reclog = os.path.join(out, "recorder_log.txt")
        for ln in list(self.lines):                           # lines logged before the folder existed
            with open(os.path.join(out, "live_log.txt"), "a", encoding="utf-8") as f:
                f.write(ln + "\n")

    def _start_chat(self):
        if not self.cfg.get("live_chat_on", True):
            return
        self.chatbuf = CH.ChatBuffer()
        if self._chat_reader:
            self._chat = self._chat_reader(self.chatbuf)
        else:
            self._chat = CH.reader_for(self.url, self.chatbuf, self.work, self.log, node_exe())
        if self._chat:
            self._chat.start()
            self.log(f"Reading {getattr(self._chat, 'name_', 'live chat')}.")
        else:
            self.chatbuf = None
            self.log("Live chat can't be read on this site; using speech only.")

    def _notify(self, text):
        PUB.telegram(self.cfg, text)

    def _space_guard(self):
        try:
            free = shutil.disk_usage(self.work).free
        except OSError:
            return
        if free < 1.5e9:
            self.log("Less than 1.5 GB of disk space left: stopping so the PC doesn't fill up.")
            self._notify("⚠️ ClipForge live monitor stopped: the disk is almost full.")
            self._stop.set()

    # ------------------------------------------------------------ recorder
    @property
    def _reclog_path(self):
        return self._reclog or os.path.join(self.work, "recorder.log")

    def _popen(self, cmd, **kw):
        """Start a recorder process with its output appended to the recorder log."""
        logf = open(self._reclog_path, "ab")
        try:
            return subprocess.Popen(cmd, stdout=kw.pop("stdout", logf), stderr=logf,
                                    creationflags=NO_WINDOW, **kw)
        finally:
            logf.close()

    def _seg_args(self, idx):
        return ["-c", "copy", "-f", "segment", "-segment_time", str(self.seg_len),
                "-segment_format", "mpegts", "-reset_timestamps", "1",
                "-segment_start_number", str(idx), os.path.join(self.work, "seg_%06d.ts")]

    def _spawn_pipe(self, idx):
        """yt-dlp downloads the live stream (it keeps up with YouTube's changes) and streams it
        into ffmpeg, which only cuts it into pieces."""
        node = node_exe()
        dl = [sys.executable, "-m", "yt_dlp", "-q", "--no-warnings", "--no-playlist", "--no-part",
              "-f", "b[vcodec!=?none][acodec!=?none][height<=?1080]/b[vcodec!=?none][acodec!=?none]",
              "--hls-use-mpegts", "--retries", "10", "--fragment-retries", "10",
              "--socket-timeout", "30", "-o", "-"]
        if node:
            dl += ["--js-runtimes", f"node:{node}"]
        dl.append(self.url)
        ff = [ffmpeg_exe(), "-hide_banner", "-loglevel", "warning", "-y", "-i", "pipe:0",
              "-map", "0:v:0", "-map", "0:a:0?"] + self._seg_args(idx)
        self._feeder = self._popen(dl, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        proc = self._popen(ff, stdin=self._feeder.stdout)
        self._feeder.stdout.close()        # ffmpeg owns the pipe now; it sees the end when yt-dlp stops
        return proc

    def _spawn(self, info, idx):
        if self._modes[0] == "pipe":
            return self._spawn_pipe(idx)
        self._feeder = None
        cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "warning", "-y"]
        for url, headers in info["inputs"]:
            if url.lower().startswith("http"):
                cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_on_network_error", "1",
                        "-reconnect_delay_max", "15", "-rw_timeout", "30000000"]
                if headers:
                    cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
            if is_hls(url):
                cmd += hls_input_opts()      # YouTube's piece addresses have no file ending
            cmd += ["-i", url]
        a = len(info["inputs"]) - 1
        cmd += ["-map", "0:v:0", "-map", f"{a}:a:0?"] + self._seg_args(idx)
        return self._popen(cmd, stdin=subprocess.PIPE)

    def _seg_files(self):
        try:
            return sorted(f for f in os.listdir(self.work) if re.fullmatch(r"seg_\d{6}\.ts", f))
        except OSError:
            return []

    def _record(self, info):
        fails = 0
        if len(info.get("inputs") or []) > 1:
            # video and audio come as separate feeds: yt-dlp can't stream that to a pipe,
            # so ffmpeg records both feeds directly (yt-dlp stays as the backup)
            self._modes = ["direct", "pipe"]
        try:
            while not self._stop.is_set():
                files = self._seg_files()
                if files:
                    self._hi = max(self._hi, int(files[-1][4:10]))
                before = set(files)
                n_in = len(info.get("inputs") or [])
                self._rec_note(f"attempt: {self._modes[0]} recorder, {n_in} input feed(s), "
                               f"hls options {hls_input_opts() if self._modes[0] == 'direct' else 'n/a'}")
                self._proc = self._spawn(info, self._hi + 1)
                born = time.time()
                while self._proc.poll() is None and not self._stop.is_set():
                    time.sleep(0.5)
                    if self._feeder is not None and self._feeder.poll() is not None and \
                            time.time() - max(born, self._newest_write()) > 20:
                        self._quit_proc()       # the downloader quit and nothing more is coming
                        break
                    if time.time() - max(born, self._newest_write()) > self.stall_s:
                        self.log("No new video is arriving; restarting the recorder.")
                        self._quit_proc()
                        break
                if self._stop.is_set():
                    break
                new = [f for f in self._seg_files() if f not in before]
                got = 0
                for f in new:
                    try:
                        got += os.path.getsize(os.path.join(self.work, f))
                    except OSError:
                        pass
                ran = time.time() - born
                made = len(new) if got > 300_000 else 0       # a few empty pieces = it never really ran
                self._rec_note(f"ended after {ran:.0f}s with exit code {self._proc.returncode}: "
                               f"{len(new)} piece(s), {got / 1e6:.2f} MB")
                try:                                   # ffmpeg ended by itself: over, or a drop?
                    again = self._resolve(self.url)
                    live = again["live"] and bool(again["inputs"])
                except NotLiveYet:
                    again, live = None, False
                except Exception as e:
                    again, live = None, None
                    self._rec_note(f"checking the stream again failed: {e}")
                if live is False:
                    self.log("The stream has ended.")
                    break
                fails = 0 if (made > 0 and live) else fails + 1
                if made == 0 and live is not False and fails % 2 == 0:
                    # this way of recording failed twice in a row: try the other one
                    self._modes.append(self._modes.pop(0))
                    self.log("Recording didn't start; trying the "
                             + ("yt-dlp downloader" if self._modes[0] == "pipe" else "direct recorder")
                             + " instead.")
                if fails >= 6:
                    self._rec_error = ("The recording kept failing, so I stopped. Last recorder message: "
                                       + self._rec_tail())
                    break
                self.log(f"The recording dropped after {ran:.0f}s ({got / 1e6:.1f} MB); reconnecting... "
                         f"[{self._rec_tail(2)}]")
                if live:
                    info = again
                self._stop.wait(min(30, 3 * (fails + 1)))
        except Exception as e:
            self._rec_error = f"Recorder error: {e}"
        finally:
            self._rec_done.set()

    def _newest_write(self):
        files = self._seg_files()
        try:
            return os.path.getmtime(os.path.join(self.work, files[-1])) if files else 0.0
        except OSError:
            return 0.0

    def _rec_note(self, text):
        try:
            with open(self._reclog_path, "a", encoding="utf-8") as f:
                f.write(f"\n===== {time.strftime('%H:%M:%S')} {text}\n")
        except OSError:
            pass

    def _rec_tail(self, n=6):
        try:
            with open(self._reclog_path, "rb") as f:
                text = f.read()[-6000:].decode("utf-8", "replace")
        except OSError:
            return "none"
        text = re.sub(r"https?://\S+", "<link>", text)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("=====")]
        return " | ".join(lines[-n:])[:700] or "none"

    def _quit_proc(self):
        p, feeder = self._proc, self._feeder
        if feeder is not None and feeder.poll() is None:
            try:
                feeder.terminate()             # ffmpeg then sees the end of the stream and finishes cleanly
                feeder.wait(8)
            except Exception:
                try:
                    feeder.kill()
                except Exception:
                    pass
        if not p or p.poll() is not None:
            return
        try:
            if feeder is not None:
                p.wait(10)
            else:
                p.stdin.write(b"q")
                p.stdin.flush()
                p.wait(8)
        except Exception:
            pass
        if p.poll() is None:
            try:
                p.terminate()
                p.wait(5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    # ------------------------------------------------------------ pieces
    def _scan(self):
        """Register finished pieces (the newest file is still being written while ffmpeg runs)."""
        names = self._seg_files()
        if self._proc is not None and self._proc.poll() is None and names:
            names = names[:-1]
        for n in names:
            idx = int(n[4:10])
            self._hi = max(self._hi, idx)
            if idx in self._segs or idx <= self._done_idx:
                continue
            p = os.path.join(self.work, n)
            try:
                info = probe(p)
                dur = float(info["duration"] or 0) if info.get("width") else 0.0
            except Exception:
                dur = 0.0
            if dur < 0.3:                      # empty / broken piece (e.g. cut by a reconnect)
                try:
                    os.remove(p)
                except OSError:
                    pass
                continue
            self._segs[idx] = {"path": p, "dur": dur, "end_wall": os.path.getmtime(p), "start": self._pos}
            self._pos += dur

    def _drop(self, idx):
        sg = self._segs.pop(idx, None)
        if not sg:
            return
        try:
            if self.cfg.get("live_keep"):
                keep = os.path.join(self.out_dir, "recording")
                os.makedirs(keep, exist_ok=True)
                shutil.move(sg["path"], os.path.join(keep, os.path.basename(sg["path"])))
            else:
                os.remove(sg["path"])
        except OSError:
            pass

    def _concat(self, idxs, out_base):
        """Join pieces without re-encoding. Returns the file path."""
        names = [os.path.basename(self._segs[i]["path"]) for i in idxs]
        last = None
        for ext, extra in ((".mp4", ["-movflags", "+faststart"]), (".mkv", [])):
            out = out_base + ext
            try:
                join_pieces(names, out, self.work, extra)
                return out
            except Exception as e:
                last = e
                try:
                    os.remove(out)
                except OSError:
                    pass
        raise LiveError(f"Couldn't join the recorded pieces: {str(last)[-300:]}")

    def _chat_for(self, idxs, total):
        if not self.chatbuf:
            return [], ""
        msgs, off, first_wall = [], 0.0, None
        for i in idxs:
            sg = self._segs[i]
            w1 = sg["end_wall"]
            w0 = w1 - sg["dur"]
            first_wall = w0 if first_wall is None else first_wall
            for w, text in self.chatbuf.between(w0, w1):
                msgs.append((off + (w - w0), text))
            off += sg["dur"]
        if not self.chatbuf.total:
            return [], ""
        sigs, note, _ = CH.analyse(msgs, total, base_rate=self._chat_base)
        rate = len(msgs) / max(1.0, total / CH.BUCKET)
        self._chat_base = rate if self._chat_base is None else 0.7 * self._chat_base + 0.3 * rate
        self.chatbuf.trim(first_wall - 5)
        return sigs, note

    # ------------------------------------------------------------ one check
    def _checkpoint(self, final):
        """Check everything recorded since the last check. Returns True when more is waiting."""
        self._scan()
        order = sorted(self._segs)
        new = [i for i in order if i > self._done_idx]
        new_dur = sum(self._segs[i]["dur"] for i in new)
        if new_dur < (5 if final else min(20.0, self.interval * 0.3)):
            if not final:
                self.log("Nothing new was recorded since the last check.")
            return False
        cap = max(self.interval * 2, 600)                    # catch up in parts if we fell behind
        take, acc = [], 0.0
        for i in new:
            if take and acc + self._segs[i]["dur"] > cap:
                break
            take.append(i)
            acc += self._segs[i]["dur"]
        leftover = len(take) < len(new)
        ov = [i for i in order if i <= self._done_idx]      # the overlap kept by the last check
        reviewed = sum(self._segs[i]["dur"] for i in ov)
        win = ov + take
        total = reviewed + acc
        s0 = self._segs[win[0]]["start"]                     # stream second the joined file starts at
        new0 = s0 + reviewed
        self.checks += 1
        self._cur = {"s0": s0, "reviewed": reviewed, "total": total, "final": final}
        self.log(f"Check {self.checks}: {fmt_ts(new0)} to {fmt_ts(new0 + acc)} of the recording.")
        window = key = None
        try:
            window = self._concat(win, os.path.join(self.work, f"check_{self.checks:04d}"))
            key = file_key(window)
            sigs, note = self._chat_for(win, total)
            if sigs:
                self.log("  Chat bursts at: " + ", ".join(fmt_ts(new0 - reviewed + s["t"]) for s in sigs))
            h, rem = divmod(int(new0), 3600)
            live = {"out_dir": self.out_dir, "prefix": f"{h:02d}h{rem // 60:02d}m{rem % 60:02d}s_",
                    "t0": s0, "min_score": int(self.cfg.get("live_min_score") or 70),
                    "max_clips": int(self.cfg.get("live_max_clips") or 2),
                    "chat_signals": sigs, "chat_note": note, "reviewed": reviewed,
                    "taken": [(a - s0, b - s0) for a, b in self.taken if b > s0],
                    "more_coming": not final or leftover}
            res = process(window, self.cfg, log=lambda m: self.log("  " + str(m)), live=live)
        except Exception as e:
            self._fails += 1
            msg = str(e).strip().splitlines()[0][:300] if str(e).strip() else type(e).__name__
            again = self._fails < 3 and not final
            self.log(f"  This check failed: {msg}" + (" I'll retry it with the next check." if again
                                                       else " Skipping this part."))
            if self._fails == 1:
                self._notify(f"⚠️ ClipForge live check failed: {msg}")
            if again:
                return False
            res = None
        finally:
            self._cleanup_check(window, key)
        self._fails = 0
        need_from = None                                     # a moment still unfolding starts here
        if res:
            if res.get("deferred_start") is not None:
                need_from = max(s0 + res["deferred_start"] - 3, s0 + total - 240)
            self.cost += float(res.get("est_cost_usd") or 0)
            made = res.get("clips") or []
            for c in made:
                if c.get("start") is not None:
                    self.taken.append((s0 + c["start"], s0 + c["end"]))
                    c["stream_time"] = fmt_ts(s0 + c["start"])
            if made:
                self.clips += made
                self._announce(made, bool(res.get("campaign")))
        self._done_idx = take[-1]
        keep, acc2 = set(), 0.0
        for i in reversed([i for i in sorted(self._segs) if i <= self._done_idx]):
            sg = self._segs[i]
            wanted = need_from is not None and sg["start"] + sg["dur"] > need_from
            if final and not leftover or (acc2 >= self.overlap and not wanted):
                break
            keep.add(i)
            acc2 += sg["dur"]
        for i in [i for i in list(self._segs) if i <= self._done_idx and i not in keep]:
            self._drop(i)
        self.taken = [t for t in self.taken if t[1] > self._pos - 3600]
        return leftover

    def _cleanup_check(self, window, key):
        for p in (window, os.path.join(self.work, "join.txt")):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass
        if key:
            try:
                for f in os.listdir(CACHE_DIR):
                    if f.startswith(key):
                        os.remove(os.path.join(CACHE_DIR, f))
            except OSError:
                pass

    def _announce(self, made, campaign):
        for c in made:
            self.log(f"  CLIP MADE at {c.get('stream_time', '?')}: {c['title']} (score {c.get('score')})")
            self._notify(f"🎬 Live clip made ({c.get('stream_time', '?')} into the stream): "
                         f"{c['title'][:90]}\nScore {c.get('score')} · {c['length']:.0f}s")
        if not self.cfg.get("live_upload", True):
            return
        if not PUB.connected():
            if not self._told_yt:
                self._told_yt = True
                self.log("  YouTube isn't connected, so clips are saved but not uploaded "
                         "(connect it on the Publish page).")
            return
        try:
            PUB.queue_run(made, campaign=campaign)
            up, _, _ = PUB.upload_queue({**self.cfg, "yt_mode": "review"},
                                        log=lambda m: self.log("  " + str(m)))
            self.uploaded += up
        except Exception as e:
            self.log(f"  YouTube upload problem: {e}")
            self._notify(f"⚠️ ClipForge couldn't upload a live clip: {str(e)[:300]}")

    def _finish_recording(self):
        """With 'keep the full recording': join the kept pieces into one file at the end."""
        if not (self.cfg.get("live_keep") and self.out_dir):
            return
        keep = os.path.join(self.out_dir, "recording")
        for sg in list(self._segs.values()):                 # pieces still in the work folder
            try:
                os.makedirs(keep, exist_ok=True)
                shutil.move(sg["path"], os.path.join(keep, os.path.basename(sg["path"])))
            except OSError:
                pass
        if not os.path.isdir(keep):
            return
        names = sorted(f for f in os.listdir(keep) if f.endswith(".ts"))
        if not names:
            return
        try:
            out = os.path.join(self.out_dir, "full_recording.mp4")
            join_pieces(names, out, keep)
            shutil.rmtree(keep, ignore_errors=True)
            self.log(f"Full recording saved: {out}")
        except Exception:
            self.log(f"Full recording kept as pieces in {keep}")


def join_pieces(names, out, folder, extra=()):
    """Join recorded .ts pieces (file names inside `folder`) into one file without re-encoding."""
    with open(os.path.join(folder, "join.txt"), "w", encoding="utf-8") as f:
        f.writelines(f"file '{n}'\n" for n in names)
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", "join.txt", "-map", "0:v:0", "-map", "0:a:0?",
                "-c", "copy", "-fflags", "+genpts", "-avoid_negative_ts", "make_zero", *extra,
                os.path.abspath(out)], cwd=folder)


# ------------------------------------------------------------ one session at a time
_current = None
_guard = threading.Lock()


def current():
    return _current


def start(url, cfg):
    global _current
    with _guard:
        if _current and _current.running:
            raise LiveError("A live stream is already being watched. Stop it first.")
        if not re.match(r"https?://", (url or "").strip()):
            raise LiveError("Paste the link to the live stream (it must start with http).")
        if not (cfg.get("api_key") or "").strip():
            raise LiveError("Add your OpenRouter API key in Settings first.")
        _current = LiveSession(url, cfg).start()
        return _current


def stop():
    if _current:
        _current.stop()


@atexit.register
def _kill_on_exit():
    if _current:
        _current._stop.set()
        _current._quit_proc()
        if _current._chat:
            _current._chat.stop()
