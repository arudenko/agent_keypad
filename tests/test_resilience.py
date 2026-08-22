"""The daemon must survive agterm sending something unexpected."""

import asyncio
import logging

import pytest

from herdr_km16 import main as m
from herdr_km16.agterm import AgtermError
from herdr_km16.config import Config


@pytest.mark.parametrize("malformed", [
    {"kind": "status"},                                        # no session, no payload
    {"kind": "status", "session": "s1"},                       # no payload at all
    {"kind": "status", "session": "s1", "payload": "junk"},    # payload not a dict
    {"payload": {"status": "active"}},                         # no kind
])
def test_malformed_events_do_not_kill_the_loop(malformed, monkeypatch, caplog):
    """An AttributeError/KeyError here must restart the stream, not end the process."""
    controller = m.Controller(Config())
    calls = {"n": 0}

    async def fake_reconcile():
        return ["s1"]

    class OneShotStream:
        def __init__(self, *a, **kw):
            pass

        async def baseline(self):
            pass

        async def __aiter__(self):
            yield malformed

    async def stop_after_one(_):
        calls["n"] += 1
        if calls["n"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(controller, "_reconcile", fake_reconcile)
    monkeypatch.setattr(m, "AgtermEventStream", OneShotStream)
    monkeypatch.setattr(m.asyncio, "sleep", stop_after_one)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(controller.agterm_loop())

    assert calls["n"] >= 1, "the loop must keep going rather than exit"


def test_agterm_errors_still_take_the_quiet_path(monkeypatch, caplog):
    """Expected outages log a warning, not a stack trace."""
    controller = m.Controller(Config())
    calls = {"n": 0}

    async def failing_reconcile():
        raise AgtermError("connection_closed", "socket gone")

    async def stop_after_one(_):
        calls["n"] += 1
        raise asyncio.CancelledError

    monkeypatch.setattr(controller, "_reconcile", failing_reconcile)
    monkeypatch.setattr(m.asyncio, "sleep", stop_after_one)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(controller.agterm_loop())

    assert any("agterm unavailable" in r.message for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records), "an outage is not a crash"


def test_outage_clears_the_selection_with_the_slots(monkeypatch, caplog):
    """The identity map is discarded on an outage; a dangling selection would silently
    re-arm the moment a session with the same id reappears after reconnect."""
    from herdr_km16.mapping import Agent

    controller = m.Controller(Config())
    controller.slots.sync([Agent("AAAA", "working", terminal_id="AAAA")])
    controller.router.selected = "AAAA"

    async def failing_reconcile():
        raise AgtermError("connection_closed", "socket gone")

    async def stop(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(controller, "_reconcile", failing_reconcile)
    monkeypatch.setattr(m.asyncio, "sleep", stop)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(controller.agterm_loop())

    assert controller.slots.live_agents() == []
    assert controller.router.selected is None


def test_outage_clears_a_keyless_selection_too(monkeypatch):
    """With every key action-bound no session holds a key, so live_agents() is empty --
    the clear must key off the cached sessions, or a keyless selection survives the
    outage and silently re-arms the bottom row after reconnect."""
    from herdr_km16.mapping import Agent

    controller = m.Controller(Config(action_keys={i: "approve" for i in range(16)}))
    controller.slots.sync([Agent("AAAA", "working", terminal_id="AAAA")])
    assert controller.slots.live_agents() == [], "precondition: the session is keyless"
    controller.router.selected = "AAAA"

    async def failing_reconcile():
        raise AgtermError("connection_closed", "socket gone")

    async def stop(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(controller, "_reconcile", failing_reconcile)
    monkeypatch.setattr(m.asyncio, "sleep", stop)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(controller.agterm_loop())

    assert controller.slots.session_order() == []
    assert controller.router.selected is None


def test_malformed_status_event_neither_idles_the_agent_nor_restarts(monkeypatch, caplog):
    """Events always carry an explicit status; one without must be ignored outright."""
    from herdr_km16.mapping import Agent

    controller = m.Controller(Config())
    controller.slots.sync([Agent("AAAA", "blocked", terminal_id="AAAA")])
    calls = {"n": 0}

    async def fake_reconcile():
        calls["n"] += 1
        return ["AAAA"]

    class OneShotStream:
        def __init__(self, *a, **kw):
            pass

        async def baseline(self):
            pass

        async def __aiter__(self):
            yield {"kind": "status", "session": "AAAA", "payload": {"blink": True}}
            yield {"kind": "status", "session": "AAAA", "payload": "junk"}
            raise asyncio.CancelledError

    monkeypatch.setattr(controller, "_reconcile", fake_reconcile)
    monkeypatch.setattr(m, "AgtermEventStream", OneShotStream)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(controller.agterm_loop())

    assert controller.slots.agent_at(0).status == "blocked", "must not be read as idle"
    assert calls["n"] == 1, "malformed events must not trigger resyncs either"


def test_backstop_resync_survives_a_malformed_tree(monkeypatch, caplog):
    """This task is gathered with the rest; an escaping KeyError would end the daemon."""
    controller = m.Controller(Config())
    calls = {"n": 0}

    async def bad_then_stop():
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyError("tree")
        raise asyncio.CancelledError

    async def instant_sleep(_):
        pass

    monkeypatch.setattr(controller, "_reconcile", bad_then_stop)
    monkeypatch.setattr(m.asyncio, "sleep", instant_sleep)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(controller.reconcile_loop())

    assert calls["n"] == 2, "the loop must survive the malformed response and keep polling"
    assert any("malformed" in r.message for r in caplog.records)
