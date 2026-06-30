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
import logging
import os
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

CHROMA = "http://localhost:54235/razer/chromasdk"
DEVICES = ("mouse", "headset")     # add/remove devices here
WATCHDOG_TIMEOUT = 600             # seconds of hook silence before force-release
LISTEN = ("127.0.0.1", 8777)
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "razer_light_server.log")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def _print(msg):
    print(msg)
    log.info(msg)

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
            _print("Session opened: " + session_uri)
            return
        except Exception as e:
            _print(f"init_session attempt {attempt + 1} failed: {e!r}")
            time.sleep(1)
    _print("init_session: giving up (is Synapse running?)")


def end_session():
    """Release control back to Synapse's default lighting."""
    global session_uri, blinking
    blinking = False
    if session_uri:
        try:
            requests.delete(session_uri, timeout=5)
            _print("Session released: " + session_uri)
        except Exception as e:
            _print(f"end_session failed: {e!r}")
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
                _print("Watchdog: forcing release after inactivity")
                session_states.clear()
                end_session()


def set_color(r, g, b):
    if not session_uri:
        _print("set_color skipped - no active session")
        return
    bgr = (b << 16) | (g << 8) | r     # Chroma uses BGR, not RGB
    payload = {"effect": "CHROMA_STATIC", "param": {"color": bgr}}
    for device in DEVICES:
        try:
            resp = requests.put(session_uri + "/" + device, json=payload, timeout=5)
            _print(f"set_color {device} {(r, g, b)} -> {resp.status_code}")
        except Exception as e:
            _print(f"set_color {device} FAILED: {e!r}")


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

            elif path in ("/idle", "/working", "/confirm"):
                # Re-init the Chroma session if it was released while the agent
                # kept running (spurious SessionEnd, context reset, etc.).
                if not session_uri:
                    init_session()
                session_states[sid] = path.lstrip("/")
                _apply_state()

        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    try:
        server = HTTPServer(LISTEN, Handler)
    except OSError as e:
        _print(f"Failed to bind {LISTEN[0]}:{LISTEN[1]} — already running? {e!r}")
        raise SystemExit(0)   # not an error; another instance owns the port

    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    _print(f"Razer light server listening on http://{LISTEN[0]}:{LISTEN[1]}")
    _print("Note: Razer Synapse must be running for lighting to change.")
    try:
        server.serve_forever()
    except Exception as e:
        _print(f"serve_forever exited unexpectedly: {e!r}")
        raise
