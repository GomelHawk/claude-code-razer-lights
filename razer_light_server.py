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

CHROMA = "http://localhost:54235/razer/chromasdk"
DEVICES = ("mouse", "headset")     # add/remove devices here
WATCHDOG_TIMEOUT = 600             # seconds of hook silence before force-release
LISTEN = ("127.0.0.1", 8777)

session_uri = None
active_sessions = 0
blinking = False
last_ping = time.time()
lock = threading.Lock()


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
    global active_sessions
    while True:
        time.sleep(30)
        with lock:
            if session_uri and (time.time() - last_ping) > WATCHDOG_TIMEOUT:
                print("Watchdog: forcing release after inactivity")
                active_sessions = 0
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
        global active_sessions, blinking, last_ping
        with lock:
            last_ping = time.time()

            if self.path == "/session-start":
                active_sessions += 1
                if active_sessions == 1:
                    init_session()
                    set_color(0, 255, 0)             # green on takeover

            elif self.path == "/session-end":
                active_sessions = max(0, active_sessions - 1)
                if active_sessions == 0:
                    end_session()                    # release to Synapse default

            elif session_uri:                        # only act while we hold control
                if self.path == "/idle":
                    blinking = False
                    set_color(0, 255, 0)             # green
                elif self.path == "/working":
                    blinking = False
                    set_color(255, 200, 0)           # yellow
                elif self.path == "/confirm":
                    if not blinking:
                        blinking = True
                        threading.Thread(target=blink_loop, daemon=True).start()

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
