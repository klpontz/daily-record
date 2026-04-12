# Daily Record

Pulls today's Slack and Asana activity, summarizes it via Claude API, and writes it to the daily career coaching log.

## Setup

1. Copy `.env.example` to `.env` and fill in all values
2. Install dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
3. Create the shell command:
   ```bash
   cp /Users/pontz/bin/pull-daily ~/bin/pull-daily
   chmod +x ~/bin/pull-daily
   ```
   Or create it manually:
   ```bash
   cat > ~/bin/pull-daily << 'EOF'
   #!/bin/bash
   source /Users/pontz/Projects/daily-record/venv/bin/activate
   python3 /Users/pontz/Projects/daily-record/pull_daily.py "$@"
   EOF
   chmod +x ~/bin/pull-daily
   ```

## Usage

```bash
pull-daily           # fetch, summarize, write to today's log
pull-daily --dry-run # preview output without writing
```

## Slack App Setup

1. Go to `api.slack.com/apps` → Create New App
2. Add Bot Token OAuth scopes: `channels:history`, `im:history`, `users:read`
3. Install app to workspace
4. Copy Bot User OAuth Token → `SLACK_BOT_TOKEN` in `.env`
5. Find your User ID: Slack Profile → ⋮ → Copy member ID → `SLACK_USER_ID`
6. Find channel IDs: right-click channel → View channel details → copy ID at bottom → `SLACK_CHANNELS` (comma-separated)

## Asana Setup

1. Go to `app.asana.com/0/my-apps` → Create new token
2. Copy token → `ASANA_PAT` in `.env`
3. Find workspace GID: open any Asana task, copy the number in the URL after `/0/` → `ASANA_WORKSPACE_GID`

## Config

All settings live in `.env` (copy from `.env.example`):

| Key | Description |
|-----|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `SLACK_BOT_TOKEN` | Slack Bot Token (`xoxb-...`) |
| `SLACK_USER_ID` | Your Slack user ID |
| `SLACK_CHANNELS` | Comma-separated channel IDs to pull |
| `ASANA_PAT` | Asana Personal Access Token |
| `ASANA_WORKSPACE_GID` | Asana workspace GID |
| `LOG_DIR` | Directory where daily log files live |
| `TEMPLATE_PATH` | Path to `daily-notes-template.md` |
| `OBSIDIAN_VAULT_PATH` | Vault root (optional, opens log in Obsidian after pull) |
| `RUN_TIME` | Reference only — for future cron setup |

## Prompts

`prompts/slack_summarize.md` and `prompts/asana_filter.md` control what Claude extracts from each source. Edit them to tune output without touching code.

## Future: Cron

```bash
45 17 * * 1-5 /Users/pontz/bin/pull-daily
```
