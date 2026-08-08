"""Herdr socket API client.

Transport notes that drove this design (all verified against Herdr 0.8.0-preview,
protocol 19 — see CLAUDE.md):

* On Windows the socket is a named pipe whose name embeds the full path, and Python's
  ``open()`` cannot interleave reads and writes on it. ``create_pipe_connection`` works.
* A plain RPC connection is closed by the server after one response, so ``call`` opens a
  connection per request.
* After ``events.subscribe`` the connection only carries events; requests sent on it are
  never answered. ``subscribe`` therefore owns its own connection.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, AsyncIterator

# Agent lifecycle states, per the bundled schema's AgentStatus enum.
AGENT_STATES = ("idle", "working", "blocked", "done", "unknown")


class HerdrError(RuntimeError):
    """Herdr returned an error response."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def socket_path() -> str:
    """Resolve the Herdr socket, honouring HERDR_SOCKET_PATH."""
    path = os.environ.get("HERDR_SOCKET_PATH")
    if path:
        return path
    if sys.platform == "win32":
        return os.path.join(os.environ["APPDATA"], "herdr", "herdr.sock")
    return os.path.expanduser("~/.config/herdr/herdr.sock")


def _pipe_name(path: str) -> str:
    # The pipe name literally contains the path, drive letter and all.
    return r"\\.\pipe" + "\\" + path


async def _connect(path: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if sys.platform == "win32":
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        transport, protocol = await loop.create_pipe_connection(
            lambda: asyncio.StreamReaderProtocol(reader), _pipe_name(path)
        )
        return reader, asyncio.StreamWriter(transport, protocol, reader, loop)
    return await asyncio.open_unix_connection(path)


def _encode(request_id: str, method: str, params: dict[str, Any] | None) -> bytes:
    # `params` is mandatory even when empty; omitting it is an invalid_request.
    return (json.dumps({"id": request_id, "method": method, "params": params or {}}) + "\n").encode()


def _unwrap(message: dict[str, Any]) -> Any:
    if "error" in message:
        err = message["error"]
        raise HerdrError(err.get("code", "unknown"), err.get("message", ""))
    return message.get("result")


class HerdrClient:
    """Request/response half of the API. Each call uses a fresh connection."""

    def __init__(self, path: str | None = None):
        self.path = path or socket_path()
        self._seq = 0

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._seq += 1
        request_id = f"km16_{self._seq}"
        reader, writer = await _connect(self.path)
        try:
            writer.write(_encode(request_id, method, params))
            await writer.drain()
            line = await reader.readline()
            if not line:
                raise HerdrError("connection_closed", f"no response to {method}")
            return _unwrap(json.loads(line))
        finally:
            writer.close()

    async def ping(self) -> dict[str, Any]:
        return await self.call("ping")

    async def list_agents(self) -> list[dict[str, Any]]:
        return (await self.call("agent.list"))["agents"]

    async def snapshot(self) -> dict[str, Any]:
        return (await self.call("session.snapshot"))["snapshot"]

    async def focus_agent(self, target: str) -> Any:
        return await self.call("agent.focus", {"target": target})

    async def send_keys(self, target: str, keys: list[str]) -> Any:
        """Send literal key names. Never used to auto-approve a blocked agent."""
        return await self.call("agent.send_keys", {"target": target, "keys": keys})

    async def explain_agent(self, target: str) -> Any:
        return await self.call("agent.explain", {"target": target})


class HerdrEventStream:
    """Long-lived subscription connection.

    ``pane.agent_status_changed`` has no wildcard form, so the caller passes the pane IDs to
    watch. Because a subscribed connection cannot accept further requests, changing the pane
    set means tearing this stream down and opening a new one.
    """

    GLOBAL_EVENTS = (
        "pane.created",
        "pane.closed",
        "pane.exited",
        "pane.agent_detected",
        "pane.moved",
    )

    def __init__(self, pane_ids: list[str], path: str | None = None):
        self.path = path or socket_path()
        self.pane_ids = list(pane_ids)
        self._writer: asyncio.StreamWriter | None = None

    def _subscriptions(self) -> list[dict[str, Any]]:
        subs: list[dict[str, Any]] = [
            {"type": "pane.agent_status_changed", "pane_id": pane_id, "agent_status": None}
            for pane_id in self.pane_ids
        ]
        subs += [{"type": name} for name in self.GLOBAL_EVENTS]
        return subs

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        reader, writer = await _connect(self.path)
        self._writer = writer
        try:
            writer.write(_encode("km16_sub", "events.subscribe", {"subscriptions": self._subscriptions()}))
            await writer.drain()
            ack = await reader.readline()
            if not ack:
                raise HerdrError("connection_closed", "subscribe got no response")
            _unwrap(json.loads(ack))  # raises if the subscription was rejected

            while True:
                line = await reader.readline()
                if not line:
                    return  # server closed; caller reconnects
                yield json.loads(line)
        finally:
            self._writer = None
            writer.close()

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
