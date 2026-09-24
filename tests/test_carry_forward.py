import json
from pathlib import Path
from carry_forward import load_state, save_state, extract_tomorrow_items


def test_load_state_missing_file_returns_empty(tmp_path):
    assert load_state(tmp_path / "does-not-exist.json") == {"items": []}


def test_save_state_then_load_state_round_trips(tmp_path):
    path = tmp_path / ".carry-forward.json"
    state = {"items": [{"id": "abc123", "text": "Call Albert Hu", "first_seen": "2026-09-16",
                        "last_seen": "2026-09-23", "resolved": False, "resolved_date": None}]}

    save_state(path, state)
    result = load_state(path)

    assert result == state


def test_extract_tomorrow_items_pulls_unchecked_lines():
    text = (
        "### Tomorrow\n"
        "- [ ] Call Albert Hu\n"
        "- [x] Already done, should not appear\n"
        "- [ ] Follow up with Brian's email\n"
    )
    assert extract_tomorrow_items(text) == ["Call Albert Hu", "Follow up with Brian's email"]


def test_extract_tomorrow_items_skips_blank_checkbox():
    text = "### Tomorrow\n- [ ] \n- [ ] Real item\n"
    assert extract_tomorrow_items(text) == ["Real item"]


def test_extract_tomorrow_items_missing_section_returns_empty():
    assert extract_tomorrow_items("### Plan\n- [ ] something\n") == []
