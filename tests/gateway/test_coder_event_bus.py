"""Tests for the coder_event_bus — register/unregister + cross-thread dispatch."""
import asyncio
import threading
import time

import pytest

from gateway import coder_event_bus


@pytest.fixture(autouse=True)
def _reset_bus():
    coder_event_bus._reset_for_tests()
    yield
    coder_event_bus._reset_for_tests()


def test_dispatch_with_no_handlers_is_noop():
    coder_event_bus.dispatch("coder-1", {"event": "x"})  # must not raise


def test_register_then_dispatch_invokes_handler():
    loop = asyncio.new_event_loop()
    received = []

    async def handler(cid, payload):
        received.append((cid, payload))

    coder_event_bus.register_handler(handler, loop)

    # Run loop briefly so the scheduled coroutine actually executes.
    def _run_loop():
        loop.run_forever()

    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()
    try:
        coder_event_bus.dispatch("coder-x", {"event": "thread.started"})
        # Give the scheduled coroutine a moment to land.
        time.sleep(0.1)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    assert received == [("coder-x", {"event": "thread.started"})]


def test_register_is_idempotent():
    loop = asyncio.new_event_loop()
    received = []

    async def handler(cid, payload):
        received.append(cid)

    coder_event_bus.register_handler(handler, loop)
    coder_event_bus.register_handler(handler, loop)  # double-register no-op

    def _run_loop():
        loop.run_forever()

    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()
    try:
        coder_event_bus.dispatch("c", {})
        time.sleep(0.1)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    assert received == ["c"]  # only one delivery, not two


def test_unregister_stops_delivery():
    loop = asyncio.new_event_loop()
    received = []

    async def handler(cid, payload):
        received.append(cid)

    coder_event_bus.register_handler(handler, loop)
    coder_event_bus.unregister_handler(handler)

    def _run_loop():
        loop.run_forever()

    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()
    try:
        coder_event_bus.dispatch("c", {})
        time.sleep(0.1)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    assert received == []
