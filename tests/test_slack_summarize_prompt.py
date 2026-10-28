from pathlib import Path

PROMPT = Path(__file__).parent.parent / "prompts" / "slack_summarize.md"


def test_prompt_asks_for_attribution_and_drops_old_labels():
    text = PROMPT.read_text().lower()
    # instructs naming participants
    assert "name" in text and ("who" in text or "participant" in text)
    # no longer references the retired YOU:/CONTEXT: input labels
    assert "context:" not in text
    assert "> you:" not in text


def test_prompt_keeps_no_activity_sentinel():
    assert "_No notable Slack activity today_" in PROMPT.read_text()


def test_prompt_asks_for_source_link_and_forbids_invented_urls():
    text = PROMPT.read_text()
    assert "([link](URL))" in text
    assert "never invent" in text.lower()
    assert "no links" not in text.lower()


def test_prompt_keeps_actionable_details_of_assignments():
    text = PROMPT.read_text().lower()
    for detail in ("where the work lives", "deadline", "co-assignee"):
        assert detail in text
