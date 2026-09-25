# Daily Record

Pulls the day's Slack and Asana activity, summarizes it through the Claude API, and appends the result to a dated markdown log.

I keep a daily work log. Writing it from memory at 5pm meant the log recorded what I *remembered* doing, which is not the same as what I did — the long troubleshooting thread gets remembered, the three-message decision that actually mattered does not. This pulls the record from the source instead.

## What it produces

Appends a section like this to `LOG_DIR/YYYY-MM-DD.md`:

```markdown
### Slack
- #client-success: Priya Raman flagged a district request to bulk-delete 300+
  sign-ins, and Kevin committed to confirming with the expanded-learning team first.
- #training: Marco Ruiz asked how summer sessions are handled under the same rule.
- DM with Bea Okafor: Bea asked Kevin to cover an outstanding reply, and Kevin agreed.

### Asana
- Completed: Lompoc September attendance reconciliation
- Completed: Draft help article for bulk session editing
```

One bullet per *exchange*, not per message — a twelve-message thread about one problem is one bullet. That rule lives in `prompts/slack_summarize.md`, and getting it to hold was most of the work.

Asana items are filtered differently: a [TypeSafe](https://docs.typesafe.ai) Jev Noul call judges which completed tasks and comments are substantive (each comment is judged alongside its task's description and the up-to-four thread comments before it), and the survivors are formatted in plain code — no prompt, no generation, just a typed yes/no per item.

## Requirements

Python 3.9+. An Anthropic API key, a Slack bot token, an Asana personal access token, and a TypeSafe API key.

## Setup

1. Clone the repo and enter it:
   ```bash
   git clone https://github.com/klpontz/daily-record.git
   cd daily-record
   ```
2. Install dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in every value. See the setup sections below for where each token comes from.
4. Run it:
   ```bash
   python3 pull_daily.py --dry-run   # preview, writes nothing
   python3 pull_daily.py             # fetch, summarize, write
   ```

### Optional: a shell shortcut

```bash
REPO="$(pwd)"
mkdir -p ~/bin
cat > ~/bin/pull-daily <<EOF
#!/bin/bash
source "$REPO/venv/bin/activate"
python3 "$REPO/pull_daily.py" "\$@"
EOF
chmod +x ~/bin/pull-daily
```

Then `pull-daily` and `pull-daily --dry-run` work from anywhere, provided `~/bin` is on your `PATH`.

## Slack setup

1. Go to `api.slack.com/apps` → Create New App
2. Add Bot Token OAuth scopes: `channels:history`, `im:history`, `users:read`
3. Install the app to your workspace
4. Copy the Bot User OAuth Token → `SLACK_BOT_TOKEN`
5. Find your user ID: Slack profile → ⋮ → Copy member ID → `SLACK_USER_ID`
6. Find channel IDs: right-click a channel → View channel details → copy the ID at the bottom → `SLACK_CHANNELS`

The bot only reads channels you add it to.

## Asana setup

1. Go to `app.asana.com/0/my-apps` → Create new token
2. Copy the token → `ASANA_PAT`
3. Find your workspace GID: open any Asana task and copy the number in the URL after `/0/` → `ASANA_WORKSPACE_GID`

## TypeSafe setup

Create an account and API key at [console.typesafe.ai](https://console.typesafe.ai) → `TYPESAFE_API_KEY`. Used for Asana filtering here, and by `classify_log.py` and `carry_forward.py` below.

## Config

All settings live in `.env` (copy from `.env.example`):

| Key | Description |
|-----|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `SLACK_BOT_TOKEN` | Slack bot token (`xoxb-...`) |
| `SLACK_USER_ID` | Your Slack user ID |
| `SLACK_CHANNELS` | Comma-separated channel IDs to pull |
| `SLACK_CHANNEL_NAMES` | Optional `id:name` pairs, for readable labels |
| `ASANA_PAT` | Asana personal access token |
| `ASANA_WORKSPACE_GID` | Asana workspace GID |
| `TYPESAFE_API_KEY` | TypeSafe API key, from console.typesafe.ai |
| `LOG_DIR` | Directory where daily log files live |
| `TEMPLATE_PATH` | Path to the daily note template |
| `OBSIDIAN_VAULT_PATH` | Vault root — optional, opens the log after a pull |
| `RUN_TIME` | Reference only, for cron |

## Prompts

`prompts/slack_summarize.md` controls what gets extracted from Slack. Edit it to tune the output without touching code. The names in its example block are placeholders. Asana has no prompt to tune — see `asana_filter.py` for its Noul instructions instead.

## Classifying a day's Breakdown

`classify_log.py` reads a daily log file, classifies each `### Done` bullet into
Product/QA, Ops (transferable), or Ops (noise) via a Jev Choice call, and prints a markdown
Breakdown block with computed percentages. Percentages are a bullet count, not
effort-weighted — a grounding reference for a hand-written review, not a replacement for it.

```bash
python3 classify_log.py /path/to/logs/2026-09-24.md
```

Writes nothing to disk — read the stdout output directly.

## Tracking carry-forward items

`carry_forward.py` matches today's `### Tomorrow` checklist against a running list of
previously-open items (tracked in `.carry-forward.json` next to your logs, git-tracked), via
a Jev Choice call, and prints a days-open report with each match decision shown. Recurring or
standing items (meetings, routine monitoring) are filtered out via a Jev Noul before tracking,
so they don't accumulate a misleading day-count.

```bash
python3 carry_forward.py /path/to/logs/2026-09-24.md [--state /path/to/logs/.carry-forward.json]
```

`--state` defaults to `<LOG_DIR>/.carry-forward.json`. The tool derives "today" from the log's
filename, not the system clock — always pass the log for the day you actually want tracked,
and run it in date order.

## Tests

```bash
python3 -m pytest
```

136 tests, no network calls — the Slack, Asana, Anthropic, and TypeSafe clients are all stubbed.

## Scheduling

```bash
# weekdays at 5:45pm
45 17 * * 1-5 ~/bin/pull-daily
```

## License

MIT
