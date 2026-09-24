from classify_log import extract_bullets


def test_extract_bullets_pulls_from_done_only():
    text = (
        "### Plan\n- [ ] ignored\n\n"
        "### Done\n- Fixed the thing\n- Shipped the other thing\n\n"
        "### Slack\n- #training: discussed something\n\n"
        "### Asana\n- **Project** — Task: did it.\n"
    )
    assert extract_bullets(text) == ["Fixed the thing", "Shipped the other thing"]


def test_extract_bullets_empty_log_returns_empty_list():
    text = "### Done\n- \n\n### Slack\n- \n"
    assert extract_bullets(text) == []


def test_extract_bullets_missing_done_section_returns_empty_list():
    assert extract_bullets("### Plan\n- a\n") == []
