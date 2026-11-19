"""Batch mode: clip every video in a folder (or the files/URLs you list).

  python cli.py "D:\\Videos\\GTA6"                 # every video in the folder
  python cli.py stream1.mp4 https://youtu.be/xyz --clips 10

Uses the preferences saved from the app (API key, layout, style, clip lengths...); flags
override them. A sponsor logo and campaign rules are per run, so they only come from flags:
  --logo sponsor.png  --rules rules.pdf
Facecam layout: give both boxes as x,y,width,height fractions of the frame (0-1), e.g.
  --layout facecam --cam 0.78,0.70,0.20,0.27 --game 0.2,0,0.6,1 --cam-size 0.4
(shapes are corrected automatically; drawing them in the app is easier).
"""
import argparse
import glob
import os
import sys

from cf import publish as PUB
from cf import storage
from cf.brand import store_logo
from cf.pipeline import process
from cf.rules import read_rules_file
from cf.settings import load_settings
from cf.tools import VIDEO_EXT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", help="video files, folders or URLs")
    ap.add_argument("--clips", type=int)
    ap.add_argument("--min", type=int, dest="min_len")
    ap.add_argument("--max", type=int, dest="max_len")
    ap.add_argument("--model")
    ap.add_argument("--layout", choices=["blur", "crop", "facecam", "split"])
    ap.add_argument("--cam", help="facecam box x,y,w,h (0-1 of the frame)")
    ap.add_argument("--game", help="gameplay box x,y,w,h (0-1 of the frame)")
    ap.add_argument("--cam-size", dest="cam_size", type=float, default=0.4,
                    help="facecam share of the clip's height, 0.25-0.55 (default 0.4)")
    ap.add_argument("--hint", dest="content_hint")
    ap.add_argument("--creator")
    ap.add_argument("--formats", help="comma list, e.g. cold_open,replay,top")
    ap.add_argument("--logo", help="sponsor logo image for this run")
    ap.add_argument("--logo-anim", dest="logo_anim", choices=["float", "pulse", "none"])
    ap.add_argument("--rules", help="campaign rules file for this run (.txt .md .pdf .docx)")
    ap.add_argument("--motion", choices=["on", "off"], help="HyperFrames motion graphics")
    ap.add_argument("--from", dest="range_start", help="use footage from this time only, e.g. 2:56:29")
    ap.add_argument("--to", dest="range_end", help="use footage up to this time (default: end of video)")
    ap.add_argument("--split", choices=["on", "off"],
                    help="split-screen: content on top, gameplay (from the gameplay folder) underneath")
    ap.add_argument("--game", help="gameplay folder to use with --split on (default: any)")
    ap.add_argument("--live", action="store_true",
                    help="the input is a LIVE stream link: watch it and clip viral moments until it ends (Ctrl+C stops)")
    ap.add_argument("--youtube", choices=["off", "review", "scheduled"],
                    help="upload to YouTube: review = private until you publish, "
                         "scheduled = public every --interval minutes")
    ap.add_argument("--interval", type=int, help="minutes between scheduled videos (default 10)")
    ap.add_argument("--memes", choices=["on", "off"], help="trending meme sounds from the sounds folder")
    a = ap.parse_args()

    cfg = load_settings()
    if a.formats:
        cfg["formats"] = [f.strip() for f in a.formats.split(",") if f.strip()]
    for k in ("min_len", "max_len", "model", "layout", "content_hint", "creator"):
        if getattr(a, k):
            cfg[k] = getattr(a, k)
    if a.clips:
        cfg["n_clips"] = a.clips
    if a.logo:
        cfg["logo_path"] = store_logo(a.logo)
    if a.cam or a.game:
        try:
            box = lambda t: [float(v) for v in t.split(",")][:4]   # noqa: E731
            cfg["facecam"] = {"cam": box(a.cam), "game": box(a.game), "split": a.cam_size}
        except (AttributeError, ValueError):
            sys.exit("--cam and --game both need x,y,w,h fractions, e.g. --cam 0.78,0.7,0.2,0.27")
    if a.logo_anim:
        cfg["logo_anim"] = a.logo_anim
    cfg["range_start"], cfg["range_end"] = a.range_start or "", a.range_end or ""
    if a.split:
        cfg["split_on"] = a.split == "on"
    if a.game:
        cfg["split_game"] = a.game
    if a.youtube:
        cfg["yt_on"] = a.youtube != "off"
        if a.youtube != "off":
            cfg["yt_mode"] = a.youtube
    if a.interval:
        cfg["yt_interval"] = a.interval
    if a.memes:
        cfg["meme_on"] = a.memes == "on"
    if a.motion:
        cfg["motion_on"] = a.motion == "on"
    if a.rules:
        cfg["rules_text"] = read_rules_file(a.rules)
        print("Campaign rules are ON for this run.")

    if a.live:
        import time
        from cf import live as LIVE
        sess = LIVE.start(a.inputs[0], cfg)
        for ln in list(sess.lines):
            print(ln)
        sess.on_line = print
        try:
            while sess.running:
                time.sleep(1)
        except KeyboardInterrupt:
            sess.stop()
            sess.join(900)
        return

    jobs = []
    for item in a.inputs:
        if os.path.isdir(item):
            jobs += sorted(p for p in glob.glob(os.path.join(item, "*")) if p.lower().endswith(VIDEO_EXT))
        else:
            jobs.append(item)
    if not jobs:
        sys.exit("No videos found.")
    failed = []
    for n, src in enumerate(jobs, 1):
        print(f"\n===== [{n}/{len(jobs)}] {src} =====")
        try:
            res = process(src, cfg, log=print)
            PUB.after_run(res, cfg, log=print)
        except Exception as e:
            print(f"FAILED: {e}")
            failed.append(src)
    print(f"\nFinished {len(jobs) - len(failed)}/{len(jobs)} videos." +
          (f" Failed: {failed}" if failed else ""))
    storage.clean(cfg.get("keep_days", 6), log=print)


if __name__ == "__main__":
    main()
