"""A tiny note model."""

from dataclasses import dataclass


@dataclass
class Note:
    title: str
    tags: list[str]


def make_note(title: str, tags: list[str] = []) -> Note:
    """Create a note. Tags are normalized to lowercase and de-duplicated."""
    for tag in list(tags):
        tags.remove(tag)
        if tag.lower() not in tags:
            tags.append(tag.lower())
    return Note(title=title, tags=tags)


def add_tag(note: Note, tag: str) -> None:
    if tag.lower() not in note.tags:
        note.tags.append(tag.lower())
