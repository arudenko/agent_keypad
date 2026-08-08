r"""Does pane.agent_status_changed actually fire, and does the filter shape matter?

    .\.venv\Scripts\python.exe tools\diag_status_events.py null
    .\.venv\Scripts\python.exe tools\diag_status_events.py omit

The schema types `agent_status` as AgentStatus-or-null. We send null meaning "any status",
but null may instead mean "match nothing". Run both variants side by side and see which
receives events when an agent changes state.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from herdr_km16.herdr import HerdrClient, _connect, _encode, _unwrap  # noqa: E402

DURATION = 600.0


async def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "null"
    client = HerdrClient()
    panes = [a["pane_id"] for a in await client.list_agents()]

    subs = []
    for pane_id in panes:
        sub = {"type": "pane.agent_status_changed", "pane_id": pane_id}
        if mode == "null":
            sub["agent_status"] = None
        subs.append(sub)

    reader, writer = await _connect(client.path)
    writer.write(_encode("diag", "events.subscribe", {"subscriptions": subs}))
    await writer.drain()
    ack = json.loads(await reader.readline())
    try:
        _unwrap(ack)
    except Exception as exc:
        print(f"[{mode}] SUBSCRIBE REJECTED: {exc}")
        return 1

    print(f"[{mode}] subscribed to {len(panes)} pane(s), listening {DURATION:.0f}s", flush=True)
    start = time.monotonic()
    count = 0
    while time.monotonic() - start < DURATION:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=DURATION)
        except asyncio.TimeoutError:
            break
        if not line:
            print(f"[{mode}] stream closed by server", flush=True)
            break
        event = json.loads(line)
        if event.get("event") == "pane_agent_status_changed":
            count += 1
            d = event["data"]
            print(f"[{mode}] +{time.monotonic()-start:6.1f}s  STATUS  {d['pane_id']} -> {d['agent_status']}", flush=True)
    print(f"[{mode}] done: {count} status event(s)", flush=True)
    writer.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
