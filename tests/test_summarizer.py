import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from summarizer import summarize


def test_summarize_returns_claude_response(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Summarize this data:")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="- summarized bullet")]

    with patch("summarizer.anthropic.Anthropic") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.messages.create.return_value = mock_response

        result = summarize("test-api-key", str(prompt_file), "raw data here")

    assert result == "- summarized bullet"


def test_summarize_includes_prompt_and_data_in_call(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("My prompt text")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="output")]

    with patch("summarizer.anthropic.Anthropic") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.messages.create.return_value = mock_response

        summarize("key", str(prompt_file), "my raw data")

    call_kwargs = mock_client.messages.create.call_args[1]
    message_content = call_kwargs["messages"][0]["content"]
    assert "My prompt text" in message_content
    assert "my raw data" in message_content
    assert "---" in message_content


def test_summarize_uses_haiku_model(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("prompt")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="output")]

    with patch("summarizer.anthropic.Anthropic") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.messages.create.return_value = mock_response

        summarize("key", str(prompt_file), "data")

    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["model"] == "claude-haiku-4-5-20251001"


def test_summarize_raises_when_response_was_truncated(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("prompt")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="- #training: Kevin said the thing was cut")]
    mock_response.stop_reason = "max_tokens"

    with patch("summarizer.anthropic.Anthropic") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.messages.create.return_value = mock_response

        with pytest.raises(RuntimeError, match="max_tokens"):
            summarize("key", str(prompt_file), "data")


def test_summarize_passes_api_key(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("prompt")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="output")]

    with patch("summarizer.anthropic.Anthropic") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.messages.create.return_value = mock_response

        summarize("my-secret-key", str(prompt_file), "data")

    mock_class.assert_called_once_with(api_key="my-secret-key")
