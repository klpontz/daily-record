import pytest
from pathlib import Path
from datetime import date
from log_writer import get_log_path, overwrite_section, write_log, read_section

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


def test_overwrite_section_is_idempotent_with_same_content():
    result1 = overwrite_section(SAMPLE_LOG, "Slack", "- stable content")
    result2 = overwrite_section(result1, "Slack", "- stable content")
    assert result1 == result2
    assert result2.count("### Slack") == 1


def test_read_section_extracts_middle_section():
    text = "### Plan\n- a\n\n### Done\n- b\n- c\n\n### Slack\n-\n"
    assert read_section(text, "Done") == "- b\n- c"


def test_read_section_handles_zero_content_section_without_bleeding_into_next():
    text = "### Done\n### Slack\n- x\n### Asana\n- y\n"
    assert read_section(text, "Done") == ""
    assert read_section(text, "Slack") == "- x"


def test_read_section_missing_returns_empty_string():
    assert read_section("### Plan\n- a\n", "Done") == ""


# --- Tomorrow date prefill ---

from log_writer import next_workday, fill_tomorrow_date

TEMPLATE_WITH_PLACEHOLDER = "### Tomorrow\n_For YYYY-MM-DD_\n- [ ] \n"


def test_next_workday_weekday_is_next_day():
    assert next_workday(date(2026, 9, 30)) == date(2026, 10, 1)  # Wed -> Thu


def test_next_workday_friday_skips_to_monday():
    assert next_workday(date(2026, 10, 2)) == date(2026, 10, 5)


def test_next_workday_saturday_skips_to_monday():
    assert next_workday(date(2026, 10, 3)) == date(2026, 10, 5)


def test_fill_tomorrow_date_replaces_placeholder():
    result = fill_tomorrow_date(TEMPLATE_WITH_PLACEHOLDER, date(2026, 10, 2))
    assert "_For 2026-10-05_" in result
    assert "YYYY-MM-DD" not in result


def test_fill_tomorrow_date_leaves_edited_line_alone():
    text = "### Tomorrow\n_For 2026-10-09_\n"
    assert fill_tomorrow_date(text, date(2026, 10, 2)) == text


def test_write_log_new_file_fills_date_from_log_name(tmp_path):
    template = tmp_path / "template.md"
    template.write_text(TEMPLATE_WITH_PLACEHOLDER + "\n### Slack\n- \n")
    log_path = tmp_path / "2026-10-02.md"
    write_log(log_path, str(template), {"Slack": "- hi"})
    assert "_For 2026-10-05_" in log_path.read_text()


def test_write_log_existing_file_fills_placeholder(tmp_path):
    # Obsidian creates the file from the raw template before pull-daily runs
    template = tmp_path / "template.md"
    template.write_text("unused")
    log_path = tmp_path / "2026-09-30.md"
    log_path.write_text(TEMPLATE_WITH_PLACEHOLDER + "\n### Slack\n- \n")
    write_log(log_path, str(template), {"Slack": "- hi"})
    assert "_For 2026-10-01_" in log_path.read_text()
