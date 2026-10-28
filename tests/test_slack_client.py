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


def test_channel_threads_returned_chronologically():
    with patch("slack_client.build_web_client") as mock_build, \
         patch("slack_client._today_oldest_ts", return_value="0"), \
         patch("slack_client._lookback_oldest_ts", return_value="0"):
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        # conversations_history returns NEWEST-first
        mock_client.conversations_history.return_value = {
            "messages": [
                {"user": "U123", "text": "newer post", "ts": "2000.0", "reply_count": 0},
                {"user": "U123", "text": "older post", "ts": "1000.0", "reply_count": 0},
            ]
        }
        mock_client.conversations_list.return_value = {"channels": []}
        result = fetch_today_slack("token", "U123", ["C001"])
    texts = [c["parent"]["text"] for c in result["channels"]]
    assert texts == ["older post", "newer post"]  # reversed to chronological


def test_user_post_with_no_replies_captured_with_empty_replies():
    with patch("slack_client.build_web_client") as mock_build, \
         patch("slack_client._today_oldest_ts", return_value="0"), \
         patch("slack_client._lookback_oldest_ts", return_value="0"):
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_client.conversations_history.return_value = {
            "messages": [{"user": "U123", "text": "solo post", "ts": "1000.0", "reply_count": 0}]
        }
        mock_client.conversations_list.return_value = {"channels": []}
        result = fetch_today_slack("token", "U123", ["C001"])
    ch = result["channels"][0]
    assert ch["parent"] == {"user_id": "U123", "text": "solo post"}
    assert ch["replies"] == []
    assert ch["is_mine"] is True
    mock_client.conversations_replies.assert_not_called()  # no thread fetch when reply_count == 0


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
        {"user_id": "U777", "text": "first (theirs)", "ts": "1001.0"},
        {"user_id": "U123", "text": "second (mine)", "ts": "1002.0"},
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


def test_format_attributes_names_via_directory():
    from unittest.mock import MagicMock
    directory = MagicMock()
    directory.resolve_many.return_value = {"U123": "You", "U999": "the CEO"}
    data = {
        "channels": [{
            "channel_id": "C001",
            "parent": {"user_id": "U999", "text": "next-grade isn't useful"},
            "replies": [{"user_id": "U123", "text": "agreed, it's not a reference point"}],
            "is_mine": False,
        }],
        "dms": [],
    }
    result = format_for_prompt(data, channel_names={"C001": "training"}, directory=directory)
    assert "[#training] the CEO: next-grade isn't useful" in result
    assert "You: agreed, it's not a reference point" in result


def test_format_dm_attributes_both_sides():
    from unittest.mock import MagicMock
    directory = MagicMock()
    directory.resolve_many.return_value = {"U123": "You", "U777": "a support teammate"}
    data = {
        "channels": [],
        "dms": [{
            "channel_id": "D1",
            "partner_id": "U777",
            "messages": [
                {"user_id": "U777", "text": "a district wants bulk deletes"},
                {"user_id": "U123", "text": "confirming with the team"},
            ],
        }],
    }
    result = format_for_prompt(data, directory=directory)
    assert "[DM with a support teammate]" in result
    assert "a support teammate: a district wants bulk deletes" in result
    assert "You: confirming with the team" in result


def test_format_without_directory_falls_back_to_ids():
    data = {"channels": [{"channel_id": "C001",
            "parent": {"user_id": "U999", "text": "hi"}, "replies": [], "is_mine": False}], "dms": []}
    result = format_for_prompt(data)
    assert "U999: hi" in result


def test_format_empty_returns_no_activity():
    assert format_for_prompt({"channels": [], "dms": []}) == "No Slack activity today."


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


# --- Permalinks: every exchange keeps a link back to its source ---

from slack_client import permalink, slack_permalinks, strip_unknown_links

WS = "https://acme.slack.com/"


def test_permalink_builds_archive_url_from_ts():
    assert permalink(WS, "C001", "1793113202.650909") == "https://acme.slack.com/archives/C001/p1793113202650909"


def test_permalink_tolerates_missing_trailing_slash():
    assert permalink("https://acme.slack.com", "C001", "1000.0") == "https://acme.slack.com/archives/C001/p10000"


def test_channel_thread_records_parent_ts():
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
                {"user": "U123", "text": "my reply", "ts": "1001.0"},
            ]
        }
        mock_client.conversations_list.return_value = {"channels": []}
        result = fetch_today_slack("token", "U123", ["C001"])
    assert result["channels"][0]["ts"] == "1000.0"


def test_user_post_without_replies_records_ts():
    with patch("slack_client.build_web_client") as mock_build, \
         patch("slack_client._today_oldest_ts", return_value="0"), \
         patch("slack_client._lookback_oldest_ts", return_value="0"):
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_client.conversations_history.return_value = {
            "messages": [{"user": "U123", "text": "solo post", "ts": "1000.0"}]
        }
        mock_client.conversations_list.return_value = {"channels": []}
        result = fetch_today_slack("token", "U123", ["C001"])
    assert result["channels"][0]["ts"] == "1000.0"


def test_fetch_reads_workspace_url_from_auth_test():
    with patch("slack_client.build_web_client") as mock_build:
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_client.auth_test.return_value = {"url": WS}
        mock_client.conversations_history.return_value = {"messages": []}
        mock_client.conversations_list.return_value = {"channels": []}
        result = fetch_today_slack("token", "U123", ["C001"])
    assert result["workspace_url"] == WS


def test_fetch_workspace_url_is_none_when_auth_test_fails():
    with patch("slack_client.build_web_client") as mock_build:
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_client.auth_test.side_effect = SlackApiError("boom", {"error": "invalid_auth"})
        mock_client.conversations_history.return_value = {"messages": []}
        mock_client.conversations_list.return_value = {"channels": []}
        result = fetch_today_slack("token", "U123", ["C001"])
    assert result["workspace_url"] is None


def _linked_data():
    return {
        "workspace_url": WS,
        "channels": [{
            "channel_id": "C001", "ts": "1000.0",
            "parent": {"user_id": "U999", "text": "please test"},
            "replies": [{"user_id": "U123", "text": "on it"}],
            "is_mine": False,
        }],
        "dms": [{
            "channel_id": "D1", "partner_id": "U777",
            "messages": [{"user_id": "U777", "text": "quick question", "ts": "2000.0"}],
        }],
    }


def test_format_puts_link_on_thread_header_and_dm_lines():
    result = format_for_prompt(_linked_data(), channel_names={"C001": "training"})
    assert "[#training] (link: https://acme.slack.com/archives/C001/p10000) U999: please test" in result
    assert "U777: quick question (link: https://acme.slack.com/archives/D1/p20000)" in result
    # replies don't repeat the thread link
    assert "on it (link:" not in result


def test_format_without_workspace_url_has_no_links():
    data = _linked_data()
    data["workspace_url"] = None
    assert "link:" not in format_for_prompt(data)


def test_slack_permalinks_collects_every_link_offered():
    assert slack_permalinks(_linked_data()) == {
        "https://acme.slack.com/archives/C001/p10000",
        "https://acme.slack.com/archives/D1/p20000",
    }


def test_slack_permalinks_empty_without_workspace_url():
    assert slack_permalinks({"channels": [], "dms": []}) == set()


def test_strip_unknown_links_keeps_offered_and_drops_invented():
    good = "https://acme.slack.com/archives/C001/p10000"
    summary = (
        f"- #training: A asked B to test. ([link]({good}))\n"
        "- #training: C asked D. ([link](https://acme.slack.com/archives/C001/p99999))"
    )
    out = strip_unknown_links(summary, {good})
    assert f"([link]({good}))" in out
    assert "p99999" not in out
    assert out.splitlines()[1] == "- #training: C asked D."
