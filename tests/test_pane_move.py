"""Slot stability across a pane move.

Herdr renumbers the public pane_id when a pane moves across workspaces while the same
terminal keeps running. Keying slots on pane_id reads that as "agent exited, new agent
appeared" and reshuffles the pad.
"""

from herdr_km16.mapping import Agent, SlotMap


def agent(pane, terminal, status="idle", name=None):
    return Agent(pane_id=pane, terminal_id=terminal, status=status, name=name)


def test_slot_survives_a_pane_renumbering():
    slots = SlotMap()
    slots.sync([agent("wA:p1", "term_aaa"), agent("wB:p1", "term_bbb")])
    assert slots.slot_of("term_aaa") == 0

    # Same terminals, but Herdr has renumbered the first pane.
    slots.sync([agent("wA:p3", "term_aaa"), agent("wB:p1", "term_bbb")])
    assert slots.slot_of("term_aaa") == 0, "a move must not move the key"
    assert slots.slot_of("term_bbb") == 1


def test_the_target_follows_the_new_pane_id():
    """Identity stays put, but Herdr calls must go to where the pane is now."""
    slots = SlotMap()
    slots.sync([agent("wA:p1", "term_aaa")])
    assert slots.agent_at(0).target == "wA:p1"

    slots.sync([agent("wA:p3", "term_aaa")])
    assert slots.agent_at(0).target == "wA:p3", "stale pane id would send keys nowhere"


def test_terminal_id_is_never_used_as_a_herdr_target():
    """Herdr rejects terminal IDs in agent.* calls."""
    a = agent("wA:p1", "term_aaa")
    assert a.identity == "term_aaa"
    assert a.target == "wA:p1"
    assert "term_" not in a.target


def test_a_name_outranks_both():
    a = agent("wA:p1", "term_aaa", name="reviewer")
    assert a.identity == "reviewer"
    assert a.target == "reviewer", "a named agent is addressable by name"


def test_falls_back_to_pane_id_without_a_terminal_id():
    a = Agent(pane_id="wA:p1")
    assert a.identity == "wA:p1"
    assert a.target == "wA:p1"


def test_a_genuinely_new_terminal_takes_a_new_slot():
    """The move fix must not make distinct agents collide."""
    slots = SlotMap()
    slots.sync([agent("wA:p1", "term_aaa")])
    slots.sync([agent("wA:p1", "term_aaa"), agent("wA:p2", "term_ccc")])
    assert slots.slot_of("term_aaa") == 0
    assert slots.slot_of("term_ccc") == 1


def test_status_updates_still_address_by_pane_after_a_move():
    slots = SlotMap()
    slots.sync([agent("wA:p1", "term_aaa")])
    slots.sync([agent("wA:p3", "term_aaa")])
    assert slots.update_status("wA:p3", "blocked") == 0
    assert slots.update_status("wA:p1", "done") is None, "the old pane id is gone"
    assert slots.agent_at(0).status == "blocked"
