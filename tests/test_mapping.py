from herdr_km16.mapping import Agent, SlotMap


def agent(pane, status="idle", name=None):
    return Agent(pane_id=pane, status=status, name=name)


def test_agents_fill_lowest_free_slots_in_order():
    slots = SlotMap()
    slots.sync([agent("w1:p1"), agent("w2:p1"), agent("w3:p1")])
    assert slots.slots[:3] == ["w1:p1", "w2:p1", "w3:p1"]


def test_slot_is_stable_across_status_changes():
    slots = SlotMap()
    slots.sync([agent("w1:p1", "idle"), agent("w2:p1", "blocked")])
    before = slots.slots
    slots.update_status("w1:p1", "blocked")
    slots.update_status("w2:p1", "idle")
    assert slots.slots == before, "a state change must never reshuffle the keys"


def test_slot_survives_a_resync_with_reordered_input():
    slots = SlotMap()
    slots.sync([agent("w1:p1"), agent("w2:p1")])
    slots.sync([agent("w2:p1"), agent("w1:p1")])  # snapshot returns them the other way round
    assert slots.slot_of("w1:p1") == 0
    assert slots.slot_of("w2:p1") == 1


def test_exited_agent_releases_its_slot_and_it_is_reused():
    slots = SlotMap()
    slots.sync([agent("w1:p1"), agent("w2:p1")])
    assert slots.remove_pane("w1:p1") == 0
    assert slots.slots[0] is None
    slots.upsert(agent("w9:p1"))
    assert slots.slot_of("w9:p1") == 0


def test_surviving_agent_keeps_its_slot_when_another_exits():
    slots = SlotMap()
    slots.sync([agent("w1:p1"), agent("w2:p1"), agent("w3:p1")])
    slots.sync([agent("w1:p1"), agent("w3:p1")])
    assert slots.slot_of("w3:p1") == 2, "w3 must not slide down into the freed slot"


def test_static_reservation_is_honoured_and_held_open():
    slots = SlotMap(static={2: "reviewer"})
    slots.sync([agent("w1:p1", name="backend")])
    assert slots.slots[2] == "reviewer", "reserved slot stays claimed"
    assert slots.slot_of("backend") == 0
    slots.upsert(agent("w7:p1", name="reviewer"))
    assert slots.slot_of("reviewer") == 2


def test_named_agent_identity_beats_pane_id():
    slots = SlotMap()
    slots.sync([agent("w1:p1", name="backend")])
    assert slots.slot_of("backend") == 0
    assert slots.agent_at(0).pane_id == "w1:p1"


def test_status_updates_are_addressed_by_pane_even_for_named_agents():
    slots = SlotMap()
    slots.sync([agent("w1:p1", name="backend")])
    assert slots.update_status("w1:p1", "blocked") == 0
    assert slots.agent_at(0).status == "blocked"


def test_unknown_pane_status_update_is_ignored():
    slots = SlotMap()
    slots.sync([agent("w1:p1")])
    assert slots.update_status("w9:p9", "blocked") is None


def test_attention_order_prioritises_blocked_then_done():
    slots = SlotMap()
    slots.sync([
        agent("w1:p1", "idle"),
        agent("w2:p1", "working"),
        agent("w3:p1", "blocked"),
        agent("w4:p1", "done"),
    ])
    assert slots.attention_order() == [2, 3, 1, 0]


def test_attention_order_breaks_ties_by_slot():
    slots = SlotMap()
    slots.sync([agent("w1:p1", "blocked"), agent("w2:p1", "blocked")])
    assert slots.attention_order() == [0, 1]


def test_attention_order_skips_empty_slots():
    slots = SlotMap()
    slots.sync([agent("w1:p1", "idle")])
    assert slots.attention_order() == [0]


def test_allocation_stops_at_sixteen_agents():
    slots = SlotMap()
    slots.sync([agent(f"w{i}:p1") for i in range(20)])
    assert sum(1 for s in slots.slots if s) == 16
    assert slots.slot_of("w17:p1") is None


def test_agent_at_rejects_out_of_range_slot():
    slots = SlotMap()
    assert slots.agent_at(99) is None
    assert slots.agent_at(-1) is None
