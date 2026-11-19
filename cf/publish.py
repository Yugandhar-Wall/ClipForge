"""YouTube upload + Telegram notifications. No extra packages: OAuth and the resumable
upload are done with `requests` against Google's documented HTTP endpoints.

Flow
- connect(): opens the browser once for Google sign-in; the refresh token is kept in
  youtube_token.json next to the app (never sent anywhere but Google).
- queue_run(): adds a finished run's clips to youtube_queue.json.
- upload_queue(): uploads every pending item, one by one, as PRIVATE. In "scheduled" mode
  each video also gets a publishAt time, `interval` minutes apart, so YouTube makes them
  public on its own. Campaign clips are never scheduled: they wait for your review.
- publish(ids): switches reviewed videos to public.
Every upload / failure sends a Telegram message when a bot token + chat id are set.
"""
import base64
import glob
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone

import requests

from .tools import ROOT, read_json

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
TELEGRAM = "https://api.telegram.org"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]   # second one: switch private -> public
TOKEN_FILE = os.path.join(ROOT, "youtube_token.json")
QUEUE_FILE = os.path.join(ROOT, "youtube_queue.json")
CHUNK = 8 * 1024 * 1024
CATEGORIES = {"Gaming": "20", "Entertainment": "24", "People & Blogs": "22",
              "Sports": "17", "Travel & Events": "19", "Science & Technology": "28"}
_lock = threading.Lock()


class PublishError(RuntimeError):
    pass


class QuotaError(PublishError):
    pass


# ------------------------------------------------------------------ Telegram
def telegram(cfg, text):
    """Send a message; returns (ok, error). Never raises."""
    token, chat = (cfg.get("telegram_token") or "").strip(), str(cfg.get("telegram_chat") or "").strip()
    if not token or not chat:
        return False, "Telegram bot token or chat id is not set."
    try:
        r = requests.post(f"{TELEGRAM}/bot{token}/sendMessage", timeout=20,
                          json={"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True})
        d = r.json()
        return (True, "") if d.get("ok") else (False, d.get("description") or f"HTTP {r.status_code}")
    except Exception as e:
        return False, str(e)


# ------------------------------------------------------------------ OAuth
def client_file():
    """client_secret.json, or the file exactly as Google names it on download."""
    for p in [os.path.join(ROOT, "client_secret.json")] + sorted(glob.glob(os.path.join(ROOT, "client_secret*.json"))):
        if os.path.exists(p):
            return p
    return None


def _client():
    p = client_file()
    if not p:
        raise PublishError("No Google OAuth file. Put the client_secret...json you downloaded from "
                           "Google Cloud into the ClipForge folder.")
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    c = d.get("installed") or d.get("web")
    if not c or "client_id" not in c:
        raise PublishError(f"{os.path.basename(p)} isn't an OAuth client file (create a 'Desktop app' client).")
    return c["client_id"], c["client_secret"]


def connected():
    return os.path.exists(TOKEN_FILE)


def disconnect():
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)


def connect(open_browser=webbrowser.open, timeout=300):
    """Google sign-in through a one-off local page (loopback flow with PKCE). Returns channel title."""
    cid, secret = _client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in q or "error" in q:
                got.update({k: v[0] for k, v in q.items()})
            ok = "code" in q and q.get("state", [""])[0] == state
            body = ("<h2>ClipForge is connected to YouTube.</h2><p>You can close this tab.</p>" if ok else
                    "<h2>Sign-in didn't finish.</h2><p>Go back to ClipForge and try again.</p>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    open_browser(url)
    srv.timeout = 1
    end = time.time() + timeout
    while not got and time.time() < end:
        srv.handle_request()
    srv.server_close()
    if not got:
        raise PublishError("Timed out waiting for the Google sign-in (5 minutes).")
    if "error" in got:
        raise PublishError(f"Google sign-in was cancelled or refused: {got['error']}")
    if got.get("state") != state:
        raise PublishError("Sign-in reply didn't match this request. Try again.")
    r = requests.post(TOKEN_URL, timeout=30, data={
        "code": got["code"], "client_id": cid, "client_secret": secret, "redirect_uri": redirect,
        "grant_type": "authorization_code", "code_verifier": verifier})
    d = r.json()
    if "refresh_token" not in d:
        raise PublishError(f"Google didn't return a refresh token: {d.get('error_description') or d}")
    tok = {"refresh_token": d["refresh_token"], "access_token": d.get("access_token"),
           "expires_at": time.time() + int(d.get("expires_in", 3600)) - 60}
    _save_token(tok)
    return channel_title()


def _save_token(tok):
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(tok, f)


def _access_token():
    if not connected():
        raise PublishError("YouTube isn't connected. Click 'Connect YouTube' first.")
    with open(TOKEN_FILE, encoding="utf-8") as f:
        tok = json.load(f)
    if tok.get("access_token") and tok.get("expires_at", 0) > time.time():
        return tok["access_token"]
    cid, secret = _client()
    r = requests.post(TOKEN_URL, timeout=30, data={
        "client_id": cid, "client_secret": secret, "refresh_token": tok["refresh_token"],
        "grant_type": "refresh_token"})
    d = r.json()
    if "access_token" not in d:
        if d.get("error") == "invalid_grant":
            disconnect()
            raise PublishError("Google sign-in expired (apps in 'Testing' expire after 7 days). "
                               "Click 'Connect YouTube' again.")
        raise PublishError(f"Couldn't refresh the Google sign-in: {d.get('error_description') or d}")
    tok.update(access_token=d["access_token"], expires_at=time.time() + int(d.get("expires_in", 3600)) - 60)
    _save_token(tok)
    return tok["access_token"]


def _headers():
    return {"Authorization": f"Bearer {_access_token()}"}


def _check(r):
    if r.status_code < 300:
        return r
    try:
        err = r.json().get("error", {})
        reasons = [e.get("reason", "") for e in err.get("errors", [])]
        msg = err.get("message") or r.text[:300]
    except Exception:
        reasons, msg = [], r.text[:300]
    if r.status_code in (403, 429) and any("quota" in x.lower() or "ratelimit" in x.lower() or
                                           "uploadlimit" in x.lower() for x in reasons):
        raise QuotaError(f"YouTube upload limit reached ({', '.join(reasons)}). The rest stay "
                         "queued; click 'Upload queued clips' later (the limit resets daily).")
    raise PublishError(f"YouTube error {r.status_code}: {msg}")


def channel_title():
    r = _check(requests.get(f"{API}/channels", params={"part": "snippet", "mine": "true"},
                            headers=_headers(), timeout=30))
    items = r.json().get("items") or []
    if not items:
        raise PublishError("This Google account has no YouTube channel.")
    return items[0]["snippet"]["title"]


# ------------------------------------------------------------------ metadata
def _clean(s):
    return (s or "").replace("<", "").replace(">", "").strip()


def metadata(clip, category="20"):
    title = _clean(clip.get("title")) or "Clip"
    if len(title) > 100:
        title = title[:99].rstrip() + "…"
    desc = _clean(clip.get("caption"))
    while len(desc.encode("utf-8")) > 5000:
        desc = desc[:-50]
    tags, used = [], 0
    for k in clip.get("keywords") or []:
        k = _clean(k).replace(",", " ")
        cost = len(k) + (2 if " " in k else 0) + (1 if tags else 0)
        if k and used + cost <= 480:
            tags.append(k)
            used += cost
    return {"title": title, "description": desc, "tags": tags, "categoryId": str(category)}


# ------------------------------------------------------------------ queue
def load_queue():
    return read_json(QUEUE_FILE, [])


def _save_queue(q):
    tmp = QUEUE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(q, f, indent=1, ensure_ascii=False)
    os.replace(tmp, QUEUE_FILE)


def queue_run(clips, campaign=False):
    """Add a run's clips (pipeline result dicts) as pending uploads. Returns how many were added."""
    with _lock:
        q = load_queue()
        have = {x["file"] for x in q}
        n = 0
        for c in clips:
            if c["file"] in have or not os.path.exists(c["file"]):
                continue
            q.append({"file": c["file"], "title": c.get("title", ""), "caption": c.get("caption", ""),
                      "keywords": c.get("keywords") or [], "campaign": bool(campaign),
                      "status": "pending", "video_id": "", "publish_at": "", "error": "",
                      "added": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            n += 1
        _save_queue(q)
    return n


def _upload_file(path, meta, status):
    """Resumable upload in 8 MB chunks; resumes after a dropped connection."""
    size = os.path.getsize(path)
    body = {"snippet": meta, "status": status}
    r = _check(requests.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
                             headers={**_headers(), "Content-Type": "application/json; charset=UTF-8",
                                      "X-Upload-Content-Type": "video/mp4",
                                      "X-Upload-Content-Length": str(size)},
                             data=json.dumps(body), timeout=60))
    session = r.headers["Location"]
    pos, fails = 0, 0
    with open(path, "rb") as f:
        while True:
            f.seek(pos)
            chunk = f.read(CHUNK)
            end = pos + len(chunk) - 1
            try:
                r = requests.put(session, data=chunk, timeout=300, headers={
                    **_headers(), "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {pos}-{end}/{size}"})
            except requests.RequestException:
                r = None
            if r is not None and r.status_code in (200, 201):
                return r.json()
            if r is not None and r.status_code == 308:      # chunk stored, send the next
                rng = r.headers.get("Range")
                pos = int(rng.split("-")[1]) + 1 if rng else 0
                fails = 0
                continue
            if r is not None and r.status_code < 500 and r.status_code != 308:
                _check(r)
            fails += 1                                       # network drop / 5xx: ask where we are
            if fails > 8:
                raise PublishError("Upload kept failing (network). It stays queued; try again later.")
            time.sleep(min(60, 2 ** fails))
            try:
                q = requests.put(session, timeout=60, headers={
                    **_headers(), "Content-Length": "0", "Content-Range": f"bytes */{size}"})
                if q.status_code in (200, 201):
                    return q.json()
                if q.status_code == 308:
                    rng = q.headers.get("Range")
                    pos = int(rng.split("-")[1]) + 1 if rng else 0
            except requests.RequestException:
                pass


def _when(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _local(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%d %b %H:%M")
    except Exception:
        return iso


def upload_queue(cfg, log=print):
    """Upload every pending clip. Returns (uploaded, failed, remaining)."""
    mode = cfg.get("yt_mode") or "review"
    gap = max(1, int(cfg.get("yt_interval") or 10))
    first = max(0, int(cfg.get("yt_first_delay") or 10))
    cat = cfg.get("yt_category") or "20"
    with _lock:
        q = load_queue()
    todo = [x for x in q if x["status"] in ("pending", "failed")]
    if not todo:
        log("Nothing queued for YouTube.")
        return 0, 0, 0
    # schedule after anything already scheduled, so batches never publish on top of each other
    later = [datetime.fromisoformat(x["publish_at"].replace("Z", "+00:00")) for x in q
             if x["status"] == "scheduled" and x.get("publish_at")]
    slot = max([datetime.now(timezone.utc) + timedelta(minutes=first)] +
               [t + timedelta(minutes=gap) for t in later])
    up = failed = 0
    log(f"YouTube: uploading {len(todo)} clip(s) as private"
        + (f", going public every {gap} min from {_local(_when(slot))}." if mode == "scheduled" else
           ". They stay private until you publish them."))
    for n, item in enumerate(todo, 1):
        if not os.path.exists(item["file"]):
            item.update(status="failed", error="file no longer exists")
            failed += 1
            _persist(item)
            continue
        sched = mode == "scheduled" and not item["campaign"]
        status = {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                  "embeddable": True}
        if sched:
            status["publishAt"] = _when(slot)
        name = os.path.basename(item["file"])
        log(f"  [{n}/{len(todo)}] uploading {name} ...")
        try:
            v = _upload_file(item["file"], metadata(item, cat), status)
        except QuotaError as e:
            item.update(status="pending", error=str(e))
            _persist(item)
            log(f"  {e}")
            telegram(cfg, f"⚠️ ClipForge: {e}")
            break
        except Exception as e:
            item.update(status="failed", error=str(e)[:300])
            _persist(item)
            failed += 1
            log(f"  failed: {e}")
            telegram(cfg, f"❌ ClipForge upload failed: {item['title'][:80]}\n{str(e)[:300]}")
            continue
        vid = v.get("id", "")
        got = (v.get("status") or {})
        link = f"https://youtu.be/{vid}"
        if sched and not got.get("publishAt"):
            # YouTube kept it private without a schedule: tell the user instead of failing silently
            item.update(status="uploaded", video_id=vid, publish_at="",
                        error="YouTube did not accept the publish time; publish it yourself")
            msg = (f"⚠️ Uploaded but NOT scheduled: {item['title'][:80]}\n{link}\n"
                   "YouTube kept it private. Publish it from ClipForge or YouTube Studio.")
        elif sched:
            item.update(status="scheduled", video_id=vid, publish_at=got["publishAt"], error="")
            msg = f"✅ Uploaded: {item['title'][:80]}\nGoes public {_local(got['publishAt'])}\n{link}"
            slot += timedelta(minutes=gap)
        else:
            item.update(status="uploaded", video_id=vid, publish_at="", error="")
            why = " (campaign clip: check it before publishing)" if item["campaign"] else ""
            msg = f"✅ Uploaded as private{why}: {item['title'][:80]}\n{link}"
        _persist(item)
        up += 1
        ok, err = telegram(cfg, msg)
        log(f"  done: {link}" + ("" if ok else f"  (Telegram: {err})"))
    remaining = len([x for x in load_queue() if x["status"] in ("pending", "failed")])
    return up, failed, remaining


def _persist(item):
    with _lock:
        q = load_queue()
        for i, x in enumerate(q):
            if x["file"] == item["file"]:
                q[i] = item
        _save_queue(q)


def publish(video_ids, cfg, log=print):
    """Make reviewed private videos public now. Returns the number published."""
    done = 0
    for vid in video_ids:
        try:
            _check(requests.put(f"{API}/videos", params={"part": "status"}, headers=_headers(),
                                json={"id": vid, "status": {"privacyStatus": "public",
                                                            "selfDeclaredMadeForKids": False,
                                                            "embeddable": True}}, timeout=30))
        except Exception as e:
            log(f"Couldn't publish {vid}: {e}")
            continue
        with _lock:
            q = load_queue()
            title = vid
            for x in q:
                if x["video_id"] == vid:
                    x.update(status="public", publish_at="")
                    title = x["title"]
            _save_queue(q)
        done += 1
        telegram(cfg, f"🌍 Now public: {title[:80]}\nhttps://youtu.be/{vid}")
        log(f"Published {vid}")
    return done


def review_list():
    """Uploaded-but-private videos waiting for review: [(label, video_id)]."""
    return [(f"{x['title'][:70]}" + ("  [campaign]" if x["campaign"] else ""), x["video_id"])
            for x in load_queue() if x["status"] == "uploaded" and x["video_id"]]


def summary():
    q = load_queue()
    c = {}
    for x in q:
        c[x["status"]] = c.get(x["status"], 0) + 1
    if not q:
        return "Queue is empty."
    names = {"pending": "waiting to upload", "failed": "failed (will retry)", "uploaded": "private, waiting for review",
             "scheduled": "scheduled", "public": "public"}
    return " · ".join(f"{v} {names.get(k, k)}" for k, v in c.items())


def after_run(res, cfg, log=print):
    """Called when a run finishes: queue its clips and upload them if YouTube upload is on."""
    if not cfg.get("yt_on"):
        return
    campaign = bool(res.get("campaign"))
    n = queue_run(res["clips"], campaign=campaign)
    log(f"YouTube: {n} clip(s) added to the upload queue"
        + (" (campaign clips: uploaded private, never auto-scheduled)." if campaign else "."))
    try:
        upload_queue(cfg, log)
    except PublishError as e:
        log(f"YouTube: {e}")
        telegram(cfg, f"⚠️ ClipForge couldn't upload: {e}")
    log("YouTube queue: " + summary())
