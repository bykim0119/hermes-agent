"""Tests for CoderSessionManager — coder_run_id ↔ thread_id mapping + idle timeout."""
import time
import pytest

from gateway.coder_sessions import CoderSessionManager


def test_bind_and_resolve_thread():
    mgr = CoderSessionManager(idle_timeout_seconds=7200)
    mgr.bind(coder_run_id="coder-1", thread_id="thread-A", parent_channel_id="ch-100")
    assert mgr.get_thread("coder-1") == "thread-A"
    assert mgr.get_coder_by_thread("thread-A") == "coder-1"


def test_reverse_lookup_returns_none_for_unknown():
    mgr = CoderSessionManager()
    assert mgr.get_thread("nope") is None
    assert mgr.get_coder_by_thread("nope-thread") is None


def test_idle_sessions_are_evicted():
    mgr = CoderSessionManager(idle_timeout_seconds=1)
    mgr.bind(coder_run_id="coder-old", thread_id="t-old", parent_channel_id="c1")
    time.sleep(1.2)
    mgr.tick()
    assert mgr.get_thread("coder-old") is None


def test_touch_resets_idle_timer():
    mgr = CoderSessionManager(idle_timeout_seconds=1)
    mgr.bind(coder_run_id="coder-a", thread_id="t-a", parent_channel_id="c1")
    time.sleep(0.7)
    mgr.touch("coder-a")
    time.sleep(0.7)
    mgr.tick()
    assert mgr.get_thread("coder-a") == "t-a"


def test_max_concurrent_active():
    mgr = CoderSessionManager(max_concurrent=2)
    mgr.bind("c1", "t1", "ch")
    mgr.bind("c2", "t2", "ch")
    assert mgr.active_count() == 2
    with pytest.raises(ValueError, match="max_concurrent"):
        mgr.bind("c3", "t3", "ch")


def test_codex_session_id_roundtrip():
    mgr = CoderSessionManager()
    mgr.bind("coder-r", "t-r", "ch")
    assert mgr.get_codex_session_id("coder-r") is None
    mgr.set_codex_session_id("coder-r", "uuid-abc-123")
    assert mgr.get_codex_session_id("coder-r") == "uuid-abc-123"


def test_codex_session_id_unknown_coder_is_noop():
    mgr = CoderSessionManager()
    # set on a coder that was never bound is a no-op (no exception)
    mgr.set_codex_session_id("ghost", "uuid-xyz")
    assert mgr.get_codex_session_id("ghost") is None


def test_global_sessions_pointer():
    from gateway.coder_sessions import (
        set_global_sessions,
        get_global_sessions,
    )

    assert get_global_sessions() is None
    mgr = CoderSessionManager()
    set_global_sessions(mgr)
    try:
        assert get_global_sessions() is mgr
    finally:
        set_global_sessions(None)
    assert get_global_sessions() is None
