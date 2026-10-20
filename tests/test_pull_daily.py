import pytest
from unittest.mock import MagicMock, patch, call
from pathlib import Path


def test_missing_typesafe_key_exits(tmp_path):
    complete_env = {
        "ANTHROPIC_API_KEY": "key", "SLACK_BOT_TOKEN": "token", "SLACK_USER_ID": "U1",
        "SLACK_CHANNELS": "C1", "ASANA_PAT": "pat", "ASANA_WORKSPACE_GID": "WS1",
        "LOG_DIR": str(tmp_path), "TEMPLATE_PATH": str(tmp_path / "t.md"),
        # TYPESAFE_API_KEY intentionally omitted
    }
    with patch("pull_daily.load_dotenv"):
        with patch.dict("pull_daily.os.environ", complete_env, clear=True):
            import pull_daily
            with pytest.raises(SystemExit):
                pull_daily.load_config()


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
        "typesafe_api_key": "ts-key",
        "log_dir": str(log_dir),
        "template_path": str(template),
        "obsidian_vault_path": "",
    }

    with patch("pull_daily.fetch_today_slack", side_effect=Exception("Slack down")):
        with patch("pull_daily.fetch_today_asana", side_effect=Exception("Asana down")):
            import pull_daily
            # dry_run=False: proves the early return (not dry_run suppression) prevents writes
            result = pull_daily.run(config, dry_run=False)

    assert result["slack_ok"] is False
    assert result["asana_ok"] is False
    # No log file should be written when both fail — early return fires before write_log
    log_files = list(log_dir.iterdir())
    assert len(log_files) == 0


def test_slack_api_failure_skips_slack_section_but_writes_asana(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    template = tmp_path / "template.md"
    template.write_text("### Slack\n-\n\n### Asana\n-\n")

    config = {
        "anthropic_api_key": "key", "slack_bot_token": "token", "slack_user_id": "U1",
        "slack_channels": ["C1"], "asana_pat": "pat", "asana_workspace_gid": "WS1",
        "typesafe_api_key": "ts-key",
        "log_dir": str(log_dir), "template_path": str(template), "obsidian_vault_path": "",
    }

    with patch("pull_daily.fetch_today_slack", side_effect=Exception("Slack down")):
        with patch("pull_daily.fetch_today_asana", return_value={"completed_tasks": [{"name": "Task A", "gid": "T1"}], "comments": []}):
            with patch("pull_daily.filter_items", return_value=([{"name": "Task A", "gid": "T1"}], [])):
                with patch("pull_daily.format_filtered", return_value="- Task A done"):
                    import pull_daily
                    pull_daily.run(config, dry_run=False)

    from datetime import date
    log_file = log_dir / f"{date.today().isoformat()}.md"
    content = log_file.read_text()
    assert "- Task A done" in content


def test_slack_claude_failure_writes_failure_marker(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    template = tmp_path / "template.md"
    template.write_text("### Slack\n-\n\n### Asana\n-\n")

    config = {
        "anthropic_api_key": "key", "slack_bot_token": "token", "slack_user_id": "U1",
        "slack_channels": ["C1"], "asana_pat": "pat", "asana_workspace_gid": "WS1",
        "typesafe_api_key": "ts-key",
        "log_dir": str(log_dir), "template_path": str(template), "obsidian_vault_path": "",
    }

    with patch("pull_daily.fetch_today_slack", return_value={"channels": [], "dms": []}):
        with patch("pull_daily.fetch_today_asana", return_value={"completed_tasks": [], "comments": []}):
            with patch("pull_daily.summarize", side_effect=Exception("Claude down")):
                with patch("pull_daily.slack_format", return_value="raw slack"):
                    import pull_daily
                    pull_daily.run(config, dry_run=False)

    from datetime import date
    log_file = log_dir / f"{date.today().isoformat()}.md"
    content = log_file.read_text()
    assert "_Summarization failed — run manually_" in content


def test_asana_filter_failure_writes_failure_marker(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    template = tmp_path / "template.md"
    template.write_text("### Slack\n-\n\n### Asana\n-\n")

    config = {
        "anthropic_api_key": "key", "slack_bot_token": "token", "slack_user_id": "U1",
        "slack_channels": ["C1"], "asana_pat": "pat", "asana_workspace_gid": "WS1",
        "typesafe_api_key": "ts-key",
        "log_dir": str(log_dir), "template_path": str(template), "obsidian_vault_path": "",
    }

    with patch("pull_daily.fetch_today_slack", return_value={"channels": [], "dms": []}):
        with patch("pull_daily.fetch_today_asana", return_value={"completed_tasks": [{"name": "A", "gid": "T1"}], "comments": []}):
            with patch("pull_daily.filter_items", side_effect=Exception("TypeSafe down")):
                with patch("pull_daily.slack_format", return_value="raw slack"):
                    with patch("pull_daily.summarize", return_value="- ok"):
                        import pull_daily
                        pull_daily.run(config, dry_run=False)

    from datetime import date
    log_file = log_dir / f"{date.today().isoformat()}.md"
    content = log_file.read_text()
    assert "_Filtering failed — run manually_" in content


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
        "typesafe_api_key": "ts-key",
        "log_dir": str(log_dir),
        "template_path": str(template),
        "obsidian_vault_path": "",
    }

    with patch("pull_daily.fetch_today_slack", return_value={"channels": [], "dms": []}):
        with patch("pull_daily.fetch_today_asana", return_value={"completed_tasks": [], "comments": []}):
            with patch("pull_daily.summarize", return_value="- summary"):
                with patch("pull_daily.slack_format", return_value="raw"):
                    with patch("pull_daily.filter_items", return_value=([], [])):
                        import pull_daily
                        pull_daily.run(config, dry_run=True)

    log_files = list(log_dir.iterdir())
    assert len(log_files) == 0


def test_run_passes_directory_to_slack_format(tmp_path):
    log_dir = tmp_path / "logs"; log_dir.mkdir()
    template = tmp_path / "template.md"; template.write_text("### Slack\n-\n\n### Asana\n-\n")
    config = {
        "anthropic_api_key": "key", "slack_bot_token": "token", "slack_user_id": "U1",
        "slack_channels": ["C1"], "slack_channel_names": {}, "asana_pat": "pat",
        "asana_workspace_gid": "WS1", "typesafe_api_key": "ts-key", "log_dir": str(log_dir),
        "template_path": str(template),
        "obsidian_vault_path": "",
    }
    with patch("pull_daily.fetch_today_slack", return_value={"channels": [], "dms": []}), \
         patch("pull_daily.fetch_today_asana", return_value={"completed_tasks": [], "comments": []}), \
         patch("pull_daily.summarize", return_value="- ok"), \
         patch("pull_daily.slack_format") as mock_slack_format, \
         patch("pull_daily.filter_items", return_value=([], [])), \
         patch("pull_daily.build_web_client"), \
         patch("pull_daily.SlackUserDirectory") as mock_dir:
        mock_slack_format.return_value = "raw slack"
        import pull_daily
        pull_daily.run(config, dry_run=True)

    # directory instance was passed as the third arg to slack_format
    assert mock_slack_format.call_args.args[2] is mock_dir.return_value


def test_summary_line_splits_kept_and_filtered_asana_counts(tmp_path, capsys):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    template = tmp_path / "template.md"
    template.write_text("### Slack\n-\n\n### Asana\n-\n")

    config = {
        "anthropic_api_key": "key", "slack_bot_token": "token", "slack_user_id": "U1",
        "slack_channels": ["C1"], "asana_pat": "pat", "asana_workspace_gid": "WS1",
        "typesafe_api_key": "ts-key",
        "log_dir": str(log_dir), "template_path": str(template), "obsidian_vault_path": "",
    }
    comments = [{"task": f"T{i}", "project": None, "comment": "c"} for i in range(4)]

    with patch("pull_daily.fetch_today_slack", side_effect=Exception("Slack down")):
        with patch("pull_daily.fetch_today_asana", return_value={"completed_tasks": [], "comments": comments}):
            with patch("pull_daily.filter_items", return_value=([], comments[:3])):
                import pull_daily
                pull_daily.run(config, dry_run=False)

    assert "4 Asana items (3 kept, 1 filtered)" in capsys.readouterr().out


def test_created_tasks_are_counted_and_filtered(tmp_path, capsys):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    template = tmp_path / "template.md"
    template.write_text("### Slack\n-\n\n### Asana\n-\n")

    config = {
        "anthropic_api_key": "key", "slack_bot_token": "token", "slack_user_id": "U1",
        "slack_channels": ["C1"], "asana_pat": "pat", "asana_workspace_gid": "WS1",
        "typesafe_api_key": "ts-key",
        "log_dir": str(log_dir), "template_path": str(template), "obsidian_vault_path": "",
    }
    completed = [{"name": "Done", "gid": "T1"}]
    created = [{"name": "Feature request", "gid": "T2", "action": "created"}]

    with patch("pull_daily.fetch_today_slack", side_effect=Exception("Slack down")):
        with patch("pull_daily.fetch_today_asana", return_value={
            "completed_tasks": completed, "created_tasks": created, "comments": [],
        }):
            with patch("pull_daily.filter_items", return_value=(completed + created, [])) as mock_filter:
                import pull_daily
                pull_daily.run(config, dry_run=False)

    assert mock_filter.call_args.args[1] == completed + created
    assert "2 Asana items (2 kept, 0 filtered)" in capsys.readouterr().out
