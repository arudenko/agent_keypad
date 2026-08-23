"""Physical input -> agterm operations.

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
from .agterm import AgtermClient, AgtermError
from .config import Config
from .km16 import KEY_COUNT, KEY_LEFT_ENCODER, KEY_MAIN_ENCODER, KEY_RIGHT_ENCODER
from .mapping import SlotMap

log = logging.getLogger("herdr_km16.actions")

DEBOUNCE_SECONDS = 0.05

# Minimum spacing between focus calls while the knob turns. The first detent focuses
# immediately, a spin focuses at most once per window (in order, on the way past), and
# the resting position always gets the final focus.
FOCUS_COALESCE_SECONDS = 0.15

# Raising the app is once per burst of activity, not once per focus: a spin at the
# throttle rate would otherwise spawn an `open` process every window for no effect.
ACTIVATE_APP_SECONDS = 1.0

# Brightness knob limits. The floor stays above zero so the pad never looks dead.
BRIGHTNESS_MIN = 0.03
BRIGHTNESS_MAX = 1.0


@dataclass
class ActionRouter:
    config: Config
    client: AgtermClient
    slots: SlotMap
    renderer: object | None = None
    # The selected session's IDENTITY, never a key number: the wheel can land on a
    # session beyond the pad's keys, and an identity survives compaction moving keys.
    selected: str | None = None
    _press_started: dict[int, float] = field(default_factory=dict, init=False)
    _last_press: dict[int, float] = field(default_factory=dict, init=False)
    _focus_task: object = field(default=None, init=False)
    _focus_fired: float = field(default=0.0, init=False)
    _activated_at: float = field(default=0.0, init=False)

    # --- selection --------------------------------------------------------

    @property
    def selected_slot(self) -> int | None:
        """The selected session's key, when it holds one (drives the LED highlight)."""
        return self.slots.slot_of(self.selected) if self.selected is not None else None

    def _selected_agent(self):
        return self.slots.agent(self.selected) if self.selected is not None else None

    def _cycle_order(self, action: str) -> list[str]:
        if action == "cycle_attention_agents":
            order = [self.slots.agent_at(slot) for slot in self.slots.attention_order()]
            return [agent.identity for agent in order if agent is not None]
        # cycle_agents: every session in sidebar order, keyed or not.
        return self.slots.session_order()

    def cycle(self, action: str, delta: int) -> None:
        order = self._cycle_order(action)
        if not order:
            self.selected = None
            return
        if self.selected in order:
            index = (order.index(self.selected) + (1 if delta > 0 else -1)) % len(order)
        else:
            index = 0 if delta > 0 else len(order) - 1
        self.selected = order[index]
        agent = self._selected_agent()
        log.info("select %s (slot %s, %s)", agent.target if agent else "?",
                 self.selected_slot, agent.status if agent else "?")

    # --- agterm operations ------------------------------------------------

    async def focus_slot(self, slot: int) -> None:
        agent = self.slots.agent_at(slot)
        if agent is None:
            return
        self.selected = agent.identity
        await self._focus(agent)

    async def focus_selected(self) -> None:
        """Focus the selected session, whether or not it holds a key."""
        agent = self._selected_agent()
        if agent is not None:
            await self._focus(agent)

    async def _focus(self, agent) -> None:
        log.info("focus %s (slot %s, %s)", agent.target, self.selected_slot, agent.status)
        try:
            await self.client.focus_agent(agent.target)
        except (AgtermError, OSError) as exc:  # agterm gone mid-press is routine
            log.warning("focus %s failed: %s", agent.target, exc)
            return
        now = time.monotonic()
        if self.config.activate_app and now - self._activated_at >= ACTIVATE_APP_SECONDS:
            # A physical press means "show me": raise agterm over the frontmost app too.
            # Once per burst is enough -- after the first raise agterm is already front.
            self._activated_at = now
            await self.client.activate_app()

    async def send_named_key(self, action: str) -> None:
        """Send esc / enter / ctrl+c to the selected agent."""
        agent = self._selected_agent()
        if agent is None:
            return
        key = KEY_NAMES.get(action)
        if key is None:
            return
        log.info("send %r to %s (%s)", key, agent.target, agent.status)
        try:
            await self.client.send_keys(agent.target, [key])
        except (AgtermError, OSError) as exc:
            log.warning("send_keys %r to %s failed: %s", key, agent.target, exc)

    async def adjust_font(self, delta: int) -> None:
        """Step the font of the selected session, or the active one with no selection --
        a view knob that does nothing until something is selected feels broken."""
        agent = self._selected_agent()
        target = agent.target if agent else None
        log.info("font %s %s", "inc" if delta > 0 else "dec", target or "active")
        try:
            await self.client.font_step(target, delta)
        except (AgtermError, OSError) as exc:
            log.warning("font step failed: %s", exc)

    async def toggle_scratch(self) -> None:
        """Show/hide the selected session's scratch terminal."""
        agent = self._selected_agent()
        if agent is None:
            return
        log.info("scratch toggle %s", agent.target)
        try:
            await self.client.scratch_toggle(agent.target)
        except (AgtermError, OSError) as exc:
            log.warning("scratch toggle %s failed: %s", agent.target, exc)

    async def toggle_zoom(self) -> None:
        """Toggle zoom on the active surface (what the user is looking at)."""
        log.info("zoom toggle")
        try:
            await self.client.zoom_toggle()
        except (AgtermError, OSError) as exc:
            log.warning("zoom toggle failed: %s", exc)

    async def next_attention(self) -> None:
        """Server-side jump to the next blocked/completed session. Sends no keystrokes."""
        try:
            session_id = await self.client.next_attention()
        except (AgtermError, OSError) as exc:
            log.warning("next-attention jump failed: %s", exc)
            return
        if session_id is not None:
            if self.slots.agent(session_id) is not None:
                # A keyless session (overflow) is still a valid selection: agterm has
                # switched to it, and the bottom row should act on what the user sees.
                self.selected = session_id
                log.info("next-attention -> %s (slot %s)", session_id, self.selected_slot)
            else:
                # agterm landed on a session the cache does not know (stale, mid-resync).
                # It has ALREADY switched there, so keeping the old selection would aim
                # a subsequent approve at a session the user is no longer looking at.
                self.selected = None
                log.info("next-attention -> %s (unknown session; selection cleared)", session_id)
        if self.config.activate_app:
            await self.client.activate_app()

    async def run_action(self, action: str, held_ms: float) -> None:
        """Run a bottom-row action key against the selected agent."""
        if action == "next_attention":
            # Navigation only: agterm moves its own selection; nothing is typed anywhere.
            await self.next_attention()
            return

        target = self._selected_agent()
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
            await self.focus_selected()
            return
        if action in self.config.require_long_press_for and held_ms < self.config.long_press_ms:
            log.info("ignored short press for guarded action %r (%.0fms)", action, held_ms)
            return
        if action == "scratch":
            await self.toggle_scratch()
            return
        if action == "zoom":
            await self.toggle_zoom()
            return
        await self.send_named_key(action)

    def _schedule_focus(self) -> None:
        """Keep agterm following the knob without strobing it.

        Focusing every detent would hammer agterm through every session on the way
        past; focusing only the resting position (the old behaviour) froze the UI
        mid-spin and then jumped, which read as the loop being broken. So the next
        focus fires as soon as the throttle window from the LAST fired focus has
        passed: a slow turn follows detent by detent with no lag, a fast spin samples
        the loop in order at the throttle rate, and each detent cancels the pending
        task so the resting position always gets the final say.
        """
        if self._focus_task is not None:
            self._focus_task.cancel()
        delay = max(0.0, self._focus_fired + FOCUS_COALESCE_SECONDS - time.monotonic())

        async def settle() -> None:
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            self._focus_fired = time.monotonic()
            await self.focus_selected()

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
        if config.rotate == "font_size":
            await self.adjust_font(delta)
            return
        self.cycle(config.rotate, delta)
        if config.focus_on_turn:
            self._schedule_focus()
