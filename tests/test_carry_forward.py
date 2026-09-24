import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from carry_forward import load_state, save_state, extract_tomorrow_items, match_items, filter_recurring, update_state


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


def test_match_items_no_today_items_returns_empty_without_api_call():
    with patch("carry_forward.TypeSafeClient") as mock_class:
        result = match_items("key", [], [{"id": "x", "text": "open item"}])

    assert result == {}
    mock_class.assert_not_called()


def test_match_items_no_open_items_marks_everything_new_without_api_call():
    with patch("carry_forward.TypeSafeClient") as mock_class:
        result = match_items("key", ["Call Albert Hu"], [])

    assert result == {"Call Albert Hu": {"matched_id": None, "confidence": 1.0}}
    mock_class.assert_not_called()


def _mock_choice_result(answers: dict[str, tuple[str, float]]):
    result = MagicMock()
    result.choices = {k: MagicMock(choice=c, confidence=conf) for k, (c, conf) in answers.items()}
    return result


def _mock_noul_result(nouls: dict[str, float]):
    result = MagicMock()
    result.nouls = {k: MagicMock(noul=v) for k, v in nouls.items()}
    return result


def test_match_items_returns_matched_id_and_confidence():
    open_items = [{"id": "abc123", "text": "Call Albert Hu"}]
    today_items = ["Call Albert Hu — send two time slots", "Write the outage post-mortem"]

    with patch("carry_forward.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_choice_result(
            {"today_0": ("abc123", 0.91), "today_1": ("new", 0.98)}
        )

        result = match_items("key", today_items, open_items)

    assert result == {
        "Call Albert Hu — send two time slots": {"matched_id": "abc123", "confidence": 0.91},
        "Write the outage post-mortem": {"matched_id": None, "confidence": 0.98},
    }


def test_match_items_downgrades_low_confidence_match_to_new():
    open_items = [{"id": "abc123", "text": "Call Albert Hu"}]

    with patch("carry_forward.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_choice_result({"today_0": ("abc123", 0.3)})

        result = match_items("key", ["Vaguely similar item"], open_items)

    assert result["Vaguely similar item"]["matched_id"] is None


def test_match_items_raises_on_api_failure():
    with patch("carry_forward.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.side_effect = Exception("TypeSafe down")

        with pytest.raises(Exception, match="TypeSafe down"):
            match_items("key", ["today item"], [{"id": "x", "text": "open item"}])


def test_filter_recurring_empty_makes_no_api_call():
    with patch("carry_forward.TypeSafeClient") as mock_class:
        result = filter_recurring("key", [])

    assert result == ([], [])
    mock_class.assert_not_called()


def test_filter_recurring_splits_by_threshold():
    candidates = ["Call Albert Hu", "Sales and Ops meeting"]

    with patch("carry_forward.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_noul_result({"cand_0": 0.1, "cand_1": 0.85})

        non_recurring, recurring = filter_recurring("key", candidates)

    assert non_recurring == ["Call Albert Hu"]
    assert recurring == ["Sales and Ops meeting"]


def test_update_state_adds_new_item_and_does_not_mutate_input():
    state = {"items": []}
    matches = {"Call Albert Hu": {"matched_id": None, "confidence": 1.0}}

    result = update_state(state, "2026-09-24", ["Call Albert Hu"], matches, ["Call Albert Hu"])

    assert state == {"items": []}  # input untouched
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["text"] == "Call Albert Hu"
    assert item["first_seen"] == "2026-09-24"
    assert item["last_seen"] == "2026-09-24"
    assert item["resolved"] is False
    assert item["resolved_date"] is None
    assert isinstance(item["id"], str) and item["id"]


def test_update_state_bumps_last_seen_on_match():
    state = {"items": [{"id": "abc123", "text": "Call Albert Hu", "first_seen": "2026-09-16",
                        "last_seen": "2026-09-23", "resolved": False, "resolved_date": None}]}
    matches = {"Call Albert Hu again": {"matched_id": "abc123", "confidence": 0.9}}

    result = update_state(state, "2026-09-24", ["Call Albert Hu again"], matches, [])

    item = result["items"][0]
    assert item["id"] == "abc123"
    assert item["first_seen"] == "2026-09-16"
    assert item["last_seen"] == "2026-09-24"
    assert item["resolved"] is False


def test_update_state_resolves_items_not_matched_today():
    state = {"items": [{"id": "abc123", "text": "Call Albert Hu", "first_seen": "2026-09-16",
                        "last_seen": "2026-09-23", "resolved": False, "resolved_date": None}]}

    result = update_state(state, "2026-09-24", ["Something unrelated"],
                           {"Something unrelated": {"matched_id": None, "confidence": 1.0}},
                           ["Something unrelated"])

    item = next(i for i in result["items"] if i["id"] == "abc123")
    assert item["resolved"] is True
    assert item["resolved_date"] == "2026-09-24"


def test_update_state_empty_today_items_does_not_resolve_anything():
    """Regression test for the bug the first draft shipped: an empty or
    unfilled Tomorrow section must not wipe every tracked item's history."""
    state = {"items": [{"id": "abc123", "text": "Call Albert Hu", "first_seen": "2026-09-16",
                        "last_seen": "2026-09-23", "resolved": False, "resolved_date": None}]}

    result = update_state(state, "2026-09-24", [], {}, [])

    item = result["items"][0]
    assert item["resolved"] is False
    assert item["resolved_date"] is None
    assert item["last_seen"] == "2026-09-23"  # untouched


def test_update_state_excludes_recurring_items_from_tracking():
    result = update_state(
        {"items": []}, "2026-09-24", ["Sales and Ops meeting"],
        {"Sales and Ops meeting": {"matched_id": None, "confidence": 1.0}},
        non_recurring_new_texts=[],  # filter_recurring judged it recurring, so it's not in this list
    )

    assert result["items"] == []


def test_update_state_leaves_already_resolved_items_alone():
    """Regression test: during a real sweep (non-empty today_items), already-resolved
    items are never touched. Also exercises the sweep with actual state changes."""
    state = {"items": [
        {"id": "resolved_item", "text": "Old item", "first_seen": "2026-09-01",
         "last_seen": "2026-09-05", "resolved": True, "resolved_date": "2026-09-05"},
        {"id": "unresolved_item", "text": "Pending item", "first_seen": "2026-09-16",
         "last_seen": "2026-09-23", "resolved": False, "resolved_date": None},
    ]}

    result = update_state(state, "2026-09-24", ["New unrelated item"],
                           {"New unrelated item": {"matched_id": None, "confidence": 1.0}},
                           ["New unrelated item"])

    resolved = next(i for i in result["items"] if i["id"] == "resolved_item")
    unresolved = next(i for i in result["items"] if i["id"] == "unresolved_item")

    # Already-resolved item is untouched, not re-marked with today's date
    assert resolved["resolved"] is True
    assert resolved["resolved_date"] == "2026-09-05"

    # Unresolved item does get marked resolved by the sweep
    assert unresolved["resolved"] is True
    assert unresolved["resolved_date"] == "2026-09-24"
