#!/usr/bin/env python3
"""Daily Record — pull today's Slack and Asana activity into the career log."""

import argparse
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

from asana_client import fetch_today_asana, format_for_prompt as asana_format
from log_writer import get_log_path, write_log
from slack_client import fetch_today_slack, format_for_prompt as slack_format, build_web_client
from user_directory import SlackUserDirectory
from summarizer import summarize

REQUIRED_KEYS = [
    "ANTHROPIC_API_KEY",
    "ASANA_PAT",
    "ASANA_WORKSPACE_GID",
    "LOG_DIR",
    "SLACK_BOT_TOKEN",
    "SLACK_CHANNELS",
    "SLACK_USER_ID",
    "TEMPLATE_PATH",
    "TYPESAFE_API_KEY",
]


def load_config() -> dict:
    """Load config from .env. Exit with a clear message if any required key is missing."""
    load_dotenv()
    missing = [k for k in REQUIRED_KEYS if not os.environ.get(k)]
    if missing:
        print(f"Missing required config keys: {', '.join(missing)}")
        print("Add them to your .env file. See .env.example for reference.")
        sys.exit(1)

    return {
        "anthropic_api_key": os.environ["ANTHROPIC_API_KEY"],
        "slack_bot_token": os.environ["SLACK_BOT_TOKEN"],
        "slack_user_id": os.environ["SLACK_USER_ID"],
        "slack_channels": [c.strip() for c in os.environ["SLACK_CHANNELS"].split(",")],
        "slack_channel_names": _parse_channel_names(os.environ.get("SLACK_CHANNEL_NAMES", "")),
        "asana_pat": os.environ["ASANA_PAT"],
        "asana_workspace_gid": os.environ["ASANA_WORKSPACE_GID"],
        "log_dir": os.environ["LOG_DIR"],
        "template_path": os.environ["TEMPLATE_PATH"],
        "typesafe_api_key": os.environ["TYPESAFE_API_KEY"],
        "obsidian_vault_path": os.environ.get("OBSIDIAN_VAULT_PATH", ""),
    }


def _parse_channel_names(raw: str) -> dict:
    """Parse SLACK_CHANNEL_NAMES env var into a dict.

    Format: comma-separated id:name pairs, e.g.
        C6BDPLG8N:general,C065854RB8A:announcements
    """
    mapping = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" in pair:
            channel_id, name = pair.split(":", 1)
            mapping[channel_id.strip()] = name.strip()
    return mapping


def _prompts_dir() -> Path:
    return Path(__file__).parent / "prompts"


def run(config: dict, dry_run: bool = False, debug: bool = False) -> dict:
    """Fetch, summarize, and write the daily log.

    Returns a result dict with keys: slack_ok, asana_ok, slack_count, asana_count.
    """
    slack_content = None
    asana_content = None
    slack_ok = False
    asana_ok = False
    slack_count = 0
    asana_count = 0

    # --- Slack ---
    # None = API unreachable (section skipped entirely)
    # "_Summarization failed..." = fetched but Claude failed (marker written to section)
    if debug:
        print(f"[debug] channel name mapping: {config.get('slack_channel_names')}")

    try:
        slack_data = fetch_today_slack(
            config["slack_bot_token"],
            config["slack_user_id"],
            config["slack_channels"],
            debug=debug,
        )
        slack_count = len(slack_data["channels"]) + len(slack_data["dms"])
        directory = SlackUserDirectory(build_web_client(config["slack_bot_token"]), config["slack_user_id"])
        raw = slack_format(slack_data, config.get("slack_channel_names"), directory)
        try:
            slack_content = summarize(
                config["anthropic_api_key"],
                str(_prompts_dir() / "slack_summarize.md"),
                raw,
            )
            slack_ok = True
        except Exception as e:
            print(f"⚠ Slack summarization failed: {e}")
            slack_content = "_Summarization failed — run manually_"
    except Exception as e:
        print(f"⚠ Slack pull failed: {e}")
        # slack_content stays None — section will be skipped entirely

    # --- Asana ---
    try:
        asana_data = fetch_today_asana(
            config["asana_pat"],
            config["asana_workspace_gid"],
        )
        asana_count = len(asana_data["completed_tasks"]) + len(asana_data["comments"])
        raw = asana_format(asana_data)
        try:
            asana_content = summarize(
                config["anthropic_api_key"],
                str(_prompts_dir() / "asana_filter.md"),
                raw,
            )
            asana_ok = True
        except Exception as e:
            print(f"⚠ Asana summarization failed: {e}")
            asana_content = "_Summarization failed — run manually_"
    except Exception as e:
        print(f"⚠ Asana pull failed: {e}")
        # asana_content stays None — section will be skipped entirely

    # --- Output ---
    # If both APIs were unreachable, don't write anything
    if slack_content is None and asana_content is None:
        print("Both sources failed — log not written.")
        return {"slack_ok": False, "asana_ok": False, "slack_count": 0, "asana_count": 0}

    sections = {}
    if slack_content is not None:
        sections["Slack"] = slack_content
    if asana_content is not None:
        sections["Asana"] = asana_content

    log_path = get_log_path(config["log_dir"])

    if dry_run:
        print(f"\n--- DRY RUN: would write to {log_path} ---")
        for name, content in sections.items():
            print(f"\n### {name}")
            print(content)
    else:
        write_log(log_path, config["template_path"], sections)
        print(f"Pulled {slack_count} Slack items, {asana_count} Asana items → {log_path}")
        _open_in_obsidian(config["obsidian_vault_path"], log_path)

    return {
        "slack_ok": slack_ok,
        "asana_ok": asana_ok,
        "slack_count": slack_count,
        "asana_count": asana_count,
    }


def _open_in_obsidian(vault_path: str, log_path: Path) -> None:
    """Open the log file in Obsidian using the obsidian:// URL scheme."""
    if not vault_path:
        return
    try:
        relative = log_path.relative_to(vault_path)
        url = f"obsidian://open?path={quote(str(relative))}"
        subprocess.run(["open", url], check=False)
    except Exception:
        pass  # Not in vault or open failed — non-fatal


def main():
    parser = argparse.ArgumentParser(description="Pull today's Slack and Asana into the daily log.")
    parser.add_argument("--dry-run", action="store_true", help="Print output without writing to log")
    parser.add_argument("--debug", action="store_true", help="Print Slack fetch diagnostics")
    args = parser.parse_args()

    config = load_config()
    run(config, dry_run=args.dry_run, debug=args.debug)


if __name__ == "__main__":
    main()
