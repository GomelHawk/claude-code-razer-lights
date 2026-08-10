# tray_app.py
#
# Windows system-tray companion to the Razer light server. Shows the Claude spark
# icon tinted to mirror the Razer lighting state, and a click-to-open flyout with
# your Claude usage (the same 5-hour / weekly / credits data as `/usage`).
#
#   icon:  green = idle   yellow = working   red (blinking) = confirm
#          Claude terracotta = no active Claude session
#   click:   opens a dark card with usage bars, matching the /usage popup
#   chime:   optional soft sound when a session needs confirmation (menu toggle)
#
# State comes from the light server's read-only GET /state endpoint; usage comes
# from usage.py (official endpoint, with JSONL fallback).
#
# Requires: pip install PySide6   (runs on Windows next to razer_light_server.py)
#
# Config via env vars:
#   RAZER_STATE_URL   default http://127.0.0.1:8777/state
#   CLAUDE_HOME       path to the .claude dir (for usage + credentials);
#                     on Windows reading WSL: \\wsl.localhost\<distro>\home\<user>\.claude

import os
import sys
import json
import time
import logging
import threading
import traceback
import faulthandler
import urllib.request
import urllib.error
from logging.handlers import RotatingFileHandler

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt, QRectF
except ImportError:
    sys.stderr.write("PySide6 is required: pip install PySide6\n")
    raise

import usage  # local module (usage.py)

# When frozen by PyInstaller, __file__ points at a temp extraction dir; config,
# log, and cache must live next to the .exe instead.
if getattr(sys, "frozen", False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "tray_app.log")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "tray_config.json")
USAGE_CACHE = os.path.join(SCRIPT_DIR, "usage_cache.json")

_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
log = logging.getLogger("tray")

# Frozen/windowed builds have no console — an unhandled exception anywhere outside
# an explicit try/except would otherwise vanish with nothing but a bare exit code.
# Log it in full before the process dies so a crash is diagnosable after the fact.
def _log_uncaught(exc_type, exc_value, exc_tb):
    log.critical("UNCAUGHT EXCEPTION (main thread):\n%s",
                 "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def _log_uncaught_thread(args):
    log.critical("UNCAUGHT EXCEPTION (thread %s):\n%s", args.thread.name,
                 "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))


sys.excepthook = _log_uncaught
threading.excepthook = _log_uncaught_thread

# Catches native-level faults (e.g. a Qt/PySide abort) that bypass sys.excepthook.
_fault_log = open(LOG_FILE, "a", buffering=1, encoding="utf-8")
faulthandler.enable(file=_fault_log)


def _load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


CONFIG = _load_config()

# Precedence for paths/URL: env var > tray_config.json > default.
STATE_URL = os.environ.get("RAZER_STATE_URL") or CONFIG.get("state_url") or "http://127.0.0.1:8777/state"
STATE_POLL_MS = 1500
USAGE_POLL_MS = 300000     # 5 min — the usage endpoint rate-limits (429); windows are hours
USAGE_MIN_REFRESH_S = 120  # ignore flyout-open refreshes more frequent than this
BLINK_MS = 400

# Icon colors per effective state (RGB). "none" uses the Claude brand terracotta
# so an idle/no-session icon reads as the Claude spark at rest.
STATE_COLORS = {
    "idle": (46, 204, 113),
    "working": (241, 196, 15),
    "confirm": (231, 76, 60),
    "none": (214, 118, 85),
}
CONFIRM_DIM = (70, 22, 20)

# Flyout palette (dark card, matching the /usage popup).
CARD_BG = QtGui.QColor("#242427")
TEXT = QtGui.QColor("#e7e7ea")
SUBTLE = QtGui.QColor("#9b9ba3")
TRACK = QtGui.QColor("#3b3b42")
FILL = QtGui.QColor("#6c8cff")
WARN = QtGui.QColor("#e0a63a")
CRIT = QtGui.QColor("#e0574f")

PAD = 18
CARD_W = 380


def sev_color(sev):
    return {"warning": WARN, "critical": CRIT}.get(sev, FILL)


RAYS = 12  # fallback burst: a dozen tapered rays


def _resource_path(rel):
    """Path to a bundled read-only asset (handles PyInstaller onefile temp dir)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


_SPARK = None


def _spark_pixmap():
    """The Claude spark silhouette (white shape on transparent), loaded once."""
    global _SPARK
    if _SPARK is None:
        _SPARK = QtGui.QPixmap(_resource_path(os.path.join("assets", "claude_spark.png")))
    return _SPARK


def make_state_icon(rgb, size=128):
    """The Claude spark tinted by state. Uses the bundled silhouette asset and
    recolors it via SourceIn compositing; falls back to a drawn burst if the
    asset is missing."""
    spark = _spark_pixmap()
    if spark.isNull():
        return _draw_burst_icon(rgb, size)
    scaled = spark.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    out = QtGui.QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QtGui.QPainter(out)
    p.drawPixmap(0, 0, scaled)
    p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
    p.fillRect(out.rect(), QtGui.QColor(*rgb))   # recolor shape, keep its alpha
    p.end()
    return QtGui.QIcon(out)


def _draw_burst_icon(rgb, size=128):
    """Procedural fallback if the spark asset is unavailable."""
    pm = QtGui.QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QtGui.QColor(*rgb))
    inner, outer = size * 0.05, size * 0.47
    mid, halfw = (inner + outer) / 2.0, size * 0.085
    p.translate(size / 2.0, size / 2.0)
    for i in range(RAYS):
        p.save()
        p.rotate(i * (360.0 / RAYS))
        path = QtGui.QPainterPath()
        path.moveTo(0, -inner)
        path.quadTo(halfw, -mid, 0, -outer)
        path.quadTo(-halfw, -mid, 0, -inner)
        path.closeSubpath()
        p.drawPath(path)
        p.restore()
    p.end()
    return QtGui.QIcon(pm)


class Flyout(QtWidgets.QWidget):
    """Frameless dark card that paints the usage rows."""

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool
                         | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.header = "Claude usage"
        self.rows = []
        self.message = "Loading…"
        self.hidden_at = 0.0
        self.resize(CARD_W, 140)

    def event(self, e):
        # Dismiss when the card loses focus (click elsewhere, alt-tab, etc.).
        if e.type() == QtCore.QEvent.WindowDeactivate:
            self.hide()
        return super().event(e)

    def hideEvent(self, e):
        self.hidden_at = time.monotonic()
        super().hideEvent(e)

    def set_usage(self, rows, tier):
        self.rows = rows or []
        self.header = "Your usage limits" + (f" · {tier}" if tier else "")
        self.message = "" if self.rows else "No usage data"
        self._resize_to_content()
        self.update()

    def set_error(self, msg):
        self.rows = []
        self.header = "Claude usage"
        self.message = msg
        self._resize_to_content()
        self.update()

    def _resize_to_content(self):
        h = PAD + 26  # header
        if self.rows:
            prev = None
            for row in self.rows:
                if prev == "limit" and row["kind"] == "credits":
                    h += 8
                prev = row["kind"]
                h += 22 + 6 + 16
        else:
            h += 64
        h += PAD - 8
        self.setFixedSize(CARD_W, int(h))

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        p.fillPath(path, CARD_BG)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 22)))
        p.drawPath(path)

        x, w, y = PAD, self.width() - 2 * PAD, PAD
        f = p.font()

        f.setPointSize(10)
        p.setFont(f)
        p.setPen(SUBTLE)
        p.drawText(QRectF(x, y, w, 20), Qt.AlignLeft | Qt.AlignVCenter, self.header)
        y += 28

        if not self.rows:
            p.setPen(SUBTLE)
            p.drawText(QRectF(x, y, w, self.height() - y - PAD),
                       Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                       self.message or "No data")
            p.end()
            return

        prev = None
        for row in self.rows:
            if prev == "limit" and row["kind"] == "credits":
                y += 8
            prev = row["kind"]

            f.setPointSize(11)
            p.setFont(f)
            p.setPen(TEXT)
            p.drawText(QRectF(x, y, w, 18), Qt.AlignLeft | Qt.AlignVCenter, row["label"])

            right = row["right"]
            if row["show_pct"]:
                right = f"{right}   {row['pct']:.0f}%"
            p.setPen(SUBTLE)
            p.drawText(QRectF(x, y, w, 18), Qt.AlignRight | Qt.AlignVCenter, right)
            y += 22

            track = QtGui.QPainterPath()
            track.addRoundedRect(QRectF(x, y, w, 6), 3, 3)
            p.fillPath(track, TRACK)
            fw = max(0.0, min(1.0, row["pct"] / 100.0)) * w
            if fw > 0:
                fill = QtGui.QPainterPath()
                fill.addRoundedRect(QRectF(x, y, fw, 6), 3, 3)
                p.fillPath(fill, sev_color(row["severity"]))
            y += 6 + 16
        p.end()


class Worker(QtCore.QObject):
    """Runs blocking network/IO off the GUI thread; results come back via signals."""
    state_ready = QtCore.Signal(dict)
    usage_ready = QtCore.Signal(object, object, str)   # rows, tier, source
    usage_error = QtCore.Signal(str)
    usage_ratelimited = QtCore.Signal(int)             # retry-after seconds (0 if unknown)

    def __init__(self, home, state_url):
        super().__init__()
        self.home = home
        self.state_url = state_url

    def _bg(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def poll_state(self):
        def run():
            try:
                with urllib.request.urlopen(self.state_url, timeout=2) as r:
                    st = json.loads(r.read().decode("utf-8"))
                    st["reachable"] = True
                    self.state_ready.emit(st)
            except Exception as e:
                # Polls every 1.5s and fails routinely whenever the light server
                # isn't up yet — a one-line warning, not a full traceback per poll.
                log.warning("state poll failed: %r", e)
                self.state_ready.emit({"effective": "none", "reachable": False,
                                       "active": False, "blinking": False, "count": 0})
        self._bg(run)

    def poll_usage(self):
        def run():
            try:
                data = usage.fetch_usage(self.home)
                rows = usage.parse_usage(data)
                log.info("usage official ok: %d rows", len(rows))
                self.usage_ready.emit(rows, usage.read_tier(self.home), "official")
                return
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    try:
                        retry = int(e.headers.get("retry-after", "0") or 0)
                    except (TypeError, ValueError):
                        retry = 0
                    log.warning("usage 429 (retry-after=%s) — keeping last known", retry)
                    self.usage_ratelimited.emit(retry)
                    return
                if e.code == 401:
                    log.warning("usage 401 — Claude Code login expired")
                    self.usage_error.emit(
                        "Your Claude Code login has expired.\n"
                        "Restart Claude Code (or run `claude` to sign in) to fix this."
                    )
                    return
                log.warning("usage HTTP %s — trying local estimate", e.code)
            except Exception:
                log.exception("usage endpoint failed (home=%s) — trying estimate", self.home)

            # Local-transcript estimate — only when the official call failed (non-429).
            try:
                acc = usage.reconstruct_from_jsonl(self.home)
                rows = []
                for label, a in acc.items():
                    total = a["in"] + a["out"] + a["cache_r"] + a["cache_w"]
                    rows.append({"label": label, "pct": 0.0,
                                 "right": f"{total/1e6:.1f}M tok · ~${a['cost']:.0f}",
                                 "show_pct": False, "severity": "normal", "kind": "limit"})
                if rows:
                    self.usage_ready.emit(rows, "estimate", "estimate")
                else:
                    self.usage_error.emit(f"No usage data.\nLooking in: {self.home}")
            except Exception as e:  # noqa: BLE001
                log.exception("usage fallback failed")
                self.usage_error.emit(f"Usage unavailable: {e}\nLooking in: {self.home}")
        self._bg(run)


class RazerTray(QtCore.QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.home = os.environ.get("CLAUDE_HOME") or CONFIG.get("claude_home") or usage.claude_home()
        self.prev_effective = None
        self.blink_on = False
        self.sound_on = False
        self.usage_rows = None
        self.usage_tier = None
        self.usage_source = None
        self.last_fetch = 0.0
        log.info("tray start: pid=%s frozen=%s exe=%s home=%s state_url=%s",
                 os.getpid(), getattr(sys, "frozen", False), sys.executable, self.home, STATE_URL)

        self._load_usage_cache()

        self.worker = Worker(self.home, STATE_URL)
        self.worker.state_ready.connect(self.on_state)
        self.worker.usage_ready.connect(self.on_usage)
        self.worker.usage_error.connect(self.on_usage_error)
        self.worker.usage_ratelimited.connect(self.on_ratelimited)

        self.flyout = Flyout()
        if self.usage_rows:
            self.flyout.set_usage(self.usage_rows, self.usage_tier)

        self.tray = QtWidgets.QSystemTrayIcon(make_state_icon(STATE_COLORS["none"]))
        self.tray.setToolTip("Claude: connecting…")
        self.tray.activated.connect(self.on_activated)
        self.tray.setContextMenu(self._menu())
        self.tray.show()

        self.state_timer = QtCore.QTimer(self, interval=STATE_POLL_MS, timeout=self.worker.poll_state)
        self.state_timer.start()
        self.usage_timer = QtCore.QTimer(self, interval=USAGE_POLL_MS, timeout=self.worker.poll_usage)
        self.usage_timer.start()
        self.blink_timer = QtCore.QTimer(self, interval=BLINK_MS, timeout=self.on_blink)

        self.worker.poll_state()
        self.worker.poll_usage()

    # --- usage cache (survives restarts so the popup is never blank once seen) ---

    def _load_usage_cache(self):
        try:
            with open(USAGE_CACHE, encoding="utf-8") as f:
                c = json.load(f)
            self.usage_rows = c.get("rows")
            self.usage_tier = c.get("tier")
            self.usage_source = "official"
        except (OSError, ValueError):
            pass

    def _save_usage_cache(self, rows, tier):
        try:
            with open(USAGE_CACHE, "w", encoding="utf-8") as f:
                json.dump({"rows": rows, "tier": tier}, f)
        except OSError as e:
            log.warning("could not write usage cache: %r", e)

    def _menu(self):
        m = QtWidgets.QMenu()
        self.sound_action = m.addAction("Sound on confirm")
        self.sound_action.setCheckable(True)
        self.sound_action.toggled.connect(self._set_sound)
        m.addAction("Refresh usage", self.worker.poll_usage)
        m.addSeparator()
        m.addAction("Quit", self.app.quit)
        return m

    def _set_sound(self, on):
        self.sound_on = on

    # --- state / icon ---

    def on_state(self, st):
        eff = st.get("effective", "none")
        if eff == "confirm":
            if not self.blink_timer.isActive():
                self.blink_on = True
                self.blink_timer.start()
        else:
            self.blink_timer.stop()
            self.tray.setIcon(make_state_icon(STATE_COLORS.get(eff, STATE_COLORS["none"])))

        if eff == "confirm" and self.prev_effective != "confirm":
            self._chime()
        self.prev_effective = eff

        if not st.get("reachable", True):
            self.tray.setToolTip("Light server not reachable — start razer_light_server.py")
            return

        count = st.get("count", 0)
        label = {"idle": "idle", "working": "working", "confirm": "needs confirmation",
                 "none": "no session"}.get(eff, eff)
        self.tray.setToolTip(f"Claude: {label}" + (f" ({count} session{'s' if count != 1 else ''})"
                                                    if count else ""))

    def on_blink(self):
        self.blink_on = not self.blink_on
        rgb = STATE_COLORS["confirm"] if self.blink_on else CONFIRM_DIM
        self.tray.setIcon(make_state_icon(rgb))

    def _chime(self):
        if not self.sound_on:
            return
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            QtWidgets.QApplication.beep()

    # --- usage / flyout ---

    def on_usage(self, rows, tier, source):
        self.usage_rows = rows
        self.usage_tier = tier
        self.usage_source = source
        self.last_fetch = time.monotonic()
        if source == "official":
            self._save_usage_cache(rows, tier)
        self.flyout.set_usage(rows, tier)

    def on_usage_error(self, msg):
        # Keep showing the last known data if we have any; only surface the error
        # when we have nothing at all.
        if self.usage_rows:
            log.info("usage refresh failed; keeping last known data")
            return
        self.flyout.set_error(msg)

    def on_ratelimited(self, _retry):
        # Rate-limited: never overwrite good data with the noisy estimate.
        self.last_fetch = time.monotonic()
        if not self.usage_rows:
            self.flyout.set_error("Rate limited by the usage API — retrying shortly.")

    def on_activated(self, reason):
        if reason not in (QtWidgets.QSystemTrayIcon.Trigger,
                          QtWidgets.QSystemTrayIcon.Context):
            return
        if reason == QtWidgets.QSystemTrayIcon.Context:
            return  # let the context menu handle right-click
        if self.flyout.isVisible():
            self.flyout.hide()
            return
        # If the flyout was just dismissed by this same click (focus-out fired
        # first), don't immediately reopen it — treat the click as "close".
        if time.monotonic() - self.flyout.hidden_at < 0.25:
            return
        # Show cached data immediately; only re-fetch if it's stale (avoids 429).
        if self.usage_rows:
            self.flyout.set_usage(self.usage_rows, self.usage_tier)
        if time.monotonic() - self.last_fetch > USAGE_MIN_REFRESH_S:
            self.worker.poll_usage()
        self._show_flyout_near_cursor()

    def _show_flyout_near_cursor(self):
        self.flyout.adjustSize()
        screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos()) or QtGui.QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        pos = QtGui.QCursor.pos()
        x = min(pos.x(), area.right() - self.flyout.width() - 8)
        y = pos.y() - self.flyout.height() - 12  # above the cursor (tray is bottom)
        if y < area.top():
            y = pos.y() + 12
        x = max(area.left() + 8, x)
        self.flyout.move(x, y)
        self.flyout.show()
        self.flyout.raise_()
        self.flyout.activateWindow()


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # tray app: closing the flyout must not quit
    if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        sys.stderr.write("No system tray available on this platform.\n")
        return 1
    _tray = RazerTray(app)
    try:
        code = app.exec()
    except Exception:
        log.exception("app.exec() raised — Qt event loop terminated abnormally")
        raise
    log.info("clean shutdown: exit code %s", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
