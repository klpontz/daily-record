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
    with patch("slack_client.build_web_client") as mock_build:
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_client.conversations_history.return_value = {"messages": []}
        mock_client.conversations_list.return_value = {"channels": []}

        result = fetch_today_slack("token", "U123", ["C001"])

    assert "channels" in result
    assert "dms" in result


def test_user_top_level_message_captures_full_thread_attributed():
    with patch("slack_client.WebClient"), \
         patch("slack_client.build_web_client") as mock_build, \
         patch("slack_client._today_oldest_ts", return_value="0"), \
         patch("slack_client._lookback_oldest_ts", return_value="0"):
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_client.conversations_history.return_value = {
            "messages": [{"user": "U123", "text": "original post", "ts": "1000.0", "reply_count": 2}]
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

    ch = result["channels"][0]
    assert ch["parent"] == {"user_id": "U123", "text": "original post"}
    assert ch["replies"] == [
        {"user_id": "U999", "text": "their response"},
        {"user_id": "U123", "text": "my follow-up"},
    ]
    assert ch["is_mine"] is True


def test_thread_started_by_other_captures_everyone_when_user_replied():
    with patch("slack_client.build_web_client") as mock_build, \
         patch("slack_client._today_oldest_ts", return_value="0"), \
         patch("slack_client._lookback_oldest_ts", return_value="0"):
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_client.conversations_history.return_value = {
            "messages": [{"user": "U999", "text": "parent", "ts": "1000.0", "reply_count": 3, "latest_reply": "1002.0"}]
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

    ch = result["channels"][0]
    assert ch["parent"] == {"user_id": "U999", "text": "parent"}
    assert ch["replies"] == [
        {"user_id": "U123", "text": "my reply"},
        {"user_id": "U888", "text": "their reply"},
    ]
    assert ch["is_mine"] is False


def test_thread_where_user_never_participated_is_excluded():
    with patch("slack_client.build_web_client") as mock_build, \
         patch("slack_client._today_oldest_ts", return_value="0"), \
         patch("slack_client._lookback_oldest_ts", return_value="0"):
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_client.conversations_history.return_value = {
            "messages": [{"user": "U999", "text": "parent", "ts": "1000.0", "reply_count": 1, "latest_reply": "1001.0"}]
        }
        mock_client.conversations_replies.return_value = {
            "messages": [
                {"user": "U999", "text": "parent", "ts": "1000.0"},
                {"user": "U888", "text": "a reply", "ts": "1001.0"},
            ]
        }
        mock_client.conversations_list.return_value = {"channels": []}
        result = fetch_today_slack("token", "U123", ["C001"])
    assert result["channels"] == []


def test_other_users_standalone_message_excluded():
    with patch("slack_client.build_web_client") as mock_build:
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_client.conversations_history.return_value = {
            "messages": [{"user": "U999", "text": "someone else", "ts": "1000.0", "reply_count": 0}]
        }
        mock_client.conversations_list.return_value = {"channels": []}
        result = fetch_today_slack("token", "U123", ["C001"])
    assert result["channels"] == []


def test_dm_captures_both_sides_in_chronological_order():
    with patch("slack_client.build_web_client") as mock_build, \
         patch("slack_client._today_oldest_ts", return_value="0"):
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        # conversations_history returns NEWEST first
        mock_client.conversations_list.return_value = {"channels": [{"id": "D1", "user": "U777"}]}
        mock_client.conversations_history.return_value = {
            "messages": [
                {"user": "U123", "text": "second (mine)", "ts": "1002.0"},
                {"user": "U777", "text": "first (theirs)", "ts": "1001.0"},
            ]
        }
        result = fetch_today_slack("token", "U123", [])
    dm = result["dms"][0]
    assert dm["partner_id"] == "U777"
    assert dm["messages"] == [
        {"user_id": "U777", "text": "first (theirs)"},
        {"user_id": "U123", "text": "second (mine)"},
    ]


def test_dm_without_user_message_excluded():
    with patch("slack_client.build_web_client") as mock_build, \
         patch("slack_client._today_oldest_ts", return_value="0"):
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_client.conversations_list.return_value = {"channels": [{"id": "D1", "user": "U777"}]}
        mock_client.conversations_history.return_value = {
            "messages": [{"user": "U777", "text": "only them", "ts": "1001.0"}]
        }
        result = fetch_today_slack("token", "U123", [])
    assert result["dms"] == []


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
    with patch("slack_client.build_web_client") as mock_build:
        mock_client = MagicMock()
        mock_build.return_value = mock_client
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
    with patch("slack_client.build_web_client") as mock_build:
        mock_client = MagicMock()
        mock_build.return_value = mock_client
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
    with patch("slack_client.build_web_client") as mock_build:
        mock_client = MagicMock()
        mock_build.return_value = mock_client
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


def test_build_web_client_retries_incomplete_read_and_ssl_error():
    import ssl
    from http.client import IncompleteRead
    from slack_sdk.http_retry.builtin_handlers import ConnectionErrorRetryHandler
    from slack_client import build_web_client

    client = build_web_client("xoxb-test")
    conn_handlers = [h for h in client.retry_handlers if isinstance(h, ConnectionErrorRetryHandler)]
    assert conn_handlers, "no ConnectionErrorRetryHandler configured"
    error_types = conn_handlers[0].error_types_to_do_retries
    assert IncompleteRead in error_types
    assert ssl.SSLError in error_types
