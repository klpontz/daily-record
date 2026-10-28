import re
from pathlib import Path
from datetime import date, timedelta

TOMORROW_PLACEHOLDER = "_For YYYY-MM-DD_"


def get_log_path(log_dir: str, target_date: date = None) -> Path:
    if target_date is None:
        target_date = date.today()
    return Path(log_dir) / f"{target_date.isoformat()}.md"


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


def overwrite_section(text: str, section_name: str, new_content: str) -> str:
    """Replace content under a ### section header, preserving all other sections.

    Finds '### section_name' and replaces everything under it until the next
    '### ' header or end of file. If section not found, appends it.
    """
    pattern = rf'(### {re.escape(section_name)}\n)(.*?)(?=\n### |\Z)'
    if re.search(pattern, text, flags=re.DOTALL):
        replacement = f'### {section_name}\n{new_content}\n'
        return re.sub(pattern, replacement, text, flags=re.DOTALL)
    # Section not found — append it
    return text.rstrip('\n') + f'\n\n### {section_name}\n{new_content}\n'


def next_workday(day: date) -> date:
    """Return the next Monday-to-Friday date after day. Holidays are not skipped."""
    nxt = day + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def fill_tomorrow_date(text: str, log_date: date) -> str:
    """Replace the Tomorrow placeholder with the next workday. Edited lines stay as they are."""
    return text.replace(TOMORROW_PLACEHOLDER, f"_For {next_workday(log_date).isoformat()}_")


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

    try:
        log_date = date.fromisoformat(log_path.stem)
    except ValueError:
        log_date = None
    if log_date is not None:
        text = fill_tomorrow_date(text, log_date)

    log_path.write_text(text)
