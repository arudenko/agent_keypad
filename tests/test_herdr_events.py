"""Tests for the Herdr wire format and the event -> LED path.

These encode the transport facts verified against the live Herdr 0.8.0-preview install;
they exist so a future Herdr upgrade that changes framing fails loudly here.
"""

import json

import pytest

from herdr_km16 import herdr as h
from herdr_km16.leds import LedRenderer, scale
from herdr_km16.mapping import Agent, SlotMap


def test_request_always_includes_params():
    """Omitting `params` is rejected by the server as invalid_request."""
    payload = json.loads(h._encode("id1", "ping", None))
    assert payload == {"id": "id1", "method": "ping", "params": {}}


def test_request_is_newline_terminated():
    assert h._encode("id1", "ping", None).endswith(b"\n")


def test_unwrap_returns_result():
    assert h._unwrap({"id": "x", "result": {"type": "pong"}}) == {"type": "pong"}


def test_unwrap_raises_on_error_response():
    with pytest.raises(h.HerdrError) as excinfo:
        h._unwrap({"id": "", "error": {"code": "invalid_request", "message": "missing field"}})
    assert excinfo.value.code == "invalid_request"


def test_windows_pipe_name_embeds_the_full_path():
    assert h._pipe_name(r"C:\Users\x\AppData\Roaming\herdr\herdr.sock") == (
        r"\\.\pipe\C:\Users\x\AppData\Roaming\herdr\herdr.sock"
    )


def test_status_subscription_carries_an_explicit_pane_id():
    """pane.agent_status_changed has no wildcard form; pane_id is required."""
    stream = h.HerdrEventStream(["w1:p1", "w2:p1"])
    subs = stream._subscriptions()
    status_subs = [s for s in subs if s["type"] == "pane.agent_status_changed"]
    assert [s["pane_id"] for s in status_subs] == ["w1:p1", "w2:p1"]
    assert all("pane_id" in s for s in status_subs)


def test_global_subscriptions_do_not_include_noisy_pane_focused():
    assert "pane.focused" not in h.HerdrEventStream.GLOBAL_EVENTS


def test_subscription_types_use_dot_case():
    for name in h.HerdrEventStream.GLOBAL_EVENTS:
        assert "." in name and "_" not in name.split(".")[0]


def test_agent_states_match_schema_enum():
    assert set(h.AGENT_STATES) == {"idle", "working", "blocked", "done", "unknown"}


# --- event -> LED frame ----------------------------------------------------


def test_status_change_event_repaints_only_its_slot():
    slots = SlotMap()
    slots.sync([Agent("w1:p1", "idle"), Agent("w2:p1", "idle")])
    renderer = LedRenderer(pulse=False)
    before = renderer.key_frame(slots)

    # Shape taken from a real envelope: {"event": ..., "data": {...}}
    event = {
        "event": "pane_agent_status_changed",
        "data": {"pane_id": "w2:p1", "workspace_id": "w2", "agent_status": "blocked"},
    }
    slots.update_status(event["data"]["pane_id"], event["data"]["agent_status"])
    after = renderer.key_frame(slots)

    assert after[0] == before[0]
    assert after[1] != before[1]
    assert after[1] == scale(renderer.colors["blocked"], renderer.brightness)


def test_every_state_renders_a_distinct_colour():
    slots = SlotMap()
    slots.sync([Agent(f"w{i}:p1", s) for i, s in enumerate(h.AGENT_STATES)])
    frame = LedRenderer(pulse=False).key_frame(slots)
    rendered = frame[: len(h.AGENT_STATES)]
    assert len(set(rendered)) == len(h.AGENT_STATES)


def test_empty_slots_are_off():
    frame = LedRenderer(pulse=False).key_frame(SlotMap())
    assert frame == [0x000000] * 16


def test_selection_brightens_without_changing_hue():
    slots = SlotMap()
    slots.sync([Agent("w1:p1", "working")])
    renderer = LedRenderer(pulse=False, brightness=0.3, selected_boost=2.0)
    plain = renderer.key_frame(slots)[0]
    picked = renderer.key_frame(slots, selected=0)[0]
    assert picked != plain
    # Same hue: blue-dominant either way, and strictly brighter.
    assert picked & 0xFF > plain & 0xFF
    assert (picked >> 16) & 0xFF == 0


def test_underglow_reports_the_most_urgent_state():
    slots = SlotMap()
    renderer = LedRenderer(pulse=False)
    slots.sync([Agent("w1:p1", "working"), Agent("w2:p1", "done")])
    assert renderer.underglow_frame(slots)[0] == scale(renderer.colors["done"], renderer.brightness)
    slots.update_status("w1:p1", "blocked")
    assert renderer.underglow_frame(slots)[0] == scale(renderer.colors["blocked"], renderer.brightness)


def test_underglow_frame_is_six_leds():
    assert len(LedRenderer(pulse=False).underglow_frame(SlotMap())) == 6


def test_pulse_only_requested_when_a_pulsing_state_is_visible():
    slots = SlotMap()
    renderer = LedRenderer()
    slots.sync([Agent("w1:p1", "idle")])
    assert not renderer.wants_animation(slots)
    slots.update_status("w1:p1", "blocked")
    assert renderer.wants_animation(slots)


def test_scale_clamps_and_never_overflows_a_channel():
    assert scale(0xFFFFFF, 2.0) == 0xFFFFFF
    assert scale(0xFFFFFF, -1.0) == 0x000000
