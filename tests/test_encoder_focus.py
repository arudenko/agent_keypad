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


def test_a_fast_spin_focuses_only_the_resting_agent():
    """Every detent must not fire its own focus call."""
    router, client = make()

    async def go():
        for _ in range(3):
            await router.handle_encoder(KEY_MAIN_ENCODER, 1)
            await asyncio.sleep(0.01)
        await asyncio.sleep(FOCUS_COALESCE_SECONDS * 3)

    asyncio.run(go())
    assert len(client.focused) == 1, f"expected one focus, got {client.focused}"
    assert client.focused[0] == router.slots.agent_at(router.selected).key


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
