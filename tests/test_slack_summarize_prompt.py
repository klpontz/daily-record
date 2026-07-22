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
