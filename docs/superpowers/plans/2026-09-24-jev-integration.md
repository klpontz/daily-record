# TypeSafe/Jev Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace three hand-done or free-text-LLM judgment calls in the daily-record → career-coach pipeline with TypeSafe/Jev typed judgments (Noul/Choice), keeping generation and formatting in code.

**Architecture:** Three independent subsystems sharing one new config key and one new dependency. (1) `asana_filter.py` replaces the Asana `summarize()` call with a Noul filter + code formatter. (2) `classify_log.py` is a new standalone CLI that reads a career-coach log file and prints a Choice-classified Breakdown of `### Done` only. (3) `carry_forward.py` is a new standalone CLI that matches today's `### Tomorrow` items against a small git-tracked state file via Choice, and prints a days-open report. Every Noul/Choice question in every subsystem carries its own item's text directly in a structured `instructions` object — confirmed live against the real API (see spec's Verification section) rather than relying on the model resolving an array index into a separate `state` blob.

**Tech Stack:** Python 3, `typesafe-sdk` (new dependency), `python-dotenv` (already a dependency), pytest + unittest.mock (existing pattern).

**Spec:** `docs/superpowers/specs/2026-09-24-jev-integration-design.md`

**Revision note:** This plan replaces the 2026-09-24 first draft after an adversarial review found a plan-breaking test, a date bug, and a data-loss bug in the carry-forward tracker, plus a design flaw in the Breakdown scope. Every fix below traces to a specific review finding; where a finding changed a function's shape or behavior, the change is called out in that task.

## Global Constraints

- New dependency: `typesafe-sdk`, pinned in `requirements.txt` after install (matches existing exact-pin style, e.g. `anthropic==0.49.0`). No dependency conflicts (verified: `pip install --dry-run` during review).
- New required config key: `TYPESAFE_API_KEY` (`.env.example`, `REQUIRED_KEYS`, `load_config()` → `config["typesafe_api_key"]`, **and the real `.env` file** — Task 1 adds the actual value, not just the requirement).
- **Every** `TypeSafeClient(...)` construction passes `timeout=60.0` explicitly (SDK default is 10s; too short for a batch of parallel questions per TypeSafe's own cookbook guidance).
- **Every** Noul/Choice question uses the structured-instructions shape: `Noul(instructions={"item": ..., "question": "..."})` / `Choice(instructions={"item": ..., "question": "..."}, criteria=...)`. Shared `state` is `{}` (the API requires *some* state value; `None` is rejected — confirmed live).
- Asana Noul threshold: module-level `NOISE_THRESHOLD = 0.5` in `asana_filter.py`.
- Carry-forward match confidence floor: `MATCH_CONFIDENCE_FLOOR = 0.5` — a match below this is treated as "new," not trusted.
- Carry-forward recurring-task threshold: `RECURRING_THRESHOLD = 0.5` in `carry_forward.py`.
- Breakdown confidence flag: any classified item with `confidence < 0.6` gets a `(low confidence — check by hand)` suffix — a starting point to recalibrate in Task 9's manual run, not a settled number.
- `classify_log.py` classifies `### Done` only — not Slack or Asana (see spec Part 2 for why).
- Carry-forward tracks **only** the `### Tomorrow` checklist.
- `classify_log.py` writes nothing to disk — stdout only.
- Carry-forward state defaults to `<LOG_DIR>/.carry-forward.json` (`LOG_DIR` from `.env`), git-tracked in career-coach, overridable with `--state`.
- Both new CLIs (`classify_log.py`, `carry_forward.py`) call `load_dotenv()` at the top of `main()` and wrap their API-calling logic in try/except — one clear line to stderr, exit 1, on failure. Neither CLI's `main()` should ever let a raw traceback reach the terminal.

## Review Focus

- **Empty log (no `### Done` content at all).** `classify_log.py` must print "No items to classify." and make zero API calls. Test in Task 5.
- **A blank `- [ ]` checkbox line, or an empty/unfilled `### Tomorrow` section.** `carry_forward.py` must skip blank lines when extracting (Task 9), and — separately, the more serious case — an empty `### Tomorrow` must **not** resolve every tracked open item (Task 12's resolution-guard test; this is a real bug the first draft shipped and its own test hid).
- **TypeSafe API call fails (network/auth error).** All three of `filter_items`, `classify_items`, and `match_items` must let the exception propagate to their caller; both CLIs' `main()` must catch it, print one clear line, and exit 1 — not print an empty or wrong report. Tests in Tasks 2, 6, and 10.
- **A carry-forward match is wrong or low-confidence.** `match_items` must not silently trust a low-confidence choice (Task 10's confidence-floor test), and `format_carry_report` must show every match decision so a bad one is visible, not hidden (Task 12).
- **Processing a log out of date order.** Running `carry_forward.py` against an older log after state has already been updated from a newer one must refuse to run, not silently corrupt `first_seen`/`resolved_date` (Task 13).

---

## Part 1 — Asana Jev Filter

### Task 1: TypeSafe config wiring

**Files:**
- Modify: `pull_daily.py:20-52` (REQUIRED_KEYS, load_config), `.env.example`, `requirements.txt`, `.env`
- Test: `tests/test_pull_daily.py`

**Interfaces:**
- Produces: `config["typesafe_api_key"]`, available to every later task's code.

- [ ] **Step 1: Install and pin the SDK**

```bash
source venv/bin/activate
pip install typesafe-sdk
pip freeze | grep -i typesafe-sdk >> requirements.txt
```

Edit `requirements.txt` so the new line matches the exact-pin style of the existing lines (e.g. `typesafe-sdk==0.7.1`), placed alphabetically.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_pull_daily.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_pull_daily.py::test_missing_typesafe_key_exits -v`
Expected: FAIL — `SystemExit` not raised, because `TYPESAFE_API_KEY` isn't required yet.

- [ ] **Step 4: Write minimal implementation**

In `pull_daily.py`, add `"TYPESAFE_API_KEY"` to `REQUIRED_KEYS`, and in `load_config()`'s returned dict add:

```python
        "typesafe_api_key": os.environ["TYPESAFE_API_KEY"],
```

Add to `.env.example`:

```
TYPESAFE_API_KEY=               # TypeSafe API key from console.typesafe.ai
```

**Add the real key to `.env`** (gitignored — confirmed via `.gitignore:1`). Without this, `pull-daily` will exit with "Missing required config keys" the moment this task lands, until someone notices and fixes it by hand — this was the first draft's gap.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_pull_daily.py::test_missing_typesafe_key_exits -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add pull_daily.py requirements.txt .env.example tests/test_pull_daily.py
git commit -m "feat: add TYPESAFE_API_KEY to required config"
```

(`.env` is gitignored and is not committed.)

---

### Task 2: Asana Noul filter

**Files:**
- Modify: `asana_client.py` (rename `_project_name` → `project_name`, a public helper — Task 3 imports it instead of duplicating it)
- Create: `asana_filter.py`
- Test: `tests/test_asana_filter.py`

**Interfaces:**
- Consumes: `project_name(task: dict) -> str | None` from `asana_client.py`.
- Produces: `filter_items(api_key: str, tasks: list[dict], comments: list[dict]) -> tuple[list[dict], list[dict]]`. `tasks`/`comments` are the same shapes `fetch_today_asana()` already returns. Returns the filtered subset of each, same shape, same order.

- [ ] **Step 1: Rename the private helper to public**

In `asana_client.py`, rename `_project_name` to `project_name` everywhere it's defined and called (the definition and its two call sites). This is a pure rename — run the existing suite to confirm nothing else references the old name:

Run: `pytest tests/test_asana_client.py -v`
Expected: PASS (no test references `_project_name` by name — confirmed by grep during review).

Commit this rename on its own:

```bash
git add asana_client.py
git commit -m "refactor: make project_name a public helper"
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_asana_filter.py`:

```python
from unittest.mock import MagicMock, patch
from asana_filter import filter_items


def test_filter_items_short_circuits_on_empty():
    with patch("asana_filter.TypeSafeClient") as mock_class:
        result = filter_items("key", [], [])

    assert result == ([], [])
    mock_class.assert_not_called()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_asana_filter.py::test_filter_items_short_circuits_on_empty -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'asana_filter'`

- [ ] **Step 4: Write the implementation**

Create `asana_filter.py`:

```python
from asana_client import project_name
from typesafe_sdk import Noul, TypeSafeClient

NOISE_THRESHOLD = 0.5


def filter_items(api_key: str, tasks: list[dict], comments: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep only substantive tasks/comments, judged by one Jev Noul per item.

    Every question carries its own item's data directly in a structured
    `instructions` object — no array-index lookup into a shared state blob.
    One API call total. Short-circuits with no API call when both lists
    are empty. Prints every dropped item with its score, so a day where
    everything scored low doesn't look identical to a day with no activity.
    """
    if not tasks and not comments:
        return [], []

    questions = {}
    for i, t in enumerate(tasks):
        questions[f"task_{i}"] = Noul(
            instructions={
                "item": {"name": t["name"], "project": project_name(t)},
                "question": (
                    "Is `item` substantive work worth surfacing in a career log "
                    "— not a trivially small task or routine admin note?"
                ),
            },
        )
    for i, c in enumerate(comments):
        questions[f"comment_{i}"] = Noul(
            instructions={
                "item": {"task": c["task"], "project": c.get("project"), "comment": c["comment"]},
                "question": (
                    "Is `item` a comment that captures a real decision or meaningful "
                    "context — not a routine or content-free note?"
                ),
            },
        )

    client = TypeSafeClient(api_key=api_key, model="jev-latest", timeout=60.0)
    result = client.system_one({}, questions)

    kept_tasks = []
    for i, t in enumerate(tasks):
        score = result.nouls[f"task_{i}"].noul
        if score > NOISE_THRESHOLD:
            kept_tasks.append(t)
        else:
            print(f"dropped (noul={score:.2f}): {t['name']}")

    kept_comments = []
    for i, c in enumerate(comments):
        score = result.nouls[f"comment_{i}"].noul
        if score > NOISE_THRESHOLD:
            kept_comments.append(c)
        else:
            print(f"dropped (noul={score:.2f}): {c['comment'][:60]}")

    return kept_tasks, kept_comments
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_asana_filter.py -v`
Expected: PASS.

- [ ] **Step 6: Add the remaining coverage**

This implementation also needs to prove: it threshold-filters correctly, it embeds each item's real text in its own question (not a shared array a future edit could misalign — the exact gap the review found no test covered), and an API failure propagates rather than getting swallowed. Add to `tests/test_asana_filter.py`:

```python
def _mock_result(nouls: dict[str, float]):
    result = MagicMock()
    result.nouls = {k: MagicMock(noul=v) for k, v in nouls.items()}
    return result


def test_filter_items_keeps_above_and_drops_at_or_below_threshold():
    tasks = [{"name": "Real work", "gid": "T1"}, {"name": "Replied to email", "gid": "T2"}]
    comments = [{"task": "Real work", "project": "P", "comment": "Left a decision"}]

    with patch("asana_filter.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_result(
            {"task_0": 0.9, "task_1": 0.5, "comment_0": 0.8}
        )

        kept_tasks, kept_comments = filter_items("key", tasks, comments)

    assert kept_tasks == [{"name": "Real work", "gid": "T1"}]
    assert kept_comments == comments


def test_filter_items_embeds_each_item_text_in_its_own_question():
    tasks = [{"name": "Task A", "gid": "T1"}, {"name": "Task B", "gid": "T2"}]

    with patch("asana_filter.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_result({"task_0": 0.9, "task_1": 0.9})

        filter_items("key", tasks, [])

    call_args = mock_client.system_one.call_args
    questions = call_args.args[1]
    assert questions["task_0"].instructions["item"]["name"] == "Task A"
    assert questions["task_1"].instructions["item"]["name"] == "Task B"


def test_filter_items_raises_on_api_failure():
    with patch("asana_filter.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.side_effect = Exception("TypeSafe down")

        with pytest.raises(Exception, match="TypeSafe down"):
            filter_items("key", [{"name": "A", "gid": "T1"}], [])
```

Add `import pytest` to the top of `tests/test_asana_filter.py`.

Run: `pytest tests/test_asana_filter.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add asana_filter.py tests/test_asana_filter.py
git commit -m "feat: filter Asana items via Jev Noul instead of a free-text prompt"
```

---

### Task 3: Asana code-side formatter

**Files:**
- Modify: `asana_filter.py`
- Test: `tests/test_asana_filter.py`

**Interfaces:**
- Consumes: `filter_items`'s return shape from Task 2.
- Produces: `format_filtered(tasks: list[dict], comments: list[dict]) -> str`.

**Design decision to confirm before writing this task** (see spec Part 1): pure code formatting is the default. Task bullets read `"marked complete."` instead of the old prompt's paraphrased verb. If that reads too flat once you see Task 4's real dry-run output, the fallback is a second, filter-only Claude formatting call — not built here.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_asana_filter.py`:

```python
from asana_filter import format_filtered


def test_format_filtered_renders_tasks_and_comments():
    tasks = [{"name": "Student Kiosk Feature", "memberships": [{"project": {"name": "Help articles"}}]}]
    comments = [{"task": "Family lock prototype", "project": "Feature QA", "comment": "left a decision"}]

    result = format_filtered(tasks, comments)

    assert "- **Help articles** — Student Kiosk Feature: marked complete." in result
    assert "- **Feature QA** — Family lock prototype: left a decision" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_asana_filter.py::test_format_filtered_renders_tasks_and_comments -v`
Expected: FAIL — `ImportError: cannot import name 'format_filtered'`

- [ ] **Step 3: Write the implementation**

Add to `asana_filter.py`:

```python
import re


def format_filtered(tasks: list[dict], comments: list[dict]) -> str:
    if not tasks and not comments:
        return "_No notable Asana activity today_"

    lines = []
    for t in tasks:
        project = project_name(t)
        prefix = f"**{project}** — " if project else ""
        lines.append(f"- {prefix}{t['name']}: marked complete.")
    for c in comments:
        project = c.get("project")
        prefix = f"**{project}** — " if project else ""
        lines.append(f"- {prefix}{c['task']}: {_clean_comment(c['comment'])}")
    return "\n".join(lines)


def _clean_comment(text: str, max_len: int = 200) -> str:
    """Collapse whitespace/newlines and truncate — raw Asana story text can
    span paragraphs or contain its own '- ' list, which would corrupt the
    log's bullet structure and confuse classify_log.py's section parser."""
    collapsed = " ".join(text.split())
    if len(collapsed) > max_len:
        return collapsed[:max_len].rstrip() + "…"
    return collapsed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_asana_filter.py -v`
Expected: PASS.

- [ ] **Step 5: Add the remaining coverage**

Add to `tests/test_asana_filter.py`:

```python
def test_format_filtered_falls_back_when_both_empty():
    assert format_filtered([], []) == "_No notable Asana activity today_"


def test_format_filtered_collapses_and_truncates_comment_text():
    comments = [{"task": "T", "project": "P", "comment": "line one\n- inner bullet\nline three " + "x" * 250}]

    result = format_filtered([], comments)

    assert "\n" not in result.splitlines()[0]  # the whole bullet is one line
    assert result.count("- ") == 1  # the inner "- inner bullet" did not become its own bullet
    assert len(result) < 250
```

Run: `pytest tests/test_asana_filter.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add asana_filter.py tests/test_asana_filter.py
git commit -m "feat: format filtered Asana items in code, sanitized, no LLM call"
```

---

### Task 4: Wire the filter into `pull_daily.py`

**Files:**
- Modify: `pull_daily.py:14-18` (imports), `pull_daily.py:116-136` (Asana branch in `run()`)
- Delete: `prompts/asana_filter.md`
- Modify: `README.md` (remove the line documenting `asana_filter.md`, which the first draft's plan forgot)
- Test: `tests/test_pull_daily.py`

**Interfaces:**
- Consumes: `filter_items` and `format_filtered` from Task 2/3.

- [ ] **Step 1: Update the three config dicts that need the new key**

In `tests/test_pull_daily.py`, add `"typesafe_api_key": "ts-key"` to the `config` dict in `test_both_sources_fail_writes_nothing`, `test_dry_run_does_not_write_log`, and `test_run_passes_directory_to_slack_format` (three occurrences — verified by reading the file during review; the other three test functions either need no change, as shown in the next steps, or are replaced wholesale with the key already included).

- [ ] **Step 2: Replace the Asana mocking in the Slack-failure test**

`test_slack_api_failure_skips_slack_section_but_writes_asana` currently patches `pull_daily.summarize` and `pull_daily.asana_format` for its Asana half — neither is called after this task. Replace its body:

```python
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
```

- [ ] **Step 3: Split the failure-marker test in two**

`test_claude_failure_writes_failure_marker` currently exercises both sources failing through the same `summarize` patch — after this task they fail independently with different markers. Delete it and add two tests:

```python
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
```

- [ ] **Step 4: Wrap the two remaining tests' Asana path**

In `test_dry_run_does_not_write_log` and `test_run_passes_directory_to_slack_format`, add `with patch("pull_daily.filter_items", return_value=([], [])):` around the existing `with` blocks (both already pass empty `completed_tasks`/`comments`, so the real `filter_items` from Task 2 would short-circuit safely even unmocked — this patch is explicit isolation, not strictly required, but keeps every test from touching real Jev-calling code paths even indirectly).

- [ ] **Step 5: Run the suite to verify the expected failures**

Run: `pytest tests/test_pull_daily.py -v`
Expected: `test_slack_api_failure_skips_slack_section_but_writes_asana` and `test_asana_filter_failure_writes_failure_marker` FAIL (`pull_daily.filter_items` doesn't exist as an importable name in `pull_daily` yet). `test_slack_claude_failure_writes_failure_marker` already PASSES (Slack path is unchanged). The two Step 4 tests FAIL too, for the same reason (`patch("pull_daily.filter_items")` has nothing to patch) — four failures total, not two.

- [ ] **Step 6: Write the implementation**

In `pull_daily.py`, change the import block:

```python
from asana_client import fetch_today_asana
from asana_filter import filter_items, format_filtered
from log_writer import get_log_path, write_log
from slack_client import fetch_today_slack, format_for_prompt as slack_format, build_web_client
from user_directory import SlackUserDirectory
from summarizer import summarize
```

(`asana_client.format_for_prompt as asana_format` is dropped — nothing in `run()` calls it after this task.)

Replace the `--- Asana ---` block in `run()`:

```python
    # --- Asana ---
    try:
        asana_data = fetch_today_asana(
            config["asana_pat"],
            config["asana_workspace_gid"],
        )
        asana_count = len(asana_data["completed_tasks"]) + len(asana_data["comments"])
        try:
            kept_tasks, kept_comments = filter_items(
                config["typesafe_api_key"],
                asana_data["completed_tasks"],
                asana_data["comments"],
            )
            asana_content = format_filtered(kept_tasks, kept_comments)
            asana_ok = True
        except Exception as e:
            print(f"⚠ Asana filtering failed: {e}")
            asana_content = "_Filtering failed — run manually_"
    except Exception as e:
        print(f"⚠ Asana pull failed: {e}")
        # asana_content stays None — section will be skipped entirely
```

Delete `prompts/asana_filter.md`. In `README.md`, remove the "Gap scripts" — no, wrong file; remove the sentence documenting `asana_filter.md`'s old role in the Asana pipeline description (search the README for "asana_filter" and delete that reference).

- [ ] **Step 7: Run the suite to verify it passes**

Run: `pytest tests/test_pull_daily.py -v`
Expected: all tests PASS.

- [ ] **Step 8: Run the whole project suite**

Run: `pytest -v`
Expected: all tests PASS.

- [ ] **Step 9: Manual dry run against real data**

```bash
python3 pull_daily.py --dry-run
```

Confirm the Asana section reads acceptably (see the design decision noted at the top of Task 3) and that any dropped items print to stdout with their score, visible above the written section.

- [ ] **Step 10: Commit**

```bash
git add pull_daily.py prompts/asana_filter.md README.md tests/test_pull_daily.py
git commit -m "feat: wire Jev Asana filter into pull_daily, retire asana_filter.md"
```

---

## Part 2 — Breakdown Classifier

### Task 5: `read_section()` in `log_writer.py`

**Files:**
- Modify: `log_writer.py`
- Test: `tests/test_log_writer.py`

**Interfaces:**
- Produces: `read_section(text: str, section_name: str) -> str` — returns the raw content under `### {section_name}` up to the next `### ` header or end of string, stripped. Returns `""` if the section isn't found, or if it's genuinely empty.

**Note on the implementation below:** this does *not* reuse `overwrite_section`'s regex. The review found that pattern (`(.*?)(?=\n### |\Z)`, a lazy match plus lookahead) mis-captures when a section has zero characters before the next header — it backtracks past the empty section and swallows the next section's content. The implementation here finds every header's position first, then slices strictly between two consecutive positions, which has no backtracking case left to get wrong.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_log_writer.py`:

```python
from log_writer import read_section


def test_read_section_extracts_middle_section():
    text = "### Plan\n- a\n\n### Done\n- b\n- c\n\n### Slack\n-\n"
    assert read_section(text, "Done") == "- b\n- c"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_log_writer.py::test_read_section_extracts_middle_section -v`
Expected: FAIL — `ImportError: cannot import name 'read_section'`

- [ ] **Step 3: Write the implementation**

Add to `log_writer.py`:

```python
def read_section(text: str, section_name: str) -> str:
    """Return the raw content under a ### section header, stripped. '' if not found or empty.

    Locates every '### ' header by position first, then slices strictly
    between two consecutive header positions — avoids the lazy-match/
    lookahead bug in overwrite_section, which mis-captures when a section
    has zero content before the next header.
    """
    headers = list(re.finditer(r'^### (.+)$', text, flags=re.MULTILINE))
    for i, match in enumerate(headers):
        if match.group(1).strip() == section_name:
            start = match.end()
            end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
            return text[start:end].strip()
    return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_log_writer.py -v`
Expected: PASS.

- [ ] **Step 5: Add the regression test for the bug this rewrite fixes**

Add to `tests/test_log_writer.py`:

```python
def test_read_section_handles_zero_content_section_without_bleeding_into_next():
    text = "### Done\n### Slack\n- x\n### Asana\n- y\n"
    assert read_section(text, "Done") == ""
    assert read_section(text, "Slack") == "- x"


def test_read_section_missing_returns_empty_string():
    assert read_section("### Plan\n- a\n", "Done") == ""
```

Run: `pytest tests/test_log_writer.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add log_writer.py tests/test_log_writer.py
git commit -m "feat: add read_section, correctly handling zero-content sections"
```

---

### Task 6: `extract_bullets()` in `classify_log.py`

**Files:**
- Create: `classify_log.py`
- Test: `tests/test_classify_log.py`

**Interfaces:**
- Consumes: `read_section` from Task 5.
- Produces: `extract_bullets(log_text: str) -> list[str]`. **Scope note:** `### Done` only — Slack and Asana are excluded (see spec Part 2 for why; the first draft included all three and would have double-counted the same work).

- [ ] **Step 1: Write the failing test**

Create `tests/test_classify_log.py`:

```python
from classify_log import extract_bullets


def test_extract_bullets_pulls_from_done_only():
    text = (
        "### Plan\n- [ ] ignored\n\n"
        "### Done\n- Fixed the thing\n- Shipped the other thing\n\n"
        "### Slack\n- #training: discussed something\n\n"
        "### Asana\n- **Project** — Task: did it.\n"
    )
    assert extract_bullets(text) == ["Fixed the thing", "Shipped the other thing"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_classify_log.py::test_extract_bullets_pulls_from_done_only -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'classify_log'`

- [ ] **Step 3: Write the implementation**

Create `classify_log.py`:

```python
from log_writer import read_section


def extract_bullets(log_text: str) -> list[str]:
    """Pull every non-empty '- ' line from the Done section."""
    section = read_section(log_text, "Done")
    bullets = []
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("- ") and line[2:].strip():
            bullets.append(line[2:].strip())
    return bullets
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_classify_log.py -v`
Expected: PASS.

- [ ] **Step 5: Add the empty-log case**

Add to `tests/test_classify_log.py`:

```python
def test_extract_bullets_empty_log_returns_empty_list():
    text = "### Done\n- \n\n### Slack\n- \n"
    assert extract_bullets(text) == []


def test_extract_bullets_missing_done_section_returns_empty_list():
    assert extract_bullets("### Plan\n- a\n") == []
```

Run: `pytest tests/test_classify_log.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add classify_log.py tests/test_classify_log.py
git commit -m "feat: extract Done bullets from a log file"
```

---

### Task 7: `classify_items()` via Choice

**Files:**
- Modify: `classify_log.py`
- Test: `tests/test_classify_log.py`

**Interfaces:**
- Produces: `classify_items(api_key: str, items: list[str]) -> list[dict]`, each dict `{"text": str, "category": str, "confidence": float}`. `category` is one of `"product_qa"`, `"ops_transferable"`, `"ops_noise"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_classify_log.py`:

```python
from unittest.mock import MagicMock, patch
from classify_log import classify_items


def test_classify_items_empty_makes_no_api_call():
    with patch("classify_log.TypeSafeClient") as mock_class:
        result = classify_items("key", [])

    assert result == []
    mock_class.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_classify_log.py::test_classify_items_empty_makes_no_api_call -v`
Expected: FAIL — `ImportError: cannot import name 'classify_items'`

- [ ] **Step 3: Write the implementation**

Add to `classify_log.py`:

```python
from typesafe_sdk import Choice, TypeSafeClient

CATEGORY_CRITERIA = {
    "product_qa": (
        "Technical or product work: debugging with a named root cause, a shipped fix, "
        "a spec or design doc, documented UX/product feedback, a SQL/data finding, "
        "AI-prototyping or eval work, a decision made directly with product leadership, "
        "or a portfolio-worthy technical finding."
    ),
    "ops_transferable": (
        "Operations or account work that is formally documented, process-improving, or "
        "transferable: coaching or managing a report with a written structure, a "
        "documented policy/process decision, an account resolution with stated reasoning, "
        "delegation with tracked follow-through."
    ),
    "ops_noise": (
        "Routine account administration, inbox/channel monitoring, task routing with no "
        "decision attached, or a brief approval with no stated reasoning."
    ),
}


def classify_items(api_key: str, items: list[str]) -> list[dict]:
    """Classify each Done bullet via one Jev Choice call, one question per item."""
    if not items:
        return []

    questions = {
        f"item_{i}": Choice(
            instructions={"item": item, "question": "Which category best describes `item`?"},
            criteria=CATEGORY_CRITERIA,
        )
        for i, item in enumerate(items)
    }
    client = TypeSafeClient(api_key=api_key, model="jev-latest", timeout=60.0)
    result = client.system_one({}, questions)

    return [
        {
            "text": item,
            "category": result.choices[f"item_{i}"].choice,
            "confidence": result.choices[f"item_{i}"].confidence,
        }
        for i, item in enumerate(items)
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_classify_log.py -v`
Expected: PASS.

- [ ] **Step 5: Add the remaining coverage**

Add to `tests/test_classify_log.py`:

```python
import pytest


def _mock_choice_result(answers: dict[str, tuple[str, float]]):
    result = MagicMock()
    result.choices = {k: MagicMock(choice=c, confidence=conf) for k, (c, conf) in answers.items()}
    return result


def test_classify_items_maps_category_and_confidence_per_item():
    items = ["Debugged the DFEO bug", "Monitored the hiring inbox"]

    with patch("classify_log.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_choice_result(
            {"item_0": ("product_qa", 0.95), "item_1": ("ops_noise", 0.4)}
        )

        result = classify_items("key", items)

    assert result == [
        {"text": "Debugged the DFEO bug", "category": "product_qa", "confidence": 0.95},
        {"text": "Monitored the hiring inbox", "category": "ops_noise", "confidence": 0.4},
    ]


def test_classify_items_embeds_each_item_text_in_its_own_question():
    items = ["First item text", "Second item text"]

    with patch("classify_log.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_choice_result(
            {"item_0": ("product_qa", 0.9), "item_1": ("product_qa", 0.9)}
        )

        classify_items("key", items)

    questions = mock_client.system_one.call_args.args[1]
    assert questions["item_0"].instructions["item"] == "First item text"
    assert questions["item_1"].instructions["item"] == "Second item text"


def test_classify_items_raises_on_api_failure():
    with patch("classify_log.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.side_effect = Exception("TypeSafe down")

        with pytest.raises(Exception, match="TypeSafe down"):
            classify_items("key", ["some item"])
```

Run: `pytest tests/test_classify_log.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add classify_log.py tests/test_classify_log.py
git commit -m "feat: classify Done bullets via Jev Choice"
```

---

### Task 8: `format_breakdown()`

**Files:**
- Modify: `classify_log.py`
- Test: `tests/test_classify_log.py`

**Interfaces:**
- Produces: `format_breakdown(classified_items: list[dict]) -> str`.

**Header format, verified against a real log** (`career-coach/logs/2026-09-22.md`): `- Product/QA (~65%):` and `- Ops (transferable, ~25%):` and `- Ops (noise, ~10%):` — the qualifier and percentage share one parenthetical. The first draft's code produced `Ops (transferable) (~25%):`, a double-parenthetical its own test didn't actually match against (the test asserted the correct format; the code just didn't produce it). This version builds each header explicitly per category rather than gluing a generic label to a percentage.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_classify_log.py`:

```python
from classify_log import format_breakdown


def test_format_breakdown_groups_and_computes_percentages():
    items = [
        {"text": "Debugged the DFEO bug", "category": "product_qa", "confidence": 0.95},
        {"text": "Wrote the questionnaire spec", "category": "product_qa", "confidence": 0.9},
        {"text": "Coached Ajmal in the 1:1", "category": "ops_transferable", "confidence": 0.8},
        {"text": "Monitored the hiring inbox", "category": "ops_noise", "confidence": 0.85},
    ]

    result = format_breakdown(items)

    assert "- Product/QA (~50%):" in result
    assert "    Debugged the DFEO bug" in result
    assert "    Wrote the questionnaire spec" in result
    assert "- Ops (transferable, ~25%):" in result
    assert "    Coached Ajmal in the 1:1" in result
    assert "- Ops (noise, ~25%):" in result
    assert "    Monitored the hiring inbox" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_classify_log.py::test_format_breakdown_groups_and_computes_percentages -v`
Expected: FAIL — `ImportError: cannot import name 'format_breakdown'`

- [ ] **Step 3: Write the implementation**

Add to `classify_log.py`:

```python
CATEGORY_ORDER = ["product_qa", "ops_transferable", "ops_noise"]
LOW_CONFIDENCE = 0.6


def _header(category: str, pct: int) -> str:
    if category == "product_qa":
        return f"- Product/QA (~{pct}%):"
    qualifier = "transferable" if category == "ops_transferable" else "noise"
    return f"- Ops ({qualifier}, ~{pct}%):"


def format_breakdown(classified_items: list[dict]) -> str:
    if not classified_items:
        return "No items to classify."

    total = len(classified_items)
    lines = [f"({total} Done entries, by count — not effort-weighted)", ""]
    for category in CATEGORY_ORDER:
        items_in_category = [i for i in classified_items if i["category"] == category]
        pct = round(100 * len(items_in_category) / total)
        lines.append(_header(category, pct))
        for item in items_in_category:
            suffix = " (low confidence — check by hand)" if item["confidence"] < LOW_CONFIDENCE else ""
            lines.append(f"    {item['text']}{suffix}")
    return "\n".join(lines).rstrip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_classify_log.py -v`
Expected: PASS.

- [ ] **Step 5: Add the remaining coverage**

Add to `tests/test_classify_log.py`:

```python
def test_format_breakdown_flags_low_confidence_items():
    items = [{"text": "Ambiguous item", "category": "ops_transferable", "confidence": 0.45}]

    result = format_breakdown(items)

    assert "    Ambiguous item (low confidence — check by hand)" in result


def test_format_breakdown_shows_zero_percent_categories():
    items = [{"text": "Only product work", "category": "product_qa", "confidence": 0.9}]

    result = format_breakdown(items)

    assert "- Ops (transferable, ~0%):" in result
    assert "- Ops (noise, ~0%):" in result


def test_format_breakdown_empty_returns_no_items_message():
    assert format_breakdown([]) == "No items to classify."
```

Run: `pytest tests/test_classify_log.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add classify_log.py tests/test_classify_log.py
git commit -m "feat: format classified items into a Breakdown block matching the log's header convention"
```

---

### Task 9: `classify_log.py` CLI

**Files:**
- Modify: `classify_log.py`
- Test: `tests/test_classify_log.py`

**Interfaces:**
- Produces: `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_classify_log.py`:

```python
from classify_log import main


def test_main_empty_log_prints_no_items_message(tmp_path, capsys):
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Done\n- \n\n### Slack\n- \n")

    with patch.dict("classify_log.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
        exit_code = main([str(log_file)])

    assert exit_code == 0
    assert "No items to classify." in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_classify_log.py::test_main_empty_log_prints_no_items_message -v`
Expected: FAIL — `ImportError: cannot import name 'main'`

- [ ] **Step 3: Write the implementation**

Add to `classify_log.py`:

```python
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("Usage: python3 classify_log.py <path-to-log.md>")
        return 1

    log_text = Path(argv[0]).read_text()
    items = extract_bullets(log_text)
    if not items:
        print(format_breakdown([]))
        return 0

    try:
        api_key = os.environ["TYPESAFE_API_KEY"]
        classified = classify_items(api_key, items)
    except Exception as e:
        print(f"Classification failed: {e}", file=sys.stderr)
        return 1

    print(format_breakdown(classified))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_classify_log.py -v`
Expected: PASS.

- [ ] **Step 5: Add the remaining coverage**

Add to `tests/test_classify_log.py`:

```python
def test_main_classifies_and_prints_breakdown(tmp_path, capsys):
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Done\n- Debugged the thing\n")

    with patch("classify_log.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_choice_result({"item_0": ("product_qa", 0.9)})

        with patch.dict("classify_log.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
            exit_code = main([str(log_file)])

    assert exit_code == 0
    assert "- Product/QA (~100%):" in capsys.readouterr().out


def test_main_api_failure_prints_clear_message_and_exits_1(tmp_path, capsys):
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Done\n- Debugged the thing\n")

    with patch("classify_log.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.side_effect = Exception("TypeSafe down")

        with patch.dict("classify_log.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
            exit_code = main([str(log_file)])

    assert exit_code == 1
    assert "Classification failed" in capsys.readouterr().err
```

Run: `pytest tests/test_classify_log.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Run the whole project suite**

Run: `pytest -v`
Expected: all tests PASS.

- [ ] **Step 7: Manual run against a real log**

```bash
python3 classify_log.py /Users/pontz/career-coach/logs/2026-09-23.md
```

Expected: a Breakdown block with computed percentages. Compare it against the hand-written Breakdown already in that day's log — read `CATEGORY_CRITERIA` closely if the categorization disagrees, and edit the wording rather than assume the model is right. This is also the moment to sanity-check `LOW_CONFIDENCE = 0.6` against real confidence values and adjust if it's flagging too much or too little.

- [ ] **Step 8: Commit**

```bash
git add classify_log.py tests/test_classify_log.py
git commit -m "feat: add classify_log.py CLI"
```

---

## Part 3 — Carry-Forward Tracker

### Task 10: State file load/save

**Files:**
- Create: `carry_forward.py`
- Test: `tests/test_carry_forward.py`

**Interfaces:**
- Produces: `load_state(path: Path) -> dict`, `save_state(path: Path, state: dict) -> None`. State shape: `{"items": [{"id": str, "text": str, "first_seen": str, "last_seen": str, "resolved": bool, "resolved_date": str | None}]}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_carry_forward.py`:

```python
import json
from pathlib import Path
from carry_forward import load_state, save_state


def test_load_state_missing_file_returns_empty(tmp_path):
    assert load_state(tmp_path / "does-not-exist.json") == {"items": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_carry_forward.py::test_load_state_missing_file_returns_empty -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'carry_forward'`

- [ ] **Step 3: Write the implementation**

Create `carry_forward.py`:

```python
import json
from pathlib import Path


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"items": []}
    return json.loads(path.read_text())


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_carry_forward.py -v`
Expected: PASS.

- [ ] **Step 5: Add the round-trip test**

Add to `tests/test_carry_forward.py`:

```python
def test_save_state_then_load_state_round_trips(tmp_path):
    path = tmp_path / ".carry-forward.json"
    state = {"items": [{"id": "abc123", "text": "Call Albert Hu", "first_seen": "2026-09-16",
                        "last_seen": "2026-09-23", "resolved": False, "resolved_date": None}]}

    save_state(path, state)
    result = load_state(path)

    assert result == state
```

Run: `pytest tests/test_carry_forward.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add carry_forward.py tests/test_carry_forward.py
git commit -m "feat: add carry-forward state load/save"
```

---

### Task 11: `extract_tomorrow_items()`

**Files:**
- Modify: `carry_forward.py`
- Test: `tests/test_carry_forward.py`

**Interfaces:**
- Produces: `extract_tomorrow_items(log_text: str) -> list[str]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_carry_forward.py`:

```python
from carry_forward import extract_tomorrow_items


def test_extract_tomorrow_items_pulls_unchecked_lines():
    text = (
        "### Tomorrow\n"
        "- [ ] Call Albert Hu\n"
        "- [x] Already done, should not appear\n"
        "- [ ] Follow up with Brian's email\n"
    )
    assert extract_tomorrow_items(text) == ["Call Albert Hu", "Follow up with Brian's email"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_carry_forward.py::test_extract_tomorrow_items_pulls_unchecked_lines -v`
Expected: FAIL — `ImportError: cannot import name 'extract_tomorrow_items'`

- [ ] **Step 3: Write the implementation**

Add to `carry_forward.py`:

```python
from log_writer import read_section


def extract_tomorrow_items(log_text: str) -> list[str]:
    section = read_section(log_text, "Tomorrow")
    items = []
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("- [ ]"):
            text = line[len("- [ ]"):].strip()
            if text:
                items.append(text)
    return items
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_carry_forward.py -v`
Expected: PASS.

- [ ] **Step 5: Add the blank-checkbox and missing-section cases**

Add to `tests/test_carry_forward.py`:

```python
def test_extract_tomorrow_items_skips_blank_checkbox():
    text = "### Tomorrow\n- [ ] \n- [ ] Real item\n"
    assert extract_tomorrow_items(text) == ["Real item"]


def test_extract_tomorrow_items_missing_section_returns_empty():
    assert extract_tomorrow_items("### Plan\n- [ ] something\n") == []
```

Run: `pytest tests/test_carry_forward.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add carry_forward.py tests/test_carry_forward.py
git commit -m "feat: extract unchecked Tomorrow items from a log"
```

---

### Task 12: `match_items()` and `filter_recurring()` via Choice/Noul

**Files:**
- Modify: `carry_forward.py`
- Test: `tests/test_carry_forward.py`

**Interfaces:**
- Produces:
  - `match_items(api_key: str, today_items: list[str], open_items: list[dict]) -> dict[str, dict]` — `open_items` entries have `"id"` and `"text"`. Returns `{today_text: {"matched_id": id_or_None, "confidence": float}}`. **Revised from the first draft:** now returns confidence alongside the match (the first draft returned only the id, so a wrong match had no visible signal), and a match below `MATCH_CONFIDENCE_FLOOR` is downgraded to `matched_id: None` rather than trusted.
  - `filter_recurring(api_key: str, candidate_items: list[str]) -> tuple[list[str], list[str]]` — new. Returns `(non_recurring, recurring)`. Only meant to be called with items that `match_items` found no match for (recurring-ness only needs judging once, when an item is first seen, not every day it reappears).

- [ ] **Step 1: Write the failing test — no open items skips the API call, everything is new**

Add to `tests/test_carry_forward.py`:

```python
from unittest.mock import MagicMock, patch
from carry_forward import match_items


def test_match_items_no_open_items_marks_everything_new_without_api_call():
    with patch("carry_forward.TypeSafeClient") as mock_class:
        result = match_items("key", ["Call Albert Hu"], [])

    assert result == {"Call Albert Hu": {"matched_id": None, "confidence": 1.0}}
    mock_class.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_carry_forward.py::test_match_items_no_open_items_marks_everything_new_without_api_call -v`
Expected: FAIL — `ImportError: cannot import name 'match_items'`

- [ ] **Step 3: Write the implementation**

Add to `carry_forward.py`:

```python
from typesafe_sdk import Choice, Noul, TypeSafeClient

NEW_OPTION = "new"
NEW_OPTION_DESCRIPTION = "This does not match any of the other items — it is a new task not previously listed."
MATCH_CONFIDENCE_FLOOR = 0.5
RECURRING_THRESHOLD = 0.5


def match_items(api_key: str, today_items: list[str], open_items: list[dict]) -> dict[str, dict]:
    """Match each today_item against open_items via one Jev Choice call.

    Returns {today_text: {"matched_id": id_or_None, "confidence": float}}.
    Skips the API call entirely when either list is empty.
    """
    if not today_items:
        return {}
    if not open_items:
        return {text: {"matched_id": None, "confidence": 1.0} for text in today_items}

    criteria = {item["id"]: item["text"] for item in open_items}
    criteria[NEW_OPTION] = NEW_OPTION_DESCRIPTION

    questions = {
        f"today_{i}": Choice(
            instructions={
                "today_item": text,
                "question": "Which existing open item does `today_item` refer to, if any?",
            },
            criteria=criteria,
        )
        for i, text in enumerate(today_items)
    }

    client = TypeSafeClient(api_key=api_key, model="jev-latest", timeout=60.0)
    result = client.system_one({}, questions)

    matches = {}
    for i, text in enumerate(today_items):
        answer = result.choices[f"today_{i}"]
        matched_id = None if answer.choice == NEW_OPTION else answer.choice
        if matched_id is not None and answer.confidence < MATCH_CONFIDENCE_FLOOR:
            matched_id = None  # low-confidence guess — treat as new rather than risk a bad merge
        matches[text] = {"matched_id": matched_id, "confidence": answer.confidence}
    return matches


def filter_recurring(api_key: str, candidate_items: list[str]) -> tuple[list[str], list[str]]:
    """Split candidate new items into (non_recurring, recurring). No API call if empty.

    Only call this with items match_items found no match for — a recurring
    item only needs judging once, the day it's first seen.
    """
    if not candidate_items:
        return [], []

    questions = {
        f"cand_{i}": Noul(
            instructions={
                "item": text,
                "question": (
                    "Is `item` a recurring or standing task, or a scheduled meeting, "
                    "rather than a one-off carry-forward item?"
                ),
            },
        )
        for i, text in enumerate(candidate_items)
    }
    client = TypeSafeClient(api_key=api_key, model="jev-latest", timeout=60.0)
    result = client.system_one({}, questions)

    non_recurring, recurring = [], []
    for i, text in enumerate(candidate_items):
        score = result.nouls[f"cand_{i}"].noul
        if score > RECURRING_THRESHOLD:
            recurring.append(text)
            print(f"skipped as recurring (noul={score:.2f}): {text}")
        else:
            non_recurring.append(text)
    return non_recurring, recurring
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_carry_forward.py -v`
Expected: PASS.

- [ ] **Step 5: Add the remaining coverage**

Add to `tests/test_carry_forward.py`:

```python
import pytest


def _mock_choice_result(answers: dict[str, tuple[str, float]]):
    result = MagicMock()
    result.choices = {k: MagicMock(choice=c, confidence=conf) for k, (c, conf) in answers.items()}
    return result


def _mock_noul_result(nouls: dict[str, float]):
    result = MagicMock()
    result.nouls = {k: MagicMock(noul=v) for k, v in nouls.items()}
    return result


def test_match_items_returns_matched_id_and_confidence():
    open_items = [{"id": "abc123", "text": "Call Albert Hu"}]
    today_items = ["Call Albert Hu — send two time slots", "Write the outage post-mortem"]

    with patch("carry_forward.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_choice_result(
            {"today_0": ("abc123", 0.91), "today_1": ("new", 0.98)}
        )

        result = match_items("key", today_items, open_items)

    assert result == {
        "Call Albert Hu — send two time slots": {"matched_id": "abc123", "confidence": 0.91},
        "Write the outage post-mortem": {"matched_id": None, "confidence": 0.98},
    }


def test_match_items_downgrades_low_confidence_match_to_new():
    open_items = [{"id": "abc123", "text": "Call Albert Hu"}]

    with patch("carry_forward.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_choice_result({"today_0": ("abc123", 0.3)})

        result = match_items("key", ["Vaguely similar item"], open_items)

    assert result["Vaguely similar item"]["matched_id"] is None


def test_match_items_raises_on_api_failure():
    with patch("carry_forward.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.side_effect = Exception("TypeSafe down")

        with pytest.raises(Exception, match="TypeSafe down"):
            match_items("key", ["today item"], [{"id": "x", "text": "open item"}])


def test_filter_recurring_empty_makes_no_api_call():
    from carry_forward import filter_recurring

    with patch("carry_forward.TypeSafeClient") as mock_class:
        result = filter_recurring("key", [])

    assert result == ([], [])
    mock_class.assert_not_called()


def test_filter_recurring_splits_by_threshold():
    from carry_forward import filter_recurring

    candidates = ["Call Albert Hu", "Sales and Ops meeting"]

    with patch("carry_forward.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_noul_result({"cand_0": 0.1, "cand_1": 0.85})

        non_recurring, recurring = filter_recurring("key", candidates)

    assert non_recurring == ["Call Albert Hu"]
    assert recurring == ["Sales and Ops meeting"]
```

Run: `pytest tests/test_carry_forward.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add carry_forward.py tests/test_carry_forward.py
git commit -m "feat: match today's items via Jev Choice, filter recurring items via Noul"
```

---

### Task 13: `update_state()`

**Files:**
- Modify: `carry_forward.py`
- Test: `tests/test_carry_forward.py`

**Interfaces:**
- Produces: `update_state(state: dict, today: str, today_items: list[str], matches: dict[str, dict], non_recurring_new_texts: list[str]) -> dict`. Genuinely pure — copies each item dict before modifying, rather than mutating the caller's `state` in place (the first draft claimed to be pure but wasn't; the review caught it). `matches` is `match_items`'s return shape from Task 12. `non_recurring_new_texts` is the first element of `filter_recurring`'s return from Task 12 — only these unmatched items get added as new state entries; anything filtered as recurring is never tracked.

**Two bug fixes from the first draft, both load-bearing (see spec Part 3):**
1. The resolution sweep (marking untouched open items as resolved) only runs when `today_items` is non-empty. An empty or unfilled `### Tomorrow` must not wipe every tracked item's history.
2. Recurring items are excluded from tracking entirely, not just reported without judgment.

- [ ] **Step 1: Write the failing test — adds a new item, does not mutate the input**

Add to `tests/test_carry_forward.py`:

```python
from carry_forward import update_state


def test_update_state_adds_new_item_and_does_not_mutate_input():
    state = {"items": []}
    matches = {"Call Albert Hu": {"matched_id": None, "confidence": 1.0}}

    result = update_state(state, "2026-09-24", ["Call Albert Hu"], matches, ["Call Albert Hu"])

    assert state == {"items": []}  # input untouched
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["text"] == "Call Albert Hu"
    assert item["first_seen"] == "2026-09-24"
    assert item["last_seen"] == "2026-09-24"
    assert item["resolved"] is False
    assert item["resolved_date"] is None
    assert isinstance(item["id"], str) and item["id"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_carry_forward.py::test_update_state_adds_new_item_and_does_not_mutate_input -v`
Expected: FAIL — `ImportError: cannot import name 'update_state'`

- [ ] **Step 3: Write the implementation**

Add to `carry_forward.py`:

```python
import uuid


def update_state(state: dict, today: str, today_items: list[str], matches: dict[str, dict],
                  non_recurring_new_texts: list[str]) -> dict:
    items_by_id = {item["id"]: dict(item) for item in state["items"]}  # copy, don't mutate input
    matched_ids = set()
    non_recurring_set = set(non_recurring_new_texts)

    for text in today_items:
        match = matches.get(text, {"matched_id": None, "confidence": 0.0})
        matched_id = match["matched_id"]
        if matched_id is not None and matched_id in items_by_id:
            items_by_id[matched_id]["last_seen"] = today
            matched_ids.add(matched_id)
        elif text in non_recurring_set:
            new_id = uuid.uuid4().hex[:8]
            items_by_id[new_id] = {
                "id": new_id, "text": text, "first_seen": today, "last_seen": today,
                "resolved": False, "resolved_date": None,
            }
            matched_ids.add(new_id)
        # else: unmatched and filtered as recurring — never tracked.

    if today_items:  # only resolve when today actually had items to compare against
        for item_id, item in items_by_id.items():
            if not item["resolved"] and item_id not in matched_ids:
                item["resolved"] = True
                item["resolved_date"] = today

    return {"items": list(items_by_id.values())}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_carry_forward.py -v`
Expected: PASS.

- [ ] **Step 5: Add the remaining coverage, including the two bug-fix regression tests**

Add to `tests/test_carry_forward.py`:

```python
def test_update_state_bumps_last_seen_on_match():
    state = {"items": [{"id": "abc123", "text": "Call Albert Hu", "first_seen": "2026-09-16",
                        "last_seen": "2026-09-23", "resolved": False, "resolved_date": None}]}
    matches = {"Call Albert Hu again": {"matched_id": "abc123", "confidence": 0.9}}

    result = update_state(state, "2026-09-24", ["Call Albert Hu again"], matches, [])

    item = result["items"][0]
    assert item["id"] == "abc123"
    assert item["first_seen"] == "2026-09-16"
    assert item["last_seen"] == "2026-09-24"
    assert item["resolved"] is False


def test_update_state_resolves_items_not_matched_today():
    state = {"items": [{"id": "abc123", "text": "Call Albert Hu", "first_seen": "2026-09-16",
                        "last_seen": "2026-09-23", "resolved": False, "resolved_date": None}]}

    result = update_state(state, "2026-09-24", ["Something unrelated"],
                           {"Something unrelated": {"matched_id": None, "confidence": 1.0}},
                           ["Something unrelated"])

    item = next(i for i in result["items"] if i["id"] == "abc123")
    assert item["resolved"] is True
    assert item["resolved_date"] == "2026-09-24"


def test_update_state_empty_today_items_does_not_resolve_anything():
    """Regression test for the bug the first draft shipped: an empty or
    unfilled Tomorrow section must not wipe every tracked item's history."""
    state = {"items": [{"id": "abc123", "text": "Call Albert Hu", "first_seen": "2026-09-16",
                        "last_seen": "2026-09-23", "resolved": False, "resolved_date": None}]}

    result = update_state(state, "2026-09-24", [], {}, [])

    item = result["items"][0]
    assert item["resolved"] is False
    assert item["resolved_date"] is None
    assert item["last_seen"] == "2026-09-23"  # untouched


def test_update_state_excludes_recurring_items_from_tracking():
    result = update_state(
        {"items": []}, "2026-09-24", ["Sales and Ops meeting"],
        {"Sales and Ops meeting": {"matched_id": None, "confidence": 1.0}},
        non_recurring_new_texts=[],  # filter_recurring judged it recurring, so it's not in this list
    )

    assert result["items"] == []


def test_update_state_leaves_already_resolved_items_alone():
    state = {"items": [{"id": "abc123", "text": "Old item", "first_seen": "2026-09-01",
                        "last_seen": "2026-09-05", "resolved": True, "resolved_date": "2026-09-05"}]}

    result = update_state(state, "2026-09-24", [], {}, [])

    item = result["items"][0]
    assert item["resolved"] is True
    assert item["resolved_date"] == "2026-09-05"
```

Run: `pytest tests/test_carry_forward.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add carry_forward.py tests/test_carry_forward.py
git commit -m "feat: update carry-forward state — pure, guards against empty-day resolution, excludes recurring items"
```

---

### Task 14: `format_carry_report()`

**Files:**
- Modify: `carry_forward.py`
- Test: `tests/test_carry_forward.py`

**Interfaces:**
- Produces: `format_carry_report(state: dict, today: str, match_log: list[dict]) -> str`. `match_log` entries: `{"today_text": str, "matched_text": str, "confidence": float}` — only for items that actually matched something (built by the CLI in Task 15). **Revised from the first draft:** now shows the match decisions before the days-open report, so a wrong merge is visible rather than hidden.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_carry_forward.py`:

```python
from carry_forward import format_carry_report


def test_format_carry_report_sorts_oldest_first_and_excludes_resolved():
    state = {"items": [
        {"id": "a", "text": "Newer item", "first_seen": "2026-09-22", "last_seen": "2026-09-24",
         "resolved": False, "resolved_date": None},
        {"id": "b", "text": "Older item", "first_seen": "2026-09-16", "last_seen": "2026-09-24",
         "resolved": False, "resolved_date": None},
        {"id": "c", "text": "Resolved item", "first_seen": "2026-09-01", "last_seen": "2026-09-05",
         "resolved": True, "resolved_date": "2026-09-05"},
    ]}

    result = format_carry_report(state, "2026-09-24", [])

    lines = result.splitlines()
    assert lines[0] == "Older item — open 8 days (since 2026-09-16)"
    assert lines[1] == "Newer item — open 2 days (since 2026-09-22)"
    assert "Resolved item" not in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_carry_forward.py::test_format_carry_report_sorts_oldest_first_and_excludes_resolved -v`
Expected: FAIL — `ImportError: cannot import name 'format_carry_report'`

- [ ] **Step 3: Write the implementation**

Add to `carry_forward.py`:

```python
from datetime import date


def format_carry_report(state: dict, today: str, match_log: list[dict]) -> str:
    match_lines = [
        f"matched: '{m['today_text']}' -> '{m['matched_text']}' (confidence {m['confidence']:.2f})"
        for m in match_log
    ]

    open_items = [item for item in state["items"] if not item["resolved"]]
    if not open_items:
        report_lines = ["No open items."]
    else:
        today_date = date.fromisoformat(today)
        open_items = sorted(open_items, key=lambda item: item["first_seen"])
        report_lines = []
        for item in open_items:
            days = (today_date - date.fromisoformat(item["first_seen"])).days
            day_word = "day" if days == 1 else "days"
            report_lines.append(f"{item['text']} — open {days} {day_word} (since {item['first_seen']})")

    if match_lines:
        return "\n".join(match_lines) + "\n\n" + "\n".join(report_lines)
    return "\n".join(report_lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_carry_forward.py -v`
Expected: PASS.

- [ ] **Step 5: Add the remaining coverage**

Add to `tests/test_carry_forward.py`:

```python
def test_format_carry_report_singular_day():
    state = {"items": [{"id": "a", "text": "Just started", "first_seen": "2026-09-23",
                        "last_seen": "2026-09-24", "resolved": False, "resolved_date": None}]}

    result = format_carry_report(state, "2026-09-24", [])

    assert "Just started — open 1 day (since 2026-09-23)" in result


def test_format_carry_report_no_open_items():
    assert format_carry_report({"items": []}, "2026-09-24", []) == "No open items."


def test_format_carry_report_shows_match_decisions():
    state = {"items": [{"id": "abc", "text": "Call Albert Hu", "first_seen": "2026-09-16",
                        "last_seen": "2026-09-24", "resolved": False, "resolved_date": None}]}
    match_log = [{"today_text": "Call Albert Hu — send two slots", "matched_text": "Call Albert Hu",
                  "confidence": 0.91}]

    result = format_carry_report(state, "2026-09-24", match_log)

    assert "matched: 'Call Albert Hu — send two slots' -> 'Call Albert Hu' (confidence 0.91)" in result
```

Run: `pytest tests/test_carry_forward.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add carry_forward.py tests/test_carry_forward.py
git commit -m "feat: format the carry-forward report with match decisions shown"
```

---

### Task 15: `carry_forward.py` CLI

**Files:**
- Modify: `carry_forward.py`
- Test: `tests/test_carry_forward.py`

**Interfaces:**
- Produces: `main(argv: list[str] | None = None) -> int`.

**The date bug fix, load-bearing (see spec Part 3):** `today` is parsed from the log filename (`date.fromisoformat(Path(log_path).stem)`), not `date.today()`. The first draft used the system clock, which its own manual-test instructions (run today's script against yesterday's log) would have silently gotten wrong. The CLI additionally refuses to run — one clear message, exit 1 — when the log's date is older than the newest `last_seen` already tracked, since processing an old log after state has already moved past it would corrupt history for no benefit.

- [ ] **Step 1: Write the failing test — refuses to run on an out-of-order log**

Add to `tests/test_carry_forward.py`:

```python
import json
from carry_forward import main


def test_main_refuses_to_run_on_a_log_older_than_tracked_state(tmp_path, capsys):
    log_file = tmp_path / "2026-09-20.md"  # older than the state file's newest last_seen
    log_file.write_text("### Tomorrow\n- [ ] Something\n")
    state_path = tmp_path / ".carry-forward.json"
    state_path.write_text(json.dumps({"items": [
        {"id": "abc", "text": "Existing", "first_seen": "2026-09-16", "last_seen": "2026-09-23",
         "resolved": False, "resolved_date": None},
    ]}))

    with patch.dict("carry_forward.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
        exit_code = main([str(log_file), "--state", str(state_path)])

    assert exit_code == 1
    assert "older than" in capsys.readouterr().err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_carry_forward.py::test_main_refuses_to_run_on_a_log_older_than_tracked_state -v`
Expected: FAIL — `ImportError: cannot import name 'main'`

- [ ] **Step 3: Write the implementation**

Add to `carry_forward.py`:

```python
import argparse
import os
import sys

from dotenv import load_dotenv


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(description="Track carry-forward Tomorrow items across days.")
    parser.add_argument("log_path", type=Path)
    parser.add_argument("--state", type=Path, default=None, help="Defaults to <LOG_DIR>/.carry-forward.json")
    args = parser.parse_args(argv)

    try:
        today = date.fromisoformat(args.log_path.stem)
    except ValueError:
        print(f"Could not parse a date from log filename: {args.log_path.stem}", file=sys.stderr)
        return 1

    state_path = args.state or (Path(os.environ["LOG_DIR"]) / ".carry-forward.json")
    state = load_state(state_path)

    last_seen_dates = [item["last_seen"] for item in state["items"]]
    if last_seen_dates and today.isoformat() < max(last_seen_dates):
        print(
            f"Refusing to run: log date {today.isoformat()} is older than the newest "
            f"tracked date {max(last_seen_dates)}.",
            file=sys.stderr,
        )
        return 1

    log_text = args.log_path.read_text()
    today_items = extract_tomorrow_items(log_text)
    open_items = [item for item in state["items"] if not item["resolved"]]

    try:
        api_key = os.environ["TYPESAFE_API_KEY"]
        matches = match_items(api_key, today_items, open_items)
        unmatched = [text for text, m in matches.items() if m["matched_id"] is None]
        non_recurring, _recurring = filter_recurring(api_key, unmatched)
    except Exception as e:
        print(f"Carry-forward matching failed: {e}", file=sys.stderr)
        return 1

    state = update_state(state, today.isoformat(), today_items, matches, non_recurring)
    save_state(state_path, state)

    items_by_id = {item["id"]: item["text"] for item in state["items"]}
    match_log = [
        {"today_text": text, "matched_text": items_by_id.get(m["matched_id"], "?"), "confidence": m["confidence"]}
        for text, m in matches.items() if m["matched_id"] is not None
    ]
    print(format_carry_report(state, today.isoformat(), match_log))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Add `from datetime import date` and `from pathlib import Path` to the top of `carry_forward.py` if not already present from earlier tasks.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_carry_forward.py -v`
Expected: PASS.

- [ ] **Step 5: Add the remaining coverage**

Add to `tests/test_carry_forward.py`:

```python
def test_main_runs_full_cycle_and_persists_state(tmp_path):
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Tomorrow\n- [ ] Call Albert Hu\n")
    state_path = tmp_path / ".carry-forward.json"

    with patch("carry_forward.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        # match_items skips the API call (no open items yet); filter_recurring is the only real call.
        mock_client.system_one.return_value = _mock_noul_result({"cand_0": 0.1})

        with patch.dict("carry_forward.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
            exit_code = main([str(log_file), "--state", str(state_path)])

    assert exit_code == 0
    assert state_path.exists()
    saved = json.loads(state_path.read_text())
    assert saved["items"][0]["text"] == "Call Albert Hu"
    assert saved["items"][0]["first_seen"] == "2026-09-24"


def test_main_api_failure_prints_clear_message_and_exits_1(tmp_path, capsys):
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Tomorrow\n- [ ] Something\n")
    state_path = tmp_path / ".carry-forward.json"

    with patch("carry_forward.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.side_effect = Exception("TypeSafe down")

        with patch.dict("carry_forward.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
            exit_code = main([str(log_file), "--state", str(state_path)])

    assert exit_code == 1
    assert "Carry-forward matching failed" in capsys.readouterr().err
    assert not state_path.exists()  # never got to save_state


def test_main_empty_tomorrow_does_not_resolve_existing_items(tmp_path):
    """End-to-end regression test for the empty-day resolution bug."""
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Tomorrow\n- [ ] \n")
    state_path = tmp_path / ".carry-forward.json"
    state_path.write_text(json.dumps({"items": [
        {"id": "abc", "text": "Old open item", "first_seen": "2026-09-10", "last_seen": "2026-09-23",
         "resolved": False, "resolved_date": None},
    ]}))

    with patch.dict("carry_forward.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
        exit_code = main([str(log_file), "--state", str(state_path)])

    assert exit_code == 0
    saved = json.loads(state_path.read_text())
    item = saved["items"][0]
    assert item["resolved"] is False
    assert "Old open item — open 14 days" in format_carry_report(saved, "2026-09-24", [])
```

Run: `pytest tests/test_carry_forward.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Run the whole project suite**

Run: `pytest -v`
Expected: all tests PASS.

- [ ] **Step 7: Manual run against real logs**

```bash
python3 carry_forward.py /Users/pontz/career-coach/logs/2026-09-23.md --state /Users/pontz/career-coach/logs/.carry-forward.json
```

First run: no existing state, so everything in that day's `### Tomorrow` either gets tracked fresh (`first_seen = 2026-09-23`) or is dropped as recurring — check the "skipped as recurring" lines against the real list (it includes "Monitor hiring inbox and #training channel," a strong candidate) and confirm the threshold is catching what it should. Then run it again against `2026-09-24.md` (once that log exists) to confirm matching bumps `last_seen` on a reworded item rather than duplicating it, and that the refusal guard doesn't trip on forward progress.

- [ ] **Step 8: Commit state tracking in career-coach**

```bash
cd /Users/pontz/career-coach
git add logs/.carry-forward.json
git commit -m "chore: add carry-forward state tracking"
```

- [ ] **Step 9: Commit in daily-record**

```bash
cd /Users/pontz/Projects/daily-record
git add carry_forward.py tests/test_carry_forward.py
git commit -m "feat: add carry_forward.py CLI"
```

---

## Part 4 — Docs

### Task 16: README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add sections documenting the three new tools**

In `README.md`, after the existing usage instructions, add:

```markdown
## Classifying a day's Breakdown

`classify_log.py` reads a career-coach log file, classifies each `### Done` bullet into
Product/QA, Ops (transferable), or Ops (noise) via a Jev Choice call, and prints a markdown
Breakdown block with computed percentages. Percentages are a bullet count, not
effort-weighted — a grounding reference for the hand-written Review, not a replacement for it.

    python3 classify_log.py /path/to/logs/2026-09-24.md

Requires `TYPESAFE_API_KEY` in `.env`. Writes nothing to disk — read the stdout output directly.

## Tracking carry-forward items

`carry_forward.py` matches today's `### Tomorrow` checklist against a running list of
previously-open items (tracked in `.carry-forward.json` next to your logs, git-tracked), via
a Jev Choice call, and prints a days-open report with each match decision shown. Recurring or
standing items (meetings, routine monitoring) are filtered out via a Jev Noul before tracking,
so they don't accumulate a misleading day-count.

    python3 carry_forward.py /path/to/logs/2026-09-24.md [--state /path/to/logs/.carry-forward.json]

Requires `TYPESAFE_API_KEY` in `.env`. `--state` defaults to `<LOG_DIR>/.carry-forward.json`.
The tool derives "today" from the log's filename, not the system clock — always pass the log
for the day you actually want tracked, and run it in date order.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document classify_log.py and carry_forward.py"
```
