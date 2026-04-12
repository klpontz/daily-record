import pytest
from unittest.mock import MagicMock, patch, call
from pathlib import Path


def test_missing_env_key_exits(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    # Don't set ANTHROPIC_API_KEY — should exit with clear message

    env_file = tmp_path / ".env"
    env_file.write_text("SLACK_BOT_TOKEN=xoxb-test\n")  # missing ANTHROPIC_API_KEY

    with patch("pull_daily.load_dotenv"):
        with patch("pull_daily.os.environ.get", return_value=None):
            import pull_daily
            with pytest.raises(SystemExit):
                pull_daily.load_config()


def test_both_sources_fail_writes_nothing(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    template = tmp_path / "template.md"
    template.write_text("### Slack\n-\n\n### Asana\n-\n")

    config = {
        "anthropic_api_key": "key",
        "slack_bot_token": "token",
        "slack_user_id": "U1",
        "slack_channels": ["C1"],
        "asana_pat": "pat",
        "asana_workspace_gid": "WS1",
        "log_dir": str(log_dir),
        "template_path": str(template),
        "obsidian_vault_path": "",
    }

    with patch("pull_daily.fetch_today_slack", side_effect=Exception("Slack down")):
        with patch("pull_daily.fetch_today_asana", side_effect=Exception("Asana down")):
            import pull_daily
            result = pull_daily.run(config, dry_run=True)

    assert result["slack_ok"] is False
    assert result["asana_ok"] is False
    # No log file should be written when both fail
    log_files = list(log_dir.iterdir())
    assert len(log_files) == 0


def test_slack_api_failure_skips_slack_section_but_writes_asana(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    template = tmp_path / "template.md"
    template.write_text("### Slack\n-\n\n### Asana\n-\n")

    config = {
        "anthropic_api_key": "key",
        "slack_bot_token": "token",
        "slack_user_id": "U1",
        "slack_channels": ["C1"],
        "asana_pat": "pat",
        "asana_workspace_gid": "WS1",
        "log_dir": str(log_dir),
        "template_path": str(template),
        "obsidian_vault_path": "",
    }

    with patch("pull_daily.fetch_today_slack", side_effect=Exception("Slack down")):
        with patch("pull_daily.fetch_today_asana", return_value={"completed_tasks": [{"name": "Task A", "gid": "T1"}], "comments": []}):
            with patch("pull_daily.summarize", return_value="- Task A done"):
                with patch("pull_daily.asana_format", return_value="raw asana data"):
                    import pull_daily
                    pull_daily.run(config, dry_run=False)

    from datetime import date
    log_file = log_dir / f"{date.today().isoformat()}.md"
    content = log_file.read_text()
    # Asana section written, Slack section left as-is from template (not written)
    assert "- Task A done" in content


def test_claude_failure_writes_failure_marker(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    template = tmp_path / "template.md"
    template.write_text("### Slack\n-\n\n### Asana\n-\n")

    config = {
        "anthropic_api_key": "key",
        "slack_bot_token": "token",
        "slack_user_id": "U1",
        "slack_channels": ["C1"],
        "asana_pat": "pat",
        "asana_workspace_gid": "WS1",
        "log_dir": str(log_dir),
        "template_path": str(template),
        "obsidian_vault_path": "",
    }

    with patch("pull_daily.fetch_today_slack", return_value={"channels": [], "dms": []}):
        with patch("pull_daily.fetch_today_asana", return_value={"completed_tasks": [], "comments": []}):
            with patch("pull_daily.summarize", side_effect=Exception("Claude down")):
                with patch("pull_daily.slack_format", return_value="raw slack"):
                    with patch("pull_daily.asana_format", return_value="raw asana"):
                        import pull_daily
                        pull_daily.run(config, dry_run=False)

    from datetime import date
    log_file = log_dir / f"{date.today().isoformat()}.md"
    content = log_file.read_text()
    assert "_Summarization failed — run manually_" in content


def test_dry_run_does_not_write_log(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    template = tmp_path / "template.md"
    template.write_text("### Slack\n-\n\n### Asana\n-\n")

    config = {
        "anthropic_api_key": "key",
        "slack_bot_token": "token",
        "slack_user_id": "U1",
        "slack_channels": ["C1"],
        "asana_pat": "pat",
        "asana_workspace_gid": "WS1",
        "log_dir": str(log_dir),
        "template_path": str(template),
        "obsidian_vault_path": "",
    }

    with patch("pull_daily.fetch_today_slack", return_value={"channels": [], "dms": []}):
        with patch("pull_daily.fetch_today_asana", return_value={"completed_tasks": [], "comments": []}):
            with patch("pull_daily.summarize", return_value="- summary"):
                with patch("pull_daily.slack_format", return_value="raw"):
                    with patch("pull_daily.asana_format", return_value="raw"):
                        import pull_daily
                        pull_daily.run(config, dry_run=True)

    log_files = list(log_dir.iterdir())
    assert len(log_files) == 0
