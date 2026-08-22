"""Bottom-row action keys.

The approve path can accept a Claude Code permission prompt, so its guards are tested as
hard requirements, not conveniences.
"""

import asyncio
from pathlib import Path

import pytest
import yaml

from herdr_km16.actions import ActionRouter
from herdr_km16.config import Config, load_config
from herdr_km16.leds import INACTIVE_ACTION_DIM, LedRenderer, scale
from herdr_km16.mapping import Agent, SlotMap

ACTIONS = {12: "approve", 13: "reject", 14: "interrupt", 15: "next_attention"}


class FakeClient:
    """Records calls instead of touching agterm."""

    def __init__(self):
        self.sent: list[tuple[str, list[str]]] = []
        self.focused: list[str] = []
        self.jumped: int = 0
        self.jump_result: str | None = "w1:p1"
        self.activated: int = 0

    async def send_keys(self, target, keys):
        self.sent.append((target, keys))

    async def focus_agent(self, target):
        self.focused.append(target)

    async def next_attention(self):
        self.jumped += 1
        return self.jump_result

    async def activate_app(self):
        self.activated += 1


def make_router(action_keys=None, **overrides):
    action_keys = ACTIONS if action_keys is None else action_keys
    config = Config(action_keys=dict(action_keys), **overrides)
    slots = SlotMap(action_slots=frozenset(action_keys))
    slots.sync([Agent("w1:p1", "blocked"), Agent("w2:p1", "idle")])
    client = FakeClient()
    return ActionRouter(config=config, client=client, slots=slots), client, slots


def press(router, key, held_ms):
    """Simulate a full press/release with a given hold time.

    Clears the debounce timestamp first so repeated presses in one test are not swallowed;
    debounce has its own test below.
    """
    router._last_press.pop(key, None)
    asyncio.run(router.handle_key(key, True))
    router._press_started[key] -= held_ms / 1000
    asyncio.run(router.handle_key(key, False))


# --- allocation -------------------------------------------------------------


def test_action_keys_are_never_given_to_agents():
    slots = SlotMap(action_slots=frozenset(ACTIONS))
    slots.sync([Agent(f"w{i}:p1") for i in range(16)])
    for key in ACTIONS:
        assert slots.agent_at(key) is None, f"key {key} must stay an action key"


def test_agent_capacity_drops_to_twelve():
    slots = SlotMap(action_slots=frozenset(ACTIONS))
    assert slots.agent_capacity == 12
    slots.sync([Agent(f"w{i}:p1") for i in range(16)])
    assert len(slots.live_agents()) == 12


def test_agents_fill_only_the_top_twelve_keys():
    slots = SlotMap(action_slots=frozenset(ACTIONS))
    slots.sync([Agent(f"w{i}:p1") for i in range(12)])
    assert [s for s in slots.slots if s] == [f"w{i}:p1" for i in range(12)]
    assert slots.slots[12:] == [None] * 4


def test_pinning_to_an_action_key_is_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"action_keys": {12: "approve"}, "mapping": {"static": {12: "x"}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="both action_keys and mapping.static"):
        load_config(p)


# --- safety -----------------------------------------------------------------


def test_approve_needs_a_long_press():
    router, client, _ = make_router()
    router.selected = "w1:p1"
    press(router, 12, held_ms=100)
    assert client.sent == [], "a short press must not approve anything"


def test_approve_works_on_a_long_press():
    router, client, _ = make_router()
    router.selected = "w1:p1"
    press(router, 12, held_ms=800)
    assert client.sent == [("w1:p1", ["\n"])], "approve is a literal Return press"


def test_interrupt_needs_a_long_press():
    router, client, _ = make_router()
    router.selected = "w1:p1"
    press(router, 14, held_ms=100)
    assert client.sent == []
    press(router, 14, held_ms=800)
    assert client.sent == [("w1:p1", ["\x03"])], "interrupt is a literal Ctrl-C"


def test_reject_is_instant():
    router, client, _ = make_router()
    router.selected = "w1:p1"
    press(router, 13, held_ms=40)
    assert client.sent == [("w1:p1", ["\x1b"])], "reject is a literal Esc"


def test_actions_do_nothing_without_a_selection():
    router, client, _ = make_router()
    router.selected = None
    for key in (12, 13, 14):
        press(router, key, held_ms=800)
    assert client.sent == []


def test_action_keys_never_focus_an_agent():
    router, client, _ = make_router()
    router.selected = "w1:p1"
    for key in ACTIONS:
        press(router, key, held_ms=800)
    assert client.focused == [], "action keys must not call session.select"


def test_agent_key_press_still_only_focuses():
    router, client, _ = make_router()
    press(router, 0, held_ms=800)
    assert client.focused == ["w1:p1"]
    assert client.sent == [], "focusing must never send a keystroke"


# --- next_attention ---------------------------------------------------------


def test_next_attention_jumps_server_side_without_sending_anything():
    """agterm's session.go picks the target; the pad only follows the answer."""
    router, client, _ = make_router()
    press(router, 15, held_ms=40)
    assert client.jumped == 1, "the jump must be delegated to session.go"
    assert router.selected == "w1:p1", "selection follows the session agterm chose"
    assert client.sent == [] and client.focused == []


def test_next_attention_is_not_gated_by_long_press():
    router, client, _ = make_router()
    press(router, 15, held_ms=10)
    assert client.jumped == 1 and router.selected is not None


def test_next_attention_landing_on_an_unknown_session_clears_the_selection():
    """agterm can land on a session the cache does not know (stale, mid-resync). agterm
    has already switched there, so keeping the old selection would aim a subsequent
    approve at a session the user is no longer looking at -- it must clear instead."""
    router, client, _ = make_router()
    client.jump_result = "not-a-known-session"
    router.selected = "w2:p1"
    press(router, 15, held_ms=40)
    assert router.selected is None
    press(router, 12, held_ms=800)  # approve with no selection must do nothing
    assert client.sent == []


def test_next_attention_follows_a_known_session_without_a_key():
    """A session beyond the pad's keys is still a valid selection: agterm has switched
    to it, and the bottom row must act on what the user is looking at."""
    router, client, slots = make_router()
    slots.sync([Agent(f"w{i}:p1", "working") for i in range(13)])  # 12 keys + 1 overflow
    assert slots.slot_of("w12:p1") is None, "the 13th session must not hold a key"
    client.jump_result = "w12:p1"
    press(router, 15, held_ms=40)
    assert router.selected == "w12:p1"
    assert router.selected_slot is None
    press(router, 12, held_ms=800)  # approve targets the keyless session
    assert client.sent == [("w12:p1", ["\n"])]


# --- app activation ---------------------------------------------------------


def test_focus_also_raises_the_macos_app():
    router, client, _ = make_router()
    press(router, 0, held_ms=50)
    assert client.focused == ["w1:p1"]
    assert client.activated == 1, "a pad press means 'show me': raise agterm too"


def test_next_attention_also_raises_the_macos_app():
    router, client, _ = make_router()
    press(router, 15, held_ms=40)
    assert client.activated == 1


def test_activate_app_false_keeps_agterm_in_the_background():
    router, client, _ = make_router(activate_app=False)
    press(router, 0, held_ms=50)
    press(router, 15, held_ms=40)
    assert client.focused == ["w1:p1"] and client.jumped == 1
    assert client.activated == 0


def test_approve_reject_interrupt_never_raise_the_app():
    """Acting on the already-selected agent is not a request to switch applications."""
    router, client, _ = make_router()
    router.selected = "w1:p1"
    for key in (12, 13, 14):
        press(router, key, held_ms=800)
    assert client.activated == 0


# --- rendering --------------------------------------------------------------


def test_action_keys_render_their_own_colours():
    _, _, slots = make_router()
    cfg = Config(action_keys=dict(ACTIONS))
    renderer = LedRenderer(pulse=False, action_keys=cfg.action_keys, action_colors=cfg.action_colors)
    frame = renderer.key_frame(slots, selected=0)
    assert frame[12] == scale(cfg.action_colors["approve"], renderer.brightness)
    assert frame[13] == scale(cfg.action_colors["reject"], renderer.brightness)


def test_target_needing_actions_dim_when_nothing_is_selected():
    _, _, slots = make_router()
    cfg = Config(action_keys=dict(ACTIONS))
    renderer = LedRenderer(pulse=False, action_keys=cfg.action_keys, action_colors=cfg.action_colors)
    dim = renderer.key_frame(slots, selected=None)
    lit = renderer.key_frame(slots, selected=0)
    assert dim[12] == scale(cfg.action_colors["approve"], renderer.brightness * INACTIVE_ACTION_DIM)
    assert dim[12] != lit[12]
    # next_attention always works, so it never dims.
    assert dim[15] == lit[15]


def test_action_keys_do_not_pulse_even_next_to_a_blocked_agent():
    _, _, slots = make_router()
    cfg = Config(action_keys=dict(ACTIONS))
    renderer = LedRenderer(action_keys=cfg.action_keys, action_colors=cfg.action_colors)
    # 0.25 is a quarter of blocked's 1s cycle (clearly mid-fade); avoid multiples of
    # the period, which alias to the crest.
    a = renderer.key_frame(slots, selected=0, phase=0.0)
    b = renderer.key_frame(slots, selected=0, phase=0.25)
    assert a[12:] == b[12:], "action keys are controls, not status; they must stay steady"
    assert a[0] != b[0], "the blocked agent should still pulse"


def test_shipped_config_binds_the_bottom_row():
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    assert cfg.action_keys == ACTIONS
    assert "approve" in cfg.require_long_press_for
    assert "interrupt" in cfg.require_long_press_for
    assert "reject" not in cfg.require_long_press_for


def test_slot_zero_can_be_actioned():
    """Regression (from the slot-index era): a falsy-looking selection was read as
    'nothing selected'. The selection is an identity now, but the guarantee stands."""
    router, client, _ = make_router()
    router.selected = "w1:p1"
    press(router, 13, held_ms=40)
    assert client.sent == [("w1:p1", ["\x1b"])]


def test_bounced_press_is_swallowed():
    router, client, _ = make_router()
    router.selected = "w1:p1"
    asyncio.run(router.handle_key(13, True))
    asyncio.run(router.handle_key(13, False))
    client.sent.clear()
    # A second press within the debounce window never registers as a new press, so the
    # release computes ~0ms held and the action is treated as an accidental bounce.
    asyncio.run(router.handle_key(13, True))
    asyncio.run(router.handle_key(13, False))
    assert 13 not in router._press_started


def test_guard_uses_the_configured_threshold_not_a_hardcoded_one():
    """A press just under the threshold is refused; just over is accepted."""
    router, client, _ = make_router(long_press_ms=300)
    router.selected = "w1:p1"

    press(router, 12, held_ms=290)
    assert client.sent == [], "290ms must not clear a 300ms guard"

    press(router, 12, held_ms=310)
    assert client.sent == [("w1:p1", ["\n"])], "310ms should clear a 300ms guard"


def test_threshold_is_honoured_when_reconfigured():
    router, client, _ = make_router(long_press_ms=1000)
    router.selected = "w1:p1"
    press(router, 12, held_ms=500)
    assert client.sent == [], "the guard must follow config, not a constant"


def test_shipped_config_uses_300ms():
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    assert cfg.long_press_ms == 300


def test_agterm_outage_during_an_action_is_a_warning_not_a_crash(caplog):
    """The socket client raises OSError (not AgtermError) when agterm is down; every
    action path must take the quiet warning path, not surface a traceback."""
    import logging

    class DeadClient(FakeClient):
        async def focus_agent(self, target):
            raise ConnectionRefusedError("agterm is down")

        async def send_keys(self, target, keys):
            raise ConnectionRefusedError("agterm is down")

        async def next_attention(self):
            raise FileNotFoundError("no socket")

    router, _, slots = make_router()
    router.client = DeadClient()
    router.selected = "w1:p1"
    with caplog.at_level(logging.DEBUG):
        press(router, 0, held_ms=50)     # focus
        press(router, 13, held_ms=40)    # reject -> send_keys
        press(router, 15, held_ms=40)    # next_attention
    assert any("failed" in r.message for r in caplog.records)
