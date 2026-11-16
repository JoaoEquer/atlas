"""The protocol's shapes: what a frame must hold, and how a shape is written."""

import re
from dataclasses import dataclass, field

import pytest

from atlas.server import EVENTS, METHODS, SERVER_REQUESTS, ProtocolError, dump, read
from atlas.server.protocol import AskResult, Json


@dataclass(frozen=True, slots=True)
class _Inner:
    name: str


@dataclass(frozen=True, slots=True)
class _Shape:
    count: int
    items: list[_Inner]
    flag: bool | None = None
    extra: Json = None
    tags: list[str] = field(default_factory=list[str])


def test_a_shape_is_read_with_every_field_checked() -> None:
    read_back = read(_Shape, {"count": 2, "items": [{"name": "a"}], "extra": {"any": [1]}})

    assert read_back == _Shape(2, [_Inner("a")], None, {"any": [1]})
    assert dump(read_back) == {
        "count": 2,
        "items": [{"name": "a"}],
        "flag": None,
        "extra": {"any": [1]},
        "tags": [],
    }


@pytest.mark.parametrize(
    ("raw", "said"),
    [
        ([], "params must be an object"),
        ({"items": []}, "params.count is missing"),
        ({"count": True, "items": []}, "params.count has the wrong type"),
        ({"count": 1, "items": [{"name": 3}]}, "params.items[0].name has the wrong type"),
        ({"count": 1, "items": {}}, "params.items must be a list"),
        ({"count": 1, "items": [], "other": 1}, "unknown keys: other"),
        ({"count": 1, "items": [], "flag": "yes"}, "params.flag has the wrong type"),
    ],
)
def test_a_wrong_shape_says_what_is_wrong(raw: object, said: str) -> None:
    with pytest.raises(ProtocolError, match=re.escape(said)):
        read(_Shape, raw)


def test_answers_take_a_choice_the_choices_or_nothing() -> None:
    raw = {"answers": {"q0": "Lisbon", "q1": ["a", "b"], "q2": None}}

    assert read(AskResult, raw).answers == {"q0": "Lisbon", "q1": ["a", "b"], "q2": None}
    assert read(AskResult, {}).answers is None
    with pytest.raises(ProtocolError, match="wrong type"):
        read(AskResult, {"answers": ["Lisbon"]})


def test_a_literal_takes_only_its_values() -> None:
    approval = SERVER_REQUESTS["approval"].result

    assert read(approval, {"choice": "always"}).choice == "always"
    with pytest.raises(ProtocolError, match="once, conversation, always, deny"):
        read(approval, {"choice": "sometimes"})


def test_every_entry_is_declared_with_a_doc() -> None:
    for entry in (*METHODS.values(), *EVENTS.values(), *SERVER_REQUESTS.values()):
        assert entry.doc
    assert set(SERVER_REQUESTS) == {
        "ask_user",
        "approval",
        "secret",
        "browser.tabs",
        "browser.open",
        "browser.close",
        "browser.show",
        "browser.attach",
        "browser.detach",
        "browser.cdp",
        "executor.approval",
        "executor.question",
    }
