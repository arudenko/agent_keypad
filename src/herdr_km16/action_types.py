"""Action vocabulary shared by config, routing and rendering.

A leaf module with no intra-package imports: `leds` needs to know which actions require a
target in order to dim their keys, and `actions` needs the same set to refuse them, but
`config` imports `leds` and `actions` imports `config`, so anywhere else creates a cycle.
"""

from __future__ import annotations

# Actions a physical key can be bound to instead of holding an agent.
VALID_ACTION_KEYS = frozenset({"approve", "reject", "interrupt", "next_attention", "none"})

# Actions that do nothing without a selected agent, so their keys dim when there is none.
ACTIONS_NEED_TARGET = frozenset({"approve", "reject", "interrupt"})

# Literal keystrokes for agterm's `session.type` (a newline is a Return press).
# `approve`/`reject` are the action-key spellings of the same keystrokes the encoders
# send, kept distinct so they can be gated separately.
KEY_NAMES = {
    "escape": "\x1b",
    "reject": "\x1b",
    "enter": "\n",
    "approve": "\n",
    "interrupt": "\x03",
}

DEFAULT_ACTION_COLORS = {
    "approve": 0x00FF44,
    "reject": 0xFF0000,
    "interrupt": 0xFF9900,
    "next_attention": 0x0066FF,
}
