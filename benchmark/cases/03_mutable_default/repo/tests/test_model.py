from notes.model import add_tag, make_note


def test_tags_are_normalized():
    note = make_note("groceries", ["Food", "food", "Errand"])
    assert sorted(note.tags) == ["errand", "food"]


def test_notes_without_tags_are_independent():
    first = make_note("first")
    add_tag(first, "urgent")
    second = make_note("second")
    assert second.tags == []


def test_caller_list_is_not_mutated():
    original = ["A", "B"]
    make_note("x", original)
    assert original == ["A", "B"]
