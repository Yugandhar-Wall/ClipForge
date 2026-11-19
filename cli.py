"""Batch mode: clip every video in a folder (or the files/URLs you list).

  python cli.py "D:\\Videos\\GTA6"                 # every video in the folder
  python cli.py stream1.mp4 https://youtu.be/xyz --clips 10

Uses the settings saved from the app (API key, layout, logo, campaign rules, etc.);
flags override them:  --logo sponsor.png  --rules rules.pdf  (--logo none / --rules none to turn off)
"""
import argparse
import glob
import os
import sys

from cf import publish as PUB
from cf.brand import store_logo
from cf.pipeline import process
from cf.rules import read_rules_file
from cf.settings import load_settings

VIDEO_EXT = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".flv", ".m4v")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", help="video files, folders or URLs")
    ap.add_argument("--clips", type=int)
    ap.add_argument("--min", type=int, dest="min_len")
    ap.add_argument("--max", type=int, dest="max_len")
    ap.add_argument("--model")
    ap.add_argument("--layout", choices=["blur", "crop"])
    ap.add_argument("--hint", dest="content_hint")
    ap.add_argument("--creator")
    ap.add_argument("--formats", help="comma list, e.g. cold_open,replay,top")
    ap.add_argument("--logo", help="sponsor logo image (replaces the saved one); 'none' = no logo")
    ap.add_argument("--logo-anim", dest="logo_anim", choices=["float", "pulse", "none"])
    ap.add_argument("--rules", help="campaign rules file (.txt .md .pdf .docx); 'none' = no rules")
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
        cfg["logo_path"] = "" if a.logo.lower() == "none" else store_logo(a.logo)
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
        cfg["rules_text"] = "" if a.rules.lower() == "none" else read_rules_file(a.rules)
    if cfg.get("rules_text"):
        print("Campaign rules are ON (saved in the app or --rules). Use --rules none to turn off.")

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


if __name__ == "__main__":
    main()
