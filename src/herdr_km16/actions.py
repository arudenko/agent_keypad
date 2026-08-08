"""Physical input -> Herdr operations.

Safety model (see CLAUDE.md §Safety rules):

* A key press only ever *focuses* an agent. Focusing is always safe, and it must never
  answer a `blocked` prompt.
* Anything that submits input (`enter`, `interrupt`) is gated behind a long press by
  default, so a knocked knob cannot approve a permission request.
* Targets are passed as structured socket parameters, never interpolated into a shell.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from .action_types import ACTIONS_NEED_TARGET, KEY_NAMES
from .config import Config
from .herdr import HerdrClient, HerdrError
from .km16 import KEY_COUNT, KEY_LEFT_ENCODER, KEY_MAIN_ENCODER, KEY_RIGHT_ENCODER
from .mapping import SlotMap

log = logging.getLogger("herdr_km16.actions")

DEBOUNCE_SECONDS = 0.05

# How long the knob must rest before its selection is focused.
FOCUS_COALESCE_SECONDS = 0.15

# Brightness knob limits. The floor stays above zero so the pad never looks dead.
BRIGHTNESS_MIN = 0.03
BRIGHTNESS_MAX = 1.0


@dataclass
class ActionRouter:
    config: Config
    client: HerdrClient
    slots: SlotMap
    renderer: object | None = None
    selected: int | None = None
    _press_started: dict[int, float] = field(default_factory=dict, init=False)
    _last_press: dict[int, float] = field(default_factory=dict, init=False)
    _focus_task: object = field(default=None, init=False)

    # --- selection --------------------------------------------------------

    def _ordered_slots(self, action: str) -> list[int]:
        if action == "cycle_attention_agents":
            return self.slots.attention_order()
        return [i for i in range(self.slots.slot_count) if self.slots.agent_at(i)]

    def cycle(self, action: str, delta: int) -> None:
        order = self._ordered_slots(action)
        if not order:
            self.selected = None
            return
        if self.selected in order:
            index = (order.index(self.selected) + (1 if delta > 0 else -1)) % len(order)
        else:
            index = 0 if delta > 0 else len(order) - 1
        self.selected = order[index]
        agent = self.slots.agent_at(self.selected)
        log.info("select slot %s (%s, %s)", self.selected, agent.key if agent else "?", agent.status if agent else "?")

    # --- herdr operations -------------------------------------------------

    async def focus_slot(self, slot: int) -> None:
        agent = self.slots.agent_at(slot)
        if agent is None:
            return
        self.selected = slot
        log.info("focus slot %s -> %s (%s)", slot, agent.key, agent.status)
        try:
            await self.client.focus_agent(agent.key)
        except HerdrError as exc:
            log.warning("focus %s failed: %s", agent.key, exc)

    async def send_named_key(self, action: str) -> None:
        """Send esc / enter / ctrl+c to the selected agent."""
        if self.selected is None:
            return
        agent = self.slots.agent_at(self.selected)
        if agent is None:
            return
        key = KEY_NAMES.get(action)
        if key is None:
            return
        log.info("send %r to %s (%s)", key, agent.key, agent.status)
        try:
            await self.client.send_keys(agent.key, [key])
        except HerdrError as exc:
            log.warning("send_keys %s to %s failed: %s", key, agent.key, exc)

    async def run_action(self, action: str, held_ms: float) -> None:
        """Run a bottom-row action key against the selected agent."""
        if action == "next_attention":
            # Pure navigation: moves the selection, sends nothing to any agent.
            self.cycle("cycle_attention_agents", 1)
            return

        # `self.selected or -1` would be wrong here: slot 0 is falsy.
        target = self.slots.agent_at(self.selected) if self.selected is not None else None
        if action in ACTIONS_NEED_TARGET and target is None:
            log.info("action %r ignored: no agent selected", action)
            return

        if action in self.config.require_long_press_for and held_ms < self.config.long_press_ms:
            log.info(
                "ignored short press for guarded action %r (%.0fms < %dms)",
                action, held_ms, self.config.long_press_ms,
            )
            return

        await self.send_named_key(action)

    # --- physical events --------------------------------------------------

    def _debounced(self, key: int) -> bool:
        now = time.monotonic()
        if now - self._last_press.get(key, 0.0) < DEBOUNCE_SECONDS:
            return True
        self._last_press[key] = now
        return False

    def _encoder_for(self, key: int):
        return {
            KEY_MAIN_ENCODER: self.config.main_encoder,
            KEY_LEFT_ENCODER: self.config.left_encoder,
            KEY_RIGHT_ENCODER: self.config.right_encoder,
        }.get(key)

    async def handle_key(self, key: int, pressed: bool) -> None:
        if pressed:
            if self._debounced(key):
                return
            self._press_started[key] = time.monotonic()
            return

        held_ms = (time.monotonic() - self._press_started.pop(key, time.monotonic())) * 1000

        if key < KEY_COUNT:
            action = self.config.action_keys.get(key)
            if action:
                await self.run_action(action, held_ms)
            elif self.config.key_press == "focus_agent":
                await self.focus_slot(key)
            return

        encoder = self._encoder_for(key)
        if encoder is None:
            return
        action = encoder.press
        if action == "none":
            return
        if action == "focus_selected":
            if self.selected is not None:
                await self.focus_slot(self.selected)
            return
        if action in self.config.require_long_press_for and held_ms < self.config.long_press_ms:
            log.info("ignored short press for guarded action %r (%.0fms)", action, held_ms)
            return
        await self.send_named_key(action)

    def _schedule_focus(self) -> None:
        """Focus the selection once the knob settles.

        A spin emits a detent every few milliseconds; focusing on each one would hammer
        Herdr and strobe the UI through every agent on the way past. Only the resting
        position is worth focusing, so each detent cancels the previous pending focus.
        """
        if self._focus_task is not None:
            self._focus_task.cancel()

        async def settle() -> None:
            try:
                await asyncio.sleep(FOCUS_COALESCE_SECONDS)
            except asyncio.CancelledError:
                return
            if self.selected is not None:
                await self.focus_slot(self.selected)

        self._focus_task = asyncio.get_running_loop().create_task(settle())

    def adjust_brightness(self, delta: int) -> float | None:
        """Step the LED brightness. Runtime only -- config.yaml is never rewritten."""
        if self.renderer is None:
            return None
        step = self.config.brightness_step * (1 if delta > 0 else -1)
        # Floor above zero: a knob that can turn the pad completely dark looks broken.
        new = min(BRIGHTNESS_MAX, max(BRIGHTNESS_MIN, round(self.renderer.brightness + step, 3)))
        if new != self.renderer.brightness:
            self.renderer.brightness = new
            log.info("brightness -> %.2f", new)
        return new

    async def handle_encoder(self, encoder: int, delta: int) -> None:
        # Turn events reuse the push-button index: 16 main, 17 left, 18 right.
        config = self._encoder_for(encoder)
        if config is None or config.rotate == "none":
            return
        if config.rotate == "brightness":
            self.adjust_brightness(delta)
            return
        self.cycle(config.rotate, delta)
        if config.focus_on_turn:
            self._schedule_focus()
