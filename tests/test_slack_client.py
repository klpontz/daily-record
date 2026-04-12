import pytest
from unittest.mock import MagicMock, patch
from slack_client import fetch_today_slack, _today_oldest_ts, format_for_prompt


def test_today_oldest_ts_returns_float_string():
    ts = _today_oldest_ts()
    assert isinstance(ts, str)
    assert float(ts) > 0


def test_fetch_returns_channels_and_dms_keys():
    with patch("slack_client.WebClient") as mock_wc_class:
        mock_client = MagicMock()
        mock_wc_class.return_value = mock_client
        mock_client.conversations_history.return_value = {"messages": []}
        mock_client.conversations_list.return_value = {"channels": []}

        result = fetch_today_slack("token", "U123", ["C001"])

    assert "channels" in result
    assert "dms" in result


def test_user_messages_included():
    with patch("slack_client.WebClient") as mock_wc_class:
        mock_client = MagicMock()
        mock_wc_class.return_value = mock_client
        mock_client.conversations_history.return_value = {
            "messages": [
                {"user": "U123", "text": "hello", "ts": "1000.0", "reply_count": 0}
            ]
        }
        mock_client.conversations_list.return_value = {"channels": []}

        result = fetch_today_slack("token", "U123", ["C001"])

    assert len(result["channels"]) == 1
    assert result["channels"][0]["text"] == "hello"


def test_other_users_messages_excluded():
    with patch("slack_client.WebClient") as mock_wc_class:
        mock_client = MagicMock()
        mock_wc_class.return_value = mock_client
        mock_client.conversations_history.return_value = {
            "messages": [
                {"user": "U999", "text": "someone else", "ts": "1000.0", "reply_count": 0}
            ]
        }
        mock_client.conversations_list.return_value = {"channels": []}

        result = fetch_today_slack("token", "U123", ["C001"])

    assert len(result["channels"]) == 0


def test_thread_with_user_reply_included():
    with patch("slack_client.WebClient") as mock_wc_class:
        mock_client = MagicMock()
        mock_wc_class.return_value = mock_client
        # Parent message from someone else, but user replied in thread
        mock_client.conversations_history.return_value = {
            "messages": [
                {"user": "U999", "text": "parent", "ts": "1000.0", "reply_count": 2}
            ]
        }
        mock_client.conversations_replies.return_value = {
            "messages": [
                {"user": "U999", "text": "parent", "ts": "1000.0"},
                {"user": "U123", "text": "my reply", "ts": "1001.0"},
            ]
        }
        mock_client.conversations_list.return_value = {"channels": []}

        result = fetch_today_slack("token", "U123", ["C001"])

    assert len(result["channels"]) == 1


def test_format_for_prompt_produces_string():
    data = {
        "channels": [
            {"channel_id": "C001", "text": "hello", "thread": None}
        ],
        "dms": []
    }
    result = format_for_prompt(data)
    assert isinstance(result, str)
    assert "hello" in result


def test_format_for_prompt_empty_returns_no_activity():
    result = format_for_prompt({"channels": [], "dms": []})
    assert result == "No Slack activity today."
