"""agterm control-socket client.

Transport facts, verified empirically against the live agterm install (by capturing
agtermctl's wire traffic and probing the real socket):

* AF_UNIX socket at ``~/Library/Application Support/agterm/agterm.sock``. Inside an agterm
  session ``$AGTERM_SOCKET`` carries the exact path; ``AGTERM_STATE_DIR`` relocates the
  state directory.
* Requests are one JSON object per line: ``{"cmd": ..., "target": ..., "args": {...}}``.
  ``target``/``args`` are optional and top-level, not wrapped in params.
* Responses are ``{"ok": true, "result": {...}}`` or ``{"ok": false, "error": "<string>"}``.
* **Every connection is one-shot**: the server closes it after a single response. Sending a
  second request on the same connection gets a reset, so each call opens a fresh
  connection. There is no subscription mode; events are polled.
* ``events.read`` returns ``{"events": {"run": UUID, "items": [...], "next": N}}``. With no
  cursor it baselines at the current tail (empty ``items``). Resume with
  ``{"run": <run>, "after": "<next>", "limit": N}`` -- ``after`` is a *string* on the wire.
  A changed ``run`` means the app restarted and the cursor is void; resync from ``tree``.
* Event items: ``{"kind", "seq", "ts", "window", "workspace"?, "session"?, "payload"}``.
  Kinds seen here: ``status``, ``notify``, ``session.created``, ``session.closed``,
  ``tree.changed``. A ``status`` payload is ``{"status", "blink", "name", "pane"?}`` and
  carries an explicit ``"idle"`` (unlike the tree, where idle is an absent field).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from typing import Any, AsyncIterator

# The internal status vocabulary is still Herdr's, so config.yaml colour keys, the LED
# renderer and the slot map all keep working unchanged. agterm speaks a different enum;
# map_status() below is the single translation point.
AGENT_STATES = ("idle", "working", "blocked", "done", "unknown")

# agterm session status -> internal state. The tree omits `status` entirely for an idle
# session, so None maps too.
STATUS_MAP = {
    "active": "working",
    "completed": "done",
    "blocked": "blocked",
    "idle": "idle",
    None: "idle",
}

# Kinds that change which sessions exist, so the caller should resync from `tree`.
TOPOLOGY_KINDS = frozenset({"session.created", "session.closed", "tree.changed"})


def map_status(raw: str | None) -> str:
    """Translate an agterm status to the internal vocabulary. Unknown values render as
    the `unknown` colour rather than raising: a future agterm may add states."""
    return STATUS_MAP.get(raw, "unknown")


class AgtermError(RuntimeError):
    """agterm returned an error response, or the transport failed."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def socket_path() -> str:
    """Resolve the control socket the same way agtermctl does."""
    path = os.environ.get("AGTERM_SOCKET")
    if path:
        return path
    state_dir = os.environ.get("AGTERM_STATE_DIR")
    if state_dir:
        return os.path.join(state_dir, "agterm.sock")
    return os.path.expanduser("~/Library/Application Support/agterm/agterm.sock")


def _encode(cmd: str, target: str | None, args: dict[str, Any] | None) -> bytes:
    payload: dict[str, Any] = {"cmd": cmd}
    if target is not None:
        payload["target"] = target
    if args is not None:
        payload["args"] = args
    return (json.dumps(payload) + "\n").encode()


def _unwrap(message: dict[str, Any]) -> Any:
    if not message.get("ok", False):
        raise AgtermError("error", str(message.get("error", "unknown error")))
    return message.get("result")


APP_BUNDLE_ID = "com.umputun.agterm"


class AgtermClient:
    """Request/response side. Each call uses a fresh connection (the server is one-shot)."""

    def __init__(self, path: str | None = None):
        self.path = path or socket_path()

    async def call(self, cmd: str, args: dict[str, Any] | None = None,
                   target: str | None = None) -> Any:
        reader, writer = await asyncio.open_unix_connection(self.path)
        try:
            writer.write(_encode(cmd, target, args))
            await writer.drain()
            line = await reader.readline()
            if not line:
                raise AgtermError("connection_closed", f"no response to {cmd}")
            return _unwrap(json.loads(line))
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def tree(self) -> dict[str, Any]:
        return (await self.call("tree"))["tree"]

    async def list_agents(self) -> list[dict[str, Any]]:
        """Flatten the frontmost window's tree into one record per session.

        Every session is a potential agent slot: the tree cannot distinguish an idle agent
        from a plain shell (idle drops the `status` field), and an agent that vanished from
        the pad whenever it went idle would lose its key. `name` is the sidebar label --
        an OSC title Claude Code rewrites constantly -- so it is display-only here; the
        stable identity and the command target are both the session UUID.
        """
        tree = await self.tree()
        records = []
        for workspace in tree.get("workspaces", []):
            for node in workspace.get("sessions", []):
                records.append({
                    "pane_id": node["id"],
                    "terminal_id": node["id"],
                    "agent_status": map_status(node.get("status")),
                    "title": node.get("name") or node.get("title"),
                    "cwd": node.get("cwd"),
                })
        return records

    async def focus_agent(self, target: str) -> Any:
        return await self.call("session.select", target=target)

    async def send_keys(self, target: str, keys: list[str]) -> Any:
        """Type literal keystrokes into a session. A newline is a Return press.

        Never used to auto-approve a blocked agent; the long-press gate lives in actions.
        """
        return await self.call(
            "session.type", target=target, args={"text": "".join(keys), "select": False}
        )

    async def activate_app(self) -> None:
        """Bring agterm to the macOS foreground.

        The control socket's window/session selection moves agterm's *internal* focus but
        never raises the app over whatever application is frontmost (verified: frontmost
        stays put after `window select`). `open -b` activates a running app without
        launching a second instance. Fixed argument array, no shell. Best-effort: a
        missing `open` (non-macOS) is not an error.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                "open", "-b", APP_BUNDLE_ID,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await process.wait()
        except (FileNotFoundError, OSError):
            pass

    async def next_attention(self) -> str | None:
        """Server-side jump to the next blocked/completed session.

        Returns the newly selected session id when the server reports one.
        """
        result = await self.call("session.go", args={"to": "next-attention"})
        if isinstance(result, dict):
            return result.get("id")
        return None


class AgtermEventStream:
    """Cursor-based event poll presented as an async iterator.

    Unlike the Herdr stream there is nothing to subscribe or tear down: the cursor is
    plain state, each poll is its own connection, and a topology change never requires
    rebuilding the stream. When the server's run UUID changes (app restarted) the cursor
    is void and this raises, so the caller resyncs from `tree` and starts a new stream.
    """

    def __init__(self, path: str | None = None, poll_seconds: float = 0.25,
                 limit: int = 500):
        self.client = AgtermClient(path)
        self.poll_seconds = poll_seconds
        self.limit = limit
        self._run: str | None = None
        self._after: int | None = None
        self._closed = False

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        while not self._closed:
            args: dict[str, Any] = {"limit": self.limit}
            if self._run is not None:
                args["run"] = self._run
                args["after"] = str(self._after)  # the wire wants a string
            events = (await self.client.call("events.read", args))["events"]
            if self._run is not None and events["run"] != self._run:
                raise AgtermError("run_changed", "agterm restarted; event cursor is void")
            self._run = events["run"]
            self._after = events["next"]
            items = events["items"]
            for item in items:
                yield item
            if not items:
                await asyncio.sleep(self.poll_seconds)

    def close(self) -> None:
        self._closed = True
