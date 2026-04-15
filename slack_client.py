from datetime import date, datetime, timezone
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


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
            has_thread_activity_today = (
                msg.get("reply_count", 0) > 0 and float(latest_reply) >= float(oldest)
            )
            msg_is_today = float(msg_ts) >= float(oldest)

            if msg.get("user") == user_id and msg_is_today:
                # User's top-level message posted today — also fetch their follow-up replies
                thread_texts = None
                if msg.get("reply_count", 0) > 0:
                    try:
                        thread = client.conversations_replies(
                            channel=channel_id, ts=msg_ts, oldest=oldest, limit=100
                        )
                        # replies[0] is the parent, already captured in msg["text"]
                        user_replies = [
                            r.get("text", "") for r in thread.get("messages", [])[1:]
                            if r.get("user") == user_id
                        ]
                        if user_replies:
                            thread_texts = user_replies
                    except SlackApiError:
                        pass
                results.append({"channel_id": channel_id, "text": msg.get("text", ""), "thread": thread_texts})
            elif has_thread_activity_today:
                # Thread with replies today — include if user sent or replied in the thread
                try:
                    thread = client.conversations_replies(
                        channel=channel_id, ts=msg_ts, oldest=oldest, limit=100
                    )
                    all_replies = thread.get("messages", [])
                    parent = all_replies[0] if all_replies else msg
                    # replies[0] is the parent message
                    user_replies = [
                        r.get("text", "") for r in all_replies[1:]
                        if r.get("user") == user_id
                    ]
                    user_started = msg.get("user") == user_id
                    if user_replies or user_started:
                        results.append({
                            "channel_id": channel_id,
                            "text": parent.get("text", msg.get("text", "")),
                            "thread": user_replies or None,
                        })
                except SlackApiError:
                    pass
    except SlackApiError as e:
        print(f"⚠ Slack API error fetching channel {channel_id}: {e}")
    return results


def _fetch_dm_messages(client: WebClient, user_id: str, oldest: str) -> list:
    """Fetch DM conversations where the user sent messages today."""
    results = []
    try:
        dms = client.conversations_list(types="im", limit=50).get("channels", [])
        for dm in dms:
            try:
                history = client.conversations_history(
                    channel=dm["id"], oldest=oldest, limit=100
                )
                user_msgs = [
                    m.get("text", "") for m in history.get("messages", [])
                    if m.get("user") == user_id
                ]
                if user_msgs:
                    results.append({"channel_id": dm["id"], "messages": user_msgs})
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
            "channels": [{"channel_id": str, "text": str, "thread": list|None}, ...],
            "dms": [{"channel_id": str, "messages": [str, ...]}, ...]
        }
    """
    client = WebClient(token=token)
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


def format_for_prompt(data: dict, channel_names: dict | None = None) -> str:
    """Format fetched Slack data as a readable string for the Claude prompt.

    Args:
        data: Output of fetch_today_slack.
        channel_names: Optional mapping of channel ID -> display name,
                       e.g. {"C6BDPLG8N": "general"}. IDs without a mapping
                       are shown as-is.
    """
    if not data["channels"] and not data["dms"]:
        return "No Slack activity today."

    names = channel_names or {}

    def _channel_label(channel_id: str) -> str:
        name = names.get(channel_id, channel_id)
        return f"#{name}" if not name.startswith("#") else name

    lines = []

    if data["channels"]:
        lines.append("## Channel Messages")
        for item in data["channels"]:
            label = _channel_label(item["channel_id"])
            lines.append(f"[{label}] {item['text']}")
            if item.get("thread"):
                for reply in item["thread"]:
                    lines.append(f"  > {reply}")

    if data["dms"]:
        lines.append("## Direct Messages")
        for dm in data["dms"]:
            for msg in dm["messages"]:
                lines.append(f"[DM] {msg}")

    return "\n".join(lines)
