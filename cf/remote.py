"""Phone access from anywhere: a Gradio share link, only ever with a login.
The password is stored as a salted PBKDF2 hash in settings.json, never in plain text."""
import hashlib
import hmac
import os

ITER = 200_000


def hash_password(pw):
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, ITER)
    return f"pbkdf2${ITER}${salt.hex()}${h.hex()}"


def check_password(pw, stored):
    try:
        _, it, salt, h = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", (pw or "").encode("utf-8"), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(got.hex(), h)
    except Exception:
        return False


def make_auth(user, stored):
    def auth(u, p):
        return hmac.compare_digest((u or "").strip().lower(), user.lower()) and check_password(p, stored)
    return auth


def ready(s):
    """(ok, why): share link only when it is switched on AND a username + password are set."""
    if not s.get("share_on"):
        return False, ""
    if not (s.get("share_user") or "").strip() or not s.get("share_hash"):
        return False, "Phone access is on but no password is set, so the share link was NOT started."
    return True, ""


def retry_share_tunnel(errors):
    """Gradio hides why a share link failed: keep the real reason (appended to `errors`) and
    retry once."""
    import time
    import gradio.networking as gnet
    orig = gnet.setup_tunnel

    def tunnel(*a, **k):
        for attempt in (1, 2):
            try:
                return orig(*a, **k)
            except Exception as e:
                errors.append(e)
                if attempt == 1:
                    print("Share link attempt 1 failed, retrying...")
                    time.sleep(3)
        raise errors[-1]
    gnet.setup_tunnel = tunnel


def explain_share_error(err):
    """Turn Gradio's hidden share-link failure into a reason and a fix."""
    if err is None:
        return "Reason: unknown (Gradio gave no details)."
    chain, e = [], err
    while e is not None and len(chain) < 5:
        chain.append(f"{type(e).__name__}: {str(e).strip()[:400]}")
        e = e.__cause__ or e.__context__
    text = " | ".join(chain)
    low = text.lower()
    if "api server" in low or "api.gradio.app" in low:
        tip = ("ClipForge couldn't reach api.gradio.app. Open https://api.gradio.app/v3/tunnel-request "
               "in your browser: if it doesn't load, your network, VPN, firewall or ISP is blocking it.")
    elif "winerror 225" in low or "virus" in low or "permission" in low or "access is denied" in low \
            or isinstance(err, (PermissionError, OSError)) and "frpc" in low:
        tip = ("Windows or your antivirus blocked Gradio's helper program (frpc). Allow it in "
               "Windows Security > Virus & threat protection > Protection history, then restart.")
    elif "login to server failed" in low or "timeout" in low or "could not create share link" in low \
            or "tunnel" in low:
        tip = ("The helper started but couldn't connect to Gradio's share server (it uses port 7000). "
               "A firewall, VPN, office/college Wi-Fi or ISP is probably blocking that port. Try a "
               "different network (e.g. phone hotspot) to confirm.")
    else:
        tip = "Send this reason to Claude to fix."
    return f"Reason: {text}\nFix: {tip}"
