import pytest
from unittest.mock import MagicMock, patch
from asana_filter import filter_items, format_filtered


def test_filter_items_short_circuits_on_empty():
    with patch("asana_filter.TypeSafeClient") as mock_class:
        result = filter_items("key", [], [])

    assert result == ([], [])
    mock_class.assert_not_called()


def _mock_result(nouls: dict[str, float]):
    result = MagicMock()
    result.nouls = {k: MagicMock(noul=v) for k, v in nouls.items()}
    return result


def test_filter_items_keeps_above_and_drops_at_or_below_threshold():
    tasks = [{"name": "Real work", "gid": "T1"}, {"name": "Replied to email", "gid": "T2"}]
    comments = [{"task": "Real work", "project": "P", "comment": "Left a decision"}]

    with patch("asana_filter.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_result(
            {"task_0": 0.9, "task_1": 0.5, "comment_0": 0.8}
        )

        kept_tasks, kept_comments = filter_items("key", tasks, comments)

    assert kept_tasks == [{"name": "Real work", "gid": "T1"}]
    assert kept_comments == comments


def test_filter_items_embeds_each_item_text_in_its_own_question():
    tasks = [{"name": "Task A", "gid": "T1"}, {"name": "Task B", "gid": "T2"}]

    with patch("asana_filter.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_result({"task_0": 0.9, "task_1": 0.9})

        filter_items("key", tasks, [])

    call_args = mock_client.system_one.call_args
    # Regression test: state must be empty dict, not None (API rejects None with 422)
    assert call_args.args[0] == {}
    questions = call_args.args[1]
    assert questions["task_0"].instructions["item"]["name"] == "Task A"
    assert questions["task_1"].instructions["item"]["name"] == "Task B"


def test_filter_items_raises_on_api_failure():
    with patch("asana_filter.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.side_effect = Exception("TypeSafe down")

        with pytest.raises(Exception, match="TypeSafe down"):
            filter_items("key", [{"name": "A", "gid": "T1"}], [])


def test_format_filtered_renders_tasks_and_comments():
    tasks = [{"name": "Student Kiosk Feature", "memberships": [{"project": {"name": "Help articles"}}]}]
    comments = [{"task": "Family lock prototype", "project": "Feature QA", "comment": "left a decision"}]

    result = format_filtered(tasks, comments)

    assert "- **Help articles** — Student Kiosk Feature: marked complete." in result
    assert "- **Feature QA** — Family lock prototype: left a decision" in result


def test_format_filtered_falls_back_when_both_empty():
    assert format_filtered([], []) == "_No notable Asana activity today_"


def test_format_filtered_reports_dropped_count_when_everything_filtered():
    result = format_filtered([], [], dropped_count=3)
    assert result == "_No notable Asana activity today_ (3 items filtered as noise)"


def test_format_filtered_bare_fallback_when_genuinely_empty_input():
    # dropped_count defaults to 0 when the input was empty to begin with —
    # distinguishes "nothing happened" from "things happened but got filtered."
    assert format_filtered([], [], dropped_count=0) == "_No notable Asana activity today_"


def test_format_filtered_collapses_and_truncates_comment_text():
    comments = [{"task": "T", "project": "P", "comment": "line one\n- inner bullet\nline three " + "x" * 250}]

    result = format_filtered([], comments)

    assert "\n" not in result.splitlines()[0]  # the whole bullet is one line
    assert result.count("- ") == 1  # the inner "- inner bullet" did not become its own bullet
    assert len(result) < 250


def test_format_filtered_preserves_legitimate_hyphens():
    """Verify that mid-sentence hyphens (punctuation) survive sanitization."""
    comments = [{"task": "T", "project": "P", "comment": "Left a decision - go with option B for now"}]

    result = format_filtered([], comments)

    # The legitimate hyphen in "decision - go" should be preserved
    assert "decision - go" in result


def test_filter_items_drop_line_names_task_and_prints_full_comment(capsys):
    # Sep 24 regression: the drop line showed 60 chars and no task name,
    # so a dropped approval could not be found from the output.
    long_comment = "Apologies for the delay. This looks great.\nJust need an Applies to table and you are ready to publish."
    comments = [{"task": "When I login, I see my kids for pickup", "project": None, "comment": long_comment}]

    with patch("asana_filter.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_result({"comment_0": 0.44})

        filter_items("key", [], comments)

    out = capsys.readouterr().out
    assert "When I login, I see my kids for pickup" in out
    assert "you are ready to publish." in out


def test_format_filtered_notes_dropped_count_alongside_kept_items():
    comments = [{"task": "Task A", "project": "P", "comment": "Kept"}]
    result = format_filtered([], comments, dropped_count=1)
    assert "- **P** — Task A: Kept" in result
    assert result.endswith("_(1 item filtered as noise)_")


def test_format_filtered_uses_singular_for_one_dropped_item():
    assert format_filtered([], [], dropped_count=1) == "_No notable Asana activity today_ (1 item filtered as noise)"


def test_comment_question_carries_description_thread_and_log_owner_framing():
    prior = [{"author": "teammate", "comment": "Draft is ready for review"}]
    comments = [{"task": "Pickup article", "project": "Help Articles Updates", "comment": "Looks great, publish",
                 "task_description": "Help article request form", "prior_comments": prior}]

    with patch("asana_filter.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_result({"comment_0": 0.9})

        filter_items("key", [], comments)

    q = mock_client.system_one.call_args.args[1]["comment_0"].instructions
    assert q["item"]["task_description"] == "Help article request form"
    assert q["item"]["prior_comments"] == prior
    assert "log owner" in q["question"]
    assert "approval" in q["question"]
