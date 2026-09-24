import json
from pathlib import Path
from carry_forward import load_state, save_state


def test_load_state_missing_file_returns_empty(tmp_path):
    assert load_state(tmp_path / "does-not-exist.json") == {"items": []}


def test_save_state_then_load_state_round_trips(tmp_path):
    path = tmp_path / ".carry-forward.json"
    state = {"items": [{"id": "abc123", "text": "Call Albert Hu", "first_seen": "2026-09-16",
                        "last_seen": "2026-09-23", "resolved": False, "resolved_date": None}]}

    save_state(path, state)
    result = load_state(path)

    assert result == state
