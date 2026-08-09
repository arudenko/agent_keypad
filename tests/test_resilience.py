"""The daemon must survive Herdr sending something unexpected."""

import asyncio
import json
import logging

import pytest

from herdr_km16 import main as m
from herdr_km16.config import Config
from herdr_km16.herdr import HerdrError, event_kind


def test_event_kind_normalises_both_spellings():
    assert event_kind({"event": "pane.agent_status_changed"}) == "pane_agent_status_changed"
    assert event_kind({"event": "pane_agent_status_changed"}) == "pane_agent_status_changed"
    assert event_kind({"event": "pane_agent_detected"}) == "pane_agent_detected"
    assert event_kind({}) == ""


@pytest.mark.parametrize("malformed", [
    {"event": "pane.agent_status_changed"},                       # no data at all
    {"event": "pane.agent_status_changed", "data": {}},           # missing both fields
    {"event": "pane.agent_status_changed", "data": {"pane_id": "w1:p1"}},  # no status
    {"data": {"pane_id": "w1:p1"}},                               # no event name
])
def test_malformed_events_do_not_kill_the_loop(malformed, monkeypatch, caplog):
    """A KeyError here used to propagate out of herdr_loop and end the process."""
    controller = m.Controller(Config())
    calls = {"n": 0}

    async def fake_reconcile():
        return ["w1:p1"]

    class OneShotStream:
        def __init__(self, *a, **kw):
            pass

        async def __aiter__(self):
            yield malformed

    async def stop_after_one(_):
        calls["n"] += 1
        if calls["n"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(controller, "_reconcile", fake_reconcile)
    monkeypatch.setattr(m, "HerdrEventStream", OneShotStream)
    monkeypatch.setattr(m.asyncio, "sleep", stop_after_one)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(controller.herdr_loop())

    assert calls["n"] >= 1, "the loop must keep going rather than exit"


def test_herdr_errors_still_take_the_quiet_path(monkeypatch, caplog):
    """Expected outages log a warning, not a stack trace."""
    controller = m.Controller(Config())
    calls = {"n": 0}

    async def failing_reconcile():
        raise HerdrError("connection_closed", "socket gone")

    async def stop_after_one(_):
        calls["n"] += 1
        raise asyncio.CancelledError

    monkeypatch.setattr(controller, "_reconcile", failing_reconcile)
    monkeypatch.setattr(m.asyncio, "sleep", stop_after_one)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(controller.herdr_loop())

    assert any("herdr unavailable" in r.message for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records), "an outage is not a crash"
