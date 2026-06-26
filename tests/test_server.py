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
    yield


@pytest.fixture()
def with_session():
    srv.session_uri = "http://fake-chroma/session/1"


def make_handler(path):
    handler = srv.Handler.__new__(srv.Handler)
    handler.path = path
    handler.send_response = MagicMock()
    handler.end_headers = MagicMock()
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


def test_no_apply_without_session_uri():
    srv.session_states["A"] = "idle"
    with patch.object(srv, "_apply_state") as mock_apply:
        make_handler("/working?sid=A").do_GET()
    mock_apply.assert_not_called()


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
