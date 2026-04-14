import pytest
from unittest.mock import MagicMock, patch
from slack_sdk.errors import SlackApiError
from slack_client import fetch_today_slack, _today_oldest_ts, format_for_prompt


def test_today_oldest_ts_returns_local_midnight():
    ts = _today_oldest_ts()
    assert isinstance(ts, str)
    # Should be a valid timestamp at or before now
    from datetime import datetime
    assert float(ts) <= datetime.now().timestamp()
    # Should be within the last 24 hours
    assert datetime.now().timestamp() - float(ts) < 86400


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


def test_thread_started_by_other_includes_only_user_replies():
    with patch("slack_client.WebClient") as mock_wc_class:
        mock_client = MagicMock()
        mock_wc_class.return_value = mock_client
        # Parent message from someone else, user and another person replied
        mock_client.conversations_history.return_value = {
            "messages": [
                {"user": "U999", "text": "parent", "ts": "1000.0", "reply_count": 3}
            ]
        }
        mock_client.conversations_replies.return_value = {
            "messages": [
                {"user": "U999", "text": "parent", "ts": "1000.0"},
                {"user": "U123", "text": "my reply", "ts": "1001.0"},
                {"user": "U888", "text": "their reply", "ts": "1002.0"},
            ]
        }
        mock_client.conversations_list.return_value = {"channels": []}

        result = fetch_today_slack("token", "U123", ["C001"])

    assert len(result["channels"]) == 1
    assert result["channels"][0]["thread"] == ["my reply"]


def test_user_top_level_message_captures_their_thread_replies():
    with patch("slack_client.WebClient") as mock_wc_class:
        mock_client = MagicMock()
        mock_wc_class.return_value = mock_client
        mock_client.conversations_history.return_value = {
            "messages": [
                {"user": "U123", "text": "original post", "ts": "1000.0", "reply_count": 2}
            ]
        }
        mock_client.conversations_replies.return_value = {
            "messages": [
                {"user": "U123", "text": "original post", "ts": "1000.0"},
                {"user": "U999", "text": "their response", "ts": "1001.0"},
                {"user": "U123", "text": "my follow-up", "ts": "1002.0"},
            ]
        }
        mock_client.conversations_list.return_value = {"channels": []}

        result = fetch_today_slack("token", "U123", ["C001"])

    assert len(result["channels"]) == 1
    assert result["channels"][0]["text"] == "original post"
    assert result["channels"][0]["thread"] == ["my follow-up"]


def test_user_top_level_message_with_no_own_replies_has_null_thread():
    with patch("slack_client.WebClient") as mock_wc_class:
        mock_client = MagicMock()
        mock_wc_class.return_value = mock_client
        mock_client.conversations_history.return_value = {
            "messages": [
                {"user": "U123", "text": "original post", "ts": "1000.0", "reply_count": 1}
            ]
        }
        mock_client.conversations_replies.return_value = {
            "messages": [
                {"user": "U123", "text": "original post", "ts": "1000.0"},
                {"user": "U999", "text": "their response", "ts": "1001.0"},
            ]
        }
        mock_client.conversations_list.return_value = {"channels": []}

        result = fetch_today_slack("token", "U123", ["C001"])

    assert len(result["channels"]) == 1
    assert result["channels"][0]["thread"] is None


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


def test_channel_api_error_logs_warning_and_returns_empty(capsys):
    with patch("slack_client.WebClient") as mock_wc_class:
        mock_client = MagicMock()
        mock_wc_class.return_value = mock_client
        mock_client.conversations_history.side_effect = SlackApiError(
            "channel_not_found", {"error": "channel_not_found"}
        )
        mock_client.conversations_list.return_value = {"channels": []}

        result = fetch_today_slack("token", "U123", ["C001"])

    assert result["channels"] == []
    captured = capsys.readouterr()
    assert "C001" in captured.out
    assert "⚠" in captured.out


def test_dm_list_api_error_logs_warning_and_returns_empty(capsys):
    with patch("slack_client.WebClient") as mock_wc_class:
        mock_client = MagicMock()
        mock_wc_class.return_value = mock_client
        mock_client.conversations_history.return_value = {"messages": []}
        mock_client.conversations_list.side_effect = SlackApiError(
            "missing_scope", {"error": "missing_scope"}
        )

        result = fetch_today_slack("token", "U123", [])

    assert result["dms"] == []
    captured = capsys.readouterr()
    assert "⚠" in captured.out


def test_dm_missing_scope_prints_once_and_stops(capsys):
    """missing_scope on DM history should print one warning and bail, not one per DM."""
    with patch("slack_client.WebClient") as mock_wc_class:
        mock_client = MagicMock()
        mock_wc_class.return_value = mock_client
        mock_client.conversations_list.return_value = {
            "channels": [{"id": "D001"}, {"id": "D002"}, {"id": "D003"}]
        }
        mock_client.conversations_history.side_effect = SlackApiError(
            "missing_scope", {"error": "missing_scope", "needed": "im:history"}
        )

        result = fetch_today_slack("token", "U123", [])

    assert result["dms"] == []
    captured = capsys.readouterr()
    assert captured.out.count("⚠") == 1
    assert "im:history" in captured.out
