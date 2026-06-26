# razer_light_server.py
#
# Claude Code status lighting for Razer devices (Viper Mini + Kraken V3) via the
# Chroma REST SDK on Windows.
#
#   green          -> idle (Claude finished / waiting for you)
#   yellow         -> working
#   red, blinking  -> Claude is waiting for confirmation (permission prompt)
#   no session     -> control released back to Synapse's normal lighting
#
# Requirements:
#   - Razer Synapse 3 or 4 MUST be running, with Chroma Connect installed and
#     app/SDK control enabled for both devices.
#   - pip install requests
#
# Run:
#   python  razer_light_server.py   (visible window, prints diagnostics)
#   pythonw razer_light_server.py   (windowless, for the Scheduled Task)

import time
import threading
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

CHROMA = "http://localhost:54235/razer/chromasdk"
DEVICES = ("mouse", "headset")     # add/remove devices here
WATCHDOG_TIMEOUT = 600             # seconds of hook silence before force-release
LISTEN = ("127.0.0.1", 8777)

session_uri = None
session_states = {}    # sid -> "idle" | "working" | "confirm"
blinking = False
last_ping = time.time()
lock = threading.Lock()


def _effective_state():
    """Priority: confirm > working > idle. Call under lock."""
    vals = set(session_states.values())
    if "confirm" in vals:
        return "confirm"
    if "working" in vals:
        return "working"
    return "idle"


def _apply_state():
    """Update lighting to match _effective_state(). Call under lock."""
    global blinking
    state = _effective_state()
    if state == "confirm":
        if not blinking:
            blinking = True
            threading.Thread(target=blink_loop, daemon=True).start()
    elif state == "working":
        blinking = False
        set_color(255, 200, 0)
    else:
        blinking = False
        set_color(0, 255, 0)


def init_session():
    """Open a Chroma session and take control of lighting. Retries briefly in
    case Synapse is still coming up."""
    global session_uri
    for attempt in range(5):
        try:
            r = requests.post(CHROMA, json={
                "title": "ClaudeCodeLights",
                "description": "Claude Code status lighting",
                "author": {"name": "you", "contact": "you@example.com"},
                "device_supported": list(DEVICES),
                "category": "application",
            }, timeout=5)
            session_uri = r.json()["uri"]   # SDK returns the real session URI/port
            print("Session opened:", session_uri)
            return
        except Exception as e:
            print(f"init_session attempt {attempt + 1} failed:", repr(e))
            time.sleep(1)
    print("init_session: giving up (is Synapse running?)")


def end_session():
    """Release control back to Synapse's default lighting."""
    global session_uri, blinking
    blinking = False
    if session_uri:
        try:
            requests.delete(session_uri, timeout=5)
            print("Session released:", session_uri)
        except Exception as e:
            print("end_session failed:", repr(e))
        session_uri = None


def heartbeat():
    while True:
        if session_uri:
            try:
                requests.put(session_uri + "/heartbeat", timeout=5)
            except Exception:
                pass
        time.sleep(4)


def watchdog():
    """If no hook has pinged in WATCHDOG_TIMEOUT seconds (e.g. Claude Code
    crashed without firing SessionEnd), force-release the lights."""
    while True:
        time.sleep(30)
        with lock:
            if session_uri and (time.time() - last_ping) > WATCHDOG_TIMEOUT:
                print("Watchdog: forcing release after inactivity")
                session_states.clear()
                end_session()


def set_color(r, g, b):
    if not session_uri:
        print("set_color skipped - no active session")
        return
    bgr = (b << 16) | (g << 8) | r     # Chroma uses BGR, not RGB
    payload = {"effect": "CHROMA_STATIC", "param": {"color": bgr}}
    for device in DEVICES:
        try:
            resp = requests.put(session_uri + "/" + device, json=payload, timeout=5)
            print(f"set_color {device} {(r, g, b)} -> {resp.status_code}")
        except Exception as e:
            print(f"set_color {device} FAILED:", repr(e))


def blink_loop():
    on = False
    while blinking and session_uri:
        on = not on
        set_color(255, 0, 0) if on else set_color(0, 0, 0)
        time.sleep(0.4)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global blinking, last_ping
        parsed = urlparse(self.path)
        sid = parse_qs(parsed.query).get("sid", ["default"])[0]
        path = parsed.path

        with lock:
            last_ping = time.time()

            if path == "/session-start":
                if not session_states:
                    init_session()
                    set_color(0, 255, 0)             # green on takeover
                session_states[sid] = "idle"

            elif path == "/session-end":
                session_states.pop(sid, None)
                if not session_states:
                    end_session()                    # release to Synapse default
                elif session_uri:
                    _apply_state()

            elif session_uri:                        # only act while we hold control
                if path == "/idle":
                    session_states[sid] = "idle"
                    _apply_state()
                elif path == "/working":
                    session_states[sid] = "working"
                    _apply_state()
                elif path == "/confirm":
                    session_states[sid] = "confirm"
                    _apply_state()

        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    print(f"Razer light server listening on http://{LISTEN[0]}:{LISTEN[1]}")
    print("Note: Razer Synapse must be running for lighting to change.")
    HTTPServer(LISTEN, Handler).serve_forever()
