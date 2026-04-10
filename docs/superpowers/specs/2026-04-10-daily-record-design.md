# Daily Record — Design Spec
_2026-04-10_

## Overview

A Python script that pulls today's activity from Slack and Asana, summarizes it via the Claude API, and writes the results into the daily career coaching log. Runs on demand via a shell command or Claude Code slash command. Cron-ready for future scheduling.

---

## Project Structure

```
daily-record/
├── pull_daily.py
├── prompts/
│   ├── slack_summarize.md    # Prompt for summarizing Slack messages
│   └── asana_filter.md       # Prompt for filtering/summarizing Asana tasks
├── .env                      # Secrets + config (gitignored)
├── .env.example              # Template showing all config keys
├── requirements.txt
└── README.md
```

---

## Configuration

All config lives in `.env`. No values are hardcoded in the script.

```
ANTHROPIC_API_KEY=
SLACK_BOT_TOKEN=
SLACK_USER_ID=              # Your Slack user ID — used to filter your messages
SLACK_CHANNELS=             # Comma-separated channel IDs to pull from
ASANA_PAT=                  # Asana personal access token
ASANA_WORKSPACE_GID=        # Asana workspace GID
LOG_DIR=/Users/pontz/career-coach/logs
TEMPLATE_PATH=/Users/pontz/career-coach/logs/daily-notes-template.md
RUN_TIME=17:45              # Reference only — for future cron setup
OBSIDIAN_VAULT_PATH=        # Used to open log in Obsidian after pull
```

---

## Data Sources

### Slack
- **What:** Messages from configured channels + DMs
- **Filter:** Only messages you sent, or threads you participated in
- **Time window:** Today (midnight to time of run)
- **Token:** Slack Bot Token with `channels:history`, `im:history`, `users:read` scopes

### Asana
- **What:** Tasks completed today + comments you made today (all projects in workspace)
- **Scope:** All projects — account projects and work projects
- **Excluded:** Subtasks, tasks created but not completed
- **Filter approach:** LLM pass drops low-signal items before writing to log
- **Token:** Personal access token (PAT) — no OAuth required

---

## Data Flow

```
pull_daily.py starts
│
├── Load .env, determine today's date (YYYY-MM-DD)
│
├── SLACK FETCH
│   ├── For each configured channel: fetch messages sent today
│   ├── For DMs: fetch messages sent today
│   ├── Include thread replies where you participated
│   └── Send raw data + prompts/slack_summarize.md to Claude API
│       └── Returns: summarized Slack block
│
├── ASANA FETCH
│   ├── Fetch tasks completed today (all projects)
│   ├── Fetch comments made today
│   └── Send raw data + prompts/asana_filter.md to Claude API
│       └── Returns: filtered, summarized Asana block
│
└── LOG WRITE
    ├── If YYYY-MM-DD.md exists: find ### Slack and ### Asana markers, overwrite content
    └── If not: read TEMPLATE_PATH, create new file with sections populated
```

Two separate Claude API calls — one per source. Keeps prompts focused and independently tunable.

---

## Log Write Behavior

The script writes to `LOG_DIR/YYYY-MM-DD.md`.

- **File exists:** Finds `### Slack` and `### Asana` section markers and overwrites their content. Sections written by hand (`### Plan`, `### Done`, etc.) are untouched.
- **File does not exist:** Reads `TEMPLATE_PATH`, creates new file with sections populated.
- **Re-runs are safe:** Running the script twice overwrites the previous pull with fresh data.

### Updated Template

The `daily-notes-template.md` in the career-coach repo is updated to include `### Asana`:

```markdown
### Plan
- [ ] 

### Done
- 

### Slack
- 

### Asana
- 

### Not Done / Carried
- 

### Tomorrow
- [ ] 
```

---

## Error Handling

All failures are partial — the script writes what it can and logs what it couldn't.

| Failure | Behavior |
|--------|----------|
| Slack API unreachable | Skip `### Slack`, print `⚠ Slack pull failed: <error>` |
| Asana API unreachable | Skip `### Asana`, print `⚠ Asana pull failed: <error>` |
| Claude API fails (Slack) | Write `_Summarization failed — run manually_` under `### Slack` |
| Claude API fails (Asana) | Write `_Summarization failed — run manually_` under `### Asana` |
| Both sources fail | Nothing written to log, clear error printed, no partial file created |
| Missing `.env` key | Print error naming the missing key, exit immediately |

---

## CLI Interface

### Shell command

A shell alias or wrapper script added to PATH:

```bash
pull-daily
# runs: python3 /Users/pontz/Projects/daily-record/pull_daily.py
```

**Flags:**
- `--dry-run` — fetches and summarizes but prints to stdout instead of writing to log. Skips Obsidian open. For prompt tuning and testing API connections.

### Claude Code slash command

Stored at `/Users/pontz/career-coach/.claude/commands/pull-daily.md`.

**Behavior:**
1. Runs `pull-daily` via a `$ pull-daily` bash block in the `.md` file — this captures stdout back into the Claude Code session
2. The script prints the pull summary and opens today's log in Obsidian (via `OBSIDIAN_VAULT_PATH`)
3. Claude displays the captured stdout (pull summary) in the session

**Note:** The slash command `.md` file must use Claude Code's bash execution syntax to capture output. Simply referencing the shell command without capturing stdout means output goes to the terminal and is invisible to Claude.

---

## Prompts

### `prompts/slack_summarize.md`
Instructs Claude to summarize Slack messages into concise, career-relevant bullets. Filters out noise (reactions, short acknowledgements). Preserves decisions, questions raised, work discussed, and notable interactions.

### `prompts/asana_filter.md`
Instructs Claude to drop low-signal tasks (e.g., "replied to email", trivial admin), group related items, and output a clean list of meaningful work completed. Missing an item is acceptable — the goal is signal, not completeness.

Both prompts are tunable without touching `pull_daily.py`.

---

## Dependencies

```
anthropic
slack-sdk
asana
python-dotenv
```

---

## Future: Cron

When ready to automate, add a crontab entry:

```
45 17 * * 1-5 /path/to/pull-daily
```

`RUN_TIME=17:45` in `.env` documents the intended schedule without enforcing it — no scheduler logic lives in the script itself.
