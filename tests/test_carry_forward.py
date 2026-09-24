import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from carry_forward import load_state, save_state, extract_tomorrow_items, match_items, filter_recurring, update_state, format_carry_report, main


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


def test_match_items_downgrade_records_downgraded_from():
    """Fix 2: a downgraded low-confidence match must carry what it would have matched."""
    open_items = [{"id": "abc123", "text": "Call Albert Hu"}]

    with patch("carry_forward.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_choice_result({"today_0": ("abc123", 0.3)})

        result = match_items("key", ["Vaguely similar item"], open_items)

    entry = result["Vaguely similar item"]
    assert entry["matched_id"] is None
    assert entry["downgraded_from"] == {"id": "abc123", "text": "Call Albert Hu"}


def test_match_items_genuine_new_item_has_no_downgraded_from():
    """Fix 2: a raw 'new' choice (no downgrade) must not carry downgraded_from."""
    open_items = [{"id": "abc123", "text": "Call Albert Hu"}]

    with patch("carry_forward.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_choice_result({"today_0": ("new", 0.98)})

        result = match_items("key", ["Totally new item"], open_items)

    entry = result["Totally new item"]
    assert entry["matched_id"] is None
    assert entry.get("downgraded_from") is None


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


def test_format_carry_report_sorts_oldest_first_and_excludes_resolved():
    state = {"items": [
        {"id": "a", "text": "Newer item", "first_seen": "2026-09-22", "last_seen": "2026-09-24",
         "resolved": False, "resolved_date": None},
        {"id": "b", "text": "Older item", "first_seen": "2026-09-16", "last_seen": "2026-09-24",
         "resolved": False, "resolved_date": None},
        {"id": "c", "text": "Resolved item", "first_seen": "2026-09-01", "last_seen": "2026-09-05",
         "resolved": True, "resolved_date": "2026-09-05"},
    ]}

    result = format_carry_report(state, "2026-09-24", [])

    lines = result.splitlines()
    assert lines[0] == "Older item — open 8 days (since 2026-09-16)"
    assert lines[1] == "Newer item — open 2 days (since 2026-09-22)"
    assert "Resolved item" not in result


def test_format_carry_report_singular_day():
    state = {"items": [{"id": "a", "text": "Just started", "first_seen": "2026-09-23",
                        "last_seen": "2026-09-24", "resolved": False, "resolved_date": None}]}

    result = format_carry_report(state, "2026-09-24", [])

    assert "Just started — open 1 day (since 2026-09-23)" in result


def test_format_carry_report_no_open_items():
    assert format_carry_report({"items": []}, "2026-09-24", []) == "No open items."


def test_format_carry_report_shows_match_decisions():
    state = {"items": [{"id": "abc", "text": "Call Albert Hu", "first_seen": "2026-09-16",
                        "last_seen": "2026-09-24", "resolved": False, "resolved_date": None}]}
    match_log = [{"today_text": "Call Albert Hu — send two slots", "matched_text": "Call Albert Hu",
                  "confidence": 0.91}]

    result = format_carry_report(state, "2026-09-24", match_log)

    assert "matched: 'Call Albert Hu — send two slots' -> 'Call Albert Hu' (confidence 0.91)" in result


def test_main_refuses_to_run_on_a_log_older_than_tracked_state(tmp_path, capsys):
    log_file = tmp_path / "2026-09-20.md"  # older than the state file's newest last_seen
    log_file.write_text("### Tomorrow\n- [ ] Something\n")
    state_path = tmp_path / ".carry-forward.json"
    state_path.write_text(json.dumps({"items": [
        {"id": "abc", "text": "Existing", "first_seen": "2026-09-16", "last_seen": "2026-09-23",
         "resolved": False, "resolved_date": None},
    ]}))

    with patch.dict("carry_forward.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
        exit_code = main([str(log_file), "--state", str(state_path)])

    assert exit_code == 1
    assert "older than" in capsys.readouterr().err


def test_main_runs_full_cycle_and_persists_state(tmp_path):
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Tomorrow\n- [ ] Call Albert Hu\n")
    state_path = tmp_path / ".carry-forward.json"

    with patch("carry_forward.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        # match_items skips the API call (no open items yet); filter_recurring is the only real call.
        mock_client.system_one.return_value = _mock_noul_result({"cand_0": 0.1})

        with patch.dict("carry_forward.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
            exit_code = main([str(log_file), "--state", str(state_path)])

    assert exit_code == 0
    assert state_path.exists()
    saved = json.loads(state_path.read_text())
    assert saved["items"][0]["text"] == "Call Albert Hu"
    assert saved["items"][0]["first_seen"] == "2026-09-24"


def test_main_api_failure_prints_clear_message_and_exits_1(tmp_path, capsys):
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Tomorrow\n- [ ] Something\n")
    state_path = tmp_path / ".carry-forward.json"

    with patch("carry_forward.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.side_effect = Exception("TypeSafe down")

        with patch.dict("carry_forward.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
            exit_code = main([str(log_file), "--state", str(state_path)])

    assert exit_code == 1
    assert "Carry-forward matching failed" in capsys.readouterr().err
    assert not state_path.exists()  # never got to save_state


def test_main_refuses_on_old_log_when_resolved_date_is_the_newest_tracked_date(tmp_path, capsys):
    """Fix 4: the out-of-order guard must also consider resolved_date, not just last_seen.

    All tracked items are resolved with a resolved_date newer than the incoming
    log's date, but last_seen alone (the old guard) would not catch this.
    """
    log_file = tmp_path / "2026-09-15.md"
    log_file.write_text("### Tomorrow\n- [ ] Something\n")
    state_path = tmp_path / ".carry-forward.json"
    state_path.write_text(json.dumps({"items": [
        {"id": "abc", "text": "Old item", "first_seen": "2026-09-01", "last_seen": "2026-09-10",
         "resolved": True, "resolved_date": "2026-09-20"},
    ]}))

    with patch.dict("carry_forward.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
        exit_code = main([str(log_file), "--state", str(state_path)])

    assert exit_code == 1
    assert "older than" in capsys.readouterr().err


def test_main_prints_downgrade_line_for_low_confidence_match(tmp_path, capsys):
    """Fix 2: main() must print a downgrade line before the report."""
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Tomorrow\n- [ ] Vaguely similar item\n")
    state_path = tmp_path / ".carry-forward.json"
    state_path.write_text(json.dumps({"items": [
        {"id": "abc123", "text": "Call Albert Hu", "first_seen": "2026-09-16",
         "last_seen": "2026-09-23", "resolved": False, "resolved_date": None},
    ]}))

    with patch("carry_forward.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        # First call: match_items (low-confidence match -> downgrade).
        # Second call: filter_recurring, for the now-unmatched item.
        mock_client.system_one.side_effect = [
            _mock_choice_result({"today_0": ("abc123", 0.3)}),
            _mock_noul_result({"cand_0": 0.1}),
        ]

        with patch.dict("carry_forward.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
            exit_code = main([str(log_file), "--state", str(state_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert (
        "low confidence, treated as new: 'Vaguely similar item' "
        "(closest: 'Call Albert Hu', 0.30)"
    ) in out


def test_main_prints_resolution_line_when_item_resolved(tmp_path, capsys):
    """Fix 2: main() must announce items the resolution sweep just resolved."""
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Tomorrow\n- [ ] Something totally different\n")
    state_path = tmp_path / ".carry-forward.json"
    state_path.write_text(json.dumps({"items": [
        {"id": "abc123", "text": "Old open item", "first_seen": "2026-09-10",
         "last_seen": "2026-09-23", "resolved": False, "resolved_date": None},
    ]}))

    with patch("carry_forward.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.side_effect = [
            _mock_choice_result({"today_0": ("new", 0.98)}),
            _mock_noul_result({"cand_0": 0.1}),
        ]

        with patch.dict("carry_forward.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
            exit_code = main([str(log_file), "--state", str(state_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "resolved today: 'Old open item' (was open 14 days)" in out


def test_main_missing_api_key_prints_clear_message(tmp_path, capsys):
    """Fix 3: a missing TYPESAFE_API_KEY must produce a config-specific message, not a raw KeyError."""
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Tomorrow\n- [ ] Something\n")

    with patch("carry_forward.load_dotenv"):
        with patch.dict("carry_forward.os.environ", {}, clear=True):
            exit_code = main([str(log_file)])

    assert exit_code == 1
    assert "TYPESAFE_API_KEY is not set" in capsys.readouterr().err


def test_main_missing_log_dir_without_state_flag_prints_clear_message(tmp_path, capsys):
    """Fix 3: a missing LOG_DIR (no --state given) must not raise a raw KeyError."""
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Tomorrow\n- [ ] Something\n")

    with patch("carry_forward.load_dotenv"):
        with patch.dict("carry_forward.os.environ", {"TYPESAFE_API_KEY": "ts-key"}, clear=True):
            exit_code = main([str(log_file)])

    assert exit_code == 1
    assert "LOG_DIR is not set" in capsys.readouterr().err


def test_main_nonexistent_log_path_returns_1_with_clear_message(tmp_path, capsys):
    """Fix 3: a bad log path must not raise a raw FileNotFoundError."""
    state_path = tmp_path / ".carry-forward.json"
    nonexistent = tmp_path / "2026-09-24.md"  # never created

    with patch.dict("carry_forward.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
        exit_code = main([str(nonexistent), "--state", str(state_path)])

    assert exit_code == 1
    assert "Could not read log file" in capsys.readouterr().err


def test_main_corrupt_state_file_returns_1_with_clear_message(tmp_path, capsys):
    """Fix 3: corrupt JSON in the state file must not raise a raw JSONDecodeError."""
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Tomorrow\n- [ ] Something\n")
    state_path = tmp_path / ".carry-forward.json"
    state_path.write_text("{not valid json")

    with patch.dict("carry_forward.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
        exit_code = main([str(log_file), "--state", str(state_path)])

    assert exit_code == 1
    assert "Could not read carry-forward state file" in capsys.readouterr().err


def test_main_save_state_failure_returns_1_with_clear_message(tmp_path, capsys):
    """Fix 3: a failed write must not raise a raw exception past main()."""
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Tomorrow\n- [ ] Call Albert Hu\n")
    state_path = tmp_path / ".carry-forward.json"

    with patch("carry_forward.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_noul_result({"cand_0": 0.1})

        with patch("carry_forward.save_state", side_effect=OSError("disk full")):
            with patch.dict("carry_forward.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
                exit_code = main([str(log_file), "--state", str(state_path)])

    assert exit_code == 1
    assert "Could not save carry-forward state" in capsys.readouterr().err


def test_save_state_atomic_write_leaves_original_untouched_on_failure(tmp_path):
    """Fix 6: a mid-write failure must not corrupt the existing state file."""
    path = tmp_path / ".carry-forward.json"
    original = {"items": [{"id": "abc", "text": "Original", "first_seen": "2026-09-01",
                            "last_seen": "2026-09-01", "resolved": False, "resolved_date": None}]}
    save_state(path, original)

    with patch("carry_forward.json.dumps", side_effect=OSError("simulated mid-write failure")):
        with pytest.raises(OSError):
            save_state(path, {"items": []})

    # Original file is untouched, and no stray temp file was left behind.
    assert load_state(path) == original
    leftovers = list(tmp_path.glob(".*.tmp"))
    assert leftovers == []


def test_main_empty_tomorrow_does_not_resolve_existing_items(tmp_path):
    """End-to-end regression test for the empty-day resolution bug."""
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Tomorrow\n- [ ] \n")
    state_path = tmp_path / ".carry-forward.json"
    state_path.write_text(json.dumps({"items": [
        {"id": "abc", "text": "Old open item", "first_seen": "2026-09-10", "last_seen": "2026-09-23",
         "resolved": False, "resolved_date": None},
    ]}))

    with patch.dict("carry_forward.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
        exit_code = main([str(log_file), "--state", str(state_path)])

    assert exit_code == 0
    saved = json.loads(state_path.read_text())
    item = saved["items"][0]
    assert item["resolved"] is False
    assert "Old open item — open 14 days" in format_carry_report(saved, "2026-09-24", [])
