"""Turn agent state into LED frames."""

from __future__ import annotations

import math

from .km16 import CHAIN_SIZES, CHAIN_UNDERGLOW
from .mapping import SlotMap

DEFAULT_COLORS = {
    "working": 0x0066FF,
    "blocked": 0xFF0000,
    "done": 0x00FF44,
    "idle": 0x202020,
    "unknown": 0xFF9900,
    "empty": 0x000000,
}

# States that pulse, and how strongly (0 = steady).
PULSE_DEPTH = {"blocked": 0.65, "working": 0.20}


def parse_color(value: int | str) -> int:
    """Accept 0xRRGGBB or "#rrggbb"."""
    if isinstance(value, int):
        return value & 0xFFFFFF
    return int(str(value).lstrip("#"), 16) & 0xFFFFFF


def scale(color: int, factor: float) -> int:
    factor = max(0.0, min(1.0, factor))
    r = min(255, round(((color >> 16) & 0xFF) * factor))
    g = min(255, round(((color >> 8) & 0xFF) * factor))
    b = min(255, round((color & 0xFF) * factor))
    return (r << 16) | (g << 8) | b


def pulse_factor(phase: float, depth: float) -> float:
    """Smooth 0..1 triangle-ish wave. `phase` is a free-running time in seconds."""
    if depth <= 0:
        return 1.0
    return 1.0 - depth * (0.5 - 0.5 * math.cos(2 * math.pi * phase))


class LedRenderer:
    def __init__(
        self,
        colors: dict[str, int] | None = None,
        brightness: float = 0.35,
        selected_boost: float = 2.0,
        underglow: bool = True,
        pulse: bool = True,
    ):
        self.colors = dict(DEFAULT_COLORS)
        if colors:
            self.colors.update({k: parse_color(v) for k, v in colors.items()})
        self.brightness = brightness
        self.selected_boost = selected_boost
        self.underglow = underglow
        self.pulse = pulse

    def color_for(self, status: str | None) -> int:
        if status is None:
            return self.colors["empty"]
        return self.colors.get(status, self.colors["unknown"])

    def key_frame(self, slots: SlotMap, selected: int | None = None, phase: float = 0.0) -> list[int]:
        """The 16-entry under-key frame."""
        frame = []
        for slot in range(slots.slot_count):
            agent = slots.agent_at(slot)
            if agent is None:
                frame.append(self.colors["empty"])
                continue
            base = self.color_for(agent.status)
            factor = self.brightness
            if self.pulse:
                factor *= pulse_factor(phase, PULSE_DEPTH.get(agent.status, 0.0))
            if slot == selected:
                # Brighten the selection; never replace the semantic colour.
                factor = min(1.0, factor * self.selected_boost)
            frame.append(scale(base, factor))
        return frame

    def underglow_frame(self, slots: SlotMap, phase: float = 0.0) -> list[int]:
        """Peripheral summary: the most urgent state present anywhere."""
        size = CHAIN_SIZES[CHAIN_UNDERGLOW]
        if not self.underglow:
            return [0x000000] * size
        statuses = {a.status for a in slots.live_agents()}
        for status in ("blocked", "done", "working"):
            if status in statuses:
                factor = self.brightness
                if self.pulse:
                    factor *= pulse_factor(phase, PULSE_DEPTH.get(status, 0.0))
                return [scale(self.color_for(status), factor)] * size
        return [scale(self.colors["idle"], self.brightness)] * size

    def wants_animation(self, slots: SlotMap) -> bool:
        """True when some visible state pulses, so the caller knows to keep ticking."""
        if not self.pulse:
            return False
        return any(PULSE_DEPTH.get(a.status, 0.0) > 0 for a in slots.live_agents())
