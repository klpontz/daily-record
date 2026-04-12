from datetime import date, datetime, timezone
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def _today_oldest_ts() -> str:
    """Unix timestamp string for midnight today (UTC)."""
    today_midnight = datetime.combine(date.today(), datetime.min.time())
    return str(today_midnight.replace(tzinfo=timezone.utc).timestamp())


def _fetch_channel_messages(client: WebClient, channel_id: str, user_id: str, oldest: str) -> list:
    """Fetch messages from a channel where user sent or participated in a thread."""
    results = []
    try:
        response = client.conversations_history(channel=channel_id, oldest=oldest, limit=200)
        for msg in response.get("messages", []):
            if msg.get("user") == user_id:
                results.append({"channel_id": channel_id, "text": msg.get("text", ""), "thread": None})
            elif msg.get("reply_count", 0) > 0:
                try:
                    thread = client.conversations_replies(
                        channel=channel_id, ts=msg["ts"], oldest=oldest, limit=100
                    )
                    replies = thread.get("messages", [])
                    if any(r.get("user") == user_id for r in replies):
                        thread_texts = [r.get("text", "") for r in replies]
                        results.append({"channel_id": channel_id, "text": msg.get("text", ""), "thread": thread_texts})
                except SlackApiError:
                    pass
    except SlackApiError:
        pass
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
            except SlackApiError:
                pass
    except SlackApiError:
        pass
    return results


def fetch_today_slack(token: str, user_id: str, channel_ids: list) -> dict:
    """Fetch today's Slack activity for the user.

    Returns:
        {
            "channels": [{"channel_id": str, "text": str, "thread": list|None}, ...],
            "dms": [{"channel_id": str, "messages": [str, ...]}, ...]
        }
    """
    client = WebClient(token=token)
    oldest = _today_oldest_ts()

    channels = []
    for channel_id in channel_ids:
        channels.extend(_fetch_channel_messages(client, channel_id, user_id, oldest))

    dms = _fetch_dm_messages(client, user_id, oldest)
    return {"channels": channels, "dms": dms}


def format_for_prompt(data: dict) -> str:
    """Format fetched Slack data as a readable string for the Claude prompt."""
    if not data["channels"] and not data["dms"]:
        return "No Slack activity today."

    lines = []

    if data["channels"]:
        lines.append("## Channel Messages")
        for item in data["channels"]:
            lines.append(f"[{item['channel_id']}] {item['text']}")
            if item.get("thread"):
                for reply in item["thread"]:
                    lines.append(f"  > {reply}")

    if data["dms"]:
        lines.append("\n## Direct Messages")
        for dm in data["dms"]:
            for msg in dm["messages"]:
                lines.append(f"[DM] {msg}")

    return "\n".join(lines)
