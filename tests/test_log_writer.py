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
