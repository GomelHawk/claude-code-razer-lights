import time
from unittest.mock import MagicMock, patch

import pytest

import razer_light_server as srv


@pytest.fixture(autouse=True)
def reset_state():
    srv.session_states.clear()
    srv.session_uri = None
    srv.blinking = False
    srv.last_ping = time.time()
    srv.init_cooldown_until = 0.0
    yield


@pytest.fixture()
def with_session():
    srv.session_uri = "http://fake-chroma/session/1"


def make_handler(path):
    handler = srv.Handler.__new__(srv.Handler)
    handler.path = path
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.wfile = MagicMock()
    return handler


# --- _effective_state ---

def test_effective_state_empty():
    assert srv._effective_state() == "idle"


def test_effective_state_all_idle():
    srv.session_states.update({"A": "idle", "B": "idle"})
    assert srv._effective_state() == "idle"


def test_effective_state_working_beats_idle():
    srv.session_states.update({"A": "idle", "B": "working"})
    assert srv._effective_state() == "working"


def test_effective_state_confirm_beats_working():
    srv.session_states.update({"A": "working", "B": "confirm"})
    assert srv._effective_state() == "confirm"


def test_effective_state_confirm_beats_all():
    srv.session_states.update({"A": "working", "B": "confirm", "C": "idle"})
    assert srv._effective_state() == "confirm"


# --- _apply_state ---

def test_apply_state_idle(with_session):
    srv.session_states["A"] = "idle"
    with patch.object(srv, "set_color") as mock_color:
        srv._apply_state()
    assert srv.blinking is False
    mock_color.assert_called_once_with(0, 255, 0)


def test_apply_state_working(with_session):
    srv.session_states["A"] = "working"
    with patch.object(srv, "set_color") as mock_color:
        srv._apply_state()
    assert srv.blinking is False
    mock_color.assert_called_once_with(255, 200, 0)


def test_apply_state_confirm_starts_blink(with_session):
    srv.session_states["A"] = "confirm"
    with patch("threading.Thread") as mock_thread:
        srv._apply_state()
    assert srv.blinking is True
    mock_thread.assert_called_once()


def test_apply_state_confirm_no_duplicate_thread(with_session):
    srv.session_states["A"] = "confirm"
    srv.blinking = True
    with patch("threading.Thread") as mock_thread:
        srv._apply_state()
    mock_thread.assert_not_called()


# --- session-start ---

def test_session_start_first_opens_chroma():
    with patch.object(srv, "init_session"), patch.object(srv, "set_color"):
        make_handler("/session-start?sid=A").do_GET()
    assert srv.session_states.get("A") == "idle"


def test_session_start_first_calls_init_session():
    with patch.object(srv, "init_session") as mock_init, patch.object(srv, "set_color"):
        make_handler("/session-start?sid=A").do_GET()
    mock_init.assert_called_once()


def test_session_start_second_skips_init():
    srv.session_states["B"] = "idle"
    srv.session_uri = "http://fake"
    with patch.object(srv, "init_session") as mock_init, patch.object(srv, "set_color"):
        make_handler("/session-start?sid=A").do_GET()
    mock_init.assert_not_called()
    assert "A" in srv.session_states


# --- session-end ---

def test_session_end_last_calls_end_session():
    srv.session_states["A"] = "idle"
    srv.session_uri = "http://fake"
    with patch.object(srv, "end_session") as mock_end:
        make_handler("/session-end?sid=A").do_GET()
    mock_end.assert_called_once()
    assert srv.session_states == {}


def test_session_end_not_last_applies_state():
    srv.session_states.update({"A": "working", "B": "idle"})
    srv.session_uri = "http://fake"
    with patch.object(srv, "_apply_state") as mock_apply, \
         patch.object(srv, "end_session") as mock_end:
        make_handler("/session-end?sid=A").do_GET()
    mock_end.assert_not_called()
    mock_apply.assert_called_once()
    assert "A" not in srv.session_states


def test_session_end_unknown_sid_no_crash():
    srv.session_uri = "http://fake"
    with patch.object(srv, "end_session"):
        make_handler("/session-end?sid=ghost").do_GET()


# --- working / idle / confirm ---

def test_working_updates_state(with_session):
    srv.session_states["A"] = "idle"
    with patch.object(srv, "_apply_state"):
        make_handler("/working?sid=A").do_GET()
    assert srv.session_states["A"] == "working"


def test_idle_updates_state(with_session):
    srv.session_states["A"] = "working"
    with patch.object(srv, "_apply_state"):
        make_handler("/idle?sid=A").do_GET()
    assert srv.session_states["A"] == "idle"


def test_confirm_updates_state(with_session):
    srv.session_states["A"] = "working"
    with patch.object(srv, "_apply_state"):
        make_handler("/confirm?sid=A").do_GET()
    assert srv.session_states["A"] == "confirm"


def test_working_without_session_uri_reinits():
    # Spurious session-end dropped the Chroma handle; next /working must recover.
    srv.session_states["A"] = "idle"
    with patch.object(srv, "init_session") as mock_init, \
         patch.object(srv, "_apply_state") as mock_apply:
        make_handler("/working?sid=A").do_GET()
    mock_init.assert_called_once()
    mock_apply.assert_called_once()
    assert srv.session_states["A"] == "working"


def test_idle_without_session_uri_reinits():
    srv.session_states["A"] = "working"
    with patch.object(srv, "init_session") as mock_init, \
         patch.object(srv, "_apply_state"):
        make_handler("/idle?sid=A").do_GET()
    mock_init.assert_called_once()


def test_confirm_without_session_uri_reinits():
    srv.session_states["A"] = "working"
    with patch.object(srv, "init_session") as mock_init, \
         patch.object(srv, "_apply_state"):
        make_handler("/confirm?sid=A").do_GET()
    mock_init.assert_called_once()


# --- default sid fallback ---

def test_no_sid_param_defaults_to_default():
    with patch.object(srv, "init_session"), patch.object(srv, "set_color"):
        make_handler("/session-start").do_GET()
    assert "default" in srv.session_states


# --- multi-session priority transitions ---

def test_confirm_persists_while_other_session_working():
    srv.session_states.update({"A": "confirm", "B": "working"})
    assert srv._effective_state() == "confirm"


def test_confirm_resolves_to_working_when_one_clears():
    srv.session_states.update({"A": "confirm", "B": "working"})
    srv.session_states["A"] = "idle"
    assert srv._effective_state() == "working"


def test_confirm_resolves_to_idle_when_all_clear():
    srv.session_states.update({"A": "confirm", "B": "working"})
    srv.session_states["A"] = "idle"
    srv.session_states["B"] = "idle"
    assert srv._effective_state() == "idle"


def test_two_confirms_both_must_resolve():
    srv.session_states.update({"A": "confirm", "B": "confirm"})
    srv.session_states["A"] = "idle"
    assert srv._effective_state() == "confirm"
    srv.session_states["B"] = "idle"
    assert srv._effective_state() == "idle"


# --- /state endpoint ---

def test_state_reports_effective_and_count():
    import json
    srv.session_states.update({"A": "working", "B": "idle"})
    h = make_handler("/state")
    h.do_GET()
    body = h.wfile.write.call_args[0][0].decode()
    data = json.loads(body)
    assert data["effective"] == "working"
    assert data["count"] == 2


def test_state_empty_is_none():
    import json
    h = make_handler("/state")
    h.do_GET()
    data = json.loads(h.wfile.write.call_args[0][0].decode())
    assert data["effective"] == "none"
    assert data["count"] == 0


def test_state_does_not_update_last_ping():
    # Tray polling /state must not reset the watchdog's inactivity timer.
    srv.last_ping = 0
    make_handler("/state").do_GET()
    assert srv.last_ping == 0


# --- no Razer devices / Chroma unavailable ---

def test_init_session_skips_during_cooldown():
    srv.init_cooldown_until = time.time() + 100
    with patch.object(srv.requests, "post") as mock_post:
        srv.init_session()
    mock_post.assert_not_called()
    assert srv.session_uri is None


def test_init_session_backs_off_on_failure():
    with patch.object(srv.requests, "post", side_effect=Exception("refused")), \
         patch("time.sleep"):
        srv.init_session()
    assert srv.session_uri is None
    assert srv.init_cooldown_until > time.time()   # future retry scheduled


def test_lights_disabled_never_calls_chroma(monkeypatch):
    monkeypatch.setattr(srv, "LIGHTS_ENABLED", False)
    with patch.object(srv.requests, "post") as mock_post:
        srv.init_session()
    mock_post.assert_not_called()


def test_set_color_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(srv, "LIGHTS_ENABLED", False)
    srv.session_uri = "http://fake"
    with patch.object(srv.requests, "put") as mock_put:
        srv.set_color(1, 2, 3)
    mock_put.assert_not_called()


def test_status_tracked_without_chroma(monkeypatch):
    # No devices: the tray's /state must still reflect Claude's status.
    import json
    monkeypatch.setattr(srv, "LIGHTS_ENABLED", False)
    make_handler("/session-start?sid=A").do_GET()
    make_handler("/working?sid=A").do_GET()
    h = make_handler("/state")
    h.do_GET()
    data = json.loads(h.wfile.write.call_args[0][0].decode())
    assert data["effective"] == "working"
    assert srv.session_uri is None   # never opened a Chroma session


# --- watchdog condition ---

def test_watchdog_triggers_after_timeout():
    srv.session_states["A"] = "working"
    srv.session_uri = "http://fake"
    srv.last_ping = 0
    with patch.object(srv, "end_session") as mock_end:
        with srv.lock:
            if srv.session_uri and (time.time() - srv.last_ping) > srv.WATCHDOG_TIMEOUT:
                srv.session_states.clear()
                srv.end_session()
    mock_end.assert_called_once()
    assert srv.session_states == {}


def test_watchdog_does_not_trigger_within_timeout():
    srv.session_states["A"] = "working"
    srv.session_uri = "http://fake"
    srv.last_ping = time.time()
    with patch.object(srv, "end_session") as mock_end:
        with srv.lock:
            if srv.session_uri and (time.time() - srv.last_ping) > srv.WATCHDOG_TIMEOUT:
                srv.session_states.clear()
                srv.end_session()
    mock_end.assert_not_called()
    assert srv.session_states == {"A": "working"}
