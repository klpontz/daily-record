from asana_format import format_items


def test_format_items_renders_tasks_and_comments():
    tasks = [{"name": "Student Kiosk Feature", "memberships": [{"project": {"name": "Help articles"}}]}]
    comments = [{"task": "Family lock prototype", "project": "Feature QA", "comment": "left a decision"}]

    result = format_items(tasks, comments)

    assert "- **Help articles** — Student Kiosk Feature: marked complete." in result
    assert "- **Feature QA** — Family lock prototype: left a decision" in result


def test_format_items_falls_back_when_both_empty():
    assert format_items([], []) == "_No notable Asana activity today_"


def test_format_items_collapses_and_truncates_comment_text():
    comments = [{"task": "T", "project": "P", "comment": "line one\n- inner bullet\nline three " + "x" * 250}]

    result = format_items([], comments)

    assert "\n" not in result.splitlines()[0]  # the whole bullet is one line
    assert result.count("- ") == 1  # the inner "- inner bullet" did not become its own bullet
    assert len(result) < 250


def test_format_items_preserves_legitimate_hyphens():
    """Verify that mid-sentence hyphens (punctuation) survive sanitization."""
    comments = [{"task": "T", "project": "P", "comment": "Left a decision - go with option B for now"}]

    result = format_items([], comments)

    # The legitimate hyphen in "decision - go" should be preserved
    assert "decision - go" in result


def test_completed_task_label_unchanged():
    assert format_items([{"name": "Done", "gid": "T1"}], []) == "- Done: marked complete."


def test_created_task_gets_created_label():
    assert format_items([{"name": "Feature request", "gid": "T1", "action": "created"}], []) == "- Feature request: created."


def test_every_item_is_kept_with_no_filtered_note():
    tasks = [{"name": "Replied to email", "gid": "T1"}]
    comments = [{"task": "Article", "project": "P", "comment": "Thanks! I'll take it from here."}]
    result = format_items(tasks, comments)
    assert "- Replied to email: marked complete." in result
    assert "- **P** — Article: Thanks! I'll take it from here." in result
    assert "filtered" not in result
