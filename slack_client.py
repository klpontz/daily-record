import ssl
from datetime import date, datetime, timezone
from http.client import IncompleteRead
from http.client import RemoteDisconnected
from urllib.error import URLError

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry.builtin_handlers import (
    ConnectionErrorRetryHandler,
    RateLimitErrorRetryHandler,
)


def build_web_client(token: str) -> WebClient:
    """WebClient with retry handlers that actually cover the failures we hit.

    slack-sdk's default ConnectionErrorRetryHandler only retries
    URLError/ConnectionResetError/RemoteDisconnected — NOT IncompleteRead or
    SSLEOFError, which is what Slack drops on. Adding them to error_types makes
    the SDK's retry loop (which does wrap the body read) retry correctly.
    """
    conn_retry = ConnectionErrorRetryHandler(
        max_retry_count=4,
        error_types=[URLError, ConnectionResetError, RemoteDisconnected, IncompleteRead, ssl.SSLError],
    )
    rate_retry = RateLimitErrorRetryHandler(max_retry_count=3)
    return WebClient(token=token, retry_handlers=[conn_retry, rate_retry])


def _today_oldest_ts() -> str:
    """Unix timestamp string for midnight today (local time)."""
    today_local = datetime.now().date()
    today_midnight = datetime.combine(today_local, datetime.min.time())
    return str(today_midnight.timestamp())


def _lookback_oldest_ts(days: int = 14) -> str:
    """Unix timestamp string for midnight N days ago (local time).

    Used to fetch older messages so we can detect threads that started before
    today but received replies today.
    """
    from datetime import timedelta
    today_local = datetime.now().date()
    lookback = datetime.combine(today_local - timedelta(days=days), datetime.min.time())
    return str(lookback.timestamp())


def _fetch_thread(client, channel_id, ts, oldest):
    """Full thread as ordered [{user_id, text}], parent first.
    conversations_replies returns oldest-first."""
    try:
        thread = client.conversations_replies(channel=channel_id, ts=ts, oldest=oldest, limit=100)
        return [{"user_id": m.get("user", ""), "text": m.get("text", "")} for m in thread.get("messages", [])]
    except SlackApiError:
        return []


def _fetch_channel_messages(client: WebClient, channel_id: str, user_id: str, oldest: str) -> list:
    """Fetch messages from a channel where user sent or participated in a thread.

    Uses a wider lookback window so that threads started before today but
    active today are not missed. Filters by today's oldest timestamp to
    determine whether a top-level message or thread reply qualifies.
    """
    results = []
    lookback = _lookback_oldest_ts()
    try:
        response = client.conversations_history(channel=channel_id, oldest=lookback, limit=200)
        for msg in response.get("messages", []):
            msg_ts = msg.get("ts", "0")
            latest_reply = msg.get("latest_reply", msg_ts)
            has_thread_activity_today = msg.get("reply_count", 0) > 0 and float(latest_reply) >= float(oldest)
            msg_is_today = float(msg_ts) >= float(oldest)
            user_started = msg.get("user") == user_id

            if user_started and msg_is_today:
                thread = _fetch_thread(client, channel_id, msg_ts, oldest) if msg.get("reply_count", 0) > 0 else []
                parent = thread[0] if thread else {"user_id": msg.get("user", ""), "text": msg.get("text", "")}
                replies = thread[1:] if thread else []
                results.append({"channel_id": channel_id, "parent": parent, "replies": replies, "is_mine": True})
            elif has_thread_activity_today:
                thread = _fetch_thread(client, channel_id, msg_ts, oldest)
                if not thread:
                    continue
                parent, replies = thread[0], thread[1:]
                if user_started or any(r["user_id"] == user_id for r in replies):
                    results.append({"channel_id": channel_id, "parent": parent, "replies": replies, "is_mine": user_started})
    except SlackApiError as e:
        print(f"⚠ Slack API error fetching channel {channel_id}: {e}")
    # conversations_history is newest-first; present threads oldest-first
    return list(reversed(results))


def _fetch_dm_messages(client: WebClient, user_id: str, oldest: str) -> list:
    """Fetch DM conversations where the user sent messages today."""
    results = []
    try:
        dms = client.conversations_list(types="im", limit=50).get("channels", [])
        for dm in dms:
            try:
                history = client.conversations_history(channel=dm["id"], oldest=oldest, limit=100)
                ordered = list(reversed(history.get("messages", [])))  # newest-first -> chronological
                if any(m.get("user") == user_id for m in ordered):
                    messages = [{"user_id": m.get("user", ""), "text": m.get("text", "")} for m in ordered]
                    results.append({"channel_id": dm["id"], "partner_id": dm.get("user", ""), "messages": messages})
            except SlackApiError as e:
                if e.response.get("error") == "missing_scope":
                    needed = e.response.get("needed", "unknown scope")
                    print(f"⚠ Slack DMs skipped: missing scope '{needed}'. Add it at api.slack.com/apps.")
                    break
                print(f"⚠ Slack API error fetching DM {dm.get('id')}: {e}")
    except SlackApiError as e:
        print(f"⚠ Slack API error listing DMs: {e}")
    return results


def fetch_today_slack(token: str, user_id: str, channel_ids: list, debug: bool = False) -> dict:
    """Fetch today's Slack activity for the user.

    Returns:
        {
            "channels": [{"channel_id": str, "parent": {"user_id": str, "text": str}, "replies": [{"user_id": str, "text": str}, ...], "is_mine": bool}, ...],
            "dms": [{"channel_id": str, "partner_id": str, "messages": [{"user_id": str, "text": str}, ...]}, ...]
        }
    """
    client = build_web_client(token)
    oldest = _today_oldest_ts()

    if debug:
        from datetime import datetime as _dt
        print(f"[debug] user_id={user_id}  oldest={oldest}  ({_dt.utcfromtimestamp(float(oldest)).strftime('%Y-%m-%d %H:%M UTC')})")
        try:
            all_member_channels = []
            cursor = None
            while True:
                params = {"types": "public_channel,private_channel", "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                resp = client.conversations_list(**params)
                all_member_channels.extend(resp.get("channels", []))
                cursor = resp.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            member_channels = {c["id"] for c in all_member_channels if c.get("is_member")}
            print(f"[debug] bot is member of: {member_channels or 'none'}")
        except SlackApiError:
            member_channels = set()

    channels = []
    for channel_id in channel_ids:
        if debug:
            if channel_id not in member_channels:
                print(f"[debug] channel {channel_id}: bot is NOT a member — add the app to this channel")
            else:
                try:
                    # Check with oldest filter (what the real fetch uses)
                    filtered = client.conversations_history(channel=channel_id, oldest=oldest, limit=200)
                    filtered_msgs = filtered.get("messages", [])
                    # Also peek at most recent messages without filter to check timestamps
                    recent = client.conversations_history(channel=channel_id, limit=5)
                    recent_msgs = recent.get("messages", [])
                    recent_ts = [
                        f"{_dt.utcfromtimestamp(float(m['ts'])).strftime('%Y-%m-%d %H:%M UTC')} (user={m.get('user')})"
                        for m in recent_msgs if "ts" in m
                    ]
                    user_ids_seen = {m.get("user") for m in filtered_msgs if m.get("user")}
                    matched = sum(1 for m in filtered_msgs if m.get("user") == user_id)
                    print(f"[debug] channel {channel_id}: {len(filtered_msgs)} messages since oldest, {matched} from you")
                    print(f"[debug] channel {channel_id}: last 5 messages (unfiltered): {recent_ts or 'none'}")
                except SlackApiError as e:
                    print(f"[debug] channel {channel_id}: error {e}")
        channels.extend(_fetch_channel_messages(client, channel_id, user_id, oldest))

    dms = _fetch_dm_messages(client, user_id, oldest)
    return {"channels": channels, "dms": dms}


def format_for_prompt(data, channel_names=None, directory=None):
    """Render fetched Slack data as attributed lines for the summarizer prompt."""
    if not data["channels"] and not data["dms"]:
        return "No Slack activity today."

    names = channel_names or {}

    ids = set()
    for item in data["channels"]:
        ids.add(item["parent"]["user_id"])
        ids.update(r["user_id"] for r in item["replies"])
    for dm in data["dms"]:
        ids.add(dm.get("partner_id", ""))
        ids.update(m["user_id"] for m in dm["messages"])
    ids.discard("")

    resolved = directory.resolve_many(ids) if directory else {}

    def who(uid):
        return resolved.get(uid, uid or "unknown")

    def _channel_label(channel_id):
        name = names.get(channel_id, channel_id)
        return f"#{name}" if not name.startswith("#") else name

    lines = []
    if data["channels"]:
        lines.append("## Channel Threads")
        for item in data["channels"]:
            label = _channel_label(item["channel_id"])
            p = item["parent"]
            lines.append(f"[{label}] {who(p['user_id'])}: {p['text']}")
            for r in item["replies"]:
                lines.append(f"  {who(r['user_id'])}: {r['text']}")

    if data["dms"]:
        lines.append("## Direct Messages")
        for dm in data["dms"]:
            lines.append(f"[DM with {who(dm.get('partner_id', ''))}]")
            for m in dm["messages"]:
                lines.append(f"  {who(m['user_id'])}: {m['text']}")

    return "\n".join(lines)
