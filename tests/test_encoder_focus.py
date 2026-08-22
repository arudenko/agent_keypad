"""Focus-on-turn for the main encoder."""

import asyncio

from herdr_km16.actions import FOCUS_COALESCE_SECONDS, ActionRouter
from herdr_km16.config import Config, EncoderConfig
from herdr_km16.mapping import Agent, SlotMap
from herdr_km16.km16 import KEY_LEFT_ENCODER, KEY_MAIN_ENCODER


class FakeClient:
    def __init__(self):
        self.focused = []

    async def focus_agent(self, target):
        self.focused.append(target)

    async def send_keys(self, target, keys):
        pass

    async def activate_app(self):
        pass


def make(**kw):
    cfg = Config(**kw)
    slots = SlotMap()
    slots.sync([Agent("w1:p1", "blocked"), Agent("w2:p1", "idle"), Agent("w3:p1", "working")])
    client = FakeClient()
    return ActionRouter(config=cfg, client=client, slots=slots), client


def test_turning_the_main_encoder_focuses_without_a_press():
    router, client = make()

    async def go():
        await router.handle_encoder(KEY_MAIN_ENCODER, 1)
        await asyncio.sleep(FOCUS_COALESCE_SECONDS * 3)

    asyncio.run(go())
    assert client.focused == ["w1:p1"], "landing on an agent should focus it"


def test_a_fast_spin_coalesces_and_lands_on_the_resting_agent():
    """Every detent must not fire its own focus call: detents inside the throttle
    window coalesce. The first detent still follows instantly, and the resting
    position always gets the final focus."""
    router, client = make()

    async def go():
        for _ in range(3):
            await router.handle_encoder(KEY_MAIN_ENCODER, 1)
            await asyncio.sleep(0.01)
        await asyncio.sleep(FOCUS_COALESCE_SECONDS * 3)

    asyncio.run(go())
    assert len(client.focused) <= 2, f"intermediate detents must coalesce, got {client.focused}"
    assert client.focused[0] == "w1:p1", "the first detent follows immediately"
    assert client.focused[-1] == router.slots.agent(router.selected).target


def test_a_slow_turn_follows_detent_by_detent():
    """Turning slower than the throttle window must switch agterm on every step --
    the whole point of the wheel is walking the loop visibly."""
    router, client = make()

    async def go():
        for _ in range(3):
            await router.handle_encoder(KEY_MAIN_ENCODER, 1)
            await asyncio.sleep(FOCUS_COALESCE_SECONDS * 1.5)

    asyncio.run(go())
    assert client.focused == ["w1:p1", "w2:p1", "w3:p1"]


def test_other_encoders_do_not_focus_on_turn():
    router, client = make(left_encoder=EncoderConfig("cycle_agents", "escape"))

    async def go():
        await router.handle_encoder(KEY_LEFT_ENCODER, 1)
        await asyncio.sleep(FOCUS_COALESCE_SECONDS * 3)

    asyncio.run(go())
    assert client.focused == [], "only encoders with focus_on_turn should focus"
    assert router.selected is not None, "but the selection should still move"


def test_focus_on_turn_defaults_on_for_the_main_encoder_only():
    cfg = Config()
    assert cfg.main_encoder.focus_on_turn is True
    assert cfg.left_encoder.focus_on_turn is False
    assert cfg.right_encoder.focus_on_turn is False


# --- the big wheel loops through EVERY session --------------------------------


def test_main_encoder_defaults_to_cycling_all_sessions():
    cfg = Config()
    assert cfg.main_encoder.rotate == "cycle_agents"


def test_wheel_walks_the_whole_sidebar_and_wraps():
    """Sidebar order, not attention order, and past the last session it wraps to the
    first -- turning forever revisits every session."""
    router, _ = make()
    visited = []
    for _ in range(6):  # two full laps over three sessions
        router.cycle("cycle_agents", 1)
        visited.append(router.selected)
    assert visited == ["w1:p1", "w2:p1", "w3:p1"] * 2


def test_wheel_walks_backwards_too():
    router, _ = make()
    router.cycle("cycle_agents", -1)
    assert router.selected == "w3:p1", "turning left from nowhere starts at the end"
    router.cycle("cycle_agents", -1)
    assert router.selected == "w2:p1"


def test_wheel_reaches_sessions_beyond_the_pads_keys():
    """More sessions than keys: the overflow is keyless but must still be on the loop."""
    cfg = Config()
    slots = SlotMap()
    slots.sync([Agent(f"w{i}:p1", "working") for i in range(18)])  # 16 keys + 2 overflow
    assert slots.slot_of("w16:p1") is None and slots.slot_of("w17:p1") is None
    client = FakeClient()
    router = ActionRouter(config=cfg, client=client, slots=slots)

    visited = []
    for _ in range(19):
        router.cycle("cycle_agents", 1)
        visited.append(router.selected)
    assert visited[:18] == [f"w{i}:p1" for i in range(18)], "sidebar order, nobody skipped"
    assert visited[18] == "w0:p1", "and it wraps"


def test_wheel_focuses_a_keyless_session_on_settle():
    cfg = Config()
    slots = SlotMap()
    slots.sync([Agent(f"w{i}:p1", "working") for i in range(17)])
    client = FakeClient()
    router = ActionRouter(config=cfg, client=client, slots=slots)
    router.selected = "w15:p1"

    async def go():
        await router.handle_encoder(KEY_MAIN_ENCODER, 1)  # lands on the keyless w16
        await asyncio.sleep(FOCUS_COALESCE_SECONDS * 3)

    asyncio.run(go())
    assert router.selected == "w16:p1" and router.selected_slot is None
    assert client.focused == ["w16:p1"], "no key does not mean no focus"


def test_attention_cycle_still_walks_blocked_first():
    router, _ = make()  # w1 blocked, w2 idle, w3 working
    visited = []
    for _ in range(3):
        router.cycle("cycle_attention_agents", 1)
        visited.append(router.selected)
    assert visited == ["w1:p1", "w3:p1", "w2:p1"], "blocked, then working, then idle"
