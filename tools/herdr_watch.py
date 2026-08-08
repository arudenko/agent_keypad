r"""Phase 4: prove the Herdr side without any hardware.

    .\.venv\Scripts\python.exe tools\herdr_watch.py

Pings, lists agents, shows the slot map and the LED frame that *would* be pushed, then
streams live events. Useful for confirming that pane_agent_status_changed actually fires:
leave it running and let one of your agents finish a task or hit a permission prompt.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from herdr_km16.herdr import HerdrClient, HerdrEventStream  # noqa: E402
from herdr_km16.leds import LedRenderer  # noqa: E402
from herdr_km16.mapping import Agent, SlotMap  # noqa: E402

SWATCH = {
    "working": "\x1b[44m",
    "blocked": "\x1b[41m",
    "done": "\x1b[42m",
    "idle": "\x1b[47m",
    "unknown": "\x1b[43m",
}
RESET = "\x1b[0m"


def render_pad(slots: SlotMap) -> str:
    """A 4x4 text mock of the pad, so you can sanity-check colours without hardware."""
    lines = []
    for row in range(4):
        cells = []
        for col in range(4):
            agent = slots.agent_at(row * 4 + col)
            if agent is None:
                cells.append("  ....  ")
            else:
                cells.append(f"{SWATCH.get(agent.status, '')} {agent.status[:6]:6} {RESET}")
        lines.append(" ".join(cells))
    return "\n".join(lines)


async def main() -> int:
    client = HerdrClient()
    print(f"socket: {client.path}")

    pong = await client.ping()
    print(f"herdr {pong['version']} protocol {pong['protocol']}")

    records = await client.list_agents()
    slots = SlotMap()
    slots.sync([
        Agent(
            pane_id=r["pane_id"],
            status=r.get("agent_status", "unknown"),
            name=r.get("name"),
            cwd=r.get("cwd"),
            title=r.get("terminal_title_stripped"),
        )
        for r in records
    ])

    print(f"\n{len(records)} agent(s):")
    for slot in range(16):
        agent = slots.agent_at(slot)
        if agent:
            print(f"  key {slot:2}  {agent.status:8}  {agent.pane_id:8}  {agent.cwd}")

    print("\npad preview:")
    print(render_pad(slots))

    frame = LedRenderer(pulse=False).key_frame(slots)
    print("\nLED frame that would be pushed (chain 0):")
    print("  " + " ".join(f"{c:06x}" for c in frame[:8]))
    print("  " + " ".join(f"{c:06x}" for c in frame[8:]))

    panes = [r["pane_id"] for r in records]
    print(f"\nsubscribing to {len(panes)} pane(s) + global topology events. Ctrl+C to stop.\n")

    async for event in HerdrEventStream(panes):
        kind = event.get("event")
        data = event.get("data", {})
        if kind == "pane_agent_status_changed":
            slot = slots.update_status(data["pane_id"], data["agent_status"])
            print(f"STATUS  {data['pane_id']:8} -> {data['agent_status']:8} (key {slot})")
            print(render_pad(slots))
        else:
            print(f"{kind:28} {data.get('pane_id', '')}")
    print("stream closed by server")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
