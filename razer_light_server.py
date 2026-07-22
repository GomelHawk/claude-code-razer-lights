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
import sys
import json
import requests
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

CHROMA = "http://localhost:54235/razer/chromasdk"
DEVICES = ("mouse", "headset")     # add/remove devices here
WATCHDOG_TIMEOUT = 600             # seconds of hook silence before force-release
LISTEN = ("127.0.0.1", 8777)
INIT_COOLDOWN = 60                 # back off this long after Chroma is unavailable
# Set RAZER_LIGHTS=0 to run WITHOUT any Razer hardware: the server still tracks
# Claude status for the tray + usage; it just never calls the Chroma SDK.
LIGHTS_ENABLED = os.environ.get("RAZER_LIGHTS", "1").lower() not in ("0", "false", "no")
_BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
    else os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(_BASE_DIR, "razer_light_server.log")

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
init_cooldown_until = 0.0   # don't retry Chroma before this time (device-less setups)
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
    """Open a Chroma session and take control of lighting. No-ops when lighting
    is disabled, and backs off when Chroma/Synapse is unavailable so a device-less
    setup doesn't retry (~seconds) on every hook — status is still tracked either
    way, so the tray and usage keep working without any Razer hardware."""
    global session_uri, init_cooldown_until
    if not LIGHTS_ENABLED:
        return
    if time.time() < init_cooldown_until:
        return
    for attempt in range(3):
        try:
            r = requests.post(CHROMA, json={
                "title": "ClaudeCodeLights",
                "description": "Claude Code status lighting",
                "author": {"name": "you", "contact": "you@example.com"},
                "device_supported": list(DEVICES),
                "category": "application",
            }, timeout=3)
            session_uri = r.json()["uri"]   # SDK returns the real session URI/port
            init_cooldown_until = 0.0
            _print("Session opened: " + session_uri)
            return
        except Exception as e:
            _print(f"init_session attempt {attempt + 1} failed: {e!r}")
            time.sleep(0.5)
    init_cooldown_until = time.time() + INIT_COOLDOWN
    _print(f"init_session: Chroma unavailable — status still tracked; "
           f"retrying in {INIT_COOLDOWN}s (no Razer devices / Synapse not running?)")


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
    if not LIGHTS_ENABLED or not session_uri:
        return   # no devices / no session — status is still tracked for the tray
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
    def _send_json(self, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        global blinking, last_ping
        parsed = urlparse(self.path)
        path = parsed.path

        # Read-only status for the tray app. Must NOT touch last_ping, or polling
        # it would keep the crash-safety watchdog from ever releasing the lights.
        if path == "/state":
            # Best-effort read WITHOUT the lock, so the tray stays fast even
            # while a slow Chroma update holds it. Must NOT touch last_ping, or
            # polling would defeat the crash-safety watchdog.
            try:
                sessions = dict(session_states)
            except RuntimeError:          # dict mutated mid-copy
                sessions = {}
            vals = set(sessions.values())
            effective = ("confirm" if "confirm" in vals
                         else "working" if "working" in vals
                         else "idle" if sessions else "none")
            self._send_json({
                "effective": effective,
                "sessions": sessions,
                "count": len(sessions),
                "blinking": blinking,
                "active": session_uri is not None,
            })
            return

        sid = parse_qs(parsed.query).get("sid", ["default"])[0]

        # Acknowledge the hook IMMEDIATELY, before any (possibly slow) Chroma
        # calls, so the hook's short `curl -m 2` never times out on lighting I/O.
        # Content-Length: 0 lets curl finish without waiting for connection close.
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()
        try:
            self.wfile.flush()
        except OSError:
            pass

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

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    try:
        server = ThreadingHTTPServer(LISTEN, Handler)
        server.daemon_threads = True
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
