# Daily Record Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python script that pulls today's Slack messages and Asana tasks, summarizes them via Claude API (Haiku), and writes the results into the daily career coaching log at `/Users/pontz/career-coach/logs/YYYY-MM-DD.md`.

**Architecture:** Five focused modules (`log_writer`, `summarizer`, `slack_client`, `asana_client`, `pull_daily`) with `.env` config. Each module has one responsibility and is independently testable. `pull_daily.py` orchestrates fetch → summarize → write with per-source error isolation: a failed source writes a failure marker in its section, never a partial log.

**Tech Stack:** Python 3.11+, `anthropic` (claude-haiku-4-5-20251001), `slack-sdk`, `requests` (Asana REST API directly), `python-dotenv`, `pytest`, `pytest-mock`

---

## File Map

| File | Responsibility |
|------|---------------|
| `pull_daily.py` | Entry point, CLI args, orchestration, Obsidian open, pull summary output |
| `log_writer.py` | Section overwrite logic, template-based file creation, all log file I/O |
| `summarizer.py` | Load prompt from file, call Claude API, return summarized string |
| `slack_client.py` | Fetch messages from channels and DMs, filter to user's activity |
| `asana_client.py` | Fetch completed tasks and user's comments via Asana REST API |
| `prompts/slack_summarize.md` | Prompt for Slack summarization (tunable without code changes) |
| `prompts/asana_filter.md` | Prompt for Asana filtering and summarization |
| `tests/test_log_writer.py` | Tests for section overwrite and file creation |
| `tests/test_summarizer.py` | Tests for Claude API call behavior |
| `tests/test_slack_client.py` | Tests for Slack fetch with mocked SDK |
| `tests/test_asana_client.py` | Tests for Asana fetch with mocked requests |
| `tests/test_pull_daily.py` | Integration tests for orchestrator error handling |
| `.env.example` | Config key template with comments |
| `.gitignore` | Excludes `.env`, `__pycache__`, `.pytest_cache` |
| `requirements.txt` | Pinned dependencies |

---

### Task 1: Project Scaffold

**Files:**
- Create: `/Users/pontz/Projects/daily-record/requirements.txt`
- Create: `/Users/pontz/Projects/daily-record/.env.example`
- Create: `/Users/pontz/Projects/daily-record/.gitignore`
- Create: `/Users/pontz/Projects/daily-record/prompts/.gitkeep`
- Create: `/Users/pontz/Projects/daily-record/tests/__init__.py`

- [ ] **Step 1: Verify the project directory exists and initialize git**

```bash
cd /Users/pontz/Projects/daily-record
ls
git init
```

Expected: directory exists with `docs/` already present from brainstorming. Git initialized.

- [ ] **Step 2: Create `requirements.txt`**

```
anthropic==0.49.0
slack-sdk==3.35.0
requests==2.32.3
python-dotenv==1.1.0
pytest==8.3.5
pytest-mock==3.14.0
```

- [ ] **Step 3: Create `.env.example`**

```
ANTHROPIC_API_KEY=              # Anthropic API key from console.anthropic.com
SLACK_BOT_TOKEN=                # Slack Bot Token (xoxb-...) — requires channels:history, im:history, users:read scopes
SLACK_USER_ID=                  # Your Slack user ID (e.g. U0123ABCDEF) — found in Slack profile
SLACK_CHANNELS=                 # Comma-separated channel IDs (e.g. C0123ABC,C0456DEF)
ASANA_PAT=                      # Asana Personal Access Token from app.asana.com/0/my-apps
ASANA_WORKSPACE_GID=            # Asana workspace GID — found in workspace URL
LOG_DIR=/Users/pontz/career-coach/logs
TEMPLATE_PATH=/Users/pontz/career-coach/logs/daily-notes-template.md
RUN_TIME=17:45                  # Reference only — for future cron setup
OBSIDIAN_VAULT_PATH=            # Absolute path to your Obsidian vault root
```

- [ ] **Step 4: Create `.gitignore`**

```
.env
__pycache__/
*.pyc
.pytest_cache/
.DS_Store
```

- [ ] **Step 5: Create `prompts/` directory and `tests/` package**

```bash
mkdir -p /Users/pontz/Projects/daily-record/prompts
touch /Users/pontz/Projects/daily-record/tests/__init__.py
```

- [ ] **Step 6: Install dependencies**

```bash
cd /Users/pontz/Projects/daily-record
pip install -r requirements.txt
```

Expected: all packages install without errors.

- [ ] **Step 7: Commit**

```bash
cd /Users/pontz/Projects/daily-record
git add requirements.txt .env.example .gitignore prompts/ tests/ docs/
git commit -m "feat: scaffold daily-record project"
```

---

### Task 2: Log Writer Module

**Files:**
- Create: `log_writer.py`
- Create: `tests/test_log_writer.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_log_writer.py`:

```python
import pytest
from pathlib import Path
from datetime import date
from log_writer import get_log_path, overwrite_section, write_log

SAMPLE_LOG = """### Plan
- [ ] do something

### Done
- finished a thing

### Slack
- old slack content

### Asana
- old asana content

### Not Done / Carried
- 

### Tomorrow
- [ ] 
"""


def test_get_log_path_uses_today():
    path = get_log_path("/tmp/logs")
    assert path == Path(f"/tmp/logs/{date.today().isoformat()}.md")


def test_get_log_path_accepts_specific_date():
    path = get_log_path("/tmp/logs", date(2026, 4, 10))
    assert path == Path("/tmp/logs/2026-04-10.md")


def test_overwrite_section_replaces_middle_section():
    result = overwrite_section(SAMPLE_LOG, "Slack", "- new message from Kevin")
    assert "- new message from Kevin" in result
    assert "- old slack content" not in result


def test_overwrite_section_leaves_other_sections_untouched():
    result = overwrite_section(SAMPLE_LOG, "Slack", "- new content")
    assert "### Plan" in result
    assert "### Done" in result
    assert "### Asana" in result
    assert "- old asana content" in result  # Asana untouched


def test_overwrite_section_last_section_in_file():
    text = "### Plan\n- todo\n\n### Tomorrow\n- [ ] old task\n"
    result = overwrite_section(text, "Tomorrow", "- [ ] new task")
    assert "- [ ] new task" in result
    assert "- [ ] old task" not in result
    assert "### Plan" in result


def test_overwrite_section_not_found_appends():
    text = "### Plan\n- todo\n"
    result = overwrite_section(text, "Asana", "- new asana task")
    assert "### Asana" in result
    assert "- new asana task" in result
    assert "### Plan" in result


def test_write_log_creates_from_template_when_file_missing(tmp_path):
    template = tmp_path / "template.md"
    template.write_text(SAMPLE_LOG)
    log_path = tmp_path / "2026-04-10.md"

    assert not log_path.exists()
    write_log(log_path, str(template), {"Slack": "- msg1", "Asana": "- task1"})

    content = log_path.read_text()
    assert "- msg1" in content
    assert "- task1" in content
    assert "### Plan" in content  # template structure preserved


def test_write_log_overwrites_sections_in_existing_file(tmp_path):
    log_path = tmp_path / "2026-04-10.md"
    log_path.write_text(SAMPLE_LOG)
    template = tmp_path / "template.md"
    template.write_text(SAMPLE_LOG)

    write_log(log_path, str(template), {"Slack": "- fresh pull"})

    content = log_path.read_text()
    assert "- fresh pull" in content
    assert "- old slack content" not in content
    assert "- old asana content" in content  # Asana section untouched


def test_write_log_reruns_are_safe(tmp_path):
    log_path = tmp_path / "2026-04-10.md"
    log_path.write_text(SAMPLE_LOG)
    template = tmp_path / "template.md"
    template.write_text(SAMPLE_LOG)

    write_log(log_path, str(template), {"Slack": "- first pull"})
    write_log(log_path, str(template), {"Slack": "- second pull"})

    content = log_path.read_text()
    assert "- second pull" in content
    assert "- first pull" not in content
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/pontz/Projects/daily-record
pytest tests/test_log_writer.py -v
```

Expected: `ModuleNotFoundError: No module named 'log_writer'`

- [ ] **Step 3: Create `log_writer.py`**

```python
import re
from pathlib import Path
from datetime import date


def get_log_path(log_dir: str, target_date: date = None) -> Path:
    if target_date is None:
        target_date = date.today()
    return Path(log_dir) / f"{target_date.isoformat()}.md"


def overwrite_section(text: str, section_name: str, new_content: str) -> str:
    """Replace content under a ### section header, preserving all other sections.

    Finds '### section_name' and replaces everything under it until the next
    '### ' header or end of file. If section not found, appends it.
    """
    pattern = rf'(### {re.escape(section_name)}\n)(.*?)(?=\n### |\Z)'
    replacement = f'### {section_name}\n{new_content}\n'
    result = re.sub(pattern, replacement, text, flags=re.DOTALL)
    if result == text:
        # Section not found — append it
        return text.rstrip('\n') + f'\n\n### {section_name}\n{new_content}\n'
    return result


def write_log(log_path: Path, template_path: str, sections: dict) -> None:
    """Write sections to log file.

    If the file exists, overwrites the named sections and leaves all others untouched.
    If the file does not exist, creates it from the template then writes sections.

    Args:
        log_path: Path to the daily log file (e.g. .../2026-04-10.md)
        template_path: Path to daily-notes-template.md
        sections: dict mapping section_name -> content string
    """
    if log_path.exists():
        text = log_path.read_text()
    else:
        text = Path(template_path).read_text()

    for section_name, content in sections.items():
        text = overwrite_section(text, section_name, content)

    log_path.write_text(text)
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
cd /Users/pontz/Projects/daily-record
pytest tests/test_log_writer.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add log_writer.py tests/test_log_writer.py
git commit -m "feat: add log_writer module with section overwrite"
```

---

### Task 3: Prompts

**Files:**
- Create: `prompts/slack_summarize.md`
- Create: `prompts/asana_filter.md`

- [ ] **Step 1: Create `prompts/slack_summarize.md`**

```markdown
You are summarizing a day's worth of Slack activity for a personal career log.

The input is raw Slack message data including messages sent and threads participated in.

Your job:
- Output a concise bullet list of meaningful Slack activity
- Keep each bullet to one line
- Preserve: decisions made, questions raised, work discussed, notable context given or received, cross-team coordination
- Drop: short acknowledgements ("sounds good", "thanks", "ok"), emoji-only messages, messages with no work content
- Group related messages into a single bullet when they're part of the same thread or topic
- Use plain, factual language — no interpretation or coaching

Format:
- [channel or DM context] summary of what happened

Example output:
- [#product] Gave feedback on student lock feature prototype — flagged three-state model as blocking issue
- [DM: Sarah] Confirmed timeline for Gateway account split
- [#eng] Asked about deploy window for help article updates

If there is no meaningful activity, output exactly: _No notable Slack activity today_
```

- [ ] **Step 2: Create `prompts/asana_filter.md`**

```markdown
You are filtering and summarizing a day's worth of Asana activity for a personal career log.

The input is raw Asana data: completed tasks and comments made today.

Your job:
- Output a concise bullet list of meaningful work completed
- Keep each bullet to one line
- Preserve: tasks that represent real work, comments that capture decisions or context
- Drop: trivially small tasks (e.g. "replied to email", "reviewed notification"), admin noise, duplicate entries
- Group subtasks under a parent if the parent task is also present
- Missing an item is acceptable — the goal is signal, not completeness

Format:
- [project name] task or activity description

Example output:
- [Help Articles] Published Student Kiosk Feature help article
- [Feature QA] Completed QA pass on family lock prototype — filed 3 issues
- [Gateway] Left comment on school split request with recommended approach

If there is no meaningful activity, output exactly: _No notable Asana activity today_
```

- [ ] **Step 3: Commit**

```bash
git add prompts/
git commit -m "feat: add slack and asana summarization prompts"
```

---

### Task 4: Summarizer Module

**Files:**
- Create: `summarizer.py`
- Create: `tests/test_summarizer.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_summarizer.py`:

```python
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from summarizer import summarize


def test_summarize_returns_claude_response(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Summarize this data:")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="- summarized bullet")]

    with patch("summarizer.anthropic.Anthropic") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.messages.create.return_value = mock_response

        result = summarize("test-api-key", str(prompt_file), "raw data here")

    assert result == "- summarized bullet"


def test_summarize_includes_prompt_and_data_in_call(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("My prompt text")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="output")]

    with patch("summarizer.anthropic.Anthropic") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.messages.create.return_value = mock_response

        summarize("key", str(prompt_file), "my raw data")

    call_kwargs = mock_client.messages.create.call_args[1]
    message_content = call_kwargs["messages"][0]["content"]
    assert "My prompt text" in message_content
    assert "my raw data" in message_content


def test_summarize_uses_haiku_model(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("prompt")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="output")]

    with patch("summarizer.anthropic.Anthropic") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.messages.create.return_value = mock_response

        summarize("key", str(prompt_file), "data")

    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["model"] == "claude-haiku-4-5-20251001"


def test_summarize_passes_api_key(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("prompt")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="output")]

    with patch("summarizer.anthropic.Anthropic") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.messages.create.return_value = mock_response

        summarize("my-secret-key", str(prompt_file), "data")

    mock_class.assert_called_once_with(api_key="my-secret-key")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_summarizer.py -v
```

Expected: `ModuleNotFoundError: No module named 'summarizer'`

- [ ] **Step 3: Create `summarizer.py`**

```python
from pathlib import Path
import anthropic


def summarize(api_key: str, prompt_path: str, data: str) -> str:
    """Summarize raw data using Claude API.

    Loads the prompt from a file, combines it with the raw data, and
    returns Claude's response as a string.

    Args:
        api_key: Anthropic API key
        prompt_path: Path to the prompt markdown file
        data: Raw data string to summarize
    """
    prompt = Path(prompt_path).read_text()
    client = anthropic.Anthropic(api_key=api_key)

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": f"{prompt}\n\n---\n\n{data}"
            }
        ]
    )
    return message.content[0].text
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
pytest tests/test_summarizer.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add summarizer.py tests/test_summarizer.py
git commit -m "feat: add summarizer module using claude haiku"
```

---

### Task 5: Slack Client Module

**Files:**
- Create: `slack_client.py`
- Create: `tests/test_slack_client.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_slack_client.py`:

```python
import pytest
from unittest.mock import MagicMock, patch, call
from slack_client import fetch_today_slack, _today_oldest_ts, format_for_prompt


def make_slack_response(**kwargs):
    mock = MagicMock()
    for k, v in kwargs.items():
        mock.__getitem__ = lambda self, key, d=kwargs: d[key]
    mock.get = lambda key, default=None: kwargs.get(key, default)
    return kwargs  # slack-sdk returns plain dicts


def test_today_oldest_ts_returns_float_string():
    ts = _today_oldest_ts()
    assert isinstance(ts, str)
    assert float(ts) > 0


def test_fetch_returns_channels_and_dms_keys():
    with patch("slack_client.WebClient") as mock_wc_class:
        mock_client = MagicMock()
        mock_wc_class.return_value = mock_client
        mock_client.conversations_history.return_value = {"messages": []}
        mock_client.conversations_list.return_value = {"channels": []}

        result = fetch_today_slack("token", "U123", ["C001"])

    assert "channels" in result
    assert "dms" in result


def test_user_messages_included():
    with patch("slack_client.WebClient") as mock_wc_class:
        mock_client = MagicMock()
        mock_wc_class.return_value = mock_client
        mock_client.conversations_history.return_value = {
            "messages": [
                {"user": "U123", "text": "hello", "ts": "1000.0", "reply_count": 0}
            ]
        }
        mock_client.conversations_list.return_value = {"channels": []}

        result = fetch_today_slack("token", "U123", ["C001"])

    assert len(result["channels"]) == 1
    assert result["channels"][0]["text"] == "hello"


def test_other_users_messages_excluded():
    with patch("slack_client.WebClient") as mock_wc_class:
        mock_client = MagicMock()
        mock_wc_class.return_value = mock_client
        mock_client.conversations_history.return_value = {
            "messages": [
                {"user": "U999", "text": "someone else", "ts": "1000.0", "reply_count": 0}
            ]
        }
        mock_client.conversations_list.return_value = {"channels": []}

        result = fetch_today_slack("token", "U123", ["C001"])

    assert len(result["channels"]) == 0


def test_thread_with_user_reply_included():
    with patch("slack_client.WebClient") as mock_wc_class:
        mock_client = MagicMock()
        mock_wc_class.return_value = mock_client
        # Parent message from someone else, but user replied in thread
        mock_client.conversations_history.return_value = {
            "messages": [
                {"user": "U999", "text": "parent", "ts": "1000.0", "reply_count": 2}
            ]
        }
        mock_client.conversations_replies.return_value = {
            "messages": [
                {"user": "U999", "text": "parent", "ts": "1000.0"},
                {"user": "U123", "text": "my reply", "ts": "1001.0"},
            ]
        }
        mock_client.conversations_list.return_value = {"channels": []}

        result = fetch_today_slack("token", "U123", ["C001"])

    assert len(result["channels"]) == 1


def test_format_for_prompt_produces_string():
    data = {
        "channels": [
            {"channel_id": "C001", "text": "hello", "thread": None}
        ],
        "dms": []
    }
    result = format_for_prompt(data)
    assert isinstance(result, str)
    assert "hello" in result


def test_format_for_prompt_empty_returns_no_activity():
    result = format_for_prompt({"channels": [], "dms": []})
    assert result == "No Slack activity today."
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_slack_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'slack_client'`

- [ ] **Step 3: Create `slack_client.py`**

```python
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
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
pytest tests/test_slack_client.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add slack_client.py tests/test_slack_client.py
git commit -m "feat: add slack_client module for channel and DM fetching"
```

---

### Task 6: Asana Client Module

**Files:**
- Create: `asana_client.py`
- Create: `tests/test_asana_client.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_asana_client.py`:

```python
import pytest
from unittest.mock import patch, MagicMock, call
from asana_client import fetch_today_asana, format_for_prompt


def mock_get(url, **kwargs):
    """Helper: returns a mock requests.Response."""
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.status_code = 200
    if "users/me" in url:
        mock.json.return_value = {"data": {"gid": "U1"}}
    elif "stories" in url:
        mock.json.return_value = {"data": []}
    elif "tasks" in url:
        mock.json.return_value = {"data": []}
    else:
        mock.json.return_value = {"data": []}
    return mock


def test_fetch_returns_completed_tasks_and_comments_keys():
    with patch("asana_client.requests.get", side_effect=mock_get):
        result = fetch_today_asana("pat", "WS1")
    assert "completed_tasks" in result
    assert "comments" in result


def test_completed_tasks_filters_to_completed_only():
    def side_effect(url, **kwargs):
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.status_code = 200
        if "users/me" in url:
            mock.json.return_value = {"data": {"gid": "U1"}}
        elif "stories" in url:
            mock.json.return_value = {"data": []}
        else:
            mock.json.return_value = {
                "data": [
                    {"gid": "T1", "name": "Done task", "completed": True, "completed_at": "2026-04-10T15:00:00Z"},
                    {"gid": "T2", "name": "Not done", "completed": False},
                ]
            }
        return mock

    with patch("asana_client.requests.get", side_effect=side_effect):
        result = fetch_today_asana("pat", "WS1")

    names = [t["name"] for t in result["completed_tasks"]]
    assert "Done task" in names
    assert "Not done" not in names


def test_comments_filters_to_user_and_today():
    today = "2026-04-10"

    def side_effect(url, **kwargs):
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.status_code = 200
        if "users/me" in url:
            mock.json.return_value = {"data": {"gid": "U1"}}
        elif "stories" in url:
            mock.json.return_value = {
                "data": [
                    {"type": "comment", "created_by": {"gid": "U1"}, "created_at": f"{today}T10:00:00Z", "text": "my comment"},
                    {"type": "comment", "created_by": {"gid": "U999"}, "created_at": f"{today}T11:00:00Z", "text": "other"},
                    {"type": "system", "created_by": {"gid": "U1"}, "created_at": f"{today}T12:00:00Z", "text": "moved task"},
                ]
            }
        else:
            mock.json.return_value = {"data": [{"gid": "T1", "name": "Some task"}]}
        return mock

    with patch("asana_client.requests.get", side_effect=side_effect):
        with patch("asana_client.date") as mock_date:
            mock_date.today.return_value.isoformat.return_value = today
            result = fetch_today_asana("pat", "WS1")

    comment_texts = [c["comment"] for c in result["comments"]]
    assert "my comment" in comment_texts
    assert "other" not in comment_texts
    assert "moved task" not in comment_texts


def test_format_for_prompt_includes_task_names():
    data = {
        "completed_tasks": [{"name": "Finished the thing", "gid": "T1"}],
        "comments": [{"task": "Some task", "comment": "left a note"}]
    }
    result = format_for_prompt(data)
    assert "Finished the thing" in result
    assert "left a note" in result


def test_format_for_prompt_empty_returns_no_activity():
    result = format_for_prompt({"completed_tasks": [], "comments": []})
    assert result == "No Asana activity today."
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_asana_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'asana_client'`

- [ ] **Step 3: Create `asana_client.py`**

```python
import requests
from datetime import date

ASANA_BASE = "https://app.asana.com/api/1.0"


def _headers(pat: str) -> dict:
    return {"Authorization": f"Bearer {pat}"}


def _fetch_completed_tasks(pat: str, workspace_gid: str, today: str) -> list:
    """Fetch tasks assigned to me and completed today."""
    params = {
        "workspace": workspace_gid,
        "assignee": "me",
        "completed_since": f"{today}T00:00:00.000Z",
        "opt_fields": "name,completed,completed_at,gid",
        "limit": 100,
    }
    resp = requests.get(f"{ASANA_BASE}/tasks", headers=_headers(pat), params=params)
    resp.raise_for_status()
    tasks = resp.json().get("data", [])
    return [t for t in tasks if t.get("completed")]


def _get_my_gid(pat: str) -> str:
    """Fetch the authenticated user's GID."""
    resp = requests.get(f"{ASANA_BASE}/users/me", headers=_headers(pat))
    resp.raise_for_status()
    return resp.json()["data"]["gid"]


def _fetch_my_comments(pat: str, workspace_gid: str, today: str) -> list:
    """Fetch comments I made today on tasks modified today in my workspace."""
    user_gid = _get_my_gid(pat)

    # Fetch tasks assigned to me that were modified today (bounded set for story fetching)
    params = {
        "workspace": workspace_gid,
        "assignee": "me",
        "modified_since": f"{today}T00:00:00.000Z",
        "opt_fields": "name,gid",
        "limit": 50,
    }
    resp = requests.get(f"{ASANA_BASE}/tasks", headers=_headers(pat), params=params)
    resp.raise_for_status()
    tasks = resp.json().get("data", [])

    comments = []
    for task in tasks:
        stories_resp = requests.get(
            f"{ASANA_BASE}/tasks/{task['gid']}/stories",
            headers=_headers(pat),
            params={"opt_fields": "created_at,created_by.gid,type,text,resource_subtype"}
        )
        if stories_resp.status_code != 200:
            continue
        for story in stories_resp.json().get("data", []):
            if (
                story.get("type") == "comment"
                and story.get("created_by", {}).get("gid") == user_gid
                and story.get("created_at", "").startswith(today)
            ):
                comments.append({"task": task["name"], "comment": story.get("text", "")})

    return comments


def fetch_today_asana(pat: str, workspace_gid: str) -> dict:
    """Fetch today's Asana activity.

    Returns:
        {
            "completed_tasks": [{"name": str, "gid": str, ...}, ...],
            "comments": [{"task": str, "comment": str}, ...]
        }
    """
    today = date.today().isoformat()
    return {
        "completed_tasks": _fetch_completed_tasks(pat, workspace_gid, today),
        "comments": _fetch_my_comments(pat, workspace_gid, today),
    }


def format_for_prompt(data: dict) -> str:
    """Format Asana data as a readable string for the Claude prompt."""
    if not data["completed_tasks"] and not data["comments"]:
        return "No Asana activity today."

    lines = []

    if data["completed_tasks"]:
        lines.append("## Completed Tasks")
        for task in data["completed_tasks"]:
            lines.append(f"- {task['name']}")

    if data["comments"]:
        lines.append("\n## Comments Made")
        for item in data["comments"]:
            lines.append(f"- [{item['task']}] {item['comment']}")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
pytest tests/test_asana_client.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add asana_client.py tests/test_asana_client.py
git commit -m "feat: add asana_client module for completed tasks and comments"
```

---

### Task 7: Orchestrator (pull_daily.py)

**Files:**
- Create: `pull_daily.py`
- Create: `tests/test_pull_daily.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_pull_daily.py`:

```python
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
                with patch("pull_daily.format_for_prompt", return_value="raw"):
                    import pull_daily
                    pull_daily.run(config, dry_run=True)

    log_files = list(log_dir.iterdir())
    assert len(log_files) == 0
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_pull_daily.py -v
```

Expected: `ModuleNotFoundError: No module named 'pull_daily'`

- [ ] **Step 3: Create `pull_daily.py`**

```python
#!/usr/bin/env python3
"""Daily Record — pull today's Slack and Asana activity into the career log."""

import argparse
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from asana_client import fetch_today_asana, format_for_prompt as asana_format
from log_writer import get_log_path, write_log
from slack_client import fetch_today_slack, format_for_prompt as slack_format
from summarizer import summarize

REQUIRED_KEYS = [
    "ANTHROPIC_API_KEY",
    "SLACK_BOT_TOKEN",
    "SLACK_USER_ID",
    "SLACK_CHANNELS",
    "ASANA_PAT",
    "ASANA_WORKSPACE_GID",
    "LOG_DIR",
    "TEMPLATE_PATH",
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
        "asana_pat": os.environ["ASANA_PAT"],
        "asana_workspace_gid": os.environ["ASANA_WORKSPACE_GID"],
        "log_dir": os.environ["LOG_DIR"],
        "template_path": os.environ["TEMPLATE_PATH"],
        "obsidian_vault_path": os.environ.get("OBSIDIAN_VAULT_PATH", ""),
    }


def _prompts_dir() -> Path:
    return Path(__file__).parent / "prompts"


def run(config: dict, dry_run: bool = False) -> dict:
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
    try:
        slack_data = fetch_today_slack(
            config["slack_bot_token"],
            config["slack_user_id"],
            config["slack_channels"],
        )
        slack_count = len(slack_data["channels"]) + len(slack_data["dms"])
        raw = slack_format(slack_data)
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
        url = f"obsidian://open?path={relative}"
        subprocess.run(["open", url], check=False)
    except (ValueError, Exception):
        pass  # Not in vault or open failed — non-fatal


def main():
    parser = argparse.ArgumentParser(description="Pull today's Slack and Asana into the daily log.")
    parser.add_argument("--dry-run", action="store_true", help="Print output without writing to log")
    args = parser.parse_args()

    config = load_config()
    run(config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
pytest tests/test_pull_daily.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
pytest -v
```

Expected: all tests across all modules pass.

- [ ] **Step 6: Commit**

```bash
git add pull_daily.py tests/test_pull_daily.py
git commit -m "feat: add pull_daily orchestrator with per-source error isolation"
```

---

### Task 8: Shell Command

**Files:**
- Create: `/usr/local/bin/pull-daily` (executable wrapper script)

- [ ] **Step 1: Create the wrapper script**

```bash
cat > /usr/local/bin/pull-daily << 'EOF'
#!/bin/bash
python3 /Users/pontz/Projects/daily-record/pull_daily.py "$@"
EOF
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x /usr/local/bin/pull-daily
```

- [ ] **Step 3: Verify it's on PATH and runs**

```bash
which pull-daily
pull-daily --help
```

Expected: `/usr/local/bin/pull-daily` and the argparse help message printed.

- [ ] **Step 4: Commit note** (no git commit needed — this file is outside the repo, but note it in README)

Open `README.md` and create it:

```markdown
# Daily Record

Pulls today's Slack and Asana activity, summarizes it via Claude API, and writes it to the daily career coaching log.

## Setup

1. Copy `.env.example` to `.env` and fill in all values
2. Install dependencies: `pip install -r requirements.txt`
3. Create the shell command: `sudo bash -c 'echo "#!/bin/bash\npython3 /Users/pontz/Projects/daily-record/pull_daily.py \"\$@\"" > /usr/local/bin/pull-daily && chmod +x /usr/local/bin/pull-daily'`

## Usage

```bash
pull-daily           # fetch, summarize, write to today's log
pull-daily --dry-run # preview output without writing
```

## Slack App Setup

1. Go to api.slack.com/apps → Create New App
2. Add OAuth scopes: `channels:history`, `im:history`, `users:read`
3. Install app to workspace
4. Copy Bot User OAuth Token to `SLACK_BOT_TOKEN` in `.env`
5. Find your User ID in Slack: Profile → ⋮ → Copy member ID → `SLACK_USER_ID`
6. Find channel IDs: right-click channel → View channel details → copy ID at bottom → `SLACK_CHANNELS`

## Asana Setup

1. Go to app.asana.com/0/my-apps → Create new token
2. Copy to `ASANA_PAT` in `.env`
3. Find workspace GID: go to any task URL, the number after `/0/` is the workspace GID → `ASANA_WORKSPACE_GID`

## Future: Cron

```bash
45 17 * * 1-5 /usr/local/bin/pull-daily
```
```

```bash
git add README.md
git commit -m "docs: add README with setup instructions"
```

---

### Task 9: Claude Code Slash Command

**Files:**
- Create: `/Users/pontz/career-coach/.claude/commands/pull-daily.md`

- [ ] **Step 1: Ensure the commands directory exists**

```bash
mkdir -p /Users/pontz/career-coach/.claude/commands
```

- [ ] **Step 2: Create `pull-daily.md`**

```markdown
Run the daily record pull to fetch today's Slack messages and Asana tasks, summarize them, and write to today's log.

Execute the following:

```bash
pull-daily
```

After the command completes, show me the output above and let me know what was pulled. If there were any warnings or failures, flag them clearly.
```

- [ ] **Step 3: Verify the slash command is discoverable**

Open a Claude Code session in `/Users/pontz/career-coach` and type `/pull-daily` — it should appear as an autocomplete option.

- [ ] **Step 4: Commit the slash command to the career-coach repo**

```bash
cd /Users/pontz/career-coach
git add .claude/commands/pull-daily.md
git commit -m "feat: add /pull-daily slash command"
```

---

### Task 10: Update Daily Log Template

**Files:**
- Modify: `/Users/pontz/career-coach/logs/daily-notes-template.md`

- [ ] **Step 1: Read current template**

```bash
cat /Users/pontz/career-coach/logs/daily-notes-template.md
```

Expected current content:
```
### Plan
- [ ] 

### Done
- 

### Slack
- 

### Not Done / Carried
- 

### Tomorrow
- [ ] 
```

- [ ] **Step 2: Update template to add `### Asana` section**

Edit `/Users/pontz/career-coach/logs/daily-notes-template.md`:

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

- [ ] **Step 3: Verify the existing daily logs still look right**

```bash
head -20 /Users/pontz/career-coach/logs/2026-04-10.md
```

Existing logs don't need the Asana section added retroactively — the script will append it when it next runs against a file that doesn't already have it (handled by `overwrite_section`'s append-if-not-found behavior).

- [ ] **Step 4: Commit the template update**

```bash
cd /Users/pontz/career-coach
git add logs/daily-notes-template.md
git commit -m "feat: add Asana section to daily log template"
```

---

## End-to-End Smoke Test

After all tasks complete, run a dry-run to verify the full pipeline works before using real credentials:

```bash
# With real credentials in .env:
pull-daily --dry-run
```

Expected output:
```
--- DRY RUN: would write to /Users/pontz/career-coach/logs/2026-04-10.md ---

### Slack
- [#channel] ...

### Asana
- [Project] ...
```

If you see `⚠` warnings, check the specific API credentials named in the error.
