"""Stable agent -> key slot allocation.

The device is only useful if a key keeps meaning the same agent. Slots are therefore sticky:
an agent holds its key for as long as it is alive, new agents take the lowest free key, and
nothing is ever re-sorted on a state change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SLOT_COUNT = 16

# Which states deserve attention first, for encoder navigation.
ATTENTION_ORDER = ("blocked", "done", "working", "idle", "unknown")


@dataclass
class Agent:
    pane_id: str
    status: str = "unknown"
    name: str | None = None
    cwd: str | None = None
    title: str | None = None

    @property
    def key(self) -> str:
        """Preferred stable identity: a Herdr agent name if it has one, else the pane."""
        return self.name or self.pane_id


@dataclass
class SlotMap:
    """Maps agents to the 16 physical keys."""

    slot_count: int = SLOT_COUNT
    static: dict[int, str] = field(default_factory=dict)
    preserve_slots: bool = True
    # Keys bound to actions instead of agents; never allocated to an agent.
    action_slots: frozenset[int] = frozenset()
    _slots: list[str | None] = field(default_factory=list, init=False)
    _agents: dict[str, Agent] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._slots = [None] * self.slot_count
        self.action_slots = frozenset(self.action_slots)
        for slot, identity in self.static.items():
            if 0 <= slot < self.slot_count and slot not in self.action_slots:
                self._slots[slot] = identity

    @property
    def agent_capacity(self) -> int:
        """How many agents the pad can show, once action keys are taken out."""
        return self.slot_count - len(self.action_slots)

    # --- queries ----------------------------------------------------------

    @property
    def slots(self) -> list[str | None]:
        return list(self._slots)

    def agent_at(self, slot: int) -> Agent | None:
        if not 0 <= slot < self.slot_count:
            return None
        identity = self._slots[slot]
        return self._agents.get(identity) if identity else None

    def slot_of(self, identity: str) -> int | None:
        try:
            return self._slots.index(identity)
        except ValueError:
            return None

    def live_agents(self) -> list[Agent]:
        return [a for a in (self.agent_at(i) for i in range(self.slot_count)) if a]

    def attention_order(self) -> list[int]:
        """Occupied slots ordered by how much they want the user, then by slot for stability."""
        scored = []
        for slot in range(self.slot_count):
            agent = self.agent_at(slot)
            if agent is None:
                continue
            try:
                rank = ATTENTION_ORDER.index(agent.status)
            except ValueError:
                rank = len(ATTENTION_ORDER)
            scored.append((rank, slot))
        return [slot for _, slot in sorted(scored)]

    # --- updates ----------------------------------------------------------

    def _reserved_for(self, identity: str) -> int | None:
        for slot, pinned in self.static.items():
            if pinned == identity and 0 <= slot < self.slot_count and slot not in self.action_slots:
                return slot
        return None

    def _allocate(self, identity: str) -> int | None:
        pinned = self._reserved_for(identity)
        if pinned is not None:
            self._slots[pinned] = identity
            return pinned
        pinned_identities = set(self.static.values())
        for slot in range(self.slot_count):
            if slot in self.action_slots:
                continue
            occupant = self._slots[slot]
            # A static reservation stays empty until its agent shows up.
            if occupant is None and self.static.get(slot) is None:
                self._slots[slot] = identity
                return slot
            if occupant is not None and occupant not in self._agents and occupant not in pinned_identities:
                self._slots[slot] = identity
                return slot
        return None

    def sync(self, agents: list[Agent]) -> None:
        """Reconcile against an authoritative agent list (a fresh snapshot)."""
        incoming = {a.key: a for a in agents}
        for slot, identity in enumerate(self._slots):
            if identity and identity not in incoming:
                if not (self.preserve_slots and self.static.get(slot) == identity):
                    self._slots[slot] = self.static.get(slot)
        self._agents = incoming
        for identity in incoming:
            if self.slot_of(identity) is None:
                self._allocate(identity)

    def upsert(self, agent: Agent) -> int | None:
        """Add or update one agent, allocating a slot if it is new."""
        self._agents[agent.key] = agent
        slot = self.slot_of(agent.key)
        return slot if slot is not None else self._allocate(agent.key)

    def update_status(self, pane_id: str, status: str) -> int | None:
        """Apply a pane.agent_status_changed event. Returns the affected slot, if any."""
        for identity, agent in self._agents.items():
            if agent.pane_id == pane_id:
                agent.status = status
                return self.slot_of(identity)
        return None

    def remove_pane(self, pane_id: str) -> int | None:
        for identity, agent in list(self._agents.items()):
            if agent.pane_id == pane_id:
                slot = self.slot_of(identity)
                del self._agents[identity]
                if slot is not None:
                    self._slots[slot] = self.static.get(slot)
                return slot
        return None
